"""Dashboard tab showing model cards with status."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs


def render_dashboard(state: LauncherState) -> None:
    """Render the dashboard view.

    Args:
        state: The launcher state.
    """
    st.header("📊 Dashboard")

    # Check if we're editing a model
    editing_model = None
    for name in state.models:
        if st.session_state.get(f"editing_{name}"):
            editing_model = name
            break

    # Show edit form if editing
    if editing_model:
        render_edit_model(state, editing_model)
        return

    # Add New Model section (collapsible)
    with st.expander("➕ Add New Model", expanded=False):
        render_add_model(state)

    if not state.models:
        st.info("No models configured. Use the 'Add New Model' section above to add one.")
        return

    st.divider()
    st.subheader("Models")

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
            if is_running:
                # Show actual running port
                st.markdown(f"`{status_info.get('port')}` (running)")
            else:
                # Show default port or "Auto-allocate"
                default_port = config.default_port or "Auto-allocate"
                st.markdown(f"`{default_port}`")
            st.markdown(f"`{config.model_path.split('/')[-1]}`")
            st.markdown(f"`{config.n_gpu_layers}`")

        # Link to docs if running
        if is_running:
            running_port = status_info.get("port")
            st.markdown(
                f"[API Docs](http://localhost:{running_port}/docs) | "
                f"[Models](http://localhost:{running_port}/v1/models)"
            )

        st.divider()

        # Log viewer (always available, even if process crashed)
        log_col1, log_col2 = st.columns([4, 1])
        with log_col1:
            with st.expander("📄 Logs (last 200 lines)", expanded=False):
                logs = stream_logs(
                    pid=status_info.get("pid") if is_running else None,
                    model_name=name,
                    lines=200
                )
                if logs:
                    st.code("\n".join(logs), language="bash", height=300)
                else:
                    st.info("No logs available yet")
        with log_col2:
            if st.button("🔄 Refresh Logs", use_container_width=True, key=f"refresh_logs_{name}"):
                st.rerun()

        # Actions
        action_col1, action_col2, action_col3 = st.columns(3)

        with action_col1:
            if is_running:
                running_port = status_info.get("port")
                if st.button(
                    "⏹️ Stop",
                    use_container_width=True,
                    key=f"stop_{name}",
                ):
                    success, message = state.stop_server(running_port, caller="ui")
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
                st.rerun()

        with action_col3:
            if st.button(
                "🗑️ Delete",
                use_container_width=True,
                key=f"delete_{name}",
            ):
                if is_running:
                    running_port = status_info.get("port")
                    st.error(
                        f"Cannot delete {name}: server is running on port {running_port}"
                    )
                else:
                    from llauncher.core.config import ConfigStore

                    ConfigStore.remove_model(name)
                    del state.models[name]
                    st.success(f"Deleted {name}")
                    st.rerun()

        st.divider()


def render_add_model(state: LauncherState) -> None:
    """Render the form to add a new model.

    Args:
        state: The launcher state.
    """
    with st.form("add_model_form", clear_on_submit=True):
        name = st.text_input("Model Name", help="Unique identifier for this model")
        st.markdown("**Model Path**")
        st.caption("Common locations: ~/.cache/llama.cpp/, ~/models/, /usr/share/llama.cpp/")
        model_path = st.text_input(
            "Model Path", help="Path to the GGUF file (e.g., /path/to/model.gguf)"
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            help="Path to multimodal projector for vision models",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            default_port = st.number_input(
                "Default Port (optional)",
                min_value=1024,
                max_value=65535,
                value=8080,
                help="Leave as 0 for auto-allocation"
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=255
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=131072
            )

        col4, col5 = st.columns(2)
        with col4:
            threads = st.number_input("Threads (optional)", min_value=0, value=0)
        with col5:
            flash_attn = st.selectbox("Flash Attention", ["on", "off", "auto"], index=0)

        no_mmap = st.checkbox("Disable Memory Mapping (no-mmap)", value=False)

        # Additional options (expandable)
        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=1
                )
            with col_adv2:
                mlock = st.checkbox("Lock Memory in RAM (mlock)", value=False)

            col_adv3, col_adv4, col_adv5 = st.columns(3)
            with col_adv3:
                n_cpu_moe = st.number_input(
                    "CPU MOE Threads (-ncmoe, optional)", min_value=0, value=0
                )
            with col_adv4:
                batch_size = st.number_input(
                    "Batch Size (optional)", min_value=0, value=0
                )
            with col_adv5:
                temperature = st.number_input(
                    "Temperature (optional)", min_value=0.0, value=0.7, step=0.1
                )

            min_p = st.number_input(
                "Min-P (optional)", min_value=0.0, max_value=1.0, value=0.1, step=0.01
            )

            reverse_prompt = st.text_input(
                "Reverse Prompt (optional)",
                help="Halt generation when this string is encountered",
            )

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

                # Convert 0 to None for auto-allocation
                default_port_val = default_port if default_port >= 1024 else None

                config = ModelConfig(
                    name=name,
                    model_path=model_path,
                    mmproj_path=mmproj_path or None,
                    default_port=default_port_val,
                    n_gpu_layers=n_gpu_layers,
                    ctx_size=ctx_size,
                    threads=threads if threads > 0 else None,
                    flash_attn=flash_attn,
                    no_mmap=no_mmap,
                    parallel=parallel,
                    mlock=mlock,
                    n_cpu_moe=n_cpu_moe if n_cpu_moe > 0 else None,
                    batch_size=batch_size if batch_size > 0 else None,
                    temperature=temperature if temperature > 0 else None,
                    top_k=top_k if top_k > 0 else None,
                    min_p=min_p if min_p > 0 else None,
                    reverse_prompt=reverse_prompt or None,
                )

                ConfigStore.add_model(config)
                state.models[name] = config
                st.success(f"Added model '{name}'")
                st.rerun()

            except Exception as e:
                st.error(f"Error adding model: {e}")


def render_edit_model(state: LauncherState, model_name: str) -> None:
    """Render the form to edit an existing model.

    Args:
        state: The launcher state.
        model_name: Name of the model to edit.
    """
    config = state.models.get(model_name)
    if not config:
        st.error(f"Model '{model_name}' not found")
        return

    st.subheader(f"✏️ Edit Model: {model_name}")

    with st.form("edit_model_form", clear_on_submit=True):
        # Name is read-only during edit
        st.text_input("Model Name", value=model_name, disabled=True)

        st.markdown("**Model Path**")
        st.caption("Common locations: ~/.cache/llama.cpp/, ~/models/, /usr/share/llama.cpp/")
        model_path = st.text_input(
            "Model Path",
            value=config.model_path,
            help="Path to the GGUF file",
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            value=config.mmproj_path or "",
            help="Path to multimodal projector for vision models",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            default_port = st.number_input(
                "Default Port (optional)",
                min_value=1024,
                max_value=65535,
                value=config.default_port or 8080,
                help="Leave as 0 for auto-allocation"
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers",
                min_value=0,
                max_value=1024,
                value=config.n_gpu_layers,
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size",
                min_value=1024,
                value=config.ctx_size,
            )

        col4, col5 = st.columns(2)
        with col4:
            threads = st.number_input(
                "Threads (optional)",
                min_value=0,
                value=config.threads or 0,
            )
        with col5:
            flash_idx = ["on", "off", "auto"].index(config.flash_attn)
            flash_attn = st.selectbox(
                "Flash Attention",
                ["on", "off", "auto"],
                index=flash_idx,
            )

        no_mmap = st.checkbox(
            "Disable Memory Mapping (no-mmap)",
            value=config.no_mmap,
        )

        # Additional options (expandable)
        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=config.parallel
                )
            with col_adv2:
                mlock = st.checkbox("Lock Memory in RAM (mlock)", value=config.mlock)

            col_adv3, col_adv4, col_adv5 = st.columns(3)
            with col_adv3:
                n_cpu_moe = st.number_input(
                    "CPU MOE Threads (-ncmoe, optional)", min_value=0, value=config.n_cpu_moe or 0
                )
            with col_adv4:
                batch_size = st.number_input(
                    "Batch Size (optional)", min_value=0, value=config.batch_size or 0
                )
            with col_adv5:
                temperature = st.number_input(
                    "Temperature (optional)",
                    min_value=0.0,
                    value=config.temperature or 0.7,
                    step=0.1,
                )

            reverse_prompt = st.text_input(
                "Reverse Prompt (optional)",
                value=config.reverse_prompt or "",
                help="Halt generation when this string is encountered",
            )

        col_submit, col_cancel = st.columns(2)
        with col_submit:
            submitted = st.form_submit_button("Save Changes", use_container_width=True)
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel", use_container_width=True)

        if cancel_clicked:
            del st.session_state[f"editing_{model_name}"]
            st.rerun()

        if submitted:
            if not model_path:
                st.error("Model path is required")
                return

            try:
                from llauncher.core.config import ConfigStore

                updated_config = config.model_copy(
                    update={
                        "model_path": model_path,
                        "mmproj_path": mmproj_path or None,
                        "default_port": default_port if default_port >= 1024 else None,
                        "n_gpu_layers": n_gpu_layers,
                        "ctx_size": ctx_size,
                        "threads": threads if threads > 0 else None,
                        "flash_attn": flash_attn,
                        "no_mmap": no_mmap,
                        "parallel": parallel,
                        "mlock": mlock,
                        "n_cpu_moe": n_cpu_moe if n_cpu_moe > 0 else None,
                        "batch_size": batch_size if batch_size > 0 else None,
                        "temperature": temperature if temperature > 0 else None,
                        "top_k": top_k if top_k > 0 else None,
                        "min_p": min_p if min_p > 0 else None,
                        "reverse_prompt": reverse_prompt or None,
                    }
                )

                # Check if model exists in ConfigStore (persisted) or is discovered
                persisted_models = ConfigStore.load()
                if model_name in persisted_models:
                    ConfigStore.update_model(model_name, updated_config)
                    st.success(f"Updated model '{model_name}'")
                else:
                    # Discovered model - persist it now
                    ConfigStore.add_model(updated_config)
                    st.success(f"Saved model '{model_name}'")

                state.models[model_name] = updated_config
                del st.session_state[f"editing_{model_name}"]
                st.rerun()

            except Exception as e:
                st.error(f"Error saving model: {e}")
