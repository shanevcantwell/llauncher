"""Unit tests for the server-metrics reader (ADR-LLNCH-019, issue #179).

Covers the Prometheus-text parser, the aggregate-tier degraded envelopes
and happy path, phase derivation, the TTL cache, and the sensitive
slots-tier reader — all against the injected fetch seam
(``server_metrics._fetch_url``), never a live server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from llauncher.core import server_metrics


# ─── Fakes ─────────────────────────────────────────────────────────


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""
    _json: Any = None
    _raise_on_json: bool = False

    def json(self) -> Any:
        if self._raise_on_json:
            raise ValueError("not JSON")
        return self._json


@dataclass(frozen=True)
class _FakeLockfile:
    model: str
    started_at: str = "2026-07-01T00:00:00+00:00"
    port: int = 8081
    pid: int = 111
    llauncher_pid: int = 1


@dataclass(frozen=True)
class _FakeModelConfig:
    parallel: int = 1


class _FetchRouter:
    """Routes ``_fetch_url(url)`` calls to per-path canned responses.

    ``responses`` maps a substring (``"/health"``, ``"/metrics"``,
    ``"/slots"``) to either a ``_FakeResponse`` or ``None`` (transport
    failure). Records every URL requested for call-count assertions.
    """

    def __init__(self, **responses: _FakeResponse | None):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> _FakeResponse | None:
        self.calls.append(url)
        for key, resp in self.responses.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected fetch URL: {url}")


_PROM_TEXT = """\
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 12
llamacpp:tokens_predicted_total 34
llamacpp:prompt_tokens_seconds 5.5
llamacpp:predicted_tokens_seconds 22.1
llamacpp:requests_processing 1
llamacpp:requests_deferred 0
"""

_PROM_TEXT_IDLE = """\
llamacpp:prompt_tokens_total 12
llamacpp:tokens_predicted_total 34
llamacpp:prompt_tokens_seconds 5.5
llamacpp:predicted_tokens_seconds 22.1
llamacpp:requests_processing 0
llamacpp:requests_deferred 0
"""


@pytest.fixture(autouse=True)
def _clear_state():
    server_metrics.clear_cache()
    yield
    server_metrics.clear_cache()


@pytest.fixture
def patch_fetch(monkeypatch):
    def _apply(**responses: _FakeResponse | None) -> _FetchRouter:
        router = _FetchRouter(**responses)
        monkeypatch.setattr(server_metrics, "_fetch_url", router)
        return router

    return _apply


@pytest.fixture
def patch_disk(monkeypatch):
    def _apply(lockfile: _FakeLockfile | None, config: _FakeModelConfig | None = None):
        monkeypatch.setattr(server_metrics, "read_lockfile", lambda port: lockfile)
        monkeypatch.setattr(
            server_metrics.ConfigStore,
            "get_model",
            classmethod(lambda cls, name: config),
        )

    return _apply


@pytest.fixture(autouse=True)
def _patch_node_identity(monkeypatch):
    """Patch the underlying resolver, not ``node_identity`` itself, so the
    real ``node_identity`` code path (including its #174 swap-point
    delegation) is exercised by every test in this module.
    """
    monkeypatch.setattr("llauncher.core.node_info.get_node_name", lambda: "test-node")


# ─── Prometheus text parser ─────────────────────────────────────────


class TestParsePrometheusText:
    def test_parses_counters_and_gauges(self):
        values = server_metrics._parse_prometheus_text(_PROM_TEXT)
        assert values["prompt_tokens_total"] == 12
        assert values["tokens_predicted_total"] == 34
        assert values["prompt_tokens_seconds"] == 5.5
        assert values["predicted_tokens_seconds"] == 22.1
        assert values["requests_processing"] == 1
        assert values["requests_deferred"] == 0

    def test_skips_comment_and_blank_lines(self):
        text = "# HELP x\n# TYPE x\n\nllamacpp:requests_processing 2\n"
        values = server_metrics._parse_prometheus_text(text)
        assert values == {"requests_processing": 2}

    def test_skips_non_llamacpp_namespace(self):
        text = "process_start_time_seconds 12345\nllamacpp:requests_processing 1\n"
        values = server_metrics._parse_prometheus_text(text)
        assert values == {"requests_processing": 1}

    def test_skips_malformed_lines(self):
        text = "llamacpp:weird line with extra tokens\nllamacpp:requests_processing 1\n"
        values = server_metrics._parse_prometheus_text(text)
        assert values == {"requests_processing": 1}

    def test_skips_non_numeric_value(self):
        text = "llamacpp:requests_processing not-a-number\n"
        values = server_metrics._parse_prometheus_text(text)
        assert values == {}

    def test_empty_text_yields_empty_dict(self):
        assert server_metrics._parse_prometheus_text("") == {}


# ─── Aggregate tier: degraded envelopes ─────────────────────────────


class TestAggregateDegraded:
    def test_health_unreachable(self, patch_fetch):
        patch_fetch(**{"/health": None})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "unreachable"}

    def test_health_loading(self, patch_fetch):
        patch_fetch(**{"/health": _FakeResponse(503)})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "loading"}

    def test_health_unexpected_status_is_unreachable(self, patch_fetch):
        patch_fetch(**{"/health": _FakeResponse(500)})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "unreachable"}

    def test_metrics_transport_failure(self, patch_fetch):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": None})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "unreachable"}

    def test_metrics_disabled_flag(self, patch_fetch):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(501)})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "no-metrics-flag"}

    def test_metrics_unexpected_status_is_unreachable(self, patch_fetch):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(500)})
        result = server_metrics.get_aggregate_metrics(8081)
        assert result == {"available": False, "reason": "unreachable"}


# ─── Aggregate tier: happy path + identity stamping ─────────────────


class TestAggregateHappyPath:
    def test_full_snapshot_with_lockfile_and_config(self, patch_fetch, patch_disk):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT)})
        patch_disk(_FakeLockfile(model="qwen-canonical"), _FakeModelConfig(parallel=4))

        result = server_metrics.get_aggregate_metrics(8081)

        assert result["available"] is True
        assert result["state"] == "ok"
        assert result["gen_tok_s"] == 22.1
        assert result["prompt_tok_s"] == 5.5
        assert result["slots_busy"] == 1
        assert result["slots_total"] == 4
        assert result["requests_deferred"] == 0
        assert result["started_at"] == "2026-07-01T00:00:00+00:00"
        assert result["canonical_name"] == "qwen-canonical"
        assert result["node"] == "test-node"

    def test_no_lockfile_degrades_identity_fields_not_availability(self, patch_fetch, patch_disk):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})
        patch_disk(None, None)

        result = server_metrics.get_aggregate_metrics(8081)

        assert result["available"] is True
        assert result["started_at"] is None
        assert result["canonical_name"] is None
        assert result["slots_total"] is None

    def test_lockfile_present_but_config_missing(self, patch_fetch, patch_disk):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})
        patch_disk(_FakeLockfile(model="ghost"), None)

        result = server_metrics.get_aggregate_metrics(8081)

        assert result["canonical_name"] == "ghost"
        assert result["slots_total"] is None


# ─── Phase derivation ────────────────────────────────────────────────


class TestPhaseDerivation:
    def test_idle_when_no_requests_processing(self, patch_fetch, patch_disk):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})
        patch_disk(None, None)
        result = server_metrics.get_aggregate_metrics(8081, force_refresh=True)
        assert result["phase"] == "idle"

    def test_first_busy_poll_defaults_to_generating(self, patch_fetch, patch_disk):
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT)})
        patch_disk(None, None)
        result = server_metrics.get_aggregate_metrics(8081, force_refresh=True)
        assert result["phase"] == "generating"

    def test_predicted_delta_yields_generating(self, patch_fetch, patch_disk):
        patch_disk(None, None)
        first = "llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 10\nllamacpp:requests_processing 1\n"
        second = "llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 20\nllamacpp:requests_processing 1\n"

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=first)})
        server_metrics.get_aggregate_metrics(8081, force_refresh=True)

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=second)})
        result = server_metrics.get_aggregate_metrics(8081, force_refresh=True)
        assert result["phase"] == "generating"

    def test_prompt_only_delta_yields_prompt(self, patch_fetch, patch_disk):
        patch_disk(None, None)
        first = "llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 10\nllamacpp:requests_processing 1\n"
        second = "llamacpp:prompt_tokens_total 40\nllamacpp:tokens_predicted_total 10\nllamacpp:requests_processing 1\n"

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=first)})
        server_metrics.get_aggregate_metrics(8081, force_refresh=True)

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=second)})
        result = server_metrics.get_aggregate_metrics(8081, force_refresh=True)
        assert result["phase"] == "prompt"

    def test_busy_with_no_counter_movement_falls_back_to_generating(self, patch_fetch, patch_disk):
        patch_disk(None, None)
        same = "llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 10\nllamacpp:requests_processing 1\n"

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=same)})
        server_metrics.get_aggregate_metrics(8081, force_refresh=True)

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=same)})
        result = server_metrics.get_aggregate_metrics(8081, force_refresh=True)
        assert result["phase"] == "generating"

    def test_phase_delta_is_keyed_per_port(self, patch_fetch, patch_disk):
        """A second port's first poll must not see the first port's history."""
        patch_disk(None, None)
        busy = "llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 20\nllamacpp:requests_processing 1\n"

        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=busy)})
        server_metrics.get_aggregate_metrics(8081, force_refresh=True)

        idle_other_port = _PROM_TEXT_IDLE
        patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=idle_other_port)})
        result = server_metrics.get_aggregate_metrics(9999, force_refresh=True)
        assert result["phase"] == "idle"


# ─── TTL cache ────────────────────────────────────────────────────────


class TestAggregateCache:
    def test_second_call_within_ttl_is_cached(self, patch_fetch, patch_disk, monkeypatch):
        monkeypatch.setattr(server_metrics.settings, "LLAUNCHER_METRICS_CACHE_S", 5.0)
        patch_disk(None, None)
        router = patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})

        first = server_metrics.get_aggregate_metrics(8081)
        second = server_metrics.get_aggregate_metrics(8081)

        assert first == second
        assert len(router.calls) == 2  # one /health + one /metrics — cached, not refetched

    def test_force_refresh_bypasses_cache(self, patch_fetch, patch_disk, monkeypatch):
        monkeypatch.setattr(server_metrics.settings, "LLAUNCHER_METRICS_CACHE_S", 5.0)
        patch_disk(None, None)
        router = patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})

        server_metrics.get_aggregate_metrics(8081)
        server_metrics.get_aggregate_metrics(8081, force_refresh=True)

        assert len(router.calls) == 4  # two full probes, no cache hit

    def test_ttl_le_zero_disables_caching(self, patch_fetch, patch_disk, monkeypatch):
        monkeypatch.setattr(server_metrics.settings, "LLAUNCHER_METRICS_CACHE_S", 0.0)
        patch_disk(None, None)
        router = patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})

        server_metrics.get_aggregate_metrics(8081)
        server_metrics.get_aggregate_metrics(8081)

        assert len(router.calls) == 4

    def test_clear_cache_drops_cached_entry(self, patch_fetch, patch_disk, monkeypatch):
        monkeypatch.setattr(server_metrics.settings, "LLAUNCHER_METRICS_CACHE_S", 5.0)
        patch_disk(None, None)
        router = patch_fetch(**{"/health": _FakeResponse(200), "/metrics": _FakeResponse(200, text=_PROM_TEXT_IDLE)})

        server_metrics.get_aggregate_metrics(8081)
        server_metrics.clear_cache()
        server_metrics.get_aggregate_metrics(8081)

        assert len(router.calls) == 4


class TestDefaultFetch:
    """Exercises the real (unpatched) production fetch seam."""

    def test_returns_response_on_success(self, monkeypatch):
        sentinel = _FakeResponse(200)
        monkeypatch.setattr(server_metrics.httpx, "get", lambda url, timeout: sentinel)
        assert server_metrics._default_fetch("http://127.0.0.1:1/health") is sentinel

    def test_returns_none_on_request_error(self, monkeypatch):
        import httpx as real_httpx

        def _raise(url, timeout):
            raise real_httpx.ConnectError("refused")

        monkeypatch.setattr(server_metrics.httpx, "get", _raise)
        assert server_metrics._default_fetch("http://127.0.0.1:1/health") is None


# ─── Slots tier ───────────────────────────────────────────────────────


class TestGetSlots:
    def test_unreachable(self, patch_fetch):
        patch_fetch(**{"/slots": None})
        assert server_metrics.get_slots(8081) == {"available": False, "reason": "unreachable"}

    def test_slots_disabled(self, patch_fetch):
        patch_fetch(**{"/slots": _FakeResponse(501)})
        assert server_metrics.get_slots(8081) == {"available": False, "reason": "slots_disabled"}

    def test_unexpected_status_is_unreachable(self, patch_fetch):
        patch_fetch(**{"/slots": _FakeResponse(500)})
        assert server_metrics.get_slots(8081) == {"available": False, "reason": "unreachable"}

    def test_happy_path_returns_slots_payload(self, patch_fetch):
        payload = [{"id": 0, "prompt": "hello"}]
        patch_fetch(**{"/slots": _FakeResponse(200, _json=payload)})
        result = server_metrics.get_slots(8081)
        assert result == {"available": True, "node": "test-node", "slots": payload}

    def test_non_json_body_is_unreachable(self, patch_fetch):
        patch_fetch(**{"/slots": _FakeResponse(200, _raise_on_json=True)})
        result = server_metrics.get_slots(8081)
        assert result == {"available": False, "reason": "unreachable"}


# ─── node_identity + misc helpers ──────────────────────────────────────


def test_node_identity_delegates_to_get_node_name(monkeypatch):
    """``node_identity`` resolves via ``core.node_info.get_node_name`` — the
    single #174 swap point (module docstring / ADR §4).
    """
    monkeypatch.setattr(
        "llauncher.core.node_info.get_node_name", lambda: "the-real-node"
    )
    assert server_metrics.node_identity() == "the-real-node"


def test_to_int_passthrough_none():
    assert server_metrics._to_int(None) is None


def test_to_int_converts_float():
    assert server_metrics._to_int(3.0) == 3
