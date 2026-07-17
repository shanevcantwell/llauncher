"""In-server inference telemetry reader (ADR-LLNCH-019, issue #179).

Peer to :mod:`llauncher.core.gpu`: a stateless-per-poll collector with a
short TTL cache (``LLAUNCHER_METRICS_CACHE_S``) absorbing poll cadence,
and an injectable HTTP fetch seam (test-only, production-inert) mirroring
``gpu.py``'s ``nvidia-smi`` mock. Outbound HTTP from ``core`` to the
target model server's own port is a client call, not an upward import —
the layer rule holds (see ``docs/ARCHITECTURE.md``).

Two capability tiers, kept **physically separate** — never a shared call
gated by a flag — per the ADR:

* :func:`get_aggregate_metrics` — ``/health`` + ``/metrics`` + lockfile
  ``started_at``. Safe: no prompt text.
* :func:`get_slots` — ``/slots``. Sensitive: per-slot detail including
  prompt text.

Both return a degraded envelope ``{"available": False, "reason": ...}``
(``"loading" | "no-metrics-flag" | "unreachable"``) rather than raising,
per PARSE-AT-THE-DOOR.

Phase derivation note: the Prometheus gauges
(``prompt_tokens_seconds`` / ``predicted_tokens_seconds``) are cumulative
averages, and the counters (``prompt_tokens_total`` /
``tokens_predicted_total``) are monotonic totals — neither alone signals
real-time activity (issue #179 PM-1 de-risk finding). ``phase`` is
therefore derived from the delta between this poll's counters and the
*single* most-recently-seen sample for that port, gated by
``requests_processing > 0``. This retains one sample per port (a
one-slot-per-key cache, like the TTL cache above it) — it is not a
history/ring-buffer; that accumulation is the deferred Streamlit-monitor
scope (#176). From the caller's perspective this module is still
point-in-time: one call in, one snapshot out.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from llauncher.core import settings
from llauncher.core.config import ConfigStore
from llauncher.core.lockfile import read_lockfile

logger = logging.getLogger(__name__)


_FETCH_TIMEOUT_S = 2.0
_PROMETHEUS_PREFIX = "llamacpp:"


# ------------------------------------------------------------------
# Fetch seam (test-only, production-inert)
# ------------------------------------------------------------------


def _default_fetch(url: str) -> httpx.Response | None:
    """Default HTTP GET; ``None`` signals a transport-level failure.

    Non-2xx responses are returned (not raised) — callers branch on
    ``status_code`` to distinguish ``loading`` (503) from
    ``no-metrics-flag`` (501) from an ordinary success.
    """
    try:
        return httpx.get(url, timeout=_FETCH_TIMEOUT_S)
    except httpx.RequestError as exc:
        logger.debug("server_metrics fetch failed for %s: %s", url, exc)
        return None


# Module-level seam: tests monkeypatch this name (mirrors
# ``core.process.LOG_DIR`` / ``agent.routing._state`` patch points).
# Production code never reassigns it.
_fetch_url: Callable[[str], httpx.Response | None] = _default_fetch


def node_identity() -> str:
    """Resolve this node's identity for the ``(node, port, canonical_name)``
    series key (ADR-LLNCH-019 §4).

    Returns the agent self-report today. The mint-hardening swap point is
    tracked at #174 — this function is the single place that resolver
    would replace.
    """
    # TODO #174: node-identity mint hardening swap point.
    from llauncher.core.node_info import get_node_name

    return get_node_name()


# ------------------------------------------------------------------
# Prometheus text parsing (PM-1 de-risk: colon `llamacpp:` namespace)
# ------------------------------------------------------------------


def _parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse ``llamacpp:*`` Prometheus exposition text into a flat dict.

    Skips ``# HELP`` / ``# TYPE`` comment lines and blanks. Non-numeric
    or malformed lines are skipped rather than raising (PARSE-AT-THE-DOOR
    within a best-effort scrape — a single bad line must not blank the
    whole snapshot).
    """
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        name, raw_value = parts
        if not name.startswith(_PROMETHEUS_PREFIX):
            continue
        key = name[len(_PROMETHEUS_PREFIX):]
        try:
            values[key] = float(raw_value)
        except ValueError:
            logger.debug("server_metrics: non-numeric metric line skipped: %r", line)
            continue
    return values


# ------------------------------------------------------------------
# Aggregate tier
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _CounterSnapshot:
    """Single most-recent counter sample for one port (delta gating only)."""

    prompt_tokens_total: float
    tokens_predicted_total: float


# port -> last-seen counter snapshot. One slot per key; overwritten each
# poll. Not a history — see module docstring.
_last_counters: dict[int, _CounterSnapshot] = {}
_last_counters_lock = threading.Lock()

# port -> (envelope dict, expires_at monotonic). Short TTL cache absorbing
# poll cadence, mirroring ``agent.footer_cache``.
_aggregate_cache: dict[int, tuple[dict[str, Any], float]] = {}
_aggregate_cache_lock = threading.Lock()


def _degraded(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _derive_phase(port: int, counters: dict[str, float]) -> str:
    """Derive ``idle | prompt | generating`` from a live gauge plus a
    one-sample counter delta (see module docstring for rationale).
    """
    requests_processing = counters.get("requests_processing", 0.0)
    prompt_total = counters.get("prompt_tokens_total", 0.0)
    predicted_total = counters.get("tokens_predicted_total", 0.0)

    with _last_counters_lock:
        prev = _last_counters.get(port)
        _last_counters[port] = _CounterSnapshot(
            prompt_tokens_total=prompt_total,
            tokens_predicted_total=predicted_total,
        )

    if requests_processing <= 0:
        return "idle"

    if prev is None:
        # Busy on the first-ever poll for this port: no delta to compare
        # against yet. Generation dominates request wall-clock time in
        # practice, so it's the better single-sample default.
        return "generating"

    delta_predicted = predicted_total - prev.tokens_predicted_total
    delta_prompt = prompt_total - prev.prompt_tokens_total

    if delta_predicted > 0:
        return "generating"
    if delta_prompt > 0:
        return "prompt"
    # Busy per the gauge, but neither counter moved since the last poll
    # (e.g. a request just landed). Same best-effort default as above.
    return "generating"


def _collect_aggregate(port: int) -> dict[str, Any]:
    """Read-through: probe the live server and lockfile; no cache."""
    health_resp = _fetch_url(f"http://127.0.0.1:{port}/health")
    if health_resp is None:
        return _degraded("unreachable")
    if health_resp.status_code == 503:
        return _degraded("loading")
    if health_resp.status_code != 200:
        return _degraded("unreachable")

    metrics_resp = _fetch_url(f"http://127.0.0.1:{port}/metrics")
    if metrics_resp is None:
        return _degraded("unreachable")
    if metrics_resp.status_code == 501:
        return _degraded("no-metrics-flag")
    if metrics_resp.status_code != 200:
        return _degraded("unreachable")

    counters = _parse_prometheus_text(metrics_resp.text)

    lf = read_lockfile(port)
    started_at = lf.started_at if lf is not None else None
    canonical_name = lf.model if lf is not None else None

    slots_total: int | None = None
    if lf is not None:
        cfg = ConfigStore.get_model(lf.model)
        if cfg is not None:
            slots_total = cfg.parallel

    return {
        "available": True,
        "state": "ok",
        "phase": _derive_phase(port, counters),
        "gen_tok_s": counters.get("predicted_tokens_seconds"),
        "prompt_tok_s": counters.get("prompt_tokens_seconds"),
        "slots_busy": _to_int(counters.get("requests_processing")),
        "slots_total": slots_total,
        "requests_deferred": _to_int(counters.get("requests_deferred")),
        "started_at": started_at,
        "node": node_identity(),
        "canonical_name": canonical_name,
    }


def get_aggregate_metrics(port: int, *, force_refresh: bool = False) -> dict[str, Any]:
    """Return the aggregate-tier snapshot for ``port`` (ADR-LLNCH-019 §2).

    Cached for ``LLAUNCHER_METRICS_CACHE_S`` seconds (``<= 0`` disables
    caching). Never raises: unreachable/loading/no-metrics-flag all
    return the degraded envelope.
    """
    ttl = settings.LLAUNCHER_METRICS_CACHE_S
    now = time.monotonic()

    if ttl > 0 and not force_refresh:
        with _aggregate_cache_lock:
            cached = _aggregate_cache.get(port)
            if cached is not None and cached[1] > now:
                return cached[0]

    result = _collect_aggregate(port)

    if ttl > 0:
        with _aggregate_cache_lock:
            _aggregate_cache[port] = (result, time.monotonic() + ttl)

    return result


def clear_cache() -> None:
    """Drop all cached state (aggregate cache + phase-delta samples).

    Primarily for tests; production never needs to invalidate — the TTL
    and per-poll overwrite are self-healing.
    """
    with _aggregate_cache_lock:
        _aggregate_cache.clear()
    with _last_counters_lock:
        _last_counters.clear()


# ------------------------------------------------------------------
# Slots tier (sensitive — prompt text)
# ------------------------------------------------------------------


def get_slots(port: int) -> dict[str, Any]:
    """Return the sensitive slots-tier snapshot for ``port``.

    Not cached (each read must reflect the current slot occupants).
    Returns ``{"available": False, "reason": "slots_disabled"}`` when the
    server does not expose ``/slots`` (started without ``--slots``, i.e.
    the llama-server default posture after issue #179's ``--no-slots``
    flag policy), matching the agent's ``404 slots_disabled`` contract
    at the HTTP layer.
    """
    resp = _fetch_url(f"http://127.0.0.1:{port}/slots")
    if resp is None:
        return _degraded("unreachable")
    if resp.status_code == 501:
        return _degraded("slots_disabled")
    if resp.status_code != 200:
        return _degraded("unreachable")

    try:
        slots = resp.json()
    except ValueError:
        logger.debug("server_metrics: /slots on port %s returned non-JSON body", port)
        return _degraded("unreachable")

    return {
        "available": True,
        "node": node_identity(),
        "slots": slots,
    }


def _to_int(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value)
