"""Unit tests for GET /server-metrics/{port} and GET /server-slots/{port}
(ADR-LLNCH-019, issue #179 SP-3/SP-4).

The agent layer is a thin HTTP wrapper over
:mod:`llauncher.core.server_metrics` — these tests patch that module's
public functions and assert the routing/status-code mapping, not the
telemetry logic itself (covered by ``tests/unit/test_server_metrics.py``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from llauncher.agent.middleware import _AUTH_EXEMPT_PATHS
from llauncher.agent.server import create_app_unauthenticated


@pytest.fixture
def client() -> TestClient:
    app = create_app_unauthenticated()
    return TestClient(app)


class TestNotAuthExempt:
    """ADR-LLNCH-019 §5: same auth as /status — never exempt."""

    def test_server_metrics_path_not_in_exempt_set(self):
        assert "/server-metrics/{port}" not in _AUTH_EXEMPT_PATHS
        assert "/server-metrics/8081" not in _AUTH_EXEMPT_PATHS

    def test_server_slots_path_not_in_exempt_set(self):
        assert "/server-slots/{port}" not in _AUTH_EXEMPT_PATHS
        assert "/server-slots/8081" not in _AUTH_EXEMPT_PATHS


class TestServerMetricsEndpoint:
    """GET /server-metrics/{port} — aggregate/safe tier."""

    def test_available_snapshot_passes_through_verbatim(self, client, monkeypatch):
        snapshot = {
            "available": True,
            "state": "ok",
            "phase": "generating",
            "gen_tok_s": 22.1,
            "prompt_tok_s": 5.5,
            "slots_busy": 1,
            "slots_total": 4,
            "requests_deferred": 0,
            "started_at": "2026-07-01T00:00:00+00:00",
            "node": "test-node",
            "canonical_name": "qwen",
        }
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_aggregate_metrics",
            lambda port: snapshot if port == 8081 else None,
        )
        response = client.get("/server-metrics/8081")
        assert response.status_code == 200
        assert response.json() == snapshot

    def test_degraded_envelope_is_still_200(self, client, monkeypatch):
        degraded = {"available": False, "reason": "loading"}
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_aggregate_metrics",
            lambda port: degraded,
        )
        response = client.get("/server-metrics/9999")
        assert response.status_code == 200
        assert response.json() == degraded

    @pytest.mark.parametrize("reason", ["loading", "no-metrics-flag", "unreachable"])
    def test_every_degraded_reason_is_200(self, client, monkeypatch, reason):
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_aggregate_metrics",
            lambda port: {"available": False, "reason": reason},
        )
        response = client.get("/server-metrics/8081")
        assert response.status_code == 200
        assert response.json()["reason"] == reason


class TestServerSlotsEndpoint:
    """GET /server-slots/{port} — sensitive tier."""

    def test_available_snapshot_passes_through(self, client, monkeypatch):
        payload = {"available": True, "node": "test-node", "slots": [{"id": 0, "prompt": "hi"}]}
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_slots",
            lambda port: payload,
        )
        response = client.get("/server-slots/8081")
        assert response.status_code == 200
        assert response.json() == payload

    def test_slots_disabled_maps_to_404(self, client, monkeypatch):
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_slots",
            lambda port: {"available": False, "reason": "slots_disabled"},
        )
        response = client.get("/server-slots/8081")
        assert response.status_code == 404
        assert response.json() == {"detail": "slots_disabled"}

    def test_unreachable_is_200_degraded_not_404(self, client, monkeypatch):
        monkeypatch.setattr(
            "llauncher.core.server_metrics.get_slots",
            lambda port: {"available": False, "reason": "unreachable"},
        )
        response = client.get("/server-slots/8081")
        assert response.status_code == 200
        assert response.json() == {"available": False, "reason": "unreachable"}
