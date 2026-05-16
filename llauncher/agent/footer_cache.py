"""Per-port TTL cache for the ``/footer-context/{port}`` endpoint (ADR-012).

The footer-context endpoint is hit at footer-redraw cadence (multiple
times per second per watched port). The four fields the footer reads
(``model``, ``ctx_size``, ``parallel``, ``port``) live in the lockfile
plus the configured ``ModelConfig``; neither requires a process-table
scan nor a GPU probe. We cache the assembled tuple per port with a
short TTL so a burst of redraws collapses into a single disk read.

The cache is deliberately small and obvious:

* Keyed by ``port``. No cross-port aggregation, no sweep thread.
* Lazy eviction — expired entries are recomputed on the next request
  for that port.
* TTL is read from ``settings.LAUNCHER_FOOTER_CACHE_S`` at call time so
  tests can override via monkeypatch. ``<= 0`` disables caching.
* No invalidation hook from ``operations.start``/``swap``/``stop``: a
  bounded staleness window is acceptable per ADR-012, and wiring
  invalidation would couple ops to this module.

This module owns its own lock and dict. Read-side only; the lockfile
and ConfigStore are the underlying sources of truth.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from llauncher.core import settings
from llauncher.core.config import ConfigStore
from llauncher.core.lockfile import read_lockfile


@dataclass(frozen=True)
class FooterContext:
    """The pinned response shape for ``/footer-context/{port}`` (ADR-012)."""

    port: int
    model: str
    ctx_size: int | None
    parallel: int | None

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "model": self.model,
            "ctx_size": self.ctx_size,
            "parallel": self.parallel,
        }


# Module-level cache: ``port -> (context, expires_at_monotonic)``.
# ``threading.Lock`` is sufficient — FastAPI dispatches sync handlers on
# its threadpool, which can race two reads on the same port. Double-read
# on a miss is correct and not worth avoiding.
_cache: dict[int, tuple[FooterContext, float]] = {}
_lock = threading.Lock()


def _read_through(port: int) -> FooterContext | None:
    """Resolve a footer context from disk, bypassing the cache.

    Returns ``None`` when the port has no lockfile (the caller should
    map this to HTTP 404 per ADR-012). Returns a :class:`FooterContext`
    with ``ctx_size=None`` and ``parallel=None`` when the lockfile
    exists but the model name it names is not in :class:`ConfigStore`
    (degraded display, not not-found).
    """
    lf = read_lockfile(port)
    if lf is None:
        return None

    cfg = ConfigStore.get_model(lf.model)
    if cfg is None:
        return FooterContext(
            port=port,
            model=lf.model,
            ctx_size=None,
            parallel=None,
        )

    return FooterContext(
        port=port,
        model=lf.model,
        ctx_size=cfg.ctx_size,
        parallel=cfg.parallel,
    )


def get_footer_context(port: int) -> FooterContext | None:
    """Return the cached or freshly-read footer context for ``port``.

    Returns ``None`` if the port has no lockfile.
    """
    ttl = settings.LAUNCHER_FOOTER_CACHE_S
    if ttl <= 0:
        return _read_through(port)

    now = time.monotonic()
    with _lock:
        cached = _cache.get(port)
        if cached is not None and cached[1] > now:
            return cached[0]

    # Cache miss or expired — read outside the lock to avoid holding
    # it across disk I/O. A concurrent miss for the same port may do
    # the work twice; that is cheaper than serializing every cache hit.
    fresh = _read_through(port)

    with _lock:
        if fresh is None:
            # Don't cache absences. A new lockfile may appear at any
            # moment and we don't want a stale 404 to outlive it.
            _cache.pop(port, None)
        else:
            _cache[port] = (fresh, time.monotonic() + ttl)

    return fresh


def clear_cache() -> None:
    """Drop every cached entry. Primarily for tests."""
    with _lock:
        _cache.clear()
