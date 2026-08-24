"""``orphan`` verb — discover unmanaged llama-server processes per ADR-015.

An *orphan* is a live ``llama-server`` process that llauncher did not
launch (or whose claim it has since lost). Concretely, a process found
by :func:`llauncher.core.process.discover_all` whose ``(port, pid)`` does
not match a live, parseable lockfile in ``LAUNCHER_RUN_DIR``.

ADR-015 deliberately scopes M1 of this work to **annotation and listing
only** — there is no ``adopt`` verb in this module. A future revision
may add ``adopt_orphan`` to claim an unmanaged process by writing a
lockfile for it; that work is tracked in ADR-015 §Deferred Work.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult


@dataclass(frozen=True)
class OrphanInfo:
    """A live llama-server process not claimed by any live lockfile.

    Attributes:
        pid: OS process id of the orphan.
        port: Port the orphan is bound to, or ``None`` if not discoverable
            from argv (e.g. cmdline could not be read).
        cmdline_unreadable: True when the cmdline could not be read
            (typically ``psutil.AccessDenied``). When True, ``port`` is
            necessarily ``None``; the caller may want to log a one-time
            warning and skip reconciliation for this pid.
    """

    pid: int
    port: int | None
    cmdline_unreadable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def list_orphans(*, caller: str = "reconcile") -> list[OrphanInfo]:
    """Return all live llama-server processes not claimed by a live lockfile.

    A process counts as **managed** when a lockfile exists for its port
    AND the lockfile's recorded ``pid`` matches the observed pid AND the
    lockfile's pid is alive. Anything else is an orphan.

    This function does **not** mutate audit state on its own — emission
    cadence is owned by the caller (typically
    :meth:`llauncher.state.LauncherState.refresh_orphans`) which knows
    whether a given pid is a first sighting or a repeat.

    Args:
        caller: Audit caller field. Defaults to ``"reconcile"`` because
            the call site is almost always state refresh; tests and CLI
            invocations may override.

    Returns:
        List of :class:`OrphanInfo`, ordered by ascending pid.
    """
    del caller  # currently unused; reserved for future audit hook
    orphans: list[OrphanInfo] = []

    for info in proc.discover_all():
        try:
            pid = int(info.pid)
        except (TypeError, ValueError):  # pragma: no cover - defensive: ServerProcessInfo.pid is always an int, so this coercion guard is effectively unreachable; kept to fail safe rather than crash a reconcile scan on a hypothetically non-numeric pid.
            continue

        port = info.port
        unreadable = info.cmdline_unreadable

        if unreadable or port is None:
            # No way to match against a lockfile — surface as orphan
            # so the caller can either warn (unreadable) or just record
            # the unmanaged pid (no --port in argv).
            orphans.append(
                OrphanInfo(pid=pid, port=port, cmdline_unreadable=unreadable)
            )
            continue

        claim = lf.read_lockfile(port)
        if claim is None:
            orphans.append(OrphanInfo(pid=pid, port=port))
            continue

        if not lf.is_pid_alive(claim.pid):
            # Stale lockfile — the claim points at a dead pid, but a
            # different live pid holds the port. That live pid is
            # unmanaged from our perspective.
            orphans.append(OrphanInfo(pid=pid, port=port))
            continue

        if claim.pid != pid:
            # Lockfile claims a different pid on this port. The observed
            # pid is unmanaged.
            orphans.append(OrphanInfo(pid=pid, port=port))
            continue

        # claim.pid == pid AND alive — managed.

    orphans.sort(key=lambda o: o.pid)
    return orphans


def record_observed_orphan(
    orphan: OrphanInfo,
    *,
    caller: str = "reconcile",
) -> None:
    """Append an ``observed_orphan`` audit entry for ``orphan``.

    Idempotency / dedupe across scans is the caller's responsibility —
    this function unconditionally appends.
    """
    al.record(
        AuditAction.OBSERVED_ORPHAN,
        AuditResult.SUCCESS,
        caller=caller,
        port=orphan.port,
        pid=orphan.pid,
    )
