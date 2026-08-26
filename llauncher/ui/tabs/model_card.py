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

    # Eviction dialog renders at full card width (#412) — not nested inside
    # the [4, 1]-ratio button_col, which squeezes a warning + 2-button row
    # into a single-icon-sized column. It is gated on a session_state flag
    # (armed by _handle_start, cleared by either Cancel or Confirm below) so
    # it survives any rerun between the warning and the click, unlike the
    # old render-transient call that lived only inside the click branch.
    if not is_running:
        _render_eviction_dialog_if_armed(state, node_name, model_name)

    # Collapsed expander for details (port, logs, edit button)
    with st.expander("📋 Details", expanded=False):
        _render_model_details(state, aggregator, node_name, model_name, model, running_server)


def _arm_editing_flag(model_name: str) -> None:
    """``on_click`` callback for the Edit button (#494).

    Session-state mutation only — no ``st`` render calls — so it stays
    correct when invoked from Streamlit's pre-script callback context
    (ADR-LLNCH-025: this is view state, the same ``editing_{name}`` flag
    ``forms.py`` already reads).
    """
    st.session_state[f"editing_{model_name}"] = True


def _arm_deleting_flag(node_name: str, model_name: str) -> None:
    """``on_click`` callback for the Delete button (#494). See
    :func:`_arm_editing_flag` for why this is callback-safe.
    """
    st.session_state[f"deleting_{node_name}_{model_name}"] = True


def edit_saved_toast_key(model_name: str) -> str:
    """Session-state key for a just-saved edit's toast message (#494).

    ``forms.py``'s Save callback clears the ``editing_{name}`` flag before
    the script body runs, so ``render_edit_model`` never executes on the
    success run — the edit form has already routed back to the card grid
    by the time the script draws anything. The confirmation therefore
    can't be shown from inside the (now-unrendered) form; it is shown from
    here instead, where ``_render_model_details`` already renders once per
    model on every run regardless of edit-mode routing. Deliberate VIEW
    state (ADR-LLNCH-025): a message queued for one display, not cached
    lifecycle truth.
    """
    return f"edit_saved_{model_name}"


def _render_edit_saved_toast(model_name: str) -> None:
    """Show and clear a pending "saved" toast for ``model_name``, if any.

    Popped (not just read) so the toast fires exactly once — the next run
    without a fresh save finds nothing to show.
    """
    message = st.session_state.pop(edit_saved_toast_key(model_name), None)
    if message:
        st.toast(message, icon="✅")


def _start_error_key(node_name: str, model_name: str) -> str:
    """Session-state key for the sticky start-failure message (#401).

    Mirrors the ``deleting_{node_name}_{model_name}`` / ``editing_{name}``
    flag idiom already used by ``_render_delete_confirm``/``forms.py``:
    the value is deliberate VIEW state — a rendered-until-cleared string —
    never a cache of lifecycle truth (docs PR #411 / issue #410).
    """
    return f"start_error_{node_name}_{model_name}"


def _render_start_error(node_name: str, model_name: str) -> None:
    """Render the sticky start-failure message left by ``_handle_start``.

    ``_handle_start`` writes the message to session state instead of
    calling ``st.error`` directly, because the handler always ends in
    ``st.rerun()`` (needed so the rest of the card reflects the attempt) —
    an ``st.error`` call made just before a rerun is wiped before the
    operator can read it (#401). Rendering from session state here, on the
    pass *after* the rerun, is what makes the error actually sticky. It
    stays visible until an explicit Dismiss or the next start attempt
    supersedes it — never auto-cleared on render, so a slow reader isn't
    racing the next script pass.
    """
    key = _start_error_key(node_name, model_name)
    message = st.session_state.get(key)
    if not message:
        return

    st.error(message)
    if st.button(
        "Dismiss",
        key=f"{key}_dismiss",
        width='stretch',
    ):
        st.session_state[key] = None
        st.rerun()


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
    _render_start_error(node_name, model_name)

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

    # ADR-LLNCH-010: caller supplies the port. The picker is rendered alongside
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
            # preserves the ADR-LLNCH-010 invariant.
            return
        _handle_start(state, aggregator, node_name, model_name, target_port=chosen_port)


def _eviction_flag_key(node_name: str, port: int, model_name: str) -> str:
    """Session-state key for the pending-eviction-confirmation flag (#412).

    Mirrors the ``deleting_{node_name}_{model_name}`` idiom already used by
    ``_render_delete_confirm`` (:func:`_render_delete_confirm`): the value is
    deliberate VIEW state — armed on entry, cleared on both Cancel and
    Confirm — never a cache of lifecycle truth (docs PR #411 / issue #410).
    Keyed by ``(node_name, port, model_name)`` per the issue's acceptance
    criteria, since the port being evicted and the model requesting it are
    both part of the dialog's identity.
    """
    return f"evicting_{node_name}_{port}_{model_name}"


def _render_eviction_dialog_if_armed(
    state: LauncherState,
    node_name: str,
    model_name: str,
) -> None:
    """Render the eviction dialog if a pending confirmation is armed.

    Gates on the ``evicting_{node_name}_{port}_{model_name}`` flag set by
    ``_handle_start`` **directly** — mirroring how the sibling
    ``_render_delete_confirm`` gates on its own ``deleting_*`` flag — rather
    than scanning ``state.running``. Scanning the live occupancy meant a flag
    whose port had since freed was silently skipped (the flag leaked) and, on
    a later rerun where that port was re-occupied, could resurrect the dialog
    against a *different* occupant. The port lives in the flag key itself, so
    it is recovered from the armed flag, not from the caller.

    For a still-occupied port the occupant name is re-read live from
    ``state.running`` inside ``_render_eviction_dialog`` (thin-client
    discipline). When the flag's port is no longer occupied, the pending
    eviction is moot — the port already freed — so the flag is cleared rather
    than silently skipped, and no dialog renders.
    """
    prefix = f"evicting_{node_name}_"
    suffix = f"_{model_name}"
    for flag_key, armed in list(st.session_state.items()):
        if not armed:
            continue
        if not (flag_key.startswith(prefix) and flag_key.endswith(suffix)):
            continue
        middle = flag_key[len(prefix):len(flag_key) - len(suffix)]
        try:
            port = int(middle)
        except ValueError:
            continue
        if flag_key != _eviction_flag_key(node_name, port, model_name):
            continue
        if port not in state.running:
            # Port freed while the confirmation sat pending — the eviction is
            # moot. Clear the leaked flag so it can never resurrect the dialog
            # against a later occupant of the same port.
            st.session_state[flag_key] = False
            continue
        _render_eviction_dialog(state, node_name, port, model_name, flag_key)
        return


def _render_eviction_dialog(
    state: LauncherState,
    node_name: str,
    port: int,
    model_name: str,
    flag_key: str,
) -> None:
    """Render eviction confirmation dialog.

    Args:
        state: The launcher state.
        node_name: Name of the node.
        port: Port that is in use.
        model_name: Name of the model to start.
        flag_key: The session_state key gating this dialog, cleared on exit.
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
            st.session_state[flag_key] = False
            st.rerun()
    with col2:
        if st.button(
            "Confirm Eviction",
            key=f"evict_confirm_{node_name}_{port}_{model_name}",
            width='stretch',
            type="primary",
        ):
            st.session_state[flag_key] = False
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
            # Per ADR-LLNCH-010, port is supplied at start time, not stored on the model.
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

    # Edit / Delete buttons (only for stopped models on local)
    st.divider()
    # Flush a pending Save toast (#494) — see edit_saved_toast_key's
    # docstring for why this lives here rather than in the (possibly
    # unrendered-this-run) edit form.
    _render_edit_saved_toast(model_name)
    if not running_server and node_name == "local":
        edit_col, delete_col = st.columns(2)
        with edit_col:
            st.button(
                "✏️ Edit",
                width='stretch',
                key=f"edit_{node_name}_{model_name}_enabled",
                # on_click (#494): the callback runs BEFORE the script body,
                # so by the time render_models_tab's editing_model check
                # (models.py::_get_editing_model) runs later in this same
                # script pass, the flag is already set — the edit form
                # routes in on this one run, with no explicit st.rerun()
                # needed. The old `if st.button(): mutate; st.rerun()` shape
                # mutated too late (after that routing check had already
                # run) to take effect without a second full script run, and
                # every run pays model_registry.py's unconditional
                # state.refresh() (2 psutil walks) — the double-run defect
                # measured in #494.
                on_click=_arm_editing_flag,
                args=(model_name,),
            )
        with delete_col:
            st.button(
                "🗑️ Delete",
                width='stretch',
                key=f"delete_{node_name}_{model_name}_enabled",
                # Same on_click shape as Edit above, for the same reason
                # (#494) — Delete's confirm gate is read by
                # _render_delete_confirm just below, in this same run.
                on_click=_arm_deleting_flag,
                args=(node_name, model_name),
            )
        _render_delete_confirm(node_name, model_name)
    elif not running_server:
        st.button("✏️ Edit", width='stretch', key=f"edit_{node_name}_{model_name}_disabled", disabled=True)
        st.caption("Remote model editing not yet supported")
        st.button("🗑️ Delete", width='stretch', key=f"delete_{node_name}_{model_name}_disabled", disabled=True)
        st.caption("Remote model deletion not yet supported")


def _render_delete_confirm(node_name: str, model_name: str) -> None:
    """Render the two-step delete confirmation gate for a local model.

    Mirrors ``_render_eviction_dialog``'s Cancel/Confirm column layout and
    session-state gating idiom (#276): a click on the "🗑️ Delete" button
    upstream sets ``deleting_{node_name}_{model_name}`` in session state and
    reruns; this function renders the warning + Cancel/Confirm row only
    while that flag is set, and clears it on either path.

    Delete goes through ``ops.delete_model`` directly (never a
    ``state``/peer-endpoint seam) to satisfy the UI layer boundary test
    (ADR-LLNCH-025) — ``llauncher.operations`` imports are allowed from the UI.

    Args:
        node_name: Name of the node (only called for ``"local"``).
        model_name: Name of the model to delete.
    """
    flag_key = f"deleting_{node_name}_{model_name}"
    if not st.session_state.get(flag_key):
        return

    st.warning(
        f"Delete model config **{model_name}**? This cannot be undone.",
        icon="⚠️",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "Cancel",
            key=f"delete_cancel_{node_name}_{model_name}",
            width='stretch',
        ):
            st.session_state[flag_key] = False
            st.rerun()
    with col2:
        if st.button(
            "Confirm Delete",
            key=f"delete_confirm_{node_name}_{model_name}",
            width='stretch',
            type="primary",
        ):
            result = ops.delete_model(model_name, caller="ui")
            st.session_state[flag_key] = False

            if result.success:
                st.toast(result.message, icon="✅")
            elif result.action == "rejected_in_use":
                # Belt-and-suspenders: the UI gate (``not running_server``)
                # should normally prevent this, but the backend check
                # (live lockfile scan) is the real enforcement — surface it
                # with the same sticky-error + toast pattern
                # ``_handle_start``/``_handle_stop`` use for failures.
                st.error(result.message)
                st.toast(result.message, icon="❌")
            else:
                st.error(result.message)
                st.toast(result.message, icon="❌")
            st.rerun()


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
        # ADR-LLNCH-018 system-mode) terminable from the operator-owned Streamlit UI:
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
            # v2 ops migration (issue #57/#332): route the non-delegated
            # fallback through ``operations.stop`` instead of the legacy
            # ``state.stop_server`` path — lockfile removal + durable
            # audit-log entry, at parity with CLI (``cli.py::stop_server``)
            # and MCP (``mcp_server/tools/servers.py``), which both already
            # dispatch ``ops.stop``/``operations.stop`` here.
            result = ops.stop(port, caller="ui")
            success, message = result.success, result.message
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
        target_port: Port to bind, supplied by the port picker per ADR-LLNCH-010.
    """
    if node_name == "local":
        config = state.models.get(model_name)
        if config is None:
            st.toast(f"Model config not found: {model_name}", icon="❌")
            return

        error_key = _start_error_key(node_name, model_name)

        # Check if port is in use by another of our servers. Use the
        # already-refreshed session `state` (issue #392) — constructing and
        # refreshing a throwaway LauncherState here duplicated 2 full
        # psutil.process_iter scans per Start click for no behavioral gain,
        # since _render_eviction_dialog_if_armed below reads state.running
        # anyway.
        if target_port in state.running:
            # Port is occupied by another llauncher server. Arm the pending-
            # confirmation flag and rerun (#412) rather than rendering the
            # dialog inline here: an unconditional st.rerun() anywhere else
            # on the card between this click and the operator's Confirm/
            # Cancel would otherwise silently drop the dialog, since nothing
            # previously recorded that a confirmation was pending.
            st.session_state[
                _eviction_flag_key(node_name, target_port, model_name)
            ] = True
            st.rerun()
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
                # start routes through ``operations.start`` (ADR-LLNCH-005
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
                        st.session_state[error_key] = None
                        st.toast(res.get("message") or f"Starting {model_name}", icon="✅")
                    else:
                        err = (
                            res.get("message")
                            or res.get("error")
                            or "Local agent returned no result"
                        )
                        # Errors must be sticky — toasts disappear too quickly
                        # to read on a near-instant validation failure, and
                        # st.error() here would be wiped by the st.rerun()
                        # below before the operator can read it (#401).
                        # Persist to session_state; _render_start_error
                        # renders it on the next pass and clears it on
                        # Dismiss (deliberate VIEW state, not cached
                        # lifecycle truth — docs PR #411 / issue #410).
                        st.session_state[error_key] = err
                        st.toast(err, icon="❌")
                else:
                    result = ops.start(model_name, resolved_port, caller="ui")
                    if result.success:
                        st.session_state[error_key] = None
                        st.toast(result.message, icon="✅")
                    else:
                        st.session_state[error_key] = result.message
                        st.toast(result.message, icon="❌")
            else:
                st.session_state[error_key] = f"Cannot start: {msg}"
                st.toast(f"Cannot start: {msg}", icon="❌")
            st.rerun()
    elif aggregator:
        # Per ADR-LLNCH-010, port is at the call site. M4 Slice 13 (#50) made
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
