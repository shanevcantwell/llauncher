"""``stop`` verb — terminate the model on a port per ADR-010 semantics.

Two entry points share the same reconcile/terminate machinery:

- :func:`stop` — synchronous. Blocks through the full SIGTERM grace
  (worst case ``LLAUNCHER_STOP_CHILD_GRACE_S + LLAUNCHER_STOP_GRACE_S``,
  ~8 s by default) and returns the definitive outcome. Correct for
  in-process callers (CLI, MCP server, the remote self-loop, the agent's
  shutdown reaper) where no transport timeout exists and a short-lived
  process must not exit with a half-delivered termination.
- :func:`stop_in_background` — non-blocking (issue #140). Performs the
  same fast pre-checks synchronously, then terminates a live process on
  a background thread and returns ``action="stopping"`` immediately.
  Used by the HTTP agent so ``POST /stop/{port}`` answers within
  milliseconds instead of holding the connection for the grace period —
  callers with short HTTP timeouts (the pi-agent extension defaults to
  5 s) were observing timeout errors on stops that actually succeeded.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import process as proc
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.lockfile import Lockfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopResult:
    """Outcome of a stop operation."""

    success: bool
    action: str  # stopped | stopping | already_empty | error
    port: int
    model: str | None = None  # what was running, if anything
    pid: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# In-flight background stops keyed by port (issue #140). Process-local
# bookkeeping, NOT durable state — ADR-008 still holds: the source of
# truth for "is something on this port" remains the lockfile + process
# table, both of which the background thread updates exactly as the
# synchronous path does. This registry only dedupes repeated stop
# requests landing on the *same* agent while a termination is in
# flight; a concurrent stop driven from another process at worst
# re-terminates, which ``proc.stop_server_by_pid`` tolerates.
_inflight_lock = threading.Lock()
_inflight: dict[int, threading.Thread] = {}


def _reconcile_for_stop(
    port: int, *, caller: str
) -> tuple[StopResult | None, Lockfile | None]:
    """Fast pre-check shared by :func:`stop` and :func:`stop_in_background`.

    Returns ``(early_result, None)`` when the port needs no termination
    (empty, or stale lockfile that was cleaned up), or ``(None, lockfile)``
    when a live process must be terminated.
    """
    existing = lf.read_lockfile(port)
    if existing is None:
        return (
            StopResult(
                success=True,
                action="already_empty",
                port=port,
                message=f"No server claimed port {port}",
            ),
            None,
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
        return (
            StopResult(
                success=True,
                action="already_empty",
                port=port,
                model=existing.model,
                pid=existing.pid,
                message=f"Lockfile was stale for {existing.model}; cleaned up.",
            ),
            None,
        )

    return None, existing


def _terminate(port: int, existing: Lockfile, *, caller: str) -> StopResult:
    """Blocking termination tail: SIGTERM → grace → SIGKILL, lockfile, audit.

    Runs inline for :func:`stop`, on a worker thread for
    :func:`stop_in_background`. Either way the durable record (lockfile
    removal + audit entry) is emitted here, when the outcome is known.
    """
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


def stop(port: int, *, caller: str = "unknown") -> StopResult:
    """Stop whatever is running on ``port`` per ADR-010 verb semantics.

    - Empty port → idempotent success. Returns ``action="already_empty"``.
    - Stale lockfile (pid dead) → cleaned up, ``action="already_empty"``.
    - Live process → terminated, lockfile removed, ``action="stopped"``.
    - Termination failure → ``action="error"``.

    Blocks through the full termination grace (see module docstring).
    """
    early, existing = _reconcile_for_stop(port, caller=caller)
    if early is not None:
        return early
    assert existing is not None  # narrowed by _reconcile_for_stop contract
    return _terminate(port, existing, caller=caller)


def stop_in_background(port: int, *, caller: str = "unknown") -> StopResult:
    """Non-blocking stop (issue #140): accept now, terminate on a thread.

    Same pre-checks as :func:`stop`, but a live process is terminated on
    a background thread and this call returns immediately:

    - Empty port / stale lockfile → ``action="already_empty"``,
      synchronously — identical to :func:`stop`.
    - Live process → ``action="stopping"``, ``success=True``,
      immediately. Termination, lockfile removal, and the audit record
      happen on the thread; completion is observable via status (the
      port empties) and the audit log (``STOPPED`` with ``SUCCESS`` or
      ``ERROR``). This mirrors ADR-014's in-flight semantics for
      ``/cancel/{port}``: the endpoint acknowledges, it does not block
      on the outcome.
    - Repeated call while a stop is in flight → ``action="stopping"``
      again, idempotently; no second termination is spawned.

    The worker thread is daemonic so it can never wedge process exit;
    the agent's lifespan reaper (issue #65) re-walks the lockfile
    registry with the *blocking* :func:`stop` on shutdown, so a stop
    interrupted mid-grace is re-driven rather than leaked.
    """
    early, existing = _reconcile_for_stop(port, caller=caller)
    if early is not None:
        return early
    assert existing is not None  # narrowed by _reconcile_for_stop contract

    with _inflight_lock:
        pending = _inflight.get(port)
        if pending is not None and pending.is_alive():
            return StopResult(
                success=True,
                action="stopping",
                port=port,
                model=existing.model,
                pid=existing.pid,
                message=f"Stop already in progress for port {port}",
            )

        def _run() -> None:
            try:
                _terminate(port, existing, caller=caller)
            except OSError as exc:  # pragma: no cover - defensive: catches a filesystem error during the background thread's lockfile-removal/audit emit; psutil errors are already absorbed in core.process, and deterministically injecting an OSError into the daemon thread's durable-emit tail is not worth the threaded test scaffolding.
                # Filesystem failure in lockfile/audit emission. psutil
                # errors are already absorbed inside core.process. Log
                # rather than die silently in the thread; the next
                # status refresh reconciles whatever state remains.
                logger.error(
                    "Background stop for port %d failed: %s", port, exc
                )
            finally:
                # Reap this thread's registry entry so a long-lived
                # agent doesn't accumulate dead Thread objects, one per
                # completed stop. Only reap if the registered entry is
                # still *this* thread — a successor stop for the same
                # port may already have replaced it, and its entry must
                # survive.
                with _inflight_lock:
                    if _inflight.get(port) is threading.current_thread():
                        del _inflight[port]

        thread = threading.Thread(
            target=_run, name=f"llauncher-stop-{port}", daemon=True
        )
        _inflight[port] = thread
        thread.start()

    return StopResult(
        success=True,
        action="stopping",
        port=port,
        model=existing.model,
        pid=existing.pid,
        message=f"Stopping {existing.model} on port {port}",
    )


def wait_for_stop(port: int, timeout: float | None = None) -> bool:
    """Block until the in-flight background stop for ``port`` (if any) ends.

    Returns ``True`` when no stop remains in flight after the wait,
    ``False`` if a stop is still running when ``timeout`` expires.
    Primarily a deterministic join point for tests and diagnostics —
    production callers observe completion through status/audit instead.
    """
    with _inflight_lock:
        thread = _inflight.get(port)
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def join_inflight_stop(port: int, timeout: float | None = None) -> bool:
    """Join the in-flight background stop for ``port``, if one exists.

    Distinct from :func:`wait_for_stop`, which answers "is anything
    still in flight?": this answers "did an in-flight stop exist *and*
    complete?" — the coalescing question the agent's shutdown reaper
    asks before driving its own blocking :func:`stop` (so reaper and
    background thread don't both run SIGTERM/SIGKILL against the same
    port).

    Returns ``True`` only when an in-flight stop was registered and
    finished within ``timeout`` — the caller's own stop is then
    redundant and should be skipped. Returns ``False`` when nothing was
    in flight (the caller must drive its own stop) or when the in-flight
    stop was still running at timeout (the caller falls back to the
    blocking path; the overlap is tolerated, see below).

    Remaining tolerance: a background stop that registers *after* this
    join, or one driven from another process entirely, can still overlap
    the caller's blocking stop. That worst case is a re-termination of
    an already-dying pid, which ``proc.stop_server_by_pid`` absorbs
    (``NoSuchProcess`` → ``False``) — wasteful, never incorrect.
    """
    with _inflight_lock:
        thread = _inflight.get(port)
    if thread is None:
        return False
    thread.join(timeout)
    return not thread.is_alive()
