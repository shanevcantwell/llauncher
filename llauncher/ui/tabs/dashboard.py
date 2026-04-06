"""Dashboard tab showing model cards with status."""

import streamlit as st

from llauncher.state import LauncherState


def render_dashboard(state: LauncherState) -> None:
    """Render the dashboard view.

    Args:
        state: The launcher state.
    """
    st.header("📊 Dashboard")

    if not state.models:
        st.info("No models configured. Go to the Manager tab to add models.")
        return

    # Single-column list of models
    for name, config in state.models.items():
        render_model_entry(state, name, config)


def render_model_entry(state: LauncherState, name: str, config) -> None:
    """Render a single model entry.

    Args:
        state: The launcher state.
        name: Model name.
        config: Model configuration.
    """
    status_info = state.get_model_status(name)
    is_running = status_info.get("status") == "running"

    # Status badge
    status_icon = "🟢" if is_running else "⚫"

    with st.expander(f"**{name}** {status_icon}", expanded=False):
        # Model info
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Port**")
            st.markdown(f"**Model**")
            st.markdown(f"**GPU Layers**")
        with col2:
            st.markdown(f"`{config.port}`")
            st.markdown(f"`{config.model_path.split('/')[-1]}`")
            st.markdown(f"`{config.n_gpu_layers}`")

        # Link to docs if running
        if is_running:
            st.markdown(
                f"[API Docs](http://localhost:{config.port}/docs) | "
                f"[Models](http://localhost:{config.port}/v1/models)"
            )

        st.divider()

        # Actions
        action_col1, action_col2, action_col3 = st.columns(3)

        with action_col1:
            if is_running:
                if st.button(
                    "⏹️ Stop",
                    use_container_width=True,
                    key=f"stop_{name}",
                ):
                    success, message = state.stop_server(config.port, caller="ui")
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                    st.rerun()
            else:
                if st.button(
                    "▶️ Start",
                    use_container_width=True,
                    key=f"start_{name}",
                ):
                    valid, msg = state.can_start(config, caller="ui")
                    if valid:
                        success, message, _ = state.start_server(name, caller="ui")
                        if success:
                            st.success(message)
                        else:
                            st.error(message)
                    else:
                        st.error(f"Cannot start: {msg}")
                    st.rerun()

        with action_col2:
            if st.button(
                "✏️ Edit",
                use_container_width=True,
                key=f"edit_{name}",
            ):
                st.session_state[f"editing_{name}"] = True
                st.session_state["selected_page"] = "Manager"
                st.rerun()

        with action_col3:
            if st.button(
                "🗑️ Delete",
                use_container_width=True,
                key=f"delete_{name}",
            ):
                if is_running:
                    st.error(
                        f"Cannot delete {name}: server is running on port {config.port}"
                    )
                else:
                    from llauncher.core.config import ConfigStore

                    ConfigStore.remove_model(name)
                    del state.models[name]
                    st.success(f"Deleted {name}")
                    st.rerun()

        st.divider()
