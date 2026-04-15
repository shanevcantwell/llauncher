"""Dashboard tab showing model cards with status (multi-node support)."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs, is_port_in_use
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo, NodeStatus
from llauncher.ui.utils import format_uptime


def get_servers_to_display(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> list[RemoteServerInfo]:
    """Get servers to display based on current view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.

    Returns:
        List of RemoteServerInfo to display.
    """
    servers = []

    if registry and aggregator and selected_node:
        # Show only selected node
        if selected_node == "local":
            # Local node only
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
        else:
            # Specific remote node
            servers.extend(get_node_servers(aggregator, selected_node))
    elif registry and aggregator:
        # Show all nodes (remote + local)
        servers.extend(aggregator.get_all_servers())
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
    else:
        # Show only local
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

    return servers


def get_models_to_display(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> dict[str, list[dict]]:
    """Get models to display based on current view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.

    Returns:
        Dictionary mapping node names to their model lists.
    """
    all_models = {}

    if registry and aggregator and selected_node:
        if selected_node == "local":
            # Show only local models when "local" node is selected
            all_models["local"] = [m.to_dict() for m in state.models.values()]
        else:
            # Show only selected remote node's models
            all_models = aggregator.get_all_models()
    elif registry and aggregator:
        # Show all models grouped by node (All Nodes view)
        all_models = aggregator.get_all_models()
        # Merge in local models for "All Nodes" view
        all_models["local"] = [m.to_dict() for m in state.models.values()]
    else:
        # Show only local models
        all_models["local"] = [m.to_dict() for m in state.models.values()]

    return all_models


def get_node_servers(aggregator: RemoteAggregator, node_name: str) -> list[RemoteServerInfo]:
    """Get servers for a specific node."""
    all_servers = aggregator.get_all_servers()
    return [s for s in all_servers if s.node_name == node_name]


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

    # Get servers and models to display using helper functions
    servers = get_servers_to_display(state, registry, aggregator, selected_node)
    all_models = get_models_to_display(state, registry, aggregator, selected_node)

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

    if not all_models and not state.models:
        return

    # Render models by node
    for node_name, node_models in all_models.items():
        if selected_node and node_name != selected_node:
            continue

        st.markdown(f"**Node: {node_name}**")
        # Sort models alphabetically by name (case-insensitive)
        sorted_models = sorted(node_models, key=lambda m: m["name"].lower())
        for model in sorted_models:
            # Check if this model is currently running
            running_server = running_server_map.get((node_name, model["name"]))
            render_model_card(state, registry, aggregator, node_name, model, running_server)


def render_model_card(
    state: LauncherState,
    registry: NodeRegistry | None,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model: dict,
    running_server: RemoteServerInfo | None = None,
) -> None:
    """Render a model card with inline toggle button and collapsed details.

    The status emoji (🟢/⚫) is the clickable toggle for start/stop.
    Details (port, logs, edit) are in an expander below.

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
    status_icon = "🟢" if is_running else "⚫"

    # Create a two-column row for model name and status button
    name_col, button_col = st.columns([4, 1])

    with name_col:
        st.markdown(f"**{model_name}**")

    # Status button is the clickable toggle (outside expander)
    with button_col:
        if is_running and running_server:
            if st.button(
                status_icon,
                key=f"toggle_stop_{node_name}_{running_server.port}",
                help=f"Stop {model_name}",
                use_container_width=True,
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
                    st.toast(message, icon="✅")
                else:
                    st.toast(message, icon="❌")
                st.rerun()
        else:
            _render_start_button(
                state, registry, aggregator, node_name, model_name, status_icon
            )

    # Collapsed expander for details (port, logs, edit button)
    with st.expander("📋 Details", expanded=False):
        _render_model_details(state, registry, aggregator, node_name, model_name, model, running_server)


def _render_start_button(
    state: LauncherState,
    registry: NodeRegistry | None,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    status_icon: str,
) -> None:
    """Render the start button with eviction confirmation flow.

    Args:
        state: The launcher state.
        registry: NodeRegistry.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model_name: Name of the model.
        status_icon: The status icon to display.
    """
    config = state.models.get(model_name)
    if config is None:
        st.button(
            status_icon,
            key=f"toggle_start_{node_name}_{model_name}",
            help="Model config not found",
            use_container_width=True,
            disabled=True,
        )
        return

    target_port = config.default_port

    # Normal start button - click to start (with eviction check on click)
    if st.button(
        status_icon,
        key=f"toggle_start_{node_name}_{model_name}",
        help=f"Start {model_name}",
        use_container_width=True,
    ):
        if node_name == "local":
            if config:
                # Check if port is in use by another of our servers
                temp_state = LauncherState()
                temp_state.refresh()

                if target_port in temp_state.running:
                    # Port is occupied by another llauncher server - show eviction dialog
                    _render_eviction_dialog(
                        state, node_name, target_port, model_name, status_icon
                    )
                else:
                    valid, msg = state.can_start(config, caller="ui")
                    if valid:
                        success, message, _ = state.start_server(model_name, caller="ui")
                        if success:
                            st.toast(message, icon="✅")
                        else:
                            st.toast(message, icon="❌")
                    else:
                        st.toast(f"Cannot start: {msg}", icon="❌")
            else:
                st.toast(f"Model config not found: {model_name}", icon="❌")
        elif aggregator:
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


def _render_eviction_dialog(
    state: LauncherState,
    node_name: str,
    port: int,
    model_name: str,
    status_icon: str,
) -> None:
    """Render eviction confirmation dialog.

    Args:
        state: The launcher state.
        node_name: Name of the node.
        port: Port that is in use.
        model_name: Name of the model to start.
        status_icon: The status icon to display.
    """
    # Get the model that's currently using this port
    existing_model = state.running.get(port)
    existing_name = existing_model.config_name if existing_model else "unknown"

    st.warning(
        f"Port {port} is in use by **{existing_name}**. Clicking **Confirm** will "
        "stop the existing server and start this one.",
        icon="⚠️",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "Cancel",
            key=f"evict_cancel_{node_name}_{port}_{model_name}",
            use_container_width=True,
        ):
            st.rerun()
    with col2:
        if st.button(
            "Confirm Eviction",
            key=f"evict_confirm_{node_name}_{port}_{model_name}",
            use_container_width=True,
            type="primary",
        ):
            success, message = state.start_with_eviction(model_name, port, caller="ui")
            if success:
                st.toast(message, icon="✅")
            else:
                st.toast(f"Eviction failed: {message}", icon="❌")
            st.rerun()


def _render_model_details(
    state: LauncherState,
    registry: NodeRegistry | None,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    model: dict,
    running_server: RemoteServerInfo | None = None,
) -> None:
    """Render the model details in the expander.

    Args:
        state: The launcher state.
        registry: NodeRegistry.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model: Model data dictionary.
        running_server: Server info if model is currently running, else None.
    """
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Port**")
    with col2:
        if running_server:
            st.markdown(f"`{running_server.port}` (running)")
            st.markdown(f"*Uptime: {format_uptime(running_server.uptime_seconds)}*")
        else:
            default_port = model.get("default_port") or "Auto-allocate"
            st.markdown(f"`{default_port}`")

    with col1:
        st.markdown(f"**Model**")
    with col2:
        model_path = model.get("model_path", "")
        st.markdown(f"`{model_path.split('/')[-1]}`")

    with col1:
        st.markdown(f"**GPU Layers**")
    with col2:
        st.markdown(f"`{model.get('n_gpu_layers', 'N/A')}`")

    # API docs link if running
    if running_server:
        st.divider()
        if node_name == "local":
            st.markdown(
                f"[📖 API Docs](http://localhost:{running_server.port}/docs) | "
                f"[🔌 Models](http://localhost:{running_server.port}/v1/models)"
            )
        else:
            st.markdown(
                f"*Server running on remote node {node_name}. Access via node's IP.*"
            )

    # Logs expander (only for running servers)
    if running_server:
        st.divider()
        with st.expander("📄 Logs (last 100 lines)", expanded=False):
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

    # Edit button (only for stopped models on local)
    st.divider()
    if not running_server and node_name == "local":
        if st.button("✏️ Edit", use_container_width=True, key=f"edit_{node_name}_{model_name}"):
            st.session_state[f"editing_{model_name}"] = True
            st.rerun()
    elif not running_server:
        st.button("✏️ Edit", use_container_width=True, key=f"edit_{node_name}_{model_name}", disabled=True)
        st.caption("Remote model editing not yet supported")


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
