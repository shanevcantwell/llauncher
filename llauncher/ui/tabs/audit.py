"""Audit-tail tab for the Streamlit UI (M4 Slice 13 / issue #50, stage 1+).

Reads the audit log of the selected node (local or remote) and renders a
filterable, newest-first table. This is intentionally a read-only viewer;
mutating operations live in the other tabs.

## Scope

Issue #64 wired the **remote** dispatch path: when the sidebar's node
selector points at a remote node, the tab fetches that node's audit log
via :meth:`RemoteNode.read_audit` (HTTP GET ``/audit``). For the local
target, the tab reads the on-disk JSONL via
:func:`core.audit_log.read_entries` directly — no HTTP hop.

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

from typing import TYPE_CHECKING

import streamlit as st

from llauncher.core import audit_log
from llauncher.ui.components.node_selector import LOCAL_NODE

if TYPE_CHECKING:
    from llauncher.remote.registry import NodeRegistry


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


def _dict_to_row(d: dict) -> dict:
    """Flatten a remote AuditEntry-dict into a row dict in display order.

    Remote entries arrive over HTTP as plain dicts (already enum-coerced
    by :meth:`AuditEntry.to_dict`), so we can read fields verbatim.
    """
    return {
        "timestamp": d.get("timestamp"),
        "action": d.get("action"),
        "result": d.get("result"),
        "caller": d.get("caller"),
        "port": d.get("port"),
        "model": d.get("model"),
        "message": d.get("message", ""),
    }


def render_audit_tab(
    target: str,
    registry: "NodeRegistry | None" = None,
) -> None:
    """Render the audit tail.

    Args:
        target: Selected target node from the sidebar node selector.
            :data:`LOCAL_NODE` reads the local audit log directly; any
            other value resolves the node via ``registry`` and fetches
            its audit log over HTTP (issue #64).
        registry: Node registry used to resolve a remote target. Optional
            for backward compatibility — when omitted and ``target`` is
            non-local, the tab degrades to an error message rather than
            crashing.
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

    # Dispatch on target. Local: read entries from disk and convert each
    # AuditEntry into a row. Remote: fetch JSON dicts over HTTP and pass
    # them through ``_dict_to_row`` (skipping the AuditEntry round-trip
    # — we already have JSON-safe shapes from the agent).
    rows_source: list[dict]
    if target == LOCAL_NODE:
        entries = audit_log.read_entries(limit=int(limit))
        if selected_actions:
            entries = [e for e in entries if e.action.value in selected_actions]
        if selected_results:
            entries = [e for e in entries if e.result.value in selected_results]
        rows_source = [_entry_to_row(e) for e in entries]
    else:
        node = registry.get_node(target) if registry is not None else None
        if node is None:
            st.error(
                f"Unknown node '{target}'. Pick a node from the sidebar "
                f"or add it on the Nodes tab."
            )
            return
        remote_entries = node.read_audit(limit=int(limit))
        if remote_entries is None:
            st.error(
                f"Could not read audit log from node '{target}' "
                f"(node offline or unreachable)."
            )
            return
        if selected_actions:
            remote_entries = [
                e for e in remote_entries if e.get("action") in selected_actions
            ]
        if selected_results:
            remote_entries = [
                e for e in remote_entries if e.get("result") in selected_results
            ]
        rows_source = [_dict_to_row(e) for e in remote_entries]

    if not rows_source:
        st.info(
            "No audit entries yet. Actions you take in the Dashboard or "
            "Models tabs will appear here."
        )
        return

    # read_entries / /audit return chronological order (newest last);
    # reverse for display so the freshest entries are at the top.
    rows = list(reversed(rows_source))

    # Lazy pandas import keeps the UI bootable on minimal envs that have
    # streamlit but not pandas. (streamlit ships pandas as a hard dep, so
    # this is belt-and-suspenders, but cheap.)
    import pandas as pd

    df = pd.DataFrame(rows, columns=_DISPLAY_COLUMNS)
    st.dataframe(df, use_container_width=True, hide_index=True)
