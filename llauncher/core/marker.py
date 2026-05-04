"""Per-port in-flight swap marker.

Per ADR-011 (Swap Semantics v2). The marker is a sentinel file at
``{LAUNCHER_RUN_DIR}/{port}.swap`` created atomically (``O_EXCL``) at the
start of Phase 2 of a swap and removed when the swap reaches any terminal
phase (success, rollback, or failure).

The marker provides two guarantees:

1. **Concurrent-swap rejection.** A second swap arriving on a port whose
   marker file already exists returns ``rejected_in_progress`` immediately
   rather than racing with the in-flight swap.
2. **Stale-marker reconciliation.** If the llauncher process that holds the
   marker dies externally, the marker becomes stale. Lazy reconciliation on
   read (same pattern as :mod:`llauncher.core.lockfile`) clears the stale
   marker so subsequent swaps can proceed.

Like the lockfile, the marker format is internal — external consumers reach
llauncher through the HTTP Agent and never read this file directly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from llauncher.core.lockfile import is_pid_alive
from llauncher.core.settings import LAUNCHER_RUN_DIR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwapMarker:
    """In-flight swap claim on a port."""

    port: int
    caller: str  # "cli" | "mcp" | "http" | "ui" | ...
    started_at: str  # ISO 8601 UTC
    llauncher_pid: int
    from_model: str
    to_model: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SwapMarker":
        return cls(
            port=int(data["port"]),
            caller=str(data["caller"]),
            started_at=str(data["started_at"]),
            llauncher_pid=int(data["llauncher_pid"]),
            from_model=str(data["from_model"]),
            to_model=str(data["to_model"]),
        )


@dataclass(frozen=True)
class MarkerReconcileResult:
    """Outcome of reconciling a marker against the live process table."""

    marker: SwapMarker
    owner_alive: bool


def _resolve_run_dir(run_dir: Path | None) -> Path:
    return run_dir if run_dir is not None else LAUNCHER_RUN_DIR


def marker_path(port: int, run_dir: Path | None = None) -> Path:
    """Return the canonical marker path for a given port."""
    return _resolve_run_dir(run_dir) / f"{port}.swap"


def take_marker(
    port: int,
    *,
    caller: str,
    from_model: str,
    to_model: str,
    run_dir: Path | None = None,
) -> SwapMarker:
    """Atomically create the in-flight marker for ``port``.

    Raises:
        FileExistsError: if a marker already exists for ``port``. Callers
            should reconcile (via :func:`reconcile_marker` or
            :func:`read_marker` + the staleness check) before retrying.
    """
    base = _resolve_run_dir(run_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = marker_path(port, base)

    marker = SwapMarker(
        port=port,
        caller=caller,
        started_at=datetime.now(timezone.utc).isoformat(),
        llauncher_pid=os.getpid(),
        from_model=from_model,
        to_model=to_model,
    )

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(marker.to_dict(), f, indent=2)
    except Exception:
        # Best-effort cleanup of a partial write so reconciliation isn't
        # poisoned by an empty/corrupt marker.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    return marker


def read_marker(port: int, *, run_dir: Path | None = None) -> SwapMarker | None:
    """Read the marker for ``port``, or return ``None`` if absent or corrupt."""
    path = marker_path(port, run_dir)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return SwapMarker.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Corrupt swap marker %s: %s", path, exc)
        return None


def release_marker(port: int, *, run_dir: Path | None = None) -> bool:
    """Remove the marker for ``port``. Returns True if removed, False if absent."""
    path = marker_path(port, run_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def reconcile_marker(marker: SwapMarker) -> MarkerReconcileResult:
    """Validate that a marker's holder is still live.

    A marker is considered stale when its ``llauncher_pid`` no longer
    corresponds to a live process (the llauncher that started the swap
    died). Callers handle stale markers by clearing them and proceeding,
    typically logging an ``OBSERVED_STOPPED``-style audit entry first.
    """
    return MarkerReconcileResult(
        marker=marker,
        owner_alive=is_pid_alive(marker.llauncher_pid),
    )
