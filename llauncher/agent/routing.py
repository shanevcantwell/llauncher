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


# ─────────── Pre-flight helpers (ADR-005 / ADR-006) ──────────────

def _estimate_vram_mb(model_path: str, n_gpu_layers: int = 255) -> int:
    """Heuristic VRAM requirement estimate based on model parameters.

    Rough rule: ~1 GB per billion params for Q4_K_M quantization.
    Defaults to 7 B (≈7168 MB) when the path cannot be parsed, scaled down by
    layer-ratio if partial GPU offload is configured.
    """
    import re
    # Try to extract parameter count from common naming patterns:
    #   "llama-3-7b", "mistral-7b-v0.1", "qwen2.5-14b.Q4_K_M.gguf"
    match = re.search(r"(?<!\d)(\d+\.?\d*)\s*[bb]", model_path, re.IGNORECASE)
    if match:
        params_billion = float(match.group(1))
    else:
        params_billion = 7.0  # default fallback

    base_vram_mb = int(params_billion * 1024)

    # If only partial GPU offload (n_gpu_layers < 999), scale proportionally.
    if n_gpu_layers is not None and n_gpu_layers < 999:
        max_layers = 32  # typical Llama max layers
        layer_ratio = min(n_gpu_layers / max(1, max_layers), 1.0)
        base_vram_mb = int(base_vram_mb * layer_ratio)

    return base_vram_mb


def _check_vram_sufficient(required_mb: int) -> tuple[bool, dict | None]:
    """Check whether any GPU has sufficient free VRAM.

    Returns (True, None) when enough VRAM is available; otherwise
    ``(False, error_dict)`` with detail for the caller.  On systems without
    GPUs this check is a no-op → always returns True.
    """
    from llauncher.core.gpu import GPUHealthCollector

    collector = GPUHealthCollector()
    health = collector.get_health()

    backends = health.get("backends", [])
    if not backends:
        # No GPUs — skip pre-flight, let the process fail naturally.
        return True, None

    for device in health.get("devices", []):
        free = device.get("free_vram_mb", 0) or 0
        if free >= required_mb:
            return True, None

    max_free = max(
        (d.get("free_vram_mb") or 0 for d in health.get("devices", [])),
        default=0,
    )
    error_info = {
        "error": "insufficient_vram",
        "required_mb": required_mb,
        "available_mb": max_free,
    }
    return False, error_info



# ───────────────────── Endpoints (ADR-005 + ADR-006) ────────────

@router.get("/health")
async def health_check() -> dict:
    """Liveness probe endpoint.

    Returns:
        Health status with node name and installed version.
    """
    from llauncher import __version__ as llauncher_version

    return {
        "status": "healthy",
        "node": get_node_name(),
        "version": llauncher_version,
    }


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

    Returns GPU health data (ADR-006) when a GPU backend is available.

    Returns:
        Status object with running servers, node info, and gpu data.
    """
    from llauncher.core.gpu import GPUHealthCollector

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
            "model_config": state.models.get(server.config_name).to_dict() if server.config_name in state.models else None,
        }
        for server in state.running.values()
    ]

    response: dict = {
        "node": get_node_name(),
        "running_servers": running_servers,
        "total_running": len(running_servers),
    }

    # Append GPU health (ADR-006) — never errors even when no GPUs exist.
    try:
        collector = GPUHealthCollector()
        gpu_health = collector.get_health()
        if gpu_health.get("backends"):
            response["gpu"] = gpu_health
        else:
            response["gpu"] = {"degraded": False, "error": None}
    except Exception as e:
        response["gpu"] = {"degraded": True, "error": type(e).__name__}

    return response


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
                "np": config.np,
                "running": running_port is not None,
                "running_port": running_port,
            }
        )

    return models


# ── ADR-005: Model health endpoints ─────────────────────────────

@router.get("/models/health")
async def models_health() -> list[dict]:
    """Health status for *all* configured models (ADR-005).

    Iterates every model in the current config, calls ``check_model_health()``,
    and returns a structured JSON list.  Missing files appear with
    ``"exists": false`` rather than throwing errors.
    """
    from llauncher.core.model_health import check_model_health

    state = get_state()
    state.refresh()

    results = []
    for name, config in state.models.items():
        health = check_model_health(config.model_path)
        results.append({
            "name": name,
            "model_path": config.model_path,
            **health.model_dump(),
        })

    return results


@router.get("/models/health/{model_name}")
async def model_health_detail(model_name: str) -> dict:
    """Health status for a single model (ADR-005).

    Returns the ``ModelHealthResult`` as JSON for the named model, or a 404
    when that model is not configured.
    """
    from llauncher.core.model_health import check_model_health

    state = get_state()
    state.refresh()

    if model_name not in state.models:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    config = state.models[model_name]
    health = check_model_health(config.model_path)

    return {
        "name": model_name,
        "model_path": config.model_path,
        **health.model_dump(),
    }


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


# ── ADR-006: Pre-flight VRAM check on /start-with-eviction ──────

@router.post("/start-with-eviction/{model_name}")
async def start_server_with_eviction(model_name: str, port: int | None = None) -> dict:
    """Start a server, evicting any existing server on the target port.

    Includes VRAM pre-flight (ADR-006): when sufficient free VRAM is not
    available, returns **409 Conflict** with diagnostic detail.

    Args:
        model_name: Name of the model configuration to start.
        port: Optional specific port (uses model's default_port if not provided).

    Returns:
        Start result with port and PID.

    Raises:
        HTTPException 404: Model not found.
        HTTPException 409: Insufficient VRAM or other error.
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

    # ── ADR-006: VRAM pre-flight check ────────────────────────
    vram_required = _estimate_vram_mb(config.model_path, config.n_gpu_layers)
    vram_ok, vram_info = _check_vram_sufficient(vram_required)

    if not vram_ok:
        # Augment with model health hint (ADR-005 cross-cutting diagnostic).
        from llauncher.core.model_health import check_model_health
        health_hint = None
        try:
            mh = check_model_health(config.model_path)
            health_hint = mh.model_dump()
        except Exception:
            pass

        error_detail: dict = {**vram_info}  # type: ignore[assignment]
        if health_hint:
            error_detail["model_health_hint"] = health_hint

        raise HTTPException(
            status_code=409,
            detail=error_detail,
        )

    # ── Pre-flight model health (ADR-005) ─────────────────────
    from llauncher.core.model_health import check_model_health as ch
    mh = ch(config.model_path)
    if not mh.valid:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "model_unhealthy",
                "reason": mh.reason or "unknown",
                "path": config.model_path,
            },
        )

    # ── Proceed with eviction start ───────────────────────────
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
