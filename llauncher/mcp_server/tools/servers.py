"""MCP tools for server management (start/stop/status)."""

from mcp import Tool

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs


def get_tools() -> list[Tool]:
    """Return tool definitions for server operations."""
    return [
        Tool(
            name="start_server",
            description="Start a llama-server for a specific model by name. The model_name must exactly match a model from list_models identification.name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {
                        "type": "string",
                        "description": "Name of the model to start",
                    },
                },
                "required": ["model_name"],
            },
        ),
        Tool(
            name="stop_server",
            description="Stop a running llama-server by port number",
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
            description="Atomically swap models on a port with rollback guarantee. Stops any server on the port and starts the new model. If the new model fails to start, the old model is automatically restored. Guarantees that when this call returns, a model is serving on the port (either the new one on success, or the old one on failure). The model_name must exactly match a model from list_models identification.name.",
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
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait for new model to become ready (default: 120)",
                    },
                },
                "required": ["port", "model_name"],
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


async def start_server(state: LauncherState, args: dict) -> dict:
    """Start a llama-server for the specified model.

    Args:
        state: The launcher state.
        args: Tool arguments including 'model_name'.

    Returns:
        Dictionary with result of the operation.
    """
    model_name = args.get("model_name")

    if not model_name:
        return {"success": False, "error": "Missing required argument: model_name"}

    success, message, process = state.start_server(model_name, caller="mcp")

    return {
        "success": success,
        "message": message,
        "pid": process.pid if process else None,
    }


async def stop_server(state: LauncherState, args: dict) -> dict:
    """Stop a running llama-server.

    Args:
        state: The launcher state.
        args: Tool arguments including 'port'.

    Returns:
        Dictionary with result of the operation.
    """
    port = args.get("port")

    if port is None:
        return {"success": False, "error": "Missing required argument: port"}

    success, message = state.stop_server(port, caller="mcp")

    return {
        "success": success,
        "message": message,
    }


async def server_status(state: LauncherState, args: dict) -> dict:
    """Get status of all running servers.

    Args:
        state: The launcher state.
        args: Tool arguments (empty for this tool).

    Returns:
        Dictionary with list of running servers.
    """
    # Per-call refresh — zero-staleness on every read (ADR-006).
    from llauncher.mcp_server.server import get_mcp_state  # type: ignore[import-not-found, unused-ignore]
    get_mcp_state().refresh()
    servers = []

    for port, server in state.running.items():
        servers.append(server.to_dict())

    return {
        "running_servers": servers,
        "count": len(servers),
    }


async def get_server_logs(state: LauncherState, args: dict) -> dict:
    """Fetch recent logs for a running server.

    Args:
        state: The launcher state.
        args: Tool arguments including 'port' and optional 'lines'.

    Returns:
        Dictionary with log lines.
    """
    # Per-call refresh — prevents reading from stale server data; always re-reads running servers (ADR-006).
    from llauncher.mcp_server.server import get_mcp_state  # type: ignore[import-not-found, unused-ignore]
    get_mcp_state().refresh()

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


async def swap_server(state: LauncherState, args: dict) -> dict:
    """Atomically swap models on a port with rollback guarantee.

    Delegates to LauncherState._start_with_eviction_impl() which implements
    the full 5-phase swap flow (pre-flight, stop-old, start-new, readiness,
    rollback). This thin wrapper maps the EvictionResult into the MCP response dict.

    Contract:
    - On success (success=true): new model is serving on the port
    - On failure with rollback (success=false, rolled_back=true): old model restored and serving
    - Catastrophic failure (success=false, rolled_back=false, port_state="unavailable"):
      port is dead - manual intervention required

    Args:
        state: The launcher state.
        args: Tool arguments including 'port', 'model_name', and optional 'timeout'.

    Returns:
        Dictionary with swap result including port_state indicator.
    """
    port = args.get("port")
    new_model_name = args.get("model_name")
    timeout = args.get("timeout", 120)

    if port is None:
        return {"success": False, "error": "Missing required argument: port", "port_state": "unchanged"}

    if not new_model_name:
        return {"success": False, "error": "Missing required argument: model_name", "port_state": "unchanged"}

    result = state._start_with_eviction_impl(
        model_name=new_model_name,
        port=port,
        caller="mcp",
        readiness_timeout=timeout,
        strict_rollback=True,
    )

    return {
        "success": result.success,
        "port_state": result.port_state,
        "error": result.error if not result.success else None,
        "rolled_back": result.rolled_back,
        "restored_model": result.restored_model or None,
        "previous_model": result.previous_model or None,
        "new_model": result.new_model_attempted or None,
    }
