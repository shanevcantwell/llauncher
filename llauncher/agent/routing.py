"""FastAPI routing for the llauncher agent service.

Per ADR-010, the start/swap/stop/delete-model verbs are port-keyed (or
name-keyed for delete) and delegate to :mod:`llauncher.operations`.
The agent is a thin HTTP wrapper: it translates requests into op calls
and op results into HTTP status codes.
"""

from __future__ import annotations

import socket
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response
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

    return os.getenv("LLAUNCHER_AGENT_NODE_NAME", socket.gethostname())


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
        "rejected_preflight": 409,  # added with issue #57 / ADR-005 seam
        "rejected_in_progress": 409,  # ADR-014: marker conflict
        "cancelled": 409,  # ADR-014: caller cancelled before commit
        "error": 500,
    }.get(action, 500)


def _stop_status_code(action: str) -> int:
    return {
        "stopped": 200,
        "already_empty": 200,
        "stopping": 202,  # issue #140: accepted; termination in flight
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
        "cancelled": 503,  # ADR-014: cancel → rollback path
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
def node_info() -> dict:
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
def get_status() -> dict:
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
        # ADR-015: surface orphan (unmanaged) llama-server pids alongside
        # the managed roster. Empty list when none — callers shouldn't
        # need a presence check.
        "orphans": [o.to_dict() for o in state.orphans],
        "total_orphans": len(state.orphans),
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


@router.get("/orphans")
def list_orphans_endpoint() -> dict:
    """List unmanaged ``llama-server`` processes on this node (ADR-015).

    An orphan is a live ``llama-server`` whose ``(port, pid)`` does not
    match a live lockfile. The response shape is::

        {
            "node": "...",
            "orphans": [{"pid": 1234, "port": 8081, "cmdline_unreadable": false}, ...],
            "total": <int>,
        }

    Adopt is intentionally out of scope for this revision — see ADR-015
    §Deferred Work.
    """
    state = get_state()
    state.refresh_orphans()

    return {
        "node": get_node_name(),
        "orphans": [o.to_dict() for o in state.orphans],
        "total": len(state.orphans),
    }


@router.get("/footer-context/{port}")
def get_footer_context(port: int) -> dict:
    """Minimal footer payload for ``port`` (ADR-012).

    Response shape is **pinned** by ADR-012; do not extend without
    amending that ADR. Returns 404 with ``port_empty`` when the port
    has no lockfile, matching the ADR-011 vocabulary.
    """
    from llauncher.agent.footer_cache import get_footer_context as _get

    ctx = _get(port)
    if ctx is None:
        raise HTTPException(status_code=404, detail="port_empty")
    return ctx.to_dict()


@router.get("/models")
def list_models() -> list[dict]:
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
def models_health() -> list[dict]:
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
def model_health_detail(model_name: str) -> dict:
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
#
# CONCURRENCY (issue #143): these handlers — and the blocking read
# endpoints above — are deliberately plain ``def``, NOT ``async def``.
# The underlying ops (``ops.start`` / ``ops.swap`` / ``ops.stop``) are
# synchronous and spend most of their wall-clock time in
# ``proc.wait_for_server_ready``'s ``time.sleep`` poll loop while a
# llama-server loads a model into GPU memory. Starlette runs sync path
# operations in a worker threadpool, so the event loop stays free to
# serve ``/health`` and ``/status`` concurrently. If any of these are
# changed back to ``async def``, the blocking op will run directly on the
# single event loop and stall every other request (including health
# checks) for the whole GPU load — the exact regression #143 fixed.
# ``health_check`` stays ``async def`` so it is always answered on the
# event loop without waiting for a threadpool slot.


@router.post("/start/{port}")
def start_server(port: int, body: StartRequest) -> dict:
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
def swap_server(port: int, body: SwapRequest) -> dict:
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
def stop_server(port: int, response: Response) -> dict:
    """Stop whatever is running on ``port`` per ADR-010.

    Non-blocking per issue #140: a live process is acknowledged with
    **202** and ``action="stopping"`` immediately, and the actual
    SIGTERM → grace → SIGKILL sequence runs on a background thread
    inside the agent. The previous synchronous behavior held the
    connection for the full grace (~8 s at the defaults), which
    outlived common 5 s client timeouts — callers saw failures on
    stops that succeeded. Completion is observable via ``GET /status``
    (the port empties) and the audit log (``STOPPED`` with ``SUCCESS``
    or ``ERROR``); this mirrors the ADR-014 ``/cancel/{port}`` pattern
    of acknowledging an in-flight operation without blocking on it.

    Idempotent: returns 200 with ``action="already_empty"`` if the port
    has no live claim, and 202 with ``action="stopping"`` again if a
    stop is already in flight. A termination *failure* is no longer
    reported on this response (it happens after the 202); it lands in
    the audit log and the port remains visibly occupied in status.
    """
    result = ops.stop_in_background(port, caller="agent")
    payload = result.to_dict()
    code = _stop_status_code(result.action)

    if code >= 400:
        raise HTTPException(status_code=code, detail=payload)
    response.status_code = code
    return payload


@router.post("/cancel/{port}")
def cancel_op(port: int) -> dict:
    """Signal cancellation of an in-flight start/swap on ``port`` (ADR-014).

    Sets ``cancelled=True`` on the in-flight marker. The actual abandonment
    happens at the next phase-boundary checkpoint inside the running op;
    this endpoint does not block on it.

    Returns 200 in both cases:
    - ``marker_existed=True`` — cancel signal delivered.
    - ``marker_existed=False`` — no in-flight op (successful no-op per
      ADR-014 §5; "nothing to cancel" is not an error from the caller's
      view).
    """
    from llauncher.core import marker as mk

    delivered = mk.request_cancel(port)
    return {
        "cancelled": delivered,
        "marker_existed": delivered,
        "port": port,
    }


@router.delete("/models/{model_name}")
def delete_model(model_name: str) -> dict:
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
def get_logs(port: int, lines: Annotated[int, None] = None) -> dict:
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


# ─────────────────────── Audit log (issue #64) ───────────────────


@router.get("/audit")
def get_audit(
    limit: Annotated[int, None] = None,
    action: Annotated[str, None] = None,
    result: Annotated[str, None] = None,
) -> list[dict]:
    """Return recent audit-log entries on this node (ADR-008, issue #64).

    The audit log is process-global (not port-scoped), so query params are
    used in place of a path key. ``limit`` bounds the tail (mirrors the
    Audit tab's bounded-tail discipline); ``action`` and ``result`` filter
    entries by their respective enum values. Filtering happens in-memory
    after the bounded read — :func:`audit_log.read_entries` does not yet
    accept those kwargs, and pushing them down is a separate change.

    Returns a JSON list of :meth:`AuditEntry.to_dict` dicts. An empty list
    (200) is returned when the log is missing or empty — consumers should
    not treat "no entries" as an error.
    """
    from llauncher.core import audit_log

    # Bound the tail. Omitted ``limit`` defaults to 200 entries — callers must
    # pass an explicit ``limit`` to override.
    num = limit if limit is not None else 200
    entries = audit_log.read_entries(limit=int(num))

    if action:
        entries = [e for e in entries if e.action.value == action]
    if result:
        entries = [e for e in entries if e.result.value == result]

    return [e.to_dict() for e in entries]
