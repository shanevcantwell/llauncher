"""``swap`` verb — ADR-011 five-phase swap mechanic with rollback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import marker as mk
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig

logger = logging.getLogger(__name__)


# Cap on how many startup-log lines we attach to a SwapResult on failure,
# preserving ADR-002's prior shape (referenced in ADR-011 open question 2).
STARTUP_LOG_TAIL_MAX = 100

# Default readiness-poll timeout in seconds (ADR-011 open question 1).
DEFAULT_READINESS_TIMEOUT_S = 120


# Type alias for pre-flight check seams. Returns ``(ok, reason)``;
# ``reason`` is empty when ``ok`` is True. ``None`` means the check is
# skipped entirely. Slice 1 default; slice 2 swaps these to real adapters
# wrapping ``core.model_health`` and ``core.gpu``.
PreflightCheck = Callable[[ModelConfig], "tuple[bool, str]"]


@dataclass(frozen=True)
class SwapResult:
    """Outcome of a swap operation per ADR-011's response envelope.

    ``action`` values:

    - ``swapped`` — different model swapped in, ready (``success=True``)
    - ``already_running`` — same model already there (``success=True``)
    - ``rolled_back`` — new model failed, old model restored (``success=False``)
    - ``failed`` — new model failed, rollback also failed; port is dead
    - ``rejected_preflight`` — pre-flight check failed before any state change
    - ``rejected_stop_failed`` — couldn't stop old model; old still running
    - ``rejected_in_progress`` — swap already in flight on this port
    - ``rejected_empty`` — port had no occupant; per ADR-010 swap requires occupied

    ``port_state`` values: ``serving | restored | unchanged | unavailable``
    (semantics preserved from ADR-002).
    """

    success: bool
    action: str
    port_state: str
    port: int
    model: str | None = None  # what's currently on the port
    previous_model: str | None = None  # what was there before the swap
    pid: int | None = None
    message: str = ""
    startup_logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _tail_logs(logs: list[str]) -> list[str]:
    """Cap startup logs to the last ``STARTUP_LOG_TAIL_MAX`` lines."""
    if len(logs) <= STARTUP_LOG_TAIL_MAX:
        return list(logs)
    return list(logs[-STARTUP_LOG_TAIL_MAX:])


def _run_preflight_check(
    check: PreflightCheck | None,
    config: ModelConfig,
    label: str,
) -> tuple[bool, str]:
    """Invoke an optional pre-flight check, defaulting to pass when ``None``."""
    if check is None:
        return True, ""
    try:
        ok, reason = check(config)
    except Exception as exc:  # noqa: BLE001 — failure here must not crash swap
        logger.exception("%s pre-flight check raised; treating as failure", label)
        return False, f"{label} check raised: {exc}"
    return ok, reason


def _launch_and_await_ready(
    config: ModelConfig,
    port: int,
    *,
    server_bin: Path | None,
    readiness_timeout: int,
) -> tuple[bool, int | None, list[str], str]:
    """Launch ``config`` on ``port`` and poll readiness.

    Returns ``(ready, pid, startup_logs, error_message)``. On any failure,
    ``ready`` is False; this function takes responsibility for cleaning up
    after itself (terminating the process if it started, removing the
    lockfile if one was written).
    """
    try:
        popen = proc.start_server(config, port, server_bin=server_bin)
    except (FileNotFoundError, OSError) as exc:
        return False, None, [], f"process launch failed: {exc}"

    # Claim the port via lockfile (atomic O_EXCL). A failure here means
    # something raced us; tear down the process we just started.
    try:
        lf.write_lockfile(port, config.name, popen.pid)
    except FileExistsError:
        try:
            popen.terminate()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.exception("Failed to terminate raced-launch process %s", popen.pid)
        return False, popen.pid, [], "lockfile race: another writer claimed the port"

    ready, logs = proc.wait_for_server_ready(port, timeout=readiness_timeout)
    if not ready:
        # Process started but never reached ready — terminate and clean up.
        try:
            proc.stop_server_by_pid(popen.pid)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.exception("Failed to terminate non-ready process %s", popen.pid)
        lf.remove_lockfile(port)
        return False, popen.pid, _tail_logs(logs), "readiness timeout"

    return True, popen.pid, _tail_logs(logs), ""


def _reject_preflight(
    *,
    port: int,
    model_name: str,
    previous_model_name: str,
    previous_pid: int,
    caller: str,
    audit_message: str,
    user_message: str,
) -> SwapResult:
    """Audit + return a uniform ``rejected_preflight`` SwapResult."""
    al.record(
        AuditAction.SWAPPED,
        AuditResult.REJECTED_PREFLIGHT,
        caller=caller,
        port=port,
        model=model_name,
        from_model=previous_model_name,
        pid=previous_pid,
        message=audit_message,
    )
    return SwapResult(
        success=False,
        action="rejected_preflight",
        port_state="unchanged",
        port=port,
        model=previous_model_name,
        previous_model=previous_model_name,
        pid=previous_pid,
        message=user_message,
    )


def swap(
    model_name: str,
    port: int,
    *,
    caller: str = "unknown",
    server_bin: Path | None = None,
    readiness_timeout: int = DEFAULT_READINESS_TIMEOUT_S,
    model_health_check: PreflightCheck | None = None,
    vram_check: PreflightCheck | None = None,
) -> SwapResult:
    """Swap the model on ``port`` to ``model_name`` per ADR-011's 5-phase mechanic.

    The five phases:

    1. **Pre-flight validation** (no state mutation): model exists in config,
       port is occupied, lockfile reconciles, no in-flight marker, optional
       model-health and VRAM checks pass.
    2. **Take the in-flight marker** atomically (``O_EXCL``).
    3. **Stop the old model** (SIGTERM with grace, escalate to SIGKILL).
    4. **Start the new model** (process + lockfile).
    5. **Readiness poll** (``GET /health`` until 200 OK or timeout).

    On Phase 4 or Phase 5 failure, rollback restarts the previous model on
    the same port using the config snapshot taken at Phase 1.

    The pre-flight ``model_health_check`` and ``vram_check`` are callables
    accepting a ``ModelConfig`` and returning ``(ok, reason)``. They default
    to ``None`` (skip) in M2 slice 1; M2 slice 2 wires them to the
    ``core.model_health`` and ``core.gpu`` modules.
    """

    # ---- Phase 1: Pre-flight ------------------------------------------------
    # 1a. Read the lockfile and confirm port is occupied with a live process.
    existing = lf.read_lockfile(port)
    if existing is None:
        al.record(
            AuditAction.SWAPPED,
            AuditResult.REJECTED_EMPTY,
            caller=caller,
            port=port,
            model=model_name,
            message="port empty; swap requires occupied port",
        )
        return SwapResult(
            success=False,
            action="rejected_empty",
            port_state="unchanged",
            port=port,
            model=None,
            previous_model=None,
            message=f"Port {port} is empty; use start to launch {model_name}.",
        )

    recon = lf.reconcile_lockfile(existing)
    if not recon.pid_alive:
        # Stale lockfile — the port is effectively empty for swap purposes.
        # Per ADR-010, swap's precondition is occupied; treat this as
        # rejected_empty and let the caller use start instead. We also
        # record observed_stopped so the audit log reflects the cleanup.
        al.record(
            AuditAction.OBSERVED_STOPPED,
            AuditResult.SUCCESS,
            caller=caller,
            port=port,
            model=existing.model,
            pid=existing.pid,
            message="reconciliation: stale lockfile during swap pre-flight",
        )
        lf.remove_lockfile(port)
        al.record(
            AuditAction.SWAPPED,
            AuditResult.REJECTED_EMPTY,
            caller=caller,
            port=port,
            model=model_name,
            from_model=existing.model,
            message="port effectively empty (stale lockfile); swap requires occupied",
        )
        return SwapResult(
            success=False,
            action="rejected_empty",
            port_state="unchanged",
            port=port,
            model=None,
            previous_model=existing.model,
            message=(
                f"Port {port} had a stale lockfile for {existing.model}; "
                "use start to launch a model."
            ),
        )

    previous_model_name = existing.model
    previous_pid = existing.pid

    # 1b. Same-model swap short-circuit — return immediately, marker still
    # taken+released for concurrency safety per ADR-011.
    if previous_model_name == model_name:
        try:
            mk.take_marker(
                port,
                caller=caller,
                from_model=previous_model_name,
                to_model=model_name,
            )
        except FileExistsError:
            return _build_in_progress_result(
                port,
                model_name,
                caller,
                previous_model=previous_model_name,
                pid=previous_pid,
            )
        mk.release_marker(port)
        return SwapResult(
            success=True,
            action="already_running",
            port_state="serving",
            port=port,
            model=model_name,
            previous_model=previous_model_name,
            pid=previous_pid,
            message=f"{model_name} is already running on port {port}",
        )

    # 1c. New model must exist in config.
    new_config = ConfigStore.get_model(model_name)
    if new_config is None:
        return _reject_preflight(
            port=port,
            model_name=model_name,
            previous_model_name=previous_model_name,
            previous_pid=previous_pid,
            caller=caller,
            audit_message=f"new model not found in config: {model_name}",
            user_message=f"Model not found in config: {model_name}",
        )

    # 1d. Snapshot the rollback config now so a mid-swap config edit can't
    # poison rollback (ADR-011 §Rollback).
    previous_config = ConfigStore.get_model(previous_model_name)
    if previous_config is None:
        # Lockfile says X is on the port but config X is missing — corruption
        # case from ADR-008's reconciliation rules. Refuse to swap.
        return _reject_preflight(
            port=port,
            model_name=model_name,
            previous_model_name=previous_model_name,
            previous_pid=previous_pid,
            caller=caller,
            audit_message=(
                f"corruption: lockfile claims {previous_model_name} but config absent; "
                "rollback would be impossible"
            ),
            user_message=(
                f"Lockfile claims {previous_model_name} but its config is missing; "
                "rollback would be impossible. Manual intervention required."
            ),
        )

    # 1e. Optional health checks (model file + VRAM) on the new config.
    ok, reason = _run_preflight_check(model_health_check, new_config, "model_health")
    if not ok:
        return _reject_preflight(
            port=port,
            model_name=model_name,
            previous_model_name=previous_model_name,
            previous_pid=previous_pid,
            caller=caller,
            audit_message=f"model_health pre-flight failed: {reason}",
            user_message=f"Model health check failed: {reason}",
        )

    ok, reason = _run_preflight_check(vram_check, new_config, "vram")
    if not ok:
        return _reject_preflight(
            port=port,
            model_name=model_name,
            previous_model_name=previous_model_name,
            previous_pid=previous_pid,
            caller=caller,
            audit_message=f"vram pre-flight failed: {reason}",
            user_message=f"VRAM headroom check failed: {reason}",
        )

    # ---- Phase 2: Take the in-flight marker ---------------------------------
    try:
        mk.take_marker(
            port,
            caller=caller,
            from_model=previous_model_name,
            to_model=model_name,
        )
    except FileExistsError:
        return _build_in_progress_result(
            port, model_name, caller, previous_model=previous_model_name, pid=previous_pid
        )

    # From here on, every terminal path must release the marker.
    try:
        # ---- Phase 3: Stop the old model -----------------------------------
        stop_ok = proc.stop_server_by_port(port)
        if not stop_ok:
            al.record(
                AuditAction.SWAPPED,
                AuditResult.REJECTED_STOP_FAILED,
                caller=caller,
                port=port,
                model=model_name,
                from_model=previous_model_name,
                pid=previous_pid,
                message="failed to stop old model; old model still running",
            )
            return SwapResult(
                success=False,
                action="rejected_stop_failed",
                port_state="unchanged",
                port=port,
                model=previous_model_name,
                previous_model=previous_model_name,
                pid=previous_pid,
                message=(
                    f"Could not stop {previous_model_name} on port {port}; "
                    "swap aborted, old model still running."
                ),
            )

        lf.remove_lockfile(port)
        al.record(
            AuditAction.STOPPED,
            AuditResult.SUCCESS,
            caller=caller,
            port=port,
            model=previous_model_name,
            pid=previous_pid,
            message="phase 3 of swap",
        )

        # ---- Phase 4 + 5: Launch new + readiness poll ----------------------
        ready, new_pid, startup_logs, err = _launch_and_await_ready(
            new_config,
            port,
            server_bin=server_bin,
            readiness_timeout=readiness_timeout,
        )

        if ready:
            al.record(
                AuditAction.STARTED,
                AuditResult.SUCCESS,
                caller=caller,
                port=port,
                model=model_name,
                pid=new_pid,
                message="phase 4 of swap",
            )
            al.record(
                AuditAction.SWAPPED,
                AuditResult.SUCCESS,
                caller=caller,
                port=port,
                model=model_name,
                from_model=previous_model_name,
                pid=new_pid,
                message=f"swap complete: {previous_model_name} → {model_name}",
            )
            return SwapResult(
                success=True,
                action="swapped",
                port_state="serving",
                port=port,
                model=model_name,
                previous_model=previous_model_name,
                pid=new_pid,
                message=(
                    f"Swapped {previous_model_name} → {model_name} on port {port}"
                ),
                startup_logs=startup_logs,
            )

        # New model failed — record the failed start and fall through to rollback.
        al.record(
            AuditAction.STARTED,
            AuditResult.ERROR,
            caller=caller,
            port=port,
            model=model_name,
            pid=new_pid,
            message=f"phase 4 of swap failed: {err}",
        )

        # ---- Rollback: restore the previous model --------------------------
        rb_ready, rb_pid, rb_logs, rb_err = _launch_and_await_ready(
            previous_config,
            port,
            server_bin=server_bin,
            readiness_timeout=readiness_timeout,
        )

        if rb_ready:
            al.record(
                AuditAction.STARTED,
                AuditResult.SUCCESS,
                caller=caller,
                port=port,
                model=previous_model_name,
                pid=rb_pid,
                message="rollback restoration of previous model",
            )
            al.record(
                AuditAction.SWAPPED,
                AuditResult.ROLLED_BACK,
                caller=caller,
                port=port,
                model=previous_model_name,
                from_model=previous_model_name,
                pid=rb_pid,
                message=(
                    f"swap to {model_name} failed ({err}); rolled back to "
                    f"{previous_model_name}"
                ),
            )
            return SwapResult(
                success=False,
                action="rolled_back",
                port_state="restored",
                port=port,
                model=previous_model_name,
                previous_model=previous_model_name,
                pid=rb_pid,
                message=(
                    f"Swap to {model_name} failed ({err}); rolled back to "
                    f"{previous_model_name}. Inference session was reset."
                ),
                startup_logs=startup_logs,
            )

        # Rollback also failed — port is dead; manual intervention required.
        al.record(
            AuditAction.SWAPPED,
            AuditResult.UNAVAILABLE,
            caller=caller,
            port=port,
            model=None,
            from_model=previous_model_name,
            message=(
                f"port_dead: swap to {model_name} failed ({err}); rollback to "
                f"{previous_model_name} also failed ({rb_err})"
            ),
        )
        return SwapResult(
            success=False,
            action="failed",
            port_state="unavailable",
            port=port,
            model=None,
            previous_model=previous_model_name,
            message=(
                "Swap failed and rollback failed — manual intervention required. "
                f"new={err}; rollback={rb_err}"
            ),
            startup_logs=startup_logs + rb_logs,
        )
    finally:
        mk.release_marker(port)


def _build_in_progress_result(
    port: int,
    model_name: str,
    caller: str,
    *,
    previous_model: str | None = None,
    pid: int | None = None,
) -> SwapResult:
    """Build the response when a marker already exists for ``port``.

    Performs lazy stale-marker reconciliation: if the holder's
    ``llauncher_pid`` is dead, the marker is cleared and the operation can
    be retried. We don't auto-retry from here — the caller decides whether
    to re-issue the swap.
    """
    in_flight = mk.read_marker(port)
    if in_flight is not None:
        recon = mk.reconcile_marker(in_flight)
        if not recon.owner_alive:
            # Stale — clean up and audit. Caller may retry on a follow-up call.
            mk.release_marker(port)
            al.record(
                AuditAction.SWAP_ABORTED,
                AuditResult.SUCCESS,
                caller=caller,
                port=port,
                model=in_flight.to_model,
                from_model=in_flight.from_model,
                message="stale marker reconciled (holder pid dead)",
            )

    al.record(
        AuditAction.SWAPPED,
        AuditResult.REJECTED_IN_PROGRESS,
        caller=caller,
        port=port,
        model=model_name,
        from_model=previous_model,
        pid=pid,
        message="another swap is in flight on this port",
    )
    msg = "Another swap is in flight on this port; try again shortly."
    if in_flight is not None:
        msg += f" (in_flight_caller={in_flight.caller!r}, since={in_flight.started_at})"
    return SwapResult(
        success=False,
        action="rejected_in_progress",
        port_state="unchanged",
        port=port,
        model=previous_model,
        previous_model=previous_model,
        pid=pid,
        message=msg,
    )
