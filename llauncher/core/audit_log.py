"""Append-only JSON Lines audit log for llauncher actions and observations.

Per ADR-LLNCH-008. The audit log distinguishes:

- **Commanded** actions — things llauncher did (started, stopped, swapped,
  CRUD on configs).
- **Observed** state changes — things llauncher discovered during
  reconciliation (a tracked process found dead, an orphan process matching
  our sentinel pattern with no lockfile).

The log is plain JSON Lines, append-only, never truncated by llauncher.
Rotation and retention are out-of-scope for ADR-LLNCH-008 and tracked separately
(Tier 2).

Path is configurable via the ``LAUNCHER_AUDIT_PATH`` env var so container
deployments can mount a host directory and let in-container agents read
the log produced on the host.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from llauncher.core.settings import LAUNCHER_AUDIT_PATH

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    """Discriminator on whether llauncher took the action or merely observed it."""

    # Commanded — llauncher performed this action.
    STARTED = "started"
    STOPPED = "stopped"
    SWAPPED = "swapped"
    MODEL_ADDED = "model_added"
    MODEL_UPDATED = "model_updated"
    MODEL_REMOVED = "model_removed"

    # Observed — llauncher discovered this state during reconciliation.
    OBSERVED_STOPPED = "observed_stopped"
    OBSERVED_ORPHAN = "observed_orphan"
    SWAP_ABORTED = "swap_aborted"  # in-flight marker found stale, llauncher dead


class AuditResult(str, Enum):
    """Outcome of the action. ``success`` is the default for observed events."""

    SUCCESS = "success"
    ERROR = "error"
    REJECTED_PREFLIGHT = "rejected_preflight"
    REJECTED_IN_PROGRESS = "rejected_in_progress"
    REJECTED_OCCUPIED = "rejected_occupied"
    REJECTED_EMPTY = "rejected_empty"
    REJECTED_STOP_FAILED = "rejected_stop_failed"
    ROLLED_BACK = "rolled_back"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"  # Per ADR-LLNCH-014: caller cancelled before commit.


@dataclass(frozen=True)
class AuditEntry:
    """A single audit log line."""

    timestamp: str  # ISO 8601 UTC
    action: AuditAction
    result: AuditResult
    caller: str  # "cli" | "mcp" | "http" | "ui" | "reconcile" | ...
    port: int | None = None
    model: str | None = None
    from_model: str | None = None  # populated for swaps
    pid: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Enum fields are coerced to their string values so the result is
        directly JSON-encodable. Mirrors the shape produced by
        :meth:`to_jsonline` (minus the trailing newline) — both surfaces
        share this projection.
        """
        d = asdict(self)
        d["action"] = self.action.value
        d["result"] = self.result.value
        return d

    def to_jsonline(self) -> str:
        """Serialize to a single newline-terminated JSON object."""
        return json.dumps(self.to_dict(), separators=(",", ":")) + "\n"


def _resolve_path(path: Path | None) -> Path:
    return path if path is not None else LAUNCHER_AUDIT_PATH


def append_entry(entry: AuditEntry, *, path: Path | None = None) -> None:
    """Append an entry as one JSON line. Single-writer atomicity is sufficient."""
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(entry.to_jsonline())


def record(
    action: AuditAction,
    result: AuditResult,
    caller: str,
    *,
    port: int | None = None,
    model: str | None = None,
    from_model: str | None = None,
    pid: int | None = None,
    message: str = "",
    path: Path | None = None,
) -> AuditEntry:
    """Convenience: build an entry with current UTC timestamp, append, and return it.

    Audit logging is best-effort observability, never a gate on the
    operation it records (issue #308). Every ``operations/*`` verb calls
    ``record()`` as a side-channel record of what it did; a write failure
    here (disk full, permissions, missing/unwritable
    ``LAUNCHER_AUDIT_PATH``, ...) must never propagate up and abort the
    operation itself -- that would turn an observability outage into a
    functional outage. This is the single choke-point behind every
    ``al.record()`` call site, so the try/except lives here once rather
    than at each of them. The entry is still returned (unpersisted) so
    callers that inspect the return value (tests, in-memory chaining)
    keep working identically to the success path.
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        result=result,
        caller=caller,
        port=port,
        model=model,
        from_model=from_model,
        pid=pid,
        message=message,
    )
    try:
        append_entry(entry, path=path)
    except OSError as exc:
        logger.error("Failed to write audit log entry (%s/%s): %s", action, result, exc)
    return entry


def read_entries(
    *,
    path: Path | None = None,
    limit: int | None = None,
) -> list[AuditEntry]:
    """Read audit log entries (chronological order; newest last).

    Corrupt lines are skipped with a warning. Intended for inspection,
    not for high-frequency reads — large logs warrant a streaming reader.
    """
    target = _resolve_path(path)
    if not target.exists():
        return []

    entries: list[AuditEntry] = []
    with target.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(
                    AuditEntry(
                        timestamp=d["timestamp"],
                        action=AuditAction(d["action"]),
                        result=AuditResult(d["result"]),
                        caller=d["caller"],
                        port=d.get("port"),
                        model=d.get("model"),
                        from_model=d.get("from_model"),
                        pid=d.get("pid"),
                        message=d.get("message", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Corrupt audit line %d in %s: %s", line_no, target, exc)
                continue

    if limit is not None:
        return entries[-limit:]
    return entries
