"""Dashboard tab showing model cards with status (multi-node support)."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo
from llauncher.ui.utils import format_uptime


def render_dashboard(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> None:
    """Render the dashboard view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.
    """
    st.header("📊 Dashboard")

    # Show node filter indicator
    if registry and len(registry) > 1:
        if selected_node:
            st.markdown(f"*Viewing: **{selected_node}** only*")
        else:
            st.markdown("*Viewing: All nodes*")

    # Check if we're editing a model
    editing_model = None
    for name in state.models:
        if st.session_state.get(f"editing_{name}"):
            editing_model = name
            break

    # Show edit form if editing
    if editing_model:
        render_edit_model(state)
        return

    # Add New Model section (collapsible)
    with st.expander("➕ Add New Model", expanded=False):
        render_add_model(state)

    # Get servers to display
    if registry and aggregator and selected_node:
        # Show only selected node
        servers = get_node_servers(aggregator, selected_node)
        show_local = selected_node == "local"
    elif registry and aggregator:
        # Show all remote nodes
        servers = aggregator.get_all_servers()
        show_local = False
    else:
        # Show only local
        servers = []
        show_local = True

    # Combine with local servers if needed
    if show_local:
        state.refresh()
        for port, server in state.running.items():
            servers.append(
                RemoteServerInfo(
                    node_name="local",
                    pid=server.pid,
                    port=server.port,
                    config_name=server.config_name,
                    start_time=server.start_time.isoformat(),
                    uptime_seconds=server.uptime_seconds(),
                    logs_path=server.logs_path,
                )
            )

    if not servers and not state.models:
        st.info("No models configured. Use the 'Add New Model' section above to add one.")
        return

    st.divider()
    st.subheader("Models")

    # Build a map of running servers for quick lookup (node_name, config_name) -> server info
    running_server_map: dict[tuple[str, str], RemoteServerInfo] = {}
    for server in servers:
        key = (server.node_name, server.config_name)
        running_server_map[key] = server

    # Get models to display
    if registry and aggregator and selected_node:
        # Show only selected node's models
        all_models = aggregator.get_all_models()
    elif registry and aggregator:
        # Show all models grouped by node
        all_models = aggregator.get_all_models()
    else:
        # Show only local models
        all_models = {"local": [m.to_dict() for m in state.models.values()]}

    if not all_models and not state.models:
        return

    # Render models by node
    for node_name, node_models in all_models.items():
        if selected_node and node_name != selected_node:
            continue

        st.markdown(f"**Node: {node_name}**")
        for model in node_models:
            # Check if this model is currently running
            running_server = running_server_map.get((node_name, model["name"]))
            render_model_card(state, registry, aggregator, node_name, model, running_server)


def get_node_servers(aggregator: RemoteAggregator, node_name: str) -> list[RemoteServerInfo]:
    """Get servers for a specific node."""
    all_servers = aggregator.get_all_servers()
    return [s for s in all_servers if s.node_name == node_name]



def render_model_card(
    state: LauncherState,
    registry: NodeRegistry | None,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model: dict,
    running_server: RemoteServerInfo | None = None,
) -> None:
    """Render a unified model card with all controls.

    Args:
        state: The launcher state.
        registry: NodeRegistry.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model: Model data dictionary.
        running_server: Server info if model is currently running, else None.
    """
    model_name = model["name"]
    is_running = running_server is not None

    # Header with Edit button
    header_col1, header_col2 = st.columns([4, 1])
    with header_col1:
        st.markdown(f"**{model_name}**")
    with header_col2:
        if st.button("✏️", use_container_width=True, key=f"edit_{node_name}_{model_name}"):
            if node_name == "local":
                st.session_state[f"editing_{model_name}"] = True
                st.rerun()
            else:
                st.toast("Remote model editing not yet supported", icon="ℹ️")

    st.divider()

    if is_running and running_server:
        # Running state
        st.markdown(f"Status: **🟢 Running** on port {running_server.port}")
        st.markdown(f"Uptime: {format_uptime(running_server.uptime_seconds)}")

        # API docs link
        if node_name == "local":
            st.markdown(
                f"[📖 API Docs](http://localhost:{running_server.port}/docs) | "
                f"[🔌 Models](http://localhost:{running_server.port}/v1/models)"
            )
        else:
            st.markdown(
                f"*Server running on remote node {node_name}. Access via node's IP.*"
            )

        st.divider()

        # Action buttons
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button(
                "⏹️ Stop Server",
                use_container_width=True,
                key=f"stop_{node_name}_{running_server.port}",
            ):
                if node_name == "local":
                    success, message = state.stop_server(running_server.port, caller="ui")
                elif aggregator:
                    result = aggregator.stop_on_node(node_name, running_server.port)
                    success = result.get("success", False) if result else False
                    message = result.get("message", "Unknown error") if result else "Failed"
                else:
                    success = False
                    message = "Cannot stop: no connection to node"

                if success:
                    st.success(message)
                else:
                    st.error(message)
                st.rerun()

        with action_col2:
            st.button("📄 View Logs", use_container_width=True, key=f"view_logs_{node_name}_{running_server.port}")

        # Toggleable logs expander
        with st.expander("▼ Live Logs (last 100 lines)", expanded=False):
            if st.button("🔄 Refresh", key=f"refresh_logs_{node_name}_{running_server.port}"):
                st.rerun()

            if node_name == "local":
                logs = stream_logs(pid=running_server.pid, lines=100)
            elif aggregator:
                logs = aggregator.get_logs_on_node(node_name, running_server.port, 100) or []
            else:
                logs = []

            if logs:
                st.code("\n".join(logs), language="bash", height=200)
            else:
                st.info("No logs available")

    else:
        # Stopped state
        default_port = model.get("default_port") or "Auto-allocate"
        st.markdown(f"Status: **⚫ Stopped**")
        st.markdown(f"Default Port: `{default_port}`")
        st.markdown(f"Model: `{model.get('model_path', '').split('/')[-1]}`")
        st.markdown(f"GPU Layers: `{model.get('n_gpu_layers', 'N/A')}`")

        st.divider()

        # Start button
        if st.button(
            "▶️ Start Server",
            use_container_width=True,
            key=f"start_{node_name}_{model_name}",
        ):
            if node_name == "local":
                # Local start
                config = state.models.get(model_name)
                if config:
                    valid, msg = state.can_start(config, caller="ui")
                    if valid:
                        success, message, _ = state.start_server(model_name, caller="ui")
                        if success:
                            st.success(message)
                        else:
                            st.error(message)
                    else:
                        st.error(f"Cannot start: {msg}")
                else:
                    st.error(f"Model config not found: {model_name}")
            elif aggregator:
                # Remote start
                result = aggregator.start_on_node(node_name, model_name)
                if result:
                    if result.get("success"):
                        st.toast(f"Starting {model_name} on {node_name}...", icon="▶️")
                    else:
                        st.toast(result.get("error", "Failed to start"), icon="❌")
                st.rerun()
            else:
                st.toast(
                    f"Cannot start remote model: no connection to {node_name}",
                    icon="❌"
                )


def render_add_model(state: LauncherState) -> None:
    """Render the form to add a new model.

    Args:
        state: The launcher state.
    """
    with st.form("add_model_form", clear_on_submit=True):
        name = st.text_input("Model Name", help="Unique identifier for this model")
        st.markdown("**Model Path**")
        st.caption(
            "Common locations: ~/.cache/llama.cpp/, ~/models/, /usr/share/llama.cpp/"
        )
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
                help="Leave as 0 for auto-allocation",
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

            col_adv6, col_adv7, col_adv8 = st.columns(3)
            with col_adv6:
                top_k = st.number_input(
                    "Top-K (optional)", min_value=0, value=40
                )
            with col_adv7:
                top_p = st.number_input(
                    "Top-P (optional)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.9,
                    step=0.01,
                )
            with col_adv8:
                min_p = st.number_input(
                    "Min-P (optional)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.1,
                    step=0.01,
                )

            reverse_prompt = st.text_input(
                "Reverse Prompt (optional)",
                help="Halt generation when this string is encountered",
            )

            extra_args = st.text_input(
                "Extra Args (optional)",
                help="Additional command-line arguments (e.g., '--mcp-config /path/to/file.json')",
            )

        submitted = st.form_submit_button("Add Model", use_container_width=True)

        if submitted:
            # Strip whitespace from inputs
            name = name.strip()
            model_path = model_path.strip()
            mmproj_path = mmproj_path.strip() if mmproj_path else None

            if not name or not model_path:
                st.error("Model name and path are required")
                return

            if name in state.models:
                st.error(f"Model '{name}' already exists")
                return

            try:
                from llauncher.models.config import ModelConfig
                from llauncher.core.config import ConfigStore

                default_port_val = default_port if default_port >= 1024 else None

                config = ModelConfig(
                    name=name,
                    model_path=model_path,
                    mmproj_path=mmproj_path,
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
                    top_p=top_p if top_p > 0 else None,
                    min_p=min_p if min_p > 0 else None,
                    reverse_prompt=reverse_prompt.strip() if reverse_prompt else None,
                    extra_args=extra_args.strip() if extra_args else "",
                )

                ConfigStore.add_model(config)
                state.models[name] = config
                st.success(f"Added model '{name}'")
                st.rerun()

            except Exception as e:
                st.error(f"Error adding model: {e}")


def render_edit_model(state: LauncherState, model_name: str | None = None) -> None:
    """Render the form to edit an existing model.

    Args:
        state: The launcher state.
        model_name: Name of the model to edit.
    """
    if model_name is None:
        for name in state.models:
            if st.session_state.get(f"editing_{name}"):
                model_name = name
                break

    if not model_name:
        return

    config = state.models.get(model_name)
    if not config:
        st.error(f"Model '{model_name}' not found")
        return

    st.subheader(f"✏️ Edit Model: {model_name}")

    with st.form("edit_model_form", clear_on_submit=True):
        st.text_input("Model Name", value=model_name, disabled=True)

        st.markdown("**Model Path**")
        model_path = st.text_input(
            "Model Path", value=config.model_path, help="Path to the GGUF file"
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            value=config.mmproj_path or "",
            help="Path to multimodal projector",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            default_port = st.number_input(
                "Default Port (optional)",
                min_value=1024,
                max_value=65535,
                value=config.default_port or 8080,
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=config.n_gpu_layers
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=config.ctx_size
            )

        col4, col5 = st.columns(2)
        with col4:
            threads = st.number_input(
                "Threads (optional)", min_value=0, value=config.threads or 0
            )
        with col5:
            flash_idx = ["on", "off", "auto"].index(config.flash_attn)
            flash_attn = st.selectbox(
                "Flash Attention", ["on", "off", "auto"], index=flash_idx
            )

        no_mmap = st.checkbox("Disable Memory Mapping (no-mmap)", value=config.no_mmap)

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
                    "CPU MOE Threads", min_value=0, value=config.n_cpu_moe or 0
                )
            with col_adv4:
                batch_size = st.number_input(
                    "Batch Size", min_value=0, value=config.batch_size or 0
                )
            with col_adv5:
                temperature = st.number_input(
                    "Temperature",
                    min_value=0.0,
                    value=config.temperature or 0.7,
                    step=0.1,
                )

            col_adv6, col_adv7, col_adv8 = st.columns(3)
            with col_adv6:
                top_k = st.number_input("Top-K", min_value=0, value=config.top_k or 40)
            with col_adv7:
                top_p = st.number_input(
                    "Top-P",
                    min_value=0.0,
                    max_value=1.0,
                    value=config.top_p or 0.9,
                    step=0.01,
                )
            with col_adv8:
                min_p = st.number_input(
                    "Min-P",
                    min_value=0.0,
                    max_value=1.0,
                    value=config.min_p or 0.1,
                    step=0.01,
                )

            reverse_prompt = st.text_input(
                "Reverse Prompt", value=config.reverse_prompt or ""
            )

            extra_args = st.text_input(
                "Extra Args",
                value=config.extra_args or "",
                help="Additional command-line arguments",
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
                        "top_p": top_p if top_p > 0 else None,
                        "min_p": min_p if min_p > 0 else None,
                        "reverse_prompt": reverse_prompt or None,
                        "extra_args": extra_args or "",
                    }
                )

                persisted_models = ConfigStore.load()
                if model_name in persisted_models:
                    ConfigStore.update_model(model_name, updated_config)
                    st.success(f"Updated model '{model_name}'")
                else:
                    ConfigStore.add_model(updated_config)
                    st.success(f"Saved model '{model_name}'")

                state.models[model_name] = updated_config
                del st.session_state[f"editing_{model_name}"]
                st.rerun()

            except Exception as e:
                st.error(f"Error saving model: {e}")
