"""Utility functions for the UI layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

import streamlit as st


def format_uptime(seconds: int) -> str:
    """Format uptime seconds into human-readable string.

    Args:
        seconds: Uptime in seconds.

    Returns:
        Formatted string like "2h 34m" or "5m" or "30s".
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# render_op_result — M4 Slice 14 (issue #51)
# ---------------------------------------------------------------------------
#
# Every M4 tab that fires a verb (start, swap, stop, delete-model) needs
# to translate the operation's structured result envelope into Streamlit
# feedback. Without a single function, each tab grows its own ladder of
# ``st.success``/``st.error`` calls keyed off ``result.action`` strings —
# which is exactly the inconsistency m4-design Slice 14 calls out.
#
# This module exposes one renderer (``render_op_result``) plus a
# pure-logic classifier (``classify_action``) so the action → severity
# mapping is testable without spinning up Streamlit.


class OpResultSeverity(str, Enum):
    """Visual severity class assigned to an operation outcome.

    Maps roughly to Streamlit's notification primitives:

    - ``SUCCESS`` → ``st.toast(..., icon="✅")``
    - ``INFO``    → ``st.toast(..., icon="ℹ️")`` (idempotent no-op:
                    ``already_running``, ``already_empty``, ``not_found``)
    - ``WARNING`` → sticky ``st.warning`` *plus* a toast (recoverable
                    failures the user must read: ``rolled_back``,
                    ``rejected_preflight``, ``rejected_in_progress``)
    - ``ERROR``   → sticky ``st.error`` *plus* a toast (hard failures
                    that need human action)
    """

    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# Action → severity tables. Keep these as module constants (not buried
# inside ``classify_action``) so other code paths — for example, a
# future audit-tab filter — can import them and stay aligned.

_SUCCESS_ACTIONS: frozenset[str] = frozenset({
    "started",
    "stopped",
    "swapped",
    "deleted",
})

_INFO_ACTIONS: frozenset[str] = frozenset({
    "already_running",
    "already_empty",
    "not_found",
    # Accepted-in-flight (issue #140): a remote agent acknowledged the
    # stop with 202 and is terminating asynchronously. Informational —
    # the tab's next status refresh shows the port emptied. Not SUCCESS:
    # the ✅ icon would overclaim an outcome that hasn't landed yet.
    "stopping",
})

_WARNING_ACTIONS: frozenset[str] = frozenset({
    # Rollback succeeded — port still serves something, but not what
    # the user asked for.
    "rolled_back",
    # Pre-flight blocked the operation before any state change.
    "rejected_preflight",
    # Another swap is in flight on this port; user can retry.
    "rejected_in_progress",
})

_ERROR_ACTIONS: frozenset[str] = frozenset({
    # Hard rejects — the port is in a state incompatible with the verb.
    "rejected_occupied",
    "rejected_empty",
    "rejected_stop_failed",
    "rejected_in_use",
    # New model failed AND rollback also failed — port is dead.
    "failed",
    # Generic catch-all from any verb.
    "error",
})


def classify_action(action: str | None) -> OpResultSeverity:
    """Map an operation result's ``action`` string to its severity class.

    Pure logic — safe to call without Streamlit. Unknown actions
    (including empty / ``None``) classify as :attr:`OpResultSeverity.ERROR`
    on the principle that "unrecognized" should never fall through as
    success.
    """
    if action in _SUCCESS_ACTIONS:
        return OpResultSeverity.SUCCESS
    if action in _INFO_ACTIONS:
        return OpResultSeverity.INFO
    if action in _WARNING_ACTIONS:
        return OpResultSeverity.WARNING
    # ERROR set + everything unknown.
    return OpResultSeverity.ERROR


def _extract(result: Any, field: str, default: Any = "") -> Any:
    """Read ``field`` from a result that may be a dataclass or a dict.

    All ``operations/`` verbs return frozen dataclasses with
    ``.to_dict()``; both the dataclass and its dict envelope are valid
    inputs to :func:`render_op_result` so callers don't have to think
    about which shape they have in hand.
    """
    if isinstance(result, dict):
        return result.get(field, default)
    return getattr(result, field, default)


# Icon table aligned with the severity enum. Lives next to the
# severity-classification logic so adding a new severity requires
# updating both at once.
_SEVERITY_ICONS: dict[OpResultSeverity, str] = {
    OpResultSeverity.SUCCESS: "✅",
    OpResultSeverity.INFO: "ℹ️",
    OpResultSeverity.WARNING: "⚠️",
    OpResultSeverity.ERROR: "❌",
}


def render_op_result(
    result: Any,
    *,
    verb_label: str = "Operation",
) -> OpResultSeverity:
    """Render Streamlit feedback for an ``operations/`` verb result.

    Replaces the ad-hoc ``st.toast`` / ``st.success`` / ``st.error``
    ladders scattered across M3-era tabs. Accepts either the frozen
    dataclass returned by ``operations.start``, ``operations.stop``,
    ``operations.swap``, ``operations.delete_model``, or that
    dataclass's ``.to_dict()`` envelope (whichever the caller has).

    Severity ladder:

    - ``SUCCESS`` → toast only. The tab's normal redraw will reflect
      the new state.
    - ``INFO``    → toast only. Idempotent no-ops should not stick.
    - ``WARNING`` → toast *and* a sticky ``st.warning`` panel. The
      user needs to read the message (e.g., a swap rolled back) and
      a transient toast disappears too quickly.
    - ``ERROR``   → toast *and* a sticky ``st.error`` panel. Same
      reason; hard failures need to stay on screen.

    Args:
        result: A frozen ``*Result`` dataclass from ``operations/`` or
            its ``.to_dict()`` envelope. Must expose ``action`` and
            ``message`` fields (string or empty).
        verb_label: Optional human-readable verb name (e.g. ``"Swap"``)
            used to prefix sticky panels. The toast omits the prefix
            because Streamlit toasts are already short.

    Returns:
        The severity that was rendered. Returned (rather than ``None``)
        so call sites can branch on the outcome — e.g., a tab that
        wants to ``st.rerun()`` only on success.
    """
    action = _extract(result, "action", "")
    message = _extract(result, "message", "") or _extract(result, "error", "")

    severity = classify_action(action)
    icon = _SEVERITY_ICONS[severity]

    # Toast text intentionally short: Streamlit toasts truncate.
    toast_text = message or _default_message_for(action, verb_label)
    st.toast(toast_text, icon=icon)

    if severity is OpResultSeverity.WARNING:
        st.warning(f"{verb_label}: {toast_text}", icon=icon)
    elif severity is OpResultSeverity.ERROR:
        st.error(f"{verb_label}: {toast_text}", icon=icon)
    # SUCCESS / INFO are toast-only. The tab's redraw handles the
    # positive case; an idempotent no-op shouldn't add visual noise.

    return severity


def _default_message_for(action: str | None, verb_label: str) -> str:
    """Fallback toast text when the result envelope's ``message`` is empty.

    ``operations/`` verbs always populate ``message``; this exists
    purely for defensive rendering against handcrafted envelopes (e.g.
    a future MCP tool that mirrors the shape but forgets the field).
    """
    if not action:
        return f"{verb_label} returned no action"
    return f"{verb_label}: {action}"
