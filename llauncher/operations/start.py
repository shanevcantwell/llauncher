"""``start`` verb — launch a model on a port per ADR-010 semantics."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import marker as mk
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.config import ConfigStore
from llauncher.operations.preflight import (
    DEFAULT_READINESS_TIMEOUT_S,
    PreflightCheck,
    _tail_logs,
    default_model_health_check,
    run_preflight_check,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartResult:
    """Outcome of a start operation, mirroring ADR-010's response envelope.

    ``action`` values: ``started | already_running | rejected_occupied |
    rejected_preflight | rejected_in_progress | cancelled | error``.

    ``cancel_ignored_post_commit`` (ADR-014): True iff a cancel arrived
    between spawn-success and lockfile-write. The op completed normally.
    """

    success: bool
    action: str
    port: int
    model: str | None = None
    pid: int | None = None
    message: str = ""
    cancel_ignored_post_commit: bool = False
    ctx_size: int | None = None
    parallel: int | None = None
    startup_logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def start(
    model_name: str,
    port: int,
    *,
    caller: str = "unknown",
    server_bin: Path | None = None,
    model_health_check: PreflightCheck | None = default_model_health_check,
    readiness_timeout: int = DEFAULT_READINESS_TIMEOUT_S,
) -> StartResult:
    """Start ``model_name`` on ``port`` per ADR-010 verb semantics.

    - Empty port → start. Returns ``action="started"``.
    - Same model already running → idempotent success. Returns ``action="already_running"``.
    - Different model running → fail loudly. Returns ``action="rejected_occupied"``.
    - Stale lockfile (claimed pid is dead) → cleaned up, then start.
    - Pre-flight model-file check fails → ``action="rejected_preflight"`` with no state mutation.
    - Model not found in config / launch failure → ``action="error"``.
    - Process spawns but never becomes ready (crashes, bad argv, OOM, ...) →
      ``action="error"`` with no lockfile left behind and ``startup_logs``
      carrying a bounded tail of the server's own captured stderr (#400).

    After the process is spawned and the lockfile is written, this polls
    :func:`llauncher.core.process.wait_for_server_ready` (the same helper
    :func:`llauncher.operations.swap.swap` uses) before declaring success.
    A server that spawns and dies immediately previously left a lockfile
    claiming a live process and reported ``success=True``; the readiness
    poll now catches that and tears the lockfile back down.

    The ``model_health_check`` seam mirrors :func:`llauncher.operations.swap.swap`'s
    pattern. Defaults to :func:`llauncher.operations.preflight.default_model_health_check`,
    which wraps :mod:`llauncher.core.model_health`. Pass ``None`` to skip
    (used by unit tests with synthetic configs that don't point at real model
    files). VRAM headroom is intentionally *not* checked on the bare ``start``
    path — VRAM contention is a swap concern, since an empty port has no
    competing tenant to displace.
    """
    # Reconcile any existing lockfile against the live process table.
    existing = lf.read_lockfile(port)
    if existing is not None:
        recon = lf.reconcile_lockfile(existing)
        if recon.pid_alive:
            if existing.model == model_name:
                # Idempotent short-circuit predates the config lookup below;
                # fetch it here too so a consumer folding this result still
                # sees ctx_size/parallel refreshed on a no-op start (issue #267).
                existing_config = ConfigStore.get_model(model_name)
                return StartResult(
                    success=True,
                    action="already_running",
                    port=port,
                    model=model_name,
                    pid=existing.pid,
                    message=f"{model_name} already running on port {port}",
                    ctx_size=(
                        existing_config.ctx_size if existing_config is not None else None
                    ),
                    parallel=(
                        existing_config.parallel if existing_config is not None else None
                    ),
                )
            # Different model — caller should use swap, not start.
            al.record(
                AuditAction.STARTED,
                AuditResult.REJECTED_OCCUPIED,
                caller=caller,
                port=port,
                model=model_name,
                from_model=existing.model,
                pid=existing.pid,
                message=f"port occupied by {existing.model}",
            )
            return StartResult(
                success=False,
                action="rejected_occupied",
                port=port,
                model=existing.model,
                pid=existing.pid,
                message=(
                    f"Port {port} is occupied by {existing.model}; "
                    "use swap to replace."
                ),
            )
        # Stale lockfile — record observed_stopped and clean up before start.
        al.record(
            AuditAction.OBSERVED_STOPPED,
            AuditResult.SUCCESS,
            caller=caller,
            port=port,
            model=existing.model,
            pid=existing.pid,
            message="reconciliation: stale lockfile removed",
        )
        lf.remove_lockfile(port)

    # Look up the config.
    config = ConfigStore.get_model(model_name)
    if config is None:
        al.record(
            AuditAction.STARTED,
            AuditResult.ERROR,
            caller=caller,
            port=port,
            model=model_name,
            message=f"model not found: {model_name}",
        )
        return StartResult(
            success=False,
            action="error",
            port=port,
            model=model_name,
            message=f"Model not found: {model_name}",
        )

    # ADR-014: take the in-flight marker so a concurrent cancel can signal
    # us before commit. Same primitive as swap; from_model is empty since
    # the port is empty at this point.
    try:
        mk.take_marker(
            port,
            caller=caller,
            from_model="",
            to_model=model_name,
        )
    except FileExistsError:
        # Another op is already in flight on this port. Refuse to start.
        # NOTE: FileExistsError is an OSError subclass, so this except
        # must precede the bare OSError clause below (Python matches the
        # first applicable except in source order).
        al.record(
            AuditAction.STARTED,
            AuditResult.REJECTED_IN_PROGRESS,
            caller=caller,
            port=port,
            model=model_name,
            message="another op is in flight on this port",
        )
        return StartResult(
            success=False,
            action="rejected_in_progress",
            port=port,
            model=model_name,
            message=(
                f"Another op is in flight on port {port}; "
                "try again shortly or cancel it."
            ),
        )
    except OSError as e:
        # issue #308: a marker-write failure (disk full, permissions,
        # missing/unwritable LAUNCHER_RUN_DIR, ...) previously propagated
        # out of `start` uncaught, surfacing to the HTTP layer as an
        # unhandled exception -- a silent 500 with no audit trail and no
        # structured error body. Mirrors the existing
        # `except (FileNotFoundError, OSError)` pattern around
        # `proc.start_server` below.
        al.record(
            AuditAction.STARTED,
            AuditResult.ERROR,
            caller=caller,
            port=port,
            model=model_name,
            message=f"failed to write in-flight marker: {e}",
        )
        return StartResult(
            success=False,
            action="error",
            port=port,
            model=model_name,
            message=f"Failed to write in-flight marker: {e}",
        )

    try:
        # ADR-014 checkpoint: before each pre-flight call.
        if mk.is_cancelled(port):
            return _cancelled_result(port, model_name, caller, stage="pre-preflight")

        # Pre-flight model-file health check (ADR-005). This used to live in
        # ``state.start_server``; lifting it here removes the State→Core import
        # the audit flagged as C2 (issue #57) and gives ``start`` the same
        # pluggable seam ``swap`` already exposes.
        ok, reason = run_preflight_check(model_health_check, config, "model_health")
        if not ok:
            al.record(
                AuditAction.STARTED,
                AuditResult.REJECTED_PREFLIGHT,
                caller=caller,
                port=port,
                model=model_name,
                message=f"model_health pre-flight failed: {reason}",
            )
            return StartResult(
                success=False,
                action="rejected_preflight",
                port=port,
                model=model_name,
                message=f"Model health check failed: {reason}",
            )

        # ADR-014 checkpoint: after pre-flight, before launch.
        if mk.is_cancelled(port):
            return _cancelled_result(port, model_name, caller, stage="post-preflight")

        # Launch the process.
        try:
            popen = proc.start_server(config, port, server_bin=server_bin)
        except (FileNotFoundError, OSError) as e:
            al.record(
                AuditAction.STARTED,
                AuditResult.ERROR,
                caller=caller,
                port=port,
                model=model_name,
                message=f"process launch failed: {e}",
            )
            return StartResult(
                success=False,
                action="error",
                port=port,
                model=model_name,
                message=f"Failed to launch: {e}",
            )

        # Claim the port via lockfile (atomic O_EXCL).
        # ADR-014: by the time we successfully write the lockfile we have
        # committed — a cancel that arrives after this point is a no-op
        # with cancel_ignored_post_commit=True.
        try:
            lf.write_lockfile(port, model_name, popen.pid)
        except FileExistsError:
            # Race: another writer beat us between reconcile and write. Tear
            # down the process we just started and report the conflict.
            # Routed through stop_server_by_pid (issue #415) rather than a
            # raw popen.terminate() so this teardown also invalidates the
            # process-scan cache (issue #414) — the same intrinsic guarantee
            # the readiness-timeout rollback below already gets.
            try:
                proc.stop_server_by_pid(popen.pid)
            except psutil.AccessDenied:
                # Process already exited between the race and our cleanup,
                # or we lack permission to signal it. Logging the exception
                # preserves the traceback; the race outcome is already
                # determined and we proceed to the error record.
                logger.exception("Failed to terminate raced-launch process %s", popen.pid)
            al.record(
                AuditAction.STARTED,
                AuditResult.ERROR,
                caller=caller,
                port=port,
                model=model_name,
                pid=popen.pid,
                message="lockfile race: another writer claimed the port",
            )
            return StartResult(
                success=False,
                action="error",
                port=port,
                model=model_name,
                message="Lockfile race during start; retry.",
            )

        # Readiness poll (issue #400): a process that spawns and dies
        # immediately (bad argv, missing runtime lib, unreadable GGUF, OOM)
        # must not be reported as a successful start. Same helper
        # ``swap`` uses, reading this model's exact log file so a stale
        # same-port log from a prior occupant can't shadow the result.
        ready, startup_logs = proc.wait_for_server_ready(
            port,
            timeout=readiness_timeout,
            model_name=model_name,
        )
        if not ready:
            try:
                proc.stop_server_by_pid(popen.pid)
            except psutil.AccessDenied:
                logger.exception(
                    "Failed to terminate non-ready process %s", popen.pid
                )
            lf.remove_lockfile(port)
            tail = _tail_logs(startup_logs)
            al.record(
                AuditAction.STARTED,
                AuditResult.ERROR,
                caller=caller,
                port=port,
                model=model_name,
                pid=popen.pid,
                message="readiness timeout: process did not become ready",
            )
            return StartResult(
                success=False,
                action="error",
                port=port,
                model=model_name,
                pid=popen.pid,
                message=(
                    f"{model_name} spawned on port {port} but never became "
                    "ready; see startup_logs."
                ),
                startup_logs=tail,
            )

        # Post-commit cancel detection: per ADR-014, a cancel that arrives
        # between spawn-success/lockfile-write and this check is a no-op.
        # We surface it via the advisory flag rather than tearing down.
        cancel_ignored = mk.is_cancelled(port)

        al.record(
            AuditAction.STARTED,
            AuditResult.SUCCESS,
            caller=caller,
            port=port,
            model=model_name,
            pid=popen.pid,
            message=(
                "cancel arrived post-commit; ignored"
                if cancel_ignored
                else ""
            ),
        )
        return StartResult(
            success=True,
            action="started",
            port=port,
            model=model_name,
            pid=popen.pid,
            message=f"{model_name} started on port {port}",
            cancel_ignored_post_commit=cancel_ignored,
            ctx_size=config.ctx_size,
            parallel=config.parallel,
            startup_logs=_tail_logs(startup_logs),
        )
    finally:
        mk.release_marker(port)


def _cancelled_result(
    port: int, model_name: str, caller: str, *, stage: str
) -> StartResult:
    """ADR-014: cancel detected before commit. No state change; clean exit."""
    al.record(
        AuditAction.STARTED,
        AuditResult.CANCELLED,
        caller=caller,
        port=port,
        model=model_name,
        message=f"start cancelled at stage={stage}",
    )
    return StartResult(
        success=False,
        action="cancelled",
        port=port,
        model=model_name,
        message=f"Start of {model_name} on port {port} was cancelled at {stage}.",
    )
