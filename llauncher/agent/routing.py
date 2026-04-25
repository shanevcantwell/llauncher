"""FastAPI routing for the llauncher agent service."""

import socket
from typing import Annotated

from fastapi import APIRouter, HTTPException

from llauncher.state import EvictionResult, LauncherState

router = APIRouter()

# Global state instance - shared across all requests
_state: LauncherState | None = None


def get_state() -> LauncherState:
    """Get or create the global LauncherState instance."""
    global _state
    if _state is None:
        _state = LauncherState()
        _state.refresh()
    return _state


def get_node_name() -> str:
    """Get the node name from environment or hostname."""
    import os

    return os.getenv("LAUNCHER_AGENT_NODE_NAME", socket.gethostname())


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe endpoint.

    Returns:
        Simple health status.
    """
    return {"status": "healthy", "node": get_node_name()}


@router.get("/node-info")
async def node_info() -> dict:
    """Get information about this node.

    Returns:
        Node metadata including name, hostname, OS, and IP addresses.
    """
    import os
    import platform

    # Get all IP addresses
    ips = []
    try:
        hostname = socket.gethostname()
        addr_info = socket.getaddrinfo(hostname, None)
        ips = list(set(str(addr[4][0]) for addr in addr_info))
    except Exception:
        pass

    return {
        "node_name": get_node_name(),
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.version(),
        "python_version": platform.python_version(),
        "ip_addresses": ips,
    }


@router.get("/status")
async def get_status() -> dict:
    """Get current status of running servers on this node.

    Returns:
        Status object with running servers and node info.
    """
    state = get_state()
    state.refresh_running_servers()

    running_servers = [
        {
            "pid": server.pid,
            "port": server.port,
            "config_name": server.config_name,
            "start_time": server.start_time.isoformat(),
            "uptime_seconds": server.uptime_seconds(),
            "logs_path": server.logs_path,
        }
        for server in state.running.values()
    ]

    return {
        "node": get_node_name(),
        "running_servers": running_servers,
        "total_running": len(running_servers),
    }


@router.get("/models")
async def list_models() -> list[dict]:
    """List all configured models on this node.

    Returns:
        List of model configurations with current status.
    """
    state = get_state()
    state.refresh()

    models = []
    for name, config in state.models.items():
        # Check if this model is currently running
        running_port = None
        for server in state.running.values():
            if server.config_name == name:
                running_port = server.port
                break

        models.append(
            {
                "name": config.name,
                "model_path": config.model_path,
                "mmproj_path": config.mmproj_path,
                "default_port": config.default_port,
                "n_gpu_layers": config.n_gpu_layers,
                "ctx_size": config.ctx_size,
                "running": running_port is not None,
                "running_port": running_port,
            }
        )

    return models


@router.post("/start/{model_name}")
async def start_server(model_name: str) -> dict:
    """Start a llama-server for the specified model.

    Args:
        model_name: Name of the model configuration to start.

    Returns:
        Start result with port and PID.

    Raises:
        HTTPException 404: Model not found.
        HTTPException 409: Model already running or port conflict.
    """
    state = get_state()
    state.refresh()

    # Check if model exists
    if model_name not in state.models:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_name}")

    config = state.models[model_name]

    # Check if already running
    for server in state.running.values():
        if server.config_name == model_name:
            raise HTTPException(
                status_code=409,
                detail=f"Model '{model_name}' is already running on port {server.port}",
            )

    # Attempt to start - pass model_name string, not config object
    success, message, process = state.start_server(model_name, caller="agent")

    if not success:
        raise HTTPException(status_code=409, detail=message)

    # Refresh to get the new server info
    state.refresh_running_servers()

    # Find the newly started server to get port and pid
    for server in state.running.values():
        if server.config_name == model_name:
            return {
                "success": True,
                "message": message,
                "port": server.port,
                "pid": server.pid,
                "config_name": model_name,
            }

    # Fallback if server not found in running list
    return {"success": True, "message": message}


@router.post("/stop/{port}")
async def stop_server(port: int) -> dict:
    """Stop a llama-server running on the specified port.

    Args:
        port: Port number of the server to stop.

    Returns:
        Stop result.

    Raises:
        HTTPException 404: No server found on that port.
    """
    state = get_state()
    state.refresh()

    # Check if server is running on that port
    if port not in state.running:
        raise HTTPException(status_code=404, detail=f"No server running on port {port}")

    server = state.running[port]

    # Attempt to stop
    success, message = state.stop_server(port, caller="agent")

    if not success:
        raise HTTPException(status_code=500, detail=message)

    # Refresh state
    state.refresh_running_servers()

    return {
        "success": True,
        "message": message,
        "port": port,
        "config_name": server.config_name,
    }


@router.post("/start-with-eviction/{model_name}")
async def start_server_with_eviction(model_name: str, port: int | None = None) -> dict:
    """Start a server, evicting any existing server on the target port.

    Args:
        model_name: Name of the model configuration to start.
        port: Optional specific port (uses model's default_port if not provided).

    Returns:
        Start result with port and PID.

    Raises:
        HTTPException 404: Model not found.
        HTTPException 409: Eviction failed or other error.
    """
    state = get_state()
    state.refresh()

    if model_name not in state.models:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_name}")

    config = state.models[model_name]

    # Resolve port
    target_port = port if port is not None else config.default_port
    if target_port is None:
        raise HTTPException(status_code=400, detail="No port specified and no default_port configured")

    # Call the eviction implementation directly for structured result
    result = state._start_with_eviction_impl(
        model_name, target_port, caller="agent", readiness_timeout=120, strict_rollback=False
    )

    if result.port_state == "unavailable":
        raise HTTPException(status_code=503, detail=result.error)

    if not result.success:
        raise HTTPException(status_code=409, detail=result.error)

    # Refresh to get the new server info
    state.refresh_running_servers()

    running_server = state.running.get(target_port)
    return {
        "success": True,
        "port": target_port,
        "pid": running_server.pid if running_server else None,
        "config_name": model_name,
        "previous_model": result.previous_model or None,
        "new_model": result.new_model_attempted,
        "port_state": result.port_state,
    }


@router.get("/logs/{port}")
async def get_logs(port: int, lines: Annotated[int, None] = None) -> dict:
    """Get recent log lines for a server.

    Args:
        port: Port number of the server.
        lines: Number of lines to return (default: 100).

    Returns:
        Log lines for the server.

    Raises:
        HTTPException 404: No server found on that port.
    """
    from llauncher.core.process import stream_logs

    state = get_state()
    state.refresh()

    # Check if server is running
    if port not in state.running:
        raise HTTPException(status_code=404, detail=f"No server running on port {port}")

    server = state.running[port]

    # Get logs
    num_lines = lines or 100
    log_lines = stream_logs(pid=server.pid, lines=num_lines)

    return {
        "port": port,
        "config_name": server.config_name,
        "lines": log_lines,
        "total_lines": len(log_lines),
    }
