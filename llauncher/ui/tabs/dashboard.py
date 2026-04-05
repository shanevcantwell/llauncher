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

    # Create grid of model cards
    cols = st.columns(2)
    col_idx = 0

    for name, config in state.models.items():
        col = cols[col_idx % 2]
        col_idx += 1

        with col:
            render_model_card(state, name, config)


def render_model_card(state: LauncherState, name: str, config) -> None:
    """Render a single model card.

    Args:
        state: The launcher state.
        name: Model name.
        config: Model configuration.
    """
    status_info = state.get_model_status(name)
    is_running = status_info.get("status") == "running"

    # Status badge
    if is_running:
        status_color = "🟢"
        status_text = "Running"
    else:
        status_color = "⚫"
        status_text = "Stopped"

    # Card header
    st.markdown(f"### {name} {status_color}")
    st.markdown(f"**{status_text}**")

    # Model info
    st.markdown(f"**Port:** {config.port}")
    st.markdown(f"**Model:** `{config.model_path.split('/')[-1]}`")
    st.markdown(f"**GPU Layers:** {config.n_gpu_layers}")
    st.markdown(f"**Context:** {config.ctx_size:,}")

    # Link to docs if running
    if is_running:
        st.markdown(
            f"[API Docs](http://localhost:{config.port}/docs) | "
            f"[Models](http://localhost:{config.port}/v1/models)"
        )

    # Start/Stop button
    col1, col2 = st.columns(2)

    with col1:
        if is_running:
            if st.button("⏹️ Stop", use_container_width=True, key=f"stop_{name}"):
                success, message = state.stop_server(config.port, caller="ui")
                if success:
                    st.success(message)
                else:
                    st.error(message)
                st.rerun()
        else:
            if st.button("▶️ Start", use_container_width=True, key=f"start_{name}"):
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

    with col2:
        st.button("⚙️ Edit", use_container_width=True, key=f"edit_{name}")

    st.divider()
