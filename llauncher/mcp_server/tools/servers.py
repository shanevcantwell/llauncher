"""MCP tools for server management (start/stop/swap/status/logs).

Per ADR-010, the verb-style tools (``start_server``, ``stop_server``,
``swap_server``) are port-keyed and delegate to
:mod:`llauncher.operations`. The MCP server is a thin wrapper that
translates tool arguments into op calls and returns the ADR-010 result
envelope (``success``, ``action``, ``port``, etc.) verbatim.

The read-side tools (``server_status``, ``get_server_logs``) still
consult :class:`LauncherState` for the per-call refresh of the live
process table — they don't go through ops because they're observational,
not mutating.
"""

from __future__ import annotations

from mcp import Tool

from llauncher import operations as ops
from llauncher.core import delegation
from llauncher.core import settings
from llauncher.core.process import stream_logs
from llauncher.state import LauncherState


def _local_agent_node():
    """Build a ``RemoteNode`` aimed at the local agent for delegation (#200).

    Imported lazily so this module's import graph stays free of ``remote``
    on the in-process path (the common standalone/dev case). The node is
    named ``"local"`` and carries the resolved ``X-Api-Key``; because the
    MCP front-end is not the agent process, its ``_is_self_loop()`` is
    False and the verb call goes over HTTP to ``127.0.0.1:AGENT_PORT``.
    """
    from llauncher.core.agent_token import resolve_agent_token
    from llauncher.remote.node import RemoteNode

    token = resolve_agent_token(allow_generate=False)
    return RemoteNode("local", "127.0.0.1", port=settings.AGENT_PORT, api_key=token)


def get_tools() -> list[Tool]:
    """Return tool definitions for server operations."""
    return [
        Tool(
            name="start_server",
            description=(
                "Start a model on an empty port. Fails with "
                "action='rejected_occupied' if a different model is already "
                "running on that port — use swap_server for that case. "
                "Both 'model_name' and 'port' are required; the port is "
                "always specified by the caller (ADR-010). The model_name "
                "must exactly match a model from list_models."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {
                        "type": "string",
                        "description": "Name of the model to start",
                    },
                    "port": {
                        "type": "integer",
                        "description": "Port to start the model on",
                    },
                },
                "required": ["model_name", "port"],
            },
        ),
        Tool(
            name="stop_server",
            description=(
                "Stop whatever is running on this port. Idempotent: "
                "returns success with action='already_empty' if nothing "
                "was there. Returns action='stopped' on a successful "
                "termination."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number of the server to stop",
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="swap_server",
            description=(
                "Replace the model on this port with a different one. "
                "Primary use: an agent replacing its own brain on the "
                "harness's expected port. Performs the 5-phase swap "
                "(pre-flight, marker, stop, start, readiness) with "
                "rollback to the previous model on failure. Calling with "
                "the model already running on the port is a successful "
                "no-op (action='already_running'). Fails with "
                "action='rejected_empty' if the port is empty — use "
                "start_server for that case."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number to swap the model on",
                    },
                    "model_name": {
                        "type": "string",
                        "description": "Name of the new model to start",
                    },
                },
                "required": ["port", "model_name"],
            },
        ),
        Tool(
            name="cancel_server",
            description=(
                "Cancel an in-flight start or swap on this port (ADR-014). "
                "Sets a cancel flag on the in-flight marker; the running "
                "op picks it up at the next phase boundary (typically "
                "within ~1 s during readiness poll). Returns success with "
                "marker_existed=False if there is no in-flight op — "
                "'nothing to cancel' is a successful no-op, not an error. "
                "A cancel that arrives after the new process has been "
                "spawned and the lockfile written is ignored; the op "
                "completes and reports cancel_ignored_post_commit=True."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number of the in-flight op to cancel",
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="server_status",
            description="Get the status of all running llama-servers",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="list_orphans",
            description=(
                "List unmanaged llama-server processes on this node "
                "(ADR-015). An orphan is a live llama-server that "
                "llauncher did not launch — its (port, pid) does not "
                "match any live lockfile. Returns each orphan's pid, "
                "port (when discoverable from argv), and a "
                "cmdline_unreadable flag for processes whose argv "
                "could not be read. Adopt is intentionally not "
                "exposed in this revision — see ADR-015 §Deferred Work."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_server_logs",
            description="Fetch recent logs for a running server by port",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number of the server",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to fetch (default: 100)",
                    },
                },
                "required": ["port"],
            },
        ),
    ]


# ─────────── Verb tools (ADR-010, port-keyed, ops-backed) ──────────


async def start_server(args: dict) -> dict:
    """Start ``args['model_name']`` on ``args['port']``.

    Thin wrapper over :func:`llauncher.operations.start`. Returns the
    ADR-010 result envelope.
    """
    model_name = args.get("model_name")
    port = args.get("port")

    if not model_name:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: model_name",
        }
    if port is None:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: port",
        }

    # Delegation gate (#200): with a healthy local agent present (or an
    # explicit override), POST the launch to the agent over HTTP so the
    # ``llama-server`` is a child of the systemd-managed agent. With no
    # agent reachable, fall back to the in-process op (dev/standalone).
    if delegation.should_delegate():
        return _local_agent_node().start_server(model_name, port)

    result = ops.start(model_name, port, caller="mcp")
    return result.to_dict()


async def stop_server(args: dict) -> dict:
    """Stop whatever is on ``args['port']``.

    Thin wrapper over :func:`llauncher.operations.stop`.
    """
    port = args.get("port")

    if port is None:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: port",
        }

    # Delegation gate (#200): see ``start_server``.
    if delegation.should_delegate():
        return _local_agent_node().stop_server(port)

    result = ops.stop(port, caller="mcp")
    return result.to_dict()


async def swap_server(args: dict) -> dict:
    """Swap to ``args['model_name']`` on ``args['port']`` per ADR-011.

    Thin wrapper over :func:`llauncher.operations.swap`. Returns the
    ADR-010 result envelope, including ``rolled_back`` and
    ``previous_model`` when a rollback occurred.
    """
    port = args.get("port")
    model_name = args.get("model_name")

    if port is None:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: port",
        }
    if not model_name:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: model_name",
        }

    # Delegation gate (#200): see ``start_server``.
    if delegation.should_delegate():
        return _local_agent_node().swap_server(model_name, port)

    result = ops.swap(model_name, port, caller="mcp")
    return result.to_dict()


async def cancel_server(args: dict) -> dict:
    """Cancel an in-flight start/swap on ``args['port']`` per ADR-014.

    Thin wrapper over :func:`llauncher.core.marker.request_cancel`. Returns
    a small envelope so the caller can distinguish "cancel delivered" from
    "no in-flight op to cancel."
    """
    from llauncher.core import marker as mk

    port = args.get("port")
    if port is None:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: port",
        }

    delivered = mk.request_cancel(port)
    return {
        "success": True,
        "cancelled": delivered,
        "marker_existed": delivered,
        "port": port,
    }


# ───────────────── Read tools (state-backed) ───────────────────────


async def list_orphans(state: LauncherState, args: dict) -> dict:
    """List unmanaged llama-server processes per ADR-015.

    Read-side tool; refreshes orphan state once per call.
    """
    del args
    state.refresh_orphans()
    return {
        "orphans": [o.to_dict() for o in state.orphans],
        "total": len(state.orphans),
    }


async def server_status(state: LauncherState, args: dict) -> dict:
    """Get status of all running servers.

    Read-side tool; refreshes the live-process table once per call.
    """
    state.refresh()
    servers = [server.to_dict() for server in state.running.values()]

    return {
        "running_servers": servers,
        "count": len(servers),
    }


async def get_server_logs(state: LauncherState, args: dict) -> dict:
    """Fetch recent logs for a running server.

    Read-side tool; refreshes once per call to validate the port is
    still live before tailing logs.
    """
    state.refresh()

    port = args.get("port")
    lines = args.get("lines", 100)

    if port is None:
        return {"error": "Missing required argument: port"}

    if port not in state.running:
        return {"error": f"No server running on port {port}"}

    pid = state.running[port].pid
    log_lines = stream_logs(pid, lines)

    return {
        "port": port,
        "pid": pid,
        "logs": log_lines,
        "line_count": len(log_lines),
    }
