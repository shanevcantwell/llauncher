"""Unit tests for GET /models/validate[/{model_name}] (issue #475, ADR-027).

Replaces ADR-005's ``GET /models/health[/{model_name}]`` (deleted, no
in-repo consumer, see ``docs/adrs/completed/005-model-cache-health.md``'s
superseded note). Also pins the ADR-027 §2 hot-path guarantee: ``GET
/models`` must perform zero ``check_model_health`` calls.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_temp_model_config(name: str):
    """Create a real >1 MiB GGUF-magic temp file and a matching ModelConfig."""
    from llauncher.models.config import _skip_path_validation, ModelConfig

    tmpf = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb")
    tmpf.write(b"GGUF" + b"\x00" * (1024 * 1024))
    tmpf.close()
    path = Path(tmpf.name).resolve()
    with _skip_path_validation():
        return ModelConfig(name=name, model_path=str(path))


@pytest.fixture
def config_with_one_model(mock_config_store):
    """Real ConfigStore, isolated per-test, with one validatable model."""
    from llauncher.core.config import ConfigStore

    cfg = _write_temp_model_config("validate-model")
    ConfigStore.add_model(cfg)
    return cfg


@pytest.fixture
def mock_config_store(tmp_path):
    """Isolate ConfigStore onto a tmp dir (mirrors test_cli.py's fixture)."""
    config_dir = tmp_path / ".llauncher"
    config_path = config_dir / "config.json"
    with patch("llauncher.core.config.CONFIG_DIR", config_dir), \
            patch("llauncher.core.config.CONFIG_PATH", config_path):
        yield config_dir, config_path


@pytest.mark.real_model_health
class TestModelsValidateListEndpoint:
    def test_returns_200_with_no_models(self, mock_config_store):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/validate")
        assert response.status_code == 200
        data = response.json()
        assert data["models"] == []
        assert data["ok"] is True  # vacuously true — no entries to fail

    def test_report_shape_with_one_model(self, config_with_one_model):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/validate")
        assert response.status_code == 200
        data = response.json()
        assert "checked_at" in data
        assert "ok" in data
        assert len(data["models"]) == 1
        entry = data["models"][0]
        assert entry["name"] == "validate-model"
        assert entry["exists"] is True
        assert entry["ok"] is True
        assert any(v["check"] == "weights" for v in entry["verdicts"])


@pytest.mark.real_model_health
class TestModelValidateDetailEndpoint:
    def test_detail_returns_200_for_known_model(self, config_with_one_model):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/validate/validate-model")
        assert response.status_code == 200
        data = response.json()
        assert len(data["models"]) == 1
        assert data["models"][0]["name"] == "validate-model"

    def test_detail_returns_404_for_unknown_model(self, mock_config_store):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/validate/nonexistent-model")
        assert response.status_code == 404


class TestModelsHealthRoutesRemoved:
    """The superseded ADR-005 routes must be gone — no alias, no dual shape."""

    def test_models_health_list_route_gone(self, mock_config_store):
        """No dedicated GET route remains. FastAPI resolves the path to
        the still-live ``DELETE /models/{model_name}`` route (``"health"``
        as the name param), which has no GET handler -> 405, not the
        200 the old dedicated endpoint returned."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/health")
        assert response.status_code == 405

    def test_models_health_detail_route_gone(self, mock_config_store):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/models/health/anything")
        assert response.status_code == 404


class TestModelsEndpointHotPathGuarantee:
    """ADR-027 §2: GET /models must never call check_model_health.

    Pinned so a later "just fold validation into /models" cannot land
    silently — that endpoint is on the UI hot path (RemoteAggregator,
    called on every Streamlit rerun per node).
    """

    def test_get_models_performs_zero_health_checks(self, mock_config_store):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        app = create_app()
        client = TestClient(app)

        with patch(
            "llauncher.core.model_health.check_model_health"
        ) as mocked_health:
            response = client.get("/models")

        assert response.status_code == 200
        mocked_health.assert_not_called()


@pytest.mark.real_model_health
class TestModelsValidateVramQueryParam:
    """``?vram=false`` is the HTTP door's cheap mode (PR #481).

    Without it there was no way to run the verb over HTTP without paying an
    ``nvidia-smi`` shell-out — the flag existed only on the MCP tool.
    """

    def _client(self):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

        return TestClient(create_app())

    def test_list_endpoint_forwards_vram_false(self, config_with_one_model):
        from llauncher.models.validation import ValidationReport
        from datetime import datetime, timezone

        report = ValidationReport(checked_at=datetime.now(timezone.utc), ok=True, models=[])
        with patch("llauncher.operations.validate_models", return_value=report) as mocked:
            response = self._client().get("/models/validate?vram=false")

        assert response.status_code == 200
        assert mocked.call_args.kwargs["vram"] is False

    def test_list_endpoint_defaults_to_vram_true(self, config_with_one_model):
        from llauncher.models.validation import ValidationReport
        from datetime import datetime, timezone

        report = ValidationReport(checked_at=datetime.now(timezone.utc), ok=True, models=[])
        with patch("llauncher.operations.validate_models", return_value=report) as mocked:
            response = self._client().get("/models/validate")

        assert response.status_code == 200
        assert mocked.call_args.kwargs["vram"] is True

    def test_detail_endpoint_forwards_vram_false(self, config_with_one_model):
        from llauncher.models.validation import ValidationReport
        from datetime import datetime, timezone

        report = ValidationReport(checked_at=datetime.now(timezone.utc), ok=True, models=[])
        with patch("llauncher.operations.validate_models", return_value=report) as mocked:
            response = self._client().get("/models/validate/validate-model?vram=false")

        assert response.status_code == 200
        assert mocked.call_args.kwargs == {"names": ["validate-model"], "vram": False}

    def test_no_vram_response_carries_no_vram_verdict(self, config_with_one_model):
        response = self._client().get("/models/validate?vram=false")
        assert response.status_code == 200
        entry = response.json()["models"][0]
        assert not any(v["check"] == "vram" for v in entry["verdicts"])
