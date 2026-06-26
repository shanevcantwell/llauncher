"""``reconcile`` sweep — prune stale lockfiles for dead processes (issue #201).

The ``/status`` read path reports ``total_running`` from the live process
table, so a server that spawned then exited immediately is correctly absent
from the roster — but its ``{port}.lock`` is left behind, blocking a future
``start`` on that port (issue #201 Part 2a). The per-port ``start``/``stop``
verbs already reconcile a single port's lockfile on their way in
(:mod:`llauncher.operations.start`, :mod:`llauncher.operations.stop`); this
module hoists the same reconcile into a registry-wide sweep the agent can
run on every ``/status`` so a dead port is cleaned up without waiting for the
next command to touch it.
"""

from __future__ import annotations

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.lockfile import Lockfile


def reconcile_stale_lockfiles(*, caller: str = "reconcile") -> list[Lockfile]:
    """Remove every lockfile whose claimed pid is dead; return the pruned ones.

    For each parseable lockfile in the run directory, reconcile against the
    live process table. A lockfile whose pid is still alive is left untouched.
    A lockfile whose pid is dead is the stale claim of a process that has
    exited (cleanly or by immediate spawn death): emit one
    ``OBSERVED_STOPPED`` audit entry and remove the file — mirroring the
    single-port reconcile in :func:`llauncher.operations.start.start` and
    :func:`llauncher.operations.stop.stop`.

    Idempotent: a pruned lockfile is gone on the next sweep, so the audit
    entry is emitted exactly once per dead claim. Safe to call on every
    ``/status``.

    Args:
        caller: Audit caller field. Defaults to ``"reconcile"``; the agent's
            status path passes ``"status"`` so the entry is attributable.

    Returns:
        The list of stale lockfiles that were removed (empty when none).
    """
    pruned: list[Lockfile] = []
    for entry in lf.list_lockfiles():
        recon = lf.reconcile_lockfile(entry)
        if recon.pid_alive:
            continue
        al.record(
            AuditAction.OBSERVED_STOPPED,
            AuditResult.SUCCESS,
            caller=caller,
            port=entry.port,
            model=entry.model,
            pid=entry.pid,
            message="reconciliation: stale lockfile removed",
        )
        lf.remove_lockfile(entry.port)
        pruned.append(entry)
    return pruned
