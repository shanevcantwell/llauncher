"""Model card rendering for dashboard tab."""

import streamlit as st

from llauncher import operations as ops
from llauncher.state import LauncherState
from llauncher.core import delegation
from llauncher.core.process import stream_logs
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo, local_agent_node
from llauncher.ui.components import port_picker as _port_picker
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
            st.button(
                status_icon,
                key=f"toggle_stop_{node_name}_{model_name}",
                help=f"Stop {model_name}",
                width='stretch',
                # on_click (#498): _handle_stop's own trailing st.rerun()
                # used to force a second full script run just to reflect
                # the stop; the callback context lands it in this same run.
                on_click=_handle_stop,
                args=(state, aggregator, node_name, model_name, running_server.port),
            )
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

    Also clears any sticky edit error left behind by an earlier failed
    Save (#494 review): the message is queued for one edit session, so a
    freshly armed form must never open pre-seeded with a stale failure.
    """
    st.session_state[f"editing_{model_name}"] = True
    st.session_state.pop(edit_error_key(model_name), None)


def _arm_deleting_flag(node_name: str, model_name: str) -> None:
    """``on_click`` callback for the Delete button (#494). See
    :func:`_arm_editing_flag` for why this is callback-safe.
    """
    st.session_state[f"deleting_{node_name}_{model_name}"] = True


def edit_error_key(model_name: str) -> str:
    """Session-state key for a sticky edit-save failure message (#494).

    Defined here rather than in ``forms.py`` because both modules need it
    and the import arrow already runs ``forms.py`` -> ``model_card.py``
    (see ``edit_saved_toast_key``); putting it the other way round would
    make a cycle. ``forms.py`` imports it as ``_edit_error_key``.

    Mirrors the ``_start_error_key``/#401 idiom: the Save button's
    ``on_click`` callback runs in Streamlit's pre-script callback context,
    where ``st.error()`` calls are silently dropped — nothing has started
    rendering yet this run. A failure persists the message here instead
    and leaves ``editing_{model_name}`` set, so ``render_edit_model``
    stays in edit mode on the next (normal, non-callback) render and can
    display it. Deliberate VIEW state (ADR-LLNCH-025).

    Cleared on Cancel and on a fresh Edit arm, so it never outlives the
    edit session that produced it.
    """
    return f"edit_error_{model_name}"


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


def _dismiss_start_error(key: str) -> None:
    """``on_click`` callback for the sticky start-error Dismiss button (#498).

    Pure ``session_state`` mutation -- callback-safe (ADR-LLNCH-025 view
    state). See :func:`_render_start_error`'s docstring for why this is
    on_click rather than the old ``if st.button(): mutate; st.rerun()``.
    """
    st.session_state[key] = None


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
    st.button(
        "Dismiss",
        key=f"{key}_dismiss",
        width='stretch',
        # on_click (#498): runs before the script body, so by the time
        # this same function re-renders on the next pass, session_state
        # is already cleared -- ``message`` above reads None and the
        # ``if not message: return`` guard skips the banner this run.
        # The old ``if st.button(): mutate; st.rerun()`` shape needed a
        # second full script run to hide the banner; on_click needs none.
        on_click=_dismiss_start_error,
        args=(key,),
    )


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

    st.button(
        status_icon,
        key=f"toggle_start_{node_name}_{model_name}",
        help=f"Start {model_name}",
        width='stretch',
        disabled=chosen_port is None,
        # on_click (#498): _handle_start's own trailing st.rerun() calls
        # used to force a second full script run to reflect the attempt;
        # the callback context lands each branch in this same run.
        #
        # The port picker's *widget key* is passed, not chosen_port
        # itself: on_click args are bound to whatever was passed to
        # st.button() on the PRIOR render (the callback fires before this
        # run's script body -- including this run's render_port_picker()
        # call -- ever executes), so a value freshly typed this run would
        # arrive as last run's stale value. The widget's own entry in
        # st.session_state, in contrast, is synced from the frontend
        # before any callback runs (the same fact forms.py's
        # _save_edit_callback relies on) -- so the callback below reads
        # the port fresh by key instead.
        on_click=_start_button_clicked,
        args=(state, aggregator, node_name, model_name, f"start_{node_name}_{model_name}_port"),
    )


def _start_button_clicked(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    port_key: str,
) -> None:
    """``on_click`` wrapper for the start toggle (#498).

    Reads the chosen port fresh from ``st.session_state[port_key]`` (see
    the caller's comment for why) and re-applies the picker's blacklist
    gate before dispatching to :func:`_handle_start`. Defence-in-depth:
    ``disabled=True`` on the button already blocks the click for both
    cases (no port typed, or a blacklisted one) on the render that
    computed it, but if a future Streamlit upgrade fires the callback
    anyway, refusing to call ``_handle_start`` here preserves the
    ADR-LLNCH-010 invariant. Checked against ``_port_picker`` (module
    reference, not an imported name) so this stays in lockstep with
    :func:`render_port_picker`'s own check rather than a second,
    driftable copy of ``BLACKLISTED_PORTS``.
    """
    chosen_port = st.session_state.get(port_key)
    if chosen_port is None:
        return
    chosen_port = int(chosen_port)
    if chosen_port in _port_picker.BLACKLISTED_PORTS:
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
        st.button(
            "Cancel",
            key=f"evict_cancel_{node_name}_{port}_{model_name}",
            width='stretch',
            # on_click (#498): pure session-state mutation, callback-safe.
            on_click=_cancel_eviction,
            args=(flag_key,),
        )
    with col2:
        st.button(
            "Confirm Eviction",
            key=f"evict_confirm_{node_name}_{port}_{model_name}",
            width='stretch',
            type="primary",
            # on_click (#498): the old shape's trailing st.rerun() forced
            # a second full script run to reflect the swap.
            on_click=_confirm_eviction,
            args=(node_name, port, model_name, flag_key),
        )


def _cancel_eviction(flag_key: str) -> None:
    """``on_click`` callback for the eviction dialog's Cancel button (#498).

    Pure ``session_state`` mutation -- callback-safe (ADR-LLNCH-025).
    """
    st.session_state[flag_key] = False


def _confirm_eviction(node_name: str, port: int, model_name: str, flag_key: str) -> None:
    """``on_click`` callback for the eviction dialog's Confirm button (#498).

    Dispatches the swap verb, exactly as the old click-branch did, minus
    the trailing ``st.rerun()`` -- the callback context lands the result
    in this same run without one.

    Args:
        node_name: Name of the node.
        port: Port that is in use.
        model_name: Name of the model to start.
        flag_key: The session_state key gating the dialog, cleared here.
    """
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
            # #498: no explicit st.rerun() -- a click already causes
            # exactly one script rerun (any widget interaction does),
            # and the log read just below runs unconditionally on every
            # render, so this button needs no action of its own. The old
            # ``if st.button(): st.rerun()`` shape paid a second,
            # entirely redundant full script run per click.
            st.button("🔄 Refresh", key=f"refresh_logs_{node_name}_{model_name}")

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
                # at the time every run paid model_registry.py's
                # unconditional state.refresh() (2 psutil walks) — the
                # double-run defect measured in #494. That refresh has
                # since been hoisted to a single per-run call in app.py
                # (#497), so a second run now costs one walk pair, not
                # two; the on_click shape stays right regardless.
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
        st.button(
            "Cancel",
            key=f"delete_cancel_{node_name}_{model_name}",
            width='stretch',
            # on_click (#498): pure session-state mutation, callback-safe.
            on_click=_cancel_delete,
            args=(flag_key,),
        )
    with col2:
        st.button(
            "Confirm Delete",
            key=f"delete_confirm_{node_name}_{model_name}",
            width='stretch',
            type="primary",
            # on_click (#498): the old shape's trailing st.rerun() forced
            # a second full script run to reflect the delete.
            on_click=_confirm_delete,
            args=(model_name, flag_key),
        )


def _cancel_delete(flag_key: str) -> None:
    """``on_click`` callback for the delete gate's Cancel button (#498).

    Pure ``session_state`` mutation -- callback-safe (ADR-LLNCH-025).
    """
    st.session_state[flag_key] = False


def _confirm_delete(model_name: str, flag_key: str) -> None:
    """``on_click`` callback for the delete gate's Confirm button (#498).

    Dispatches the delete verb, exactly as the old click-branch did,
    minus the trailing ``st.rerun()`` and the ``st.error()`` call (a
    render call that would be silently dropped in this pre-script
    callback context, per forms.py::_process_edit_model's precedent) --
    the toast below already carries the same message.
    """
    result = ops.delete_model(model_name, caller="ui")
    st.session_state[flag_key] = False

    if result.success:
        st.toast(result.message, icon="✅")
    else:
        st.toast(result.message, icon="❌")


def _handle_stop(
    state: LauncherState,
    aggregator: RemoteAggregator | None,
    node_name: str,
    model_name: str,
    port: int,
) -> None:
    """Handle stopping a server with proper error handling.

    ``on_click`` callback for the card's stop toggle (#498) -- runs
    before the script body, so no explicit ``st.rerun()`` is needed to
    reflect the stop this run (the old shape's trailing rerun forced a
    wasteful second full run).

    Args:
        state: The launcher state.
        aggregator: RemoteAggregator.
        node_name: Name of the node.
        model_name: Name of the model being stopped (toast routing only).
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
            # Port is occupied by another llauncher server. Arm the
            # pending-confirmation flag (#412) rather than rendering the
            # dialog inline here: a rerun from *anywhere else* on the
            # card between this click and the operator's Confirm/Cancel
            # would otherwise silently drop a render-transient dialog,
            # since nothing would have recorded that a confirmation was
            # pending. #498: this whole handler now runs as the start
            # button's on_click callback, which fires before the script
            # body -- so this flag is already armed by the time
            # _render_eviction_dialog_if_armed checks it later in this
            # same run, with no explicit st.rerun() needed.
            st.session_state[
                _eviction_flag_key(node_name, target_port, model_name)
            ] = True
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
                        # this whole handler runs as the start button's
                        # on_click callback (#498): a bare st.error() here
                        # would be silently dropped (nothing has started
                        # rendering yet this run — see
                        # forms.py::_process_edit_model). Persist to
                        # session_state; _render_start_error renders it on
                        # the next pass and clears it on Dismiss (deliberate
                        # VIEW state, not cached lifecycle truth — docs
                        # PR #411 / issue #410).
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
    elif aggregator:
        # Per ADR-LLNCH-010, port is at the call site. M4 Slice 13 (#50) made
        # ``target_port`` required at this entry, so the previous
        # "no-port" guard is gone — the picker upstream enforces it.
        #
        # #498: no separate st.error() call — this handler now runs as
        # the start button's on_click callback, where a render call like
        # st.error() would be silently dropped (same reasoning as the
        # sticky start_error path above); the toast below already
        # carries the same message.
        result = aggregator.start_on_node(node_name, model_name, target_port)
        if result:
            if result.get("success"):
                st.toast(f"Starting {model_name} on {node_name}...", icon="▶️")
            else:
                st.toast(result.get("error", "Failed to start"), icon="❌")
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
