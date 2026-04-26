"""Time-to-live (TTL) cache for time-bound result caching.

Used by ``core/model_health`` and ``core/gpu`` to avoid repeated expensive
checks within short intervals.
"""

import time


class _TTLCache:
    """Simple in-memory TTL-aware dictionary cache.

    Parameters support a single default TTL (``ttl_seconds``), which is
    applied to every ``set()`` call unless a *per-key* override is desired
    by re-setting with an explicit expiry.

    Typical usage::

        cache = _TTLCache(ttl_seconds=60)   # 1-minute default TTL
        cache.set(key, value)               # stored for 60 s
        val = cache.get(key)                # returns None after TTL expires
        cache.invalidate_all()              # purge everything
    """

    def __init__(self, ttl_seconds: int = 5):
        self._ttl = ttl_seconds
        self._store: dict[str | object, tuple[object, float]] = {}  # key -> (value, expiry_time)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key) -> object | None:
        """Return cached value or *None* if expired / absent.

        Expired entries are lazily removed on access.
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key, value, ttl_seconds: int | None = None) -> None:
        """Store *value* under *key* for the given TTL (or default)."""
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        self._store[key] = (value, time.monotonic() + effective_ttl)

    def invalidate_all(self) -> None:
        """Remove every cached entry."""
        self._store.clear()
