"""FastAPI routing for the llauncher agent service.

Per ADR-010, the start/swap/stop/delete-model verbs are port-keyed (or
name-keyed for delete) and delegate to :mod:`llauncher.operations`.
The agent is a thin HTTP wrapper: it translates requests into op calls
and op results into HTTP status codes.
"""

from __future__ import annotations

import logging
import threading
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from llauncher import operations as ops
from llauncher.state import LauncherState

logger = logging.getLogger(__name__)

router = APIRouter()

# Global state instance — used only for the read-side ``/status``,
# ``/models``, and log endpoints. The verbs no longer route through
# LauncherState; they call the v2 ops directly (ADR-008).
_state: LauncherState | None = None

# Serializes the single construction site below. FastAPI runs these sync
# handlers on a threadpool, so two cold requests can race; the lock makes
# "did *this* call construct the state?" a fact derived from the
# construction itself rather than from a separate, racy read of ``_state``.
_state_lock = threading.Lock()


def _get_state_and_freshness() -> tuple[LauncherState, bool]:
    """Get or create the global state, reporting whether *this call* built it.

    The boolean is ``True`` only for the call that actually constructed the
    state — it is returned from inside the construction's critical section,
    never inferred from a separate read of ``_state``, so a thread can never
    observe ``None``, lose the race to another thread's construction, and
    then act on a stale "I built it" flag (issue #309 review).

    Read handlers that would otherwise unconditionally re-invoke a refresh
    immediately after getting the state use this to skip that redundant
    within-request re-scan on the one call where the state was just built
    (``LauncherState.__post_init__`` already calls
    :meth:`LauncherState.refresh`) — every other call refreshes exactly as
    before, so warm-path staleness semantics (bounded by the existing
    process-scan TTL cache) are unchanged.

    A second cold request blocks on the lock until the first finishes
    constructing; that is deliberate, and strictly better than the
    double-construction race the unlocked check-then-set allowed.
    """
    global _state
    with _state_lock:
        if _state is None:
            _state = LauncherState()
            return _state, True
        return _state, False


def get_state() -> LauncherState:
    """Get or create the global LauncherState instance.

    Thin wrapper over :func:`_get_state_and_freshness` — that function owns
    the one and only construction site. ``LauncherState.__post_init__``
    already refreshes on construction; a second, immediate ``refresh()``
    here used to redundantly re-pay every process-table scan a moment after
    construction just paid for them (issue #309: 2 extra full ``psutil``
    scans on the cold path, ~12s on this Windows box). Removed.
    """
    state, _ = _get_state_and_freshness()
    return state


def get_node_name() -> str:
    """Get the node name from environment or hostname.

    Delegates to :func:`llauncher.core.node_info.get_node_name` — the
    single resolution point shared with the in-process self-loop path
    (issue #125). Re-exported here so callers and tests that import it
    from ``agent.routing`` keep working.
    """
    from llauncher.core import node_info as _node_info

    return _node_info.get_node_name()


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
    """Get information about this node.

    The payload is built by :func:`llauncher.core.node_info.get_node_info`
    — the single source shared with the in-process self-loop path so the
    HTTP endpoint and ``RemoteNode.get_node_info`` never drift (issue #125).
    """
    from llauncher.core import node_info as _node_info

    return _node_info.get_node_info()


@router.get("/status")
def get_status() -> dict:
    """Get current status of running servers on this node.

    Returns GPU health data (ADR-006) when a GPU backend is available.
    """
    from llauncher.core.gpu import GPUHealthCollector

    # Reconcile stale lockfiles before reporting (issue #201 Part 2a). A
    # server that spawned then died immediately leaves total_running correct
    # (it's gone from the process table) but its {port}.lock behind, which
    # would block a future start on that port. The sweep prunes dead claims
    # and emits one OBSERVED_STOPPED audit entry each; it is idempotent and
    # cheap, so running it on every status poll is safe.
    ops.reconcile_stale_lockfiles(caller="status")

    # Issue #309: skip the re-scan when get_state() just constructed (and
    # therefore already fully refreshed, including running servers) the
    # state within this same request. Every subsequent request finds the
    # state already built and refreshes as before.
    state, just_constructed = _get_state_and_freshness()
    if not just_constructed:
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


@router.get("/server-metrics/{port}")
def get_server_metrics(port: int) -> dict:
    """Aggregate live-telemetry snapshot for ``port`` (ADR-LLNCH-019).

    Response shape is **pinned** by the ADR; do not extend without
    amending it. Safe tier — no prompt text. Always ``200``: an
    unreachable/loading/no-metrics-flag server is a degraded envelope
    (``{"available": false, "reason": ...}``), not an HTTP error —
    matching the ADR's PARSE-AT-THE-DOOR posture. Same auth as
    ``/status`` (not exempt, see ``agent.middleware``).
    """
    from llauncher.core import server_metrics

    return server_metrics.get_aggregate_metrics(port)


@router.get("/server-slots/{port}")
def get_server_slots(port: int) -> dict:
    """Sensitive per-slot snapshot for ``port`` — includes prompt text.

    Returns ``404 slots_disabled`` when the server was not started with
    ``--slots`` (the launcher's default posture, issue #179 SP-1).
    Other degraded states (unreachable) return ``200`` with a degraded
    envelope, matching :func:`get_server_metrics`. Same auth as
    ``/status`` (not exempt).
    """
    from llauncher.core import server_metrics

    result = server_metrics.get_slots(port)
    if result.get("reason") == "slots_disabled":
        raise HTTPException(status_code=404, detail="slots_disabled")
    return result


@router.get("/models")
def list_models() -> list[dict]:
    """List all configured models on this node."""
    # Issue #309: skip the re-refresh when get_state() just constructed
    # (and therefore already fully refreshed) the state within this same
    # request; see the matching comment on GET /status.
    state, just_constructed = _get_state_and_freshness()
    if not just_constructed:
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
                "running": running_port is not None,
                "running_port": running_port,
            }
        )

    return models


# ── Issue #475 / ADR-027: model validate endpoints ────────────────
#
# Replace (not alias) ADR-005's GET /models/health[/{name}] — no in-repo
# consumer, and two endpoints serving overlapping shapes of one artifact is
# the dual-shape the no-shims rule forbids (ADR-027 §2, Q1). Plain ``def``
# (not ``async def``, matching the verb endpoints below): the underlying op
# does blocking file stats.
#
# Deliberately NOT folded into GET /models (ADR-027 §2): that endpoint is
# on the UI hot path (RemoteAggregator.get_all_models, called on every
# Streamlit rerun per node) — validation stays an explicit, separately
# cacheable call so it doesn't put N stat()/open() calls on every rerun.


@router.get("/models/validate")
def models_validate() -> dict:
    """Validation report for *all* configured models (issue #475, ADR-027)."""
    report = ops.validate_models()
    return report.model_dump(mode="json")


@router.get("/models/validate/{model_name}")
def model_validate_detail(model_name: str) -> dict:
    """Validation report for a single model (issue #475, ADR-027)."""
    from llauncher.core.config import ConfigStore

    if model_name not in ConfigStore.list_models():
        raise HTTPException(
            status_code=404, detail=f"Model '{model_name}' not found"
        )

    report = ops.validate_models(names=[model_name])
    return report.model_dump(mode="json")


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

    Issue #308: ``ops.start`` returning ``action="error"`` is a *handled*
    failure with a structured body (the branch above/below this comment).
    An *unhandled* exception escaping ``ops.start`` — e.g. an ``OSError``
    that isn't one of the specific exceptions the op already catches — is
    a different, worse case: left alone, Starlette turns it into a bare
    500 with an empty body and only a stack trace in the server's own
    logs, giving an HTTP caller nothing to act on. This wraps the call so
    that case also gets a structured body and a logged traceback, instead
    of a silent 500.
    """
    try:
        result = ops.start(body.model, port, caller="agent")
    except Exception:
        logger.exception(
            "Unhandled exception in ops.start(model=%s, port=%d)",
            body.model,
            port,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "action": "error",
                "port": port,
                "model": body.model,
                "message": "Unhandled exception while starting the server; see agent logs.",
            },
        )

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
    """Get recent log lines for a server on ``port``.

    When a server is live, tail its log by pid. When no server is live
    (issue #201 Part 2b), fall back to the most-recent ``*-{port}.log`` on
    disk so the operator can still retrieve the death cause of a server that
    spawned then exited immediately — the previous behavior 404'd in that
    case, hiding exactly the log that explains the failure. Only when no log
    file exists for the port at all do we 404.
    """
    from llauncher.core import lockfile as lf
    from llauncher.core.process import read_logs_for_port, stream_logs

    state = get_state()
    state.refresh()

    num_lines = lines or 100

    if port in state.running:
        server = state.running[port]
        log_lines = stream_logs(pid=server.pid, lines=num_lines)
        return {
            "port": port,
            "config_name": server.config_name,
            "lines": log_lines,
            "total_lines": len(log_lines),
        }

    # No live server — serve the most recent log file for the port.
    log_lines = read_logs_for_port(port, num_lines)
    if log_lines is None:
        raise HTTPException(
            status_code=404,
            detail=f"No server running on port {port} and no log file found",
        )

    # Best-effort model name from a lingering lockfile (the status-path
    # reconcile may not have run yet); None when the claim is already gone.
    claim = lf.read_lockfile(port)
    return {
        "port": port,
        "config_name": claim.model if claim is not None else None,
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
