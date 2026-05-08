"""``stop`` verb — terminate the model on a port per ADR-010 semantics."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopResult:
    """Outcome of a stop operation."""

    success: bool
    action: str  # stopped | already_empty | error
    port: int
    model: str | None = None  # what was running, if anything
    pid: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def stop(port: int, *, caller: str = "unknown") -> StopResult:
    """Stop whatever is running on ``port`` per ADR-010 verb semantics.

    - Empty port → idempotent success. Returns ``action="already_empty"``.
    - Stale lockfile (pid dead) → cleaned up, ``action="already_empty"``.
    - Live process → terminated, lockfile removed, ``action="stopped"``.
    - Termination failure → ``action="error"``.
    """
    existing = lf.read_lockfile(port)
    if existing is None:
        return StopResult(
            success=True,
            action="already_empty",
            port=port,
            message=f"No server claimed port {port}",
        )

    recon = lf.reconcile_lockfile(existing)
    if not recon.pid_alive:
        # Stale — observed_stopped + cleanup, idempotent success.
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
        return StopResult(
            success=True,
            action="already_empty",
            port=port,
            model=existing.model,
            pid=existing.pid,
            message=f"Lockfile was stale for {existing.model}; cleaned up.",
        )

    # Live process — terminate.
    ok = proc.stop_server_by_port(port)
    if not ok:
        al.record(
            AuditAction.STOPPED,
            AuditResult.ERROR,
            caller=caller,
            port=port,
            model=existing.model,
            pid=existing.pid,
            message="process termination failed",
        )
        return StopResult(
            success=False,
            action="error",
            port=port,
            model=existing.model,
            pid=existing.pid,
            message=f"Failed to stop server on port {port}",
        )

    lf.remove_lockfile(port)
    al.record(
        AuditAction.STOPPED,
        AuditResult.SUCCESS,
        caller=caller,
        port=port,
        model=existing.model,
        pid=existing.pid,
    )
    return StopResult(
        success=True,
        action="stopped",
        port=port,
        model=existing.model,
        pid=existing.pid,
        message=f"Stopped {existing.model} on port {port}",
    )
