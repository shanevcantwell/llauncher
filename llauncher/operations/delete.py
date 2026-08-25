"""``delete_model`` verb — remove a model from config per ADR-LLNCH-008 §4.1.

Refuses when the model is currently running on any port (live lockfile).
Stale lockfiles for the target model are reconciled (cleaned up) as a
side effect, mirroring the stale-lockfile handling in ``stop``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.config import ConfigStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteModelResult:
    """Outcome of a ``delete_model`` operation."""

    success: bool
    action: str  # deleted | not_found | rejected_in_use | error
    name: str
    in_use_port: int | None = None  # populated when action == "rejected_in_use"
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def delete_model(name: str, *, caller: str = "unknown") -> DeleteModelResult:
    """Remove ``name`` from the model config, refusing if it's in use.

    Behavior:

    - Name not in config → idempotent no-op. ``action="not_found"``,
      ``success=True``, no audit entry (matches ``stop``'s
      ``already_empty`` discipline).
    - Live lockfile claims this model on any port → refuse.
      ``action="rejected_in_use"``, ``success=False``, audit
      ``MODEL_REMOVED + REJECTED_OCCUPIED``. Config is preserved.
    - Stale lockfile (dead pid) for this model → cleaned up as a side
      effect with an ``OBSERVED_STOPPED`` audit entry, then deletion
      proceeds.
    - Otherwise → config entry removed, audit
      ``MODEL_REMOVED + SUCCESS``, ``action="deleted"``. Since issue
      #60 wired audit emission into :class:`ConfigStore` itself, the
      success audit is owned by ``ConfigStore.remove_model``; this op
      only emits the operation-level events
      (``OBSERVED_STOPPED``, ``REJECTED_OCCUPIED``) that ConfigStore
      cannot know about.
    """
    cfg = ConfigStore.get_model(name)
    if cfg is None:
        return DeleteModelResult(
            success=True,
            action="not_found",
            name=name,
            message=f"No model named {name!r} in config; nothing to delete.",
        )

    # Scan for live usage. Reconcile stale claims as we go.
    for lock in lf.list_lockfiles():
        if lock.model != name:
            continue

        recon = lf.reconcile_lockfile(lock)
        if recon.pid_alive:
            al.record(
                AuditAction.MODEL_REMOVED,
                AuditResult.REJECTED_OCCUPIED,
                caller=caller,
                port=lock.port,
                model=name,
                pid=lock.pid,
                message=f"model in use on port {lock.port}",
            )
            return DeleteModelResult(
                success=False,
                action="rejected_in_use",
                name=name,
                in_use_port=lock.port,
                message=(
                    f"Model {name!r} is running on port {lock.port} "
                    f"(pid {lock.pid}); stop it before deleting."
                ),
            )

        # Stale claim for this model — observe it and clean up.
        al.record(
            AuditAction.OBSERVED_STOPPED,
            AuditResult.SUCCESS,
            caller=caller,
            port=lock.port,
            model=name,
            pid=lock.pid,
            message="reconciliation: stale lockfile removed during delete_model",
        )
        lf.remove_lockfile(lock.port)

    # ConfigStore emits the MODEL_REMOVED + SUCCESS audit entry itself
    # per issue #60; propagate ``caller`` so the entry attributes to
    # the right surface.
    ConfigStore.remove_model(name, caller=caller)
    return DeleteModelResult(
        success=True,
        action="deleted",
        name=name,
        message=f"Removed {name!r} from config.",
    )
