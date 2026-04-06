"""Manager tab for adding, editing, and deleting models."""

import streamlit as st

from llauncher.state import LauncherState


def render_manager(state: LauncherState) -> None:
    """Render the manager view.

    Args:
        state: The launcher state.
    """
    st.header("📁 Model Manager")

    # Sub-navigation
    tab1, tab2 = st.tabs(["List Models", "Add New Model"])

    with tab1:
        render_model_list(state)

    with tab2:
        render_add_model(state)


def render_model_list(state: LauncherState) -> None:
    """Render the list of configured models.

    Args:
        state: The launcher state.
    """
    if not state.models:
        st.info("No models configured. Use the 'Add New Model' tab to add one.")
        return

    for name, config in state.models.items():
        status_info = state.get_model_status(name)
        is_running = status_info.get("status") == "running"

        with st.expander(
            f"**{name}** {'🟢' if is_running else '⚫'}",
            expanded=False,
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Port**")
                st.markdown(f"**Model Path**")
                st.markdown(f"**GPU Layers**")
            with col2:
                st.markdown(f"`{config.port}`")
                st.markdown(f"`{config.model_path}`")
                st.markdown(f"`{config.n_gpu_layers}`")

            # Actions
            action_col1, action_col2 = st.columns(2)

            with action_col1:
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

            with action_col2:
                if st.button(
                    "✏️ Edit",
                    use_container_width=True,
                    key=f"edit_{name}",
                ):
                    st.session_state[f"editing_{name}"] = True
                    st.rerun()


def render_add_model(state: LauncherState) -> None:
    """Render the form to add a new model.

    Args:
        state: The launcher state.
    """
    st.subheader("Add New Model")

    with st.form("add_model_form", clear_on_submit=True):
        name = st.text_input("Model Name", help="Unique identifier for this model")
        model_path = st.text_input(
            "Model Path", help="Path to the GGUF file (e.g., /path/to/model.gguf)"
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            help="Path to multimodal projector for vision models",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            port = st.number_input(
                "Port", min_value=1024, max_value=65535, value=8080
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=255, value=255
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=131072
            )

        col4, col5, col6 = st.columns(3)
        with col4:
            host = st.text_input("Host", value="0.0.0.0")
        with col5:
            threads = st.number_input("Threads (optional)", min_value=0, value=0)
        with col6:
            flash_attn = st.selectbox("Flash Attention", ["on", "off", "auto"], index=0)

        no_mmap = st.checkbox("Disable Memory Mapping (no-mmap)", value=False)

        submitted = st.form_submit_button("Add Model", use_container_width=True)

        if submitted:
            if not name or not model_path:
                st.error("Model name and path are required")
                return

            if name in state.models:
                st.error(f"Model '{name}' already exists")
                return

            try:
                from llauncher.models.config import ModelConfig
                from llauncher.core.config import ConfigStore

                config = ModelConfig(
                    name=name,
                    model_path=model_path,
                    mmproj_path=mmproj_path or None,
                    port=port,
                    host=host,
                    n_gpu_layers=n_gpu_layers,
                    ctx_size=ctx_size,
                    threads=threads if threads > 0 else None,
                    flash_attn=flash_attn,
                    no_mmap=no_mmap,
                )

                ConfigStore.add_model(config)
                state.models[name] = config
                st.success(f"Added model '{name}'")
                st.rerun()

            except Exception as e:
                st.error(f"Error adding model: {e}")
