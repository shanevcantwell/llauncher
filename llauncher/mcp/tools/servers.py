"""MCP tools for server management (start/stop/status)."""

from pathlib import Path

from mcp import Tool

from llauncher.state import LauncherState
from llauncher.core.process import (
    stream_logs,
    wait_for_server_ready,
    stop_server_by_pid,
    find_server_by_port,
)
from llauncher.core.config import ConfigStore


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


async def swap_server(state: LauncherState, args: dict) -> dict:
    """Atomically swap models on a port with rollback guarantee.

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

    # Validate required arguments
    if port is None:
        return {
            "success": False,
            "error": "Missing required argument: port",
            "port_state": "unchanged",
        }

    if not new_model_name:
        return {
            "success": False,
            "error": "Missing required argument: model_name",
            "port_state": "unchanged",
        }

    # === PHASE 1: PRE-FLIGHT VALIDATION ===

    # 1. Validate new model exists
    if new_model_name not in state.models:
        return {
            "success": False,
            "error": f"Model not found: {new_model_name}",
            "port_state": "unchanged",
        }

    new_config = state.models[new_model_name]

    # 2. Validate new model path exists
    if not Path(new_config.model_path).exists():
        return {
            "success": False,
            "error": f"New model path does not exist: {new_config.model_path}",
            "port_state": "unchanged",
        }

    # 3. Check if new model is already running elsewhere
    for running_port, server in state.running.items():
        if server.config_name == new_model_name and running_port != port:
            return {
                "success": False,
                "error": f"Model '{new_model_name}' already running on port {running_port}",
                "port_state": "unchanged",
            }

    # Capture old model info BEFORE any state changes
    old_model_name = None
    old_model_config = None

    if port in state.running:
        old_model_name = state.running[port].config_name

        # CRITICAL: Old model must have persisted config for rollback
        if old_model_name not in state.models:
            return {
                "success": False,
                "error": f"Cannot swap: old model '{old_model_name}' has no persisted config for rollback. Add it via add_model or the UI first.",
                "port_state": "unchanged",
            }

        old_model_config = state.models[old_model_name]

        # 4. Validate old model path still exists (for rollback)
        if not Path(old_model_config.model_path).exists():
            return {
                "success": False,
                "error": f"Cannot swap: old model '{old_model_name}' path no longer exists. Cannot guarantee rollback.",
                "port_state": "unchanged",
            }

    # === PHASE 2: STOP OLD MODEL ===

    if old_model_name:
        success, msg = state.stop_server(port, caller="mcp")
        if not success:
            return {
                "success": False,
                "error": f"Failed to stop old model '{old_model_name}': {msg}",
                "port_state": "unchanged",
            }

    # === PHASE 3: START NEW MODEL ===

    success, message, process = state.start_server(
        model_name=new_model_name,
        caller="mcp",
        port=port,
    )

    if not success:
        # ROLLBACK: New model failed to start, restart old one
        if old_model_config:
            rollback_success, rollback_msg, rollback_process = state.start_server(
                model_name=old_model_name,
                caller="mcp",
                port=port,
            )

            if rollback_success:
                # Wait for rollback to be ready
                ready, _ = wait_for_server_ready(port, timeout=60)
                if ready:
                    return {
                        "success": False,
                        "error": f"New model failed to start: {message}. Rolled back to '{old_model_name}'.",
                        "rolled_back": True,
                        "port_state": "restored",
                        "restored_model": old_model_name,
                        "port": port,
                    }

        # Rollback also failed - catastrophic
        return {
            "success": False,
            "error": f"Swap failed and rollback failed: {message}",
            "rolled_back": False,
            "port_state": "unavailable",
            "port": port,
            "warning": "PORT IS UNAVAILABLE - manual intervention required",
        }

    # === PHASE 4: WAIT FOR NEW MODEL READY ===

    ready, logs = wait_for_server_ready(port, timeout=timeout)

    if not ready:
        # ROLLBACK: New model didn't become ready in time
        # Terminate the failing new model
        stop_server_by_pid(process.pid)

        if old_model_config:
            # Start old model again
            rollback_success, rollback_msg, rollback_process = state.start_server(
                model_name=old_model_name,
                caller="mcp",
                port=port,
            )

            if rollback_success:
                # Wait for rollback to be ready
                rollback_ready, _ = wait_for_server_ready(port, timeout=60)
                if rollback_ready:
                    return {
                        "success": False,
                        "error": f"New model '{new_model_name}' failed to become ready within {timeout}s. Rolled back to '{old_model_name}'.",
                        "rolled_back": True,
                        "port_state": "restored",
                        "restored_model": old_model_name,
                        "port": port,
                        "startup_logs": logs,
                    }

        # Both failed - catastrophic
        return {
            "success": False,
            "error": f"Swap failed and rollback failed",
            "rolled_back": False,
            "port_state": "unavailable",
            "port": port,
            "warning": "PORT IS UNAVAILABLE - manual intervention required",
            "startup_logs": logs,
        }

    # === PHASE 5: SUCCESS ===

    # Refresh state to pick up the new server
    state.refresh_running_servers()

    # Get the PID from the refreshed state
    new_pid = None
    if port in state.running:
        new_pid = state.running[port].pid

    return {
        "success": True,
        "port": port,
        "previous_model": old_model_name,
        "new_model": new_model_name,
        "pid": new_pid,
        "rolled_back": False,
        "port_state": "serving",
    }
