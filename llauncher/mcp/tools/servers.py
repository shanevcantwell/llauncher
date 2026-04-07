"""MCP tools for server management (start/stop/status)."""

from mcp import Tool

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs


def get_tools() -> list[Tool]:
    """Return tool definitions for server operations."""
    return [
        Tool(
            name="start_server",
            description="Start a llama-server for a specific model by name",
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
