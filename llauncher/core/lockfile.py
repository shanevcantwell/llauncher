"""Per-port lockfile registry for running llauncher-managed servers.

Per ADR-008 (LauncherState as Stateless Facade). Each lockfile is the
authoritative claim that llauncher launched a specific model on a specific
port. Stored at ``{LAUNCHER_RUN_DIR}/{port}.lock`` as JSON.

The lockfile is paired with an argv (or env-var, future) sentinel for
process identity validation; see :func:`reconcile_lockfile`.

Lockfile format is internal — external consumers (the harness footer,
remote nodes) reach llauncher through the HTTP Agent and never read this
file directly. The schema may change without affecting external contracts.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from llauncher.core.settings import LAUNCHER_RUN_DIR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lockfile:
    """Authoritative claim that llauncher launched ``pid`` running ``model`` on ``port``."""

    pid: int
    model: str
    port: int
    started_at: str  # ISO 8601 UTC
    llauncher_pid: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Lockfile":
        return cls(
            pid=int(data["pid"]),
            model=str(data["model"]),
            port=int(data["port"]),
            started_at=str(data["started_at"]),
            llauncher_pid=int(data["llauncher_pid"]),
        )


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of reconciling a lockfile with the live process table."""

    lockfile: Lockfile
    pid_alive: bool
    is_ours: bool


def _resolve_run_dir(run_dir: Path | None) -> Path:
    return run_dir if run_dir is not None else LAUNCHER_RUN_DIR


def lockfile_path(port: int, run_dir: Path | None = None) -> Path:
    """Return the canonical lockfile path for a given port."""
    return _resolve_run_dir(run_dir) / f"{port}.lock"


def write_lockfile(
    port: int,
    model: str,
    pid: int,
    *,
    run_dir: Path | None = None,
) -> Lockfile:
    """Atomically create a lockfile claiming ``port``.

    Raises:
        FileExistsError: if a lockfile already exists for ``port``. The caller
            is responsible for reconciling stale lockfiles before retrying.
    """
    base = _resolve_run_dir(run_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = lockfile_path(port, base)

    lf = Lockfile(
        pid=pid,
        model=model,
        port=port,
        started_at=datetime.now(timezone.utc).isoformat(),
        llauncher_pid=os.getpid(),
    )

    # O_EXCL gives us atomic claim semantics — fails if the file exists.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lf.to_dict(), f, indent=2)
    except Exception:
        # Best-effort cleanup of a partial write so reconciliation isn't
        # poisoned by an empty/corrupt lockfile.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    return lf


def read_lockfile(port: int, *, run_dir: Path | None = None) -> Lockfile | None:
    """Read the lockfile for ``port``, or return ``None`` if absent or corrupt."""
    path = lockfile_path(port, run_dir)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return Lockfile.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Corrupt lockfile %s: %s", path, exc)
        return None


def remove_lockfile(port: int, *, run_dir: Path | None = None) -> bool:
    """Remove the lockfile for ``port``. Returns True if removed, False if absent."""
    path = lockfile_path(port, run_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def list_lockfiles(*, run_dir: Path | None = None) -> list[Lockfile]:
    """Return all parseable lockfiles in the run directory.

    Corrupt lockfiles are skipped (and logged); reconciliation is a separate
    concern handled by callers.
    """
    base = _resolve_run_dir(run_dir)
    if not base.exists():
        return []

    result: list[Lockfile] = []
    for path in sorted(base.glob("*.lock")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            result.append(Lockfile.from_dict(data))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Corrupt lockfile %s: %s", path, exc)
            continue
    return result


def is_pid_alive(pid: int) -> bool:
    """Return True if ``pid`` corresponds to a live, non-zombie process."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def reconcile_lockfile(
    lockfile: Lockfile,
    *,
    sentinel_check: Callable[[int], bool] | None = None,
) -> ReconcileResult:
    """Validate that a lockfile's claim is still live.

    Args:
        lockfile: The claim to validate.
        sentinel_check: Optional callable taking the pid and returning True
            if the live process matches our sentinel pattern (e.g. argv
            contains the configured sentinel flag, or env contains our
            owned-pid marker). When omitted, pid-liveness is the only check.

    Returns:
        ReconcileResult with ``pid_alive`` and ``is_ours`` populated.
    """
    alive = is_pid_alive(lockfile.pid)
    if not alive:
        return ReconcileResult(lockfile=lockfile, pid_alive=False, is_ours=False)

    if sentinel_check is None:
        return ReconcileResult(lockfile=lockfile, pid_alive=True, is_ours=True)

    matches = sentinel_check(lockfile.pid)
    return ReconcileResult(lockfile=lockfile, pid_alive=True, is_ours=matches)
