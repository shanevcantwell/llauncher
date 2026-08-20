"""MCP tool for reading the audit log (issue #338).

Mirrors the agent's ``GET /audit`` contract exactly (``agent/routing.py``):
same three optional args (``limit``, ``action``, ``result``), same
in-memory enum-value filtering applied after the bounded
:func:`llauncher.core.audit_log.read_entries` tail read, same
:meth:`AuditEntry.to_dict` projection. Stateless like ``server_metrics`` —
reads local disk directly, no :class:`LauncherState` involvement.
"""

from __future__ import annotations

from mcp import Tool


def get_tools() -> list[Tool]:
    """Return tool definitions for audit-log reads."""
    return [
        Tool(
            name="read_audit",
            description=(
                "Read recent audit-log entries on this node (ADR-008, "
                "issue #64). The audit log is process-global (not "
                "port-scoped). 'limit' bounds the tail (default 200); "
                "'action' and 'result' filter entries by their exact enum "
                "value (e.g. action='started', result='success'). Returns "
                "an empty list when the log is missing or empty."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Bound the tail read (default: 200)",
                    },
                    "action": {
                        "type": "string",
                        "description": "Filter to entries with this exact action value",
                    },
                    "result": {
                        "type": "string",
                        "description": "Filter to entries with this exact result value",
                    },
                },
                "required": [],
            },
        ),
    ]


async def read_audit(args: dict) -> dict:
    """Return recent audit-log entries, mirroring ``agent/routing.py::get_audit``.

    Thin wrapper over :func:`llauncher.core.audit_log.read_entries`. Filtering
    happens in-memory after the bounded read, same as the agent endpoint.
    """
    from llauncher.core import audit_log

    limit = args.get("limit")
    action = args.get("action")
    result = args.get("result")

    num = limit if limit is not None else 200
    entries = audit_log.read_entries(limit=int(num))

    if action:
        entries = [e for e in entries if e.action.value == action]
    if result:
        entries = [e for e in entries if e.result.value == result]

    return {"entries": [e.to_dict() for e in entries]}
