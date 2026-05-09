"""Audit-tail tab for the Streamlit UI (M4 Slice 13 / issue #50, stage 1).

Reads the local audit log via :func:`core.audit_log.read_entries` and
renders a filterable, newest-first table. This is intentionally a
read-only viewer; mutating operations live in the other tabs.

## Scope

Stage 1 wires the **local** audit log only. Remote-node audit access is
deferred (filed as a follow-up issue) — when the target is not "local",
the tab still renders the local audit log and surfaces a caption
explaining the limitation. The caption is rendered above the dataframe
so the user sees the disclaimer before reading the data.

## ADR-013 hook

The "Tail (entries)" control bounds how many entries we read off disk
in a single render, mirroring the bounded-tail discipline ADR-013
applies to launcher logs. Audit logs grow append-only (per ADR-008) so
unbounded reads would scale with history; the bounded tail keeps render
cost predictable.

## Empty state

The audit log is currently a stub on the *write* path (issue #60), so
new installs will routinely have an empty/missing JSONL file. The tab
must render gracefully in that case — we show an info panel and
deliberately skip ``st.dataframe`` (calling it with an empty frame
yields a confusing "no data" hole rather than guidance).
"""

from __future__ import annotations

import streamlit as st

from llauncher.core import audit_log
from llauncher.ui.components.node_selector import LOCAL_NODE


# Column order in the rendered dataframe. Kept tight on purpose — the
# AuditEntry has a few more fields (``from_model``, ``pid``) that aren't
# useful at a glance and would push the message column off-screen.
_DISPLAY_COLUMNS: list[str] = [
    "timestamp",
    "action",
    "result",
    "caller",
    "port",
    "model",
    "message",
]


def _entry_to_row(entry: audit_log.AuditEntry) -> dict:
    """Flatten an AuditEntry into a row dict in display order."""
    return {
        "timestamp": entry.timestamp,
        "action": entry.action.value,
        "result": entry.result.value,
        "caller": entry.caller,
        "port": entry.port,
        "model": entry.model,
        "message": entry.message,
    }


def render_audit_tab(target: str) -> None:
    """Render the audit tail.

    Args:
        target: Selected target node from the sidebar node selector.
            When not :data:`LOCAL_NODE`, the tab shows a caption noting
            the local-only scope and still renders the local log.
    """
    st.header("📝 Audit log")

    # ADR-013-style bounded tail: cap how many entries we read per render.
    limit = st.number_input(
        "Tail (entries)",
        min_value=50,
        max_value=1000,
        value=200,
        step=50,
        help="How many of the most recent entries to read from disk.",
    )

    # Action / result filter widgets. Empty selection means "all".
    action_options = [a.value for a in audit_log.AuditAction]
    result_options = [r.value for r in audit_log.AuditResult]
    selected_actions = st.multiselect(
        "Filter by action",
        options=action_options,
        default=[],
        help="Empty selection = all actions.",
    )
    selected_results = st.multiselect(
        "Filter by result",
        options=result_options,
        default=[],
        help="Empty selection = all results.",
    )

    # Local-only disclaimer for non-local targets. Rendered ABOVE the data
    # so the user reads the caveat before interpreting what they see.
    if target != LOCAL_NODE:
        # Tracked as a follow-up (issue #64). Stage 1 of #50 explicitly
        # scoped this tab to the local node.
        st.caption(
            f"Showing local audit log; remote-node audit access is not yet "
            f"wired (deferred to post-M4, see issue #64). "
            f"Selected target: '{target}'."
        )

    entries = audit_log.read_entries(limit=int(limit))

    # Apply filters in-memory. Limit was applied at read time.
    if selected_actions:
        entries = [e for e in entries if e.action.value in selected_actions]
    if selected_results:
        entries = [e for e in entries if e.result.value in selected_results]

    if not entries:
        st.info(
            "No audit entries yet. Actions you take in the Dashboard or "
            "Models tabs will appear here."
        )
        return

    # read_entries returns chronological order (newest last); reverse for
    # display so the freshest entries are at the top of the table.
    rows = [_entry_to_row(e) for e in reversed(entries)]

    # Lazy pandas import keeps the UI bootable on minimal envs that have
    # streamlit but not pandas. (streamlit ships pandas as a hard dep, so
    # this is belt-and-suspenders, but cheap.)
    import pandas as pd

    df = pd.DataFrame(rows, columns=_DISPLAY_COLUMNS)
    st.dataframe(df, use_container_width=True, hide_index=True)
