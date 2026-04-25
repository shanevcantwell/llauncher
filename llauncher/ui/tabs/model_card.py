"""Model card rendering for dashboard tab."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo
from llauncher.ui.utils import format_uptime


def render_model_card(
    state: LauncherState,
    registry: RemoteAggregator | None,
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
                key=f"toggle_stop_{node_name}_{model_name}",
                help=f"Stop {model_name}",
                use_container_width=True,
            ):
                _handle_stop(state, aggregator, node_name, running_server.port)
        else:
            _render_start_button(
                state, aggregator, node_name, model_name, status_icon
            )

    # Collapsed expander for details (port, logs, edit button)
    with st.expander("📋 Details", expanded=False):
        _render_model_details(state, aggregator, node_name, model_name, model, running_server)


def _render_start_button(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    status_icon: str,
) -> None:
    """Render the start button with eviction confirmation flow.

    Args:
        state: The launcher state.
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
        _handle_start(state, aggregator, node_name, model_name, target_port)


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
            result = state.start_with_eviction_compat(model_name, port, caller="ui")
            success, message = result

            if success:
                st.toast("Server started successfully", icon="✅")
            elif message and ("rolled back" in message.lower() or "restored" in message.lower()):
                st.toast(f"Swap failed — rolled back to server ({message})", icon="⚠️")
            elif message and ("unavailable" in message.lower() or "manual intervention" in message.lower()):
                st.toast(f"Port unavailable — manual intervention required ({message})", icon="❌")
            else:
                st.toast(f"Eviction failed: {message}", icon="❌")
            st.rerun()


def _render_model_details(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    model: dict,
    running_server: RemoteServerInfo | None = None,
) -> None:
    """Render the model details in the expander.

    Args:
        state: The launcher state.
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
            if st.button("🔄 Refresh", key=f"refresh_logs_{node_name}_{model_name}"):
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
        if st.button("✏️ Edit", use_container_width=True, key=f"edit_{node_name}_{model_name}_enabled"):
            st.session_state[f"editing_{model_name}"] = True
            st.rerun()
    elif not running_server:
        st.button("✏️ Edit", use_container_width=True, key=f"edit_{node_name}_{model_name}_disabled", disabled=True)
        st.caption("Remote model editing not yet supported")


def _handle_stop(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    port: int,
) -> None:
    """Handle stopping a server with proper error handling.

    Args:
        state: The launcher state.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        port: Port of the server to stop.
    """
    if node_name == "local":
        success, message = state.stop_server(port, caller="ui")
    elif aggregator:
        result = aggregator.stop_on_node(node_name, port)
        success, message = _parse_aggregator_result(result)
    else:
        success = False
        message = "Cannot stop: no connection to node"

    if success:
        st.toast(message, icon="✅")
    else:
        st.toast(message, icon="❌")
    st.rerun()


def _handle_start(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    target_port: int | None,
) -> None:
    """Handle starting a server with eviction logic.

    Args:
        state: The launcher state.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model_name: Name of the model.
        target_port: Port to use (or None for auto-allocate).
    """
    if node_name == "local":
        config = state.models.get(model_name)
        if config is None:
            st.toast(f"Model config not found: {model_name}", icon="❌")
            return

        # Check if port is in use by another of our servers
        temp_state = LauncherState()
        temp_state.refresh()

        if target_port in temp_state.running:
            # Port is occupied by another llauncher server - show eviction dialog
            _render_eviction_dialog(state, node_name, target_port, model_name, "")
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


def _parse_aggregator_result(result) -> tuple[bool, str]:
    """Parse aggregator result with proper error handling.

    Args:
        result: Result from aggregator call (dict, string, or None).

    Returns:
        Tuple of (success, message).
    """
    if result is None:
        return False, "Unknown error"
    elif isinstance(result, dict):
        return result.get("success", False), result.get("message", "Unknown error")
    else:
        return False, str(result) if result else "Unknown error"
