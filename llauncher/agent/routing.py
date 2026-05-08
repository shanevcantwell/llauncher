"""FastAPI routing for the llauncher agent service.

Per ADR-010, the start/swap/stop/delete-model verbs are port-keyed (or
name-keyed for delete) and delegate to :mod:`llauncher.operations`.
The agent is a thin HTTP wrapper: it translates requests into op calls
and op results into HTTP status codes.
"""

from __future__ import annotations

import socket
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llauncher import operations as ops
from llauncher.state import LauncherState

router = APIRouter()

# Global state instance — used only for the read-side ``/status``,
# ``/models``, and log endpoints. The verbs no longer route through
# LauncherState; they call the v2 ops directly (ADR-008).
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


# ─────────── Request body schemas ────────────────────────────────


class StartRequest(BaseModel):
    """Body for POST /start/{port}."""

    model: str


class SwapRequest(BaseModel):
    """Body for POST /swap/{port}."""

    model: str


# ─────────── Status-code mapping per ADR-010 action discriminator ─


def _start_status_code(action: str) -> int:
    """Map a ``StartResult.action`` value to an HTTP status code."""
    return {
        "started": 200,
        "already_running": 200,
        "rejected_occupied": 409,
        "error": 500,
    }.get(action, 500)


def _stop_status_code(action: str) -> int:
    return {
        "stopped": 200,
        "already_empty": 200,
        "error": 500,
    }.get(action, 500)


def _swap_status_code(action: str) -> int:
    return {
        "swapped": 200,
        "already_running": 200,
        "rejected_empty": 400,
        "rejected_preflight": 409,
        "rejected_in_progress": 409,
        "rejected_stop_failed": 500,
        "rolled_back": 503,
        "failed": 500,
    }.get(action, 500)


def _delete_status_code(action: str) -> int:
    return {
        "deleted": 200,
        "not_found": 404,
        "rejected_in_use": 409,
        "error": 500,
    }.get(action, 500)


# ───────────────────── Read endpoints ────────────────────────────


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe endpoint."""
    from llauncher import __version__ as llauncher_version

    return {
        "status": "healthy",
        "node": get_node_name(),
        "version": llauncher_version,
    }


@router.get("/node-info")
async def node_info() -> dict:
    """Get information about this node."""
    import platform

    ips: list[str] = []
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
            "model_config": (
                state.models.get(server.config_name).to_dict()
                if server.config_name in state.models
                else None
            ),
        }
        for server in state.running.values()
    ]

    response: dict = {
        "node": get_node_name(),
        "running_servers": running_servers,
        "total_running": len(running_servers),
    }

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
    """List all configured models on this node."""
    state = get_state()
    state.refresh()

    models = []
    for name, config in state.models.items():
        running_port = None
        for server in state.running.values():
            if server.config_name == name:
                running_port = server.port
                break

        models.append(
            {
                "name": config.name,
                "model_path": config.model_path,
                "kind": config.kind.value,
                "mmproj_path": config.mmproj_path,
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
    """Health status for *all* configured models (ADR-005)."""
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
    """Health status for a single model (ADR-005)."""
    from llauncher.core.model_health import check_model_health

    state = get_state()
    state.refresh()

    if model_name not in state.models:
        raise HTTPException(
            status_code=404, detail=f"Model '{model_name}' not found"
        )

    config = state.models[model_name]
    health = check_model_health(config.model_path)

    return {
        "name": model_name,
        "model_path": config.model_path,
        **health.model_dump(),
    }


# ───────────────────── Verb endpoints (ADR-010) ──────────────────


@router.post("/start/{port}")
async def start_server(port: int, body: StartRequest) -> dict:
    """Start ``body.model`` on ``port``.

    Per ADR-010, port is at the call site; the model name is the body.
    Delegates to :func:`llauncher.operations.start`. Status code reflects
    the ``action`` discriminator (200 for ``started``/``already_running``,
    409 for ``rejected_occupied``, 500 for ``error``).
    """
    result = ops.start(body.model, port, caller="agent")
    payload = result.to_dict()
    code = _start_status_code(result.action)

    if code >= 400:
        raise HTTPException(status_code=code, detail=payload)
    return payload


@router.post("/swap/{port}")
async def swap_server(port: int, body: SwapRequest) -> dict:
    """Swap the model on ``port`` to ``body.model`` per ADR-011.

    Performs the 5-phase swap (pre-flight → marker → stop → start →
    readiness) with rollback. Pre-flight uses the operations-package
    defaults (model-file health + VRAM headroom). 4xx for caller-side
    rejections, 503 when rollback succeeded after a failed start, 500
    for unrecoverable failures.
    """
    result = ops.swap(body.model, port, caller="agent")
    payload = result.to_dict()
    code = _swap_status_code(result.action)

    if code >= 400:
        raise HTTPException(status_code=code, detail=payload)
    return payload


@router.post("/stop/{port}")
async def stop_server(port: int) -> dict:
    """Stop whatever is running on ``port`` per ADR-010.

    Idempotent: returns 200 with ``action="already_empty"`` if the port
    has no live claim. 500 only when termination is attempted and fails.
    """
    result = ops.stop(port, caller="agent")
    payload = result.to_dict()
    code = _stop_status_code(result.action)

    if code >= 400:
        raise HTTPException(status_code=code, detail=payload)
    return payload


@router.delete("/models/{model_name}")
async def delete_model(model_name: str) -> dict:
    """Remove ``model_name`` from the config per ADR-008 §4.1.

    Refuses with 409 when the model is currently running on any port.
    Idempotent on a missing name (200 + ``action="not_found"``).
    """
    result = ops.delete_model(model_name, caller="agent")
    payload = result.to_dict()
    code = _delete_status_code(result.action)

    if code >= 400:
        raise HTTPException(status_code=code, detail=payload)
    return payload


# ─────────────────────── Logs ────────────────────────────────────


@router.get("/logs/{port}")
async def get_logs(port: int, lines: Annotated[int, None] = None) -> dict:
    """Get recent log lines for a server."""
    from llauncher.core.process import stream_logs

    state = get_state()
    state.refresh()

    if port not in state.running:
        raise HTTPException(
            status_code=404, detail=f"No server running on port {port}"
        )

    server = state.running[port]
    num_lines = lines or 100
    log_lines = stream_logs(pid=server.pid, lines=num_lines)

    return {
        "port": port,
        "config_name": server.config_name,
        "lines": log_lines,
        "total_lines": len(log_lines),
    }
