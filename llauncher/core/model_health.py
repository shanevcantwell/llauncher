"""Model file health-check utilities.

Provides a Pydantic-backed ``ModelHealthResult`` and the
``check_model_health()`` function to validate model files (existence,
readability, size) with symlink resolution.  Results are cached via
the shared ``_TTLCache`` utility (60 s default).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from llauncher.util.cache import _TTLCache


# ------------------------------------------------------------------
# Public model + cache
# ------------------------------------------------------------------

class ModelHealthResult(BaseModel):
    """Structured result of a model-file health check.

    Attributes:
        valid: *True* only when the file exists, is readable, and exceeds
               the 1 MiB size heuristic.
        reason: Human-readable explanation for failure (``None`` when
                ``valid=True``).
        size_bytes: File size in bytes (only set after existence check).
        exists: Whether the path resolves to a real file.
        readable: Whether the file can be opened for reading.
        last_modified: File modification timestamp (UTC when available).
    """

    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None


# Module-level TTL cache (60 s default).  Cached per model-path string.
_health_cache = _TTLCache(ttl_seconds=60)

_MIN_SIZE_BYTES = 1024 * 1024  # 1 MiB heuristic


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def check_model_health(model_path: str) -> ModelHealthResult:
    """Validate model file existence, readability, and minimum size.

    Resolution order:
      1. Look up the cached result (TTL 60 s).  Return immediately on hit.
      2. Resolve any symlinks via ``Path.resolve()``.
      3. Check that the resolved path points to an existing file.
      4. Attempt to open for reading.
      5. Verify size exceeds 1 MiB (heuristic for a real model).

    On success returns ``ModelHealthResult(valid=True, …)``; on any failure
    returns ``valid=False`` with a descriptive ``reason`` string.

    Args:
        model_path: Path to the model file (may be a symlink).

    Returns:
        Health check result.
    """
    cached = _health_cache.get(model_path)
    if cached is not None:
        return cached  # type: ignore[return-value]

    result = ModelHealthResult()

    try:
        path = Path(model_path).resolve()
        result.exists = path.is_file()
        if not result.exists:
            result.reason = "not found"
            _health_cache.set(model_path, result)
            return result

        # File exists — grab size & modification time (best-effort)
        try:
            stat = path.stat()
            result.size_bytes = stat.st_size
            result.last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            pass  # May fail on edge cases; continue to readability check

        # Check readability by attempting an open + close.
        try:
            with open(path, "rb") as f:
                f.read(0)  # No-op read to validate access
            result.readable = True
        except (OSError, PermissionError):
            result.reason = "unreadable"
            _health_cache.set(model_path, result)
            return result

    except Exception as exc:
        result.reason = str(exc)[:200]
        _health_cache.set(model_path, result)
        return result

    # Size heuristic — real models are usually > 1 MiB.
    if (result.size_bytes or 0) < _MIN_SIZE_BYTES:
        result.reason = "too small"
        _health_cache.set(model_path, result)
        return result

    # All checks passed ✅
    result.valid = True
    result.exists = True
    result.readable = True
    _health_cache.set(model_path, result)
    return result


def invalidate_health_cache(model_path: str | None = None) -> None:
    """Invalidate the model-health cache.

    Args:
        model_path: If given, only that path is evicted; if *None*,
                    all cached entries are purged (used on config changes).
    """
    if model_path is not None:
        _health_cache.invalidate(model_path)
    else:
        _health_cache.invalidate_all()
