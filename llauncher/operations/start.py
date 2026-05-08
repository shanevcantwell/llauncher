"""``start`` verb — launch a model on a port per ADR-010 semantics."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.config import ConfigStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartResult:
    """Outcome of a start operation, mirroring ADR-010's response envelope."""

    success: bool
    action: str  # started | already_running | rejected_occupied | error
    port: int
    model: str | None = None
    pid: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def start(
    model_name: str,
    port: int,
    *,
    caller: str = "unknown",
    server_bin: Path | None = None,
) -> StartResult:
    """Start ``model_name`` on ``port`` per ADR-010 verb semantics.

    - Empty port → start. Returns ``action="started"``.
    - Same model already running → idempotent success. Returns ``action="already_running"``.
    - Different model running → fail loudly. Returns ``action="rejected_occupied"``.
    - Stale lockfile (claimed pid is dead) → cleaned up, then start.
    - Model not found in config / launch failure → ``action="error"``.
    """
    # Reconcile any existing lockfile against the live process table.
    existing = lf.read_lockfile(port)
    if existing is not None:
        recon = lf.reconcile_lockfile(existing)
        if recon.pid_alive:
            if existing.model == model_name:
                return StartResult(
                    success=True,
                    action="already_running",
                    port=port,
                    model=model_name,
                    pid=existing.pid,
                    message=f"{model_name} already running on port {port}",
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

    # Launch the process. Model-file health and VRAM pre-flight live in M2's
    # swap mechanic (ADR-011); the bare start path keeps M1 minimal.
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
    try:
        lf.write_lockfile(port, model_name, popen.pid)
    except FileExistsError:
        # Race: another writer beat us between reconcile and write. Tear
        # down the process we just started and report the conflict.
        try:
            popen.terminate()
        except Exception:  # noqa: BLE001 — best-effort cleanup
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

    al.record(
        AuditAction.STARTED,
        AuditResult.SUCCESS,
        caller=caller,
        port=port,
        model=model_name,
        pid=popen.pid,
    )
    return StartResult(
        success=True,
        action="started",
        port=port,
        model=model_name,
        pid=popen.pid,
        message=f"{model_name} started on port {port}",
    )
