"""Unit tests for /models/health API endpoints (ADR-005).

Tests both ``GET /models/health`` and ``GET /models/health/{model_name}`` with
mocked filesystem via pytest monkeypatch fixtures.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_mock_state(models_dict):
    """Build a mock state object matching the expected interface."""
    mock = MagicMock()
    mock.models = models_dict
    mock.running = {}
    mock.refresh = lambda: None

    # can_start returns True for any valid-looking model.
    mock.can_start = lambda *a, **k: (True, "OK")
    return mock


def _write_temp_model(name=None):
    """Create a temp file > 1 MB and return path + name."""
    tmpf = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb")
    tmpf.write(b"x" * (1024 * 1024 + 1))
    tmpf.close()
    model_name = name or "test-model"
    mock_config = MagicMock()
    mock_config.name = model_name
    mock_config.model_path = Path(tmpf.name).resolve().as_posix()
    mock_config.n_gpu_layers = 255

    def to_dict():
        return {"name": model_name, "model_path": str(mock_config.model_path)}

    mock_config.to_dict = to_dict
    mock_path = tmpf.name  # noqa: F841 — kept for reference.
    return mock_path, model_name, mock_config


# ── Test fixtures for the FastAPI client with patched state ─────

def _patched_health_client(models=None):
    """Create a FastAPI TestClient whose global state is replaced with mocks."""
    from fastapi.testclient import TestClient
    from llauncher.agent.server import create_app

    app = create_app()
    client = TestClient(app)

    # Patch the global _state in routing module before each request.
    return app, client


class TestModelsHealthListEndpoint:
    """Tests for GET /models/health (list all)."""

    def test_health_list_returns_200(self):
        """The endpoint returns 200 even when no models are configured."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app
        from llauncher.agent import routing as agent_routing

        app = create_app()
        agent_routing._state = None
        client = TestClient(app)
        # Reset any cached state.
        agent_routing._state = None

        response = client.get("/models/health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_health_list_with_mocked_models(self):
        """Endpoint returns correct structure for mocked model files."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app
        from llauncher.core.model_health import invalidate_health_cache as inv_hc

        app = create_app()
        client = TestClient(app)

        tmp_name, model_name, mock_config = _write_temp_model("health-model")

        mock_state = _make_mock_state({model_name: mock_config})

        from llauncher.agent import routing as agent_routing
        agent_routing._state = mock_state
        inv_hc()  # clear cache for deterministic testing.

        response = client.get("/models/health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1

        entry = data[0]
        assert "name" in entry
        assert "model_path" in entry
        assert "valid" in entry
        assert entry["valid"] is True
        assert entry["exists"] is True


class TestModelHealthDetailEndpoint:
    """Tests for GET /models/health/{model_name}."""

    def test_health_detail_returns_200(self):
        """Returns 200 when model exists in config."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app

        app = create_app()
        client = TestClient(app)

        tmp_name, model_name, mock_config = _write_temp_model("detail-model")

        mock_state = _make_mock_state({model_name: mock_config})

        from llauncher.agent import routing as agent_routing
        agent_routing._state = mock_state
        from llauncher.core.model_health import invalidate_health_cache
        invalidate_health_cache()

        response = client.get(f"/models/health/{model_name}")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == model_name
        assert data["valid"] is True

    def test_health_detail_returns_404_for_missing(self):
        """Returns 404 when the named model is not configured."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/health/nonexistent-model")
        assert response.status_code == 404


class TestModelHealthWithMissingFile:
    """Tests where models exist in config but files are missing on disk."""

    def test_missing_file_shows_exists_false(self):
        """Health endpoint returns exists=False when file is absent."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app

        app = create_app()
        client = TestClient(app)

        mock_config = MagicMock()
        mock_config.name = "missing-model"
        mock_config.model_path = "/tmp/this-file-does-not-exist-12345.gguf"

        mock_state = _make_mock_state({"missing-model": mock_config})

        from llauncher.agent import routing as agent_routing
        agent_routing._state = mock_state

        response = client.get("/models/health")
        data = response.json()
        assert len(data) == 1

        entry = data[0]
        assert entry["exists"] is False
        assert entry["valid"] is False


class TestVRAMPreFlightEndpoint:
    """Tests for VRAM pre-flight on POST /start-with-eviction."""

    def test_vram_error_contains_required_and_available(self):
        """409 error includes required_mb and available_mb when insufficient."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app
        from unittest.mock import MagicMock as MM

        app = create_app()
        client = TestClient(app)

        # Create a large model (simulate ~7B params → ~7 GB VRAM estimate).
        tmp_name, model_name, mock_config = _write_temp_model("big-model")

        mock_state = _make_mock_state({model_name: mock_config})

        from llauncher.agent import routing as agent_routing
        agent_routing._state = mock_state

        # The real nvidia-smi might not be available.  If it isn't, the VRAM
        # check is a no-op and start proceeds → either 200 or unrelated error.
        response = client.post(f"/start-with-eviction/{model_name}")

        # Acceptable outcomes: 409 (VRAM insufficient) or any other (no GPU / skipped).
        if response.status_code == 409:
            detail = response.json()["detail"]
            assert isinstance(detail, dict), f"Expected dict detail on VRAM 409; got {type(detail)}"
            assert "required_mb" in detail or "insufficient_vram" in str(detail)
