"""Model card rendering for dashboard tab."""

import streamlit as st

from llauncher import operations as ops
from llauncher.state import LauncherState
from llauncher.core import delegation
from llauncher.core.process import stream_logs
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo, local_agent_node
from llauncher.ui.components.port_picker import render_port_picker
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
                width='stretch',
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
    """Render the start button with port-picker + eviction confirmation flow.

    Stage 2 of M4 Slice 13 (#50) gates the start button on the port
    picker (``components/port_picker.py``). The button is disabled
    whenever the picker returns ``None`` (no port entered or blacklisted),
    so the caller cannot click into ``_handle_start`` without an explicit
    port — the auto-allocation seam is gone.

    Args:
        state: The launcher state.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model_name: Name of the model.
        status_icon: The status icon to display.
    """
    # Only look up local config for local nodes — remote models are served
    # by the target node's own config, so state.models (local) won't have them.
    if node_name == "local":
        config = state.models.get(model_name)
        if config is None:
            st.button(
                status_icon,
                key=f"toggle_start_{node_name}_{model_name}",
                help="Model config not found",
                width='stretch',
                disabled=True,
            )
            return

    # ADR-010: caller supplies the port. The picker is rendered alongside
    # the start button; the button stays disabled until the picker yields
    # a usable port.
    chosen_port = render_port_picker(
        state,
        key_prefix=f"start_{node_name}_{model_name}",
        model_name=model_name,
    )

    if st.button(
        status_icon,
        key=f"toggle_start_{node_name}_{model_name}",
        help=f"Start {model_name}",
        width='stretch',
        disabled=chosen_port is None,
    ):
        if chosen_port is None:
            # Defence-in-depth: ``disabled=True`` already blocks the
            # click, but if a future Streamlit upgrade fires the
            # callback anyway, refusing to call ``_handle_start`` here
            # preserves the ADR-010 invariant.
            return
        _handle_start(state, aggregator, node_name, model_name, target_port=chosen_port)


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
            width='stretch',
        ):
            st.rerun()
    with col2:
        if st.button(
            "Confirm Eviction",
            key=f"evict_confirm_{node_name}_{port}_{model_name}",
            width='stretch',
            type="primary",
        ):
            # v2 ops migration (issue #57): route eviction through
            # ``operations.swap`` instead of the legacy
            # ``state.start_with_eviction_compat`` path. The M4 tab
            # restructure (#50) must preserve this call.
            #
            # Delegation gate (#200): the evict-and-start branch of a
            # local-node launch is itself a spawn, so it routes to the
            # agent over HTTP when one is present (or an override forces
            # it). With no agent reachable it falls back to the in-process
            # swap (dev/standalone), preserving the toast taxonomy below.
            if delegation.should_delegate():
                # ``or {}`` guards the ``dict | None`` seam (see _handle_start).
                res = local_agent_node().swap_server(model_name, port) or {}
                if res.get("success"):
                    st.toast(f"{model_name} now running on port {port}", icon="✅")
                else:
                    msg = res.get("message") or res.get("error") or "Eviction failed"
                    st.toast(msg, icon="❌")
            else:
                result = ops.swap(model_name, port, caller="ui")
                if result.success and result.action == "swapped":
                    st.toast(f"{model_name} now running on port {port}", icon="✅")
                elif result.success and result.action == "already_running":
                    st.toast(f"{model_name} already running on port {port}", icon="ℹ️")
                elif result.action == "rolled_back":
                    st.toast(
                        f"Swap failed — rolled back to {result.previous_model} "
                        f"({result.message})",
                        icon="⚠️",
                    )
                elif result.action in ("failed", "rejected_stop_failed"):
                    st.toast(
                        f"Port {port} unavailable — manual intervention required "
                        f"({result.message})",
                        icon="❌",
                    )
                else:
                    st.toast(f"Eviction failed: {result.message}", icon="❌")
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
            # Per ADR-010, port is supplied at start time, not stored on the model.
            st.markdown("`—` (set at start)")

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
        if st.button("✏️ Edit", width='stretch', key=f"edit_{node_name}_{model_name}_enabled"):
            st.session_state[f"editing_{model_name}"] = True
            st.rerun()
    elif not running_server:
        st.button("✏️ Edit", width='stretch', key=f"edit_{node_name}_{model_name}_disabled", disabled=True)
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
        # Delegation gate (#200/#203): a stop is a mutating op, so it routes
        # through the local agent over HTTP when one is reachable — the same
        # gate the CLI (``cli.py::stop_server``) and MCP
        # (``mcp_server/tools/servers.py``) stop paths use. This is what makes
        # ``llama-server`` (running as the ``llauncher`` service account in
        # ADR-018 system-mode) terminable from the operator-owned Streamlit UI:
        # the agent owns the process, so no cross-uid SIGTERM (psutil
        # AccessDenied) is attempted from the UI process. Per
        # ``docs/ARCHITECTURE.md`` "Endpoints orchestrate — they do not
        # reimplement them", the UI delegates rather than re-running the
        # in-process ``core.process`` stop. With no agent reachable it falls
        # back to the in-process op (dev/standalone), preserving prior
        # behaviour exactly.
        if delegation.should_delegate():
            # ``RemoteNode.stop_server`` is ``dict | None`` (a 200 with a null
            # body yields None); ``or {}`` makes ``.get`` None-safe without
            # masking a real error dict (transport/non-2xx arrives as a dict).
            res = local_agent_node().stop_server(port) or {}
            success = bool(res.get("success"))
            message = (
                res.get("message")
                or res.get("error")
                or "Local agent returned no result"
            )
        else:
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
    target_port: int,
) -> None:
    """Handle starting a server with eviction logic.

    The port is **required**. M4 Slice 13 (#50, stage 2) deleted the
    auto-allocation fallback that used to live here; the port picker
    component owns elicitation and the button is disabled until the
    user supplies a value. Calling ``_handle_start`` without a real
    port is a programmer error.

    Args:
        state: The launcher state.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model_name: Name of the model.
        target_port: Port to bind, supplied by the port picker per ADR-010.
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
            resolved_port = target_port
            valid, msg = state.can_start(config, caller="ui", port=resolved_port)
            if valid:
                # Delegation gate (#200): with a healthy local agent present
                # (or an explicit override), POST the launch to the agent so
                # the ``llama-server`` is a child of the systemd-managed
                # agent and the operator needs no exec rights on the binary.
                # With no agent reachable, fall back to the in-process op
                # (dev/standalone) — v2 ops migration (issue #57): plain
                # start routes through ``operations.start`` (ADR-005
                # model-health pre-flight via the same seam as
                # ``operations.swap``); the M4 tab restructure (#50)
                # preserves this call.
                if delegation.should_delegate():
                    # ``RemoteNode.start_server`` is ``dict | None`` (a 200
                    # with a null body yields None); ``or {}`` makes the
                    # ``.get`` calls None-safe without masking a real error
                    # dict (transport/non-2xx arrives as a dict already).
                    res = local_agent_node().start_server(
                        model_name, resolved_port
                    ) or {}
                    if res.get("success"):
                        st.toast(res.get("message") or f"Starting {model_name}", icon="✅")
                    else:
                        err = (
                            res.get("message")
                            or res.get("error")
                            or "Local agent returned no result"
                        )
                        st.error(err)
                        st.toast(err, icon="❌")
                else:
                    result = ops.start(model_name, resolved_port, caller="ui")
                    if result.success:
                        st.toast(result.message, icon="✅")
                    else:
                        # Errors must be sticky — toasts disappear too quickly
                        # to read on a near-instant validation failure.
                        st.error(result.message)
                        st.toast(result.message, icon="❌")
            else:
                st.error(f"Cannot start: {msg}")
                st.toast(f"Cannot start: {msg}", icon="❌")
            st.rerun()
    elif aggregator:
        # Per ADR-010, port is at the call site. M4 Slice 13 (#50) made
        # ``target_port`` required at this entry, so the previous
        # "no-port" guard is gone — the picker upstream enforces it.
        result = aggregator.start_on_node(node_name, model_name, target_port)
        if result:
            if result.get("success"):
                st.toast(f"Starting {model_name} on {node_name}...", icon="▶️")
            else:
                st.error(result.get("error", "Failed to start"))
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
