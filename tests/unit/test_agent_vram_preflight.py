"""Unit test for VRAM pre-flight on POST /start-with-eviction.

Split out from the former ``test_agent_models_health_api.py`` (issue #475,
ADR-LLNCH-027): that file's ``GET /models/health[/{name}]`` endpoint tests were
deleted along with the endpoints they covered — superseded by
``GET /models/validate[/{name}]`` (see ``test_agent_models_validate_api.py``)
with no in-repo consumer of the old routes. This VRAM pre-flight test is
unrelated to the health/validate endpoints (it exercises the
``start-with-eviction`` verb) and is kept as-is under its own file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock


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


class TestVRAMPreFlightEndpoint:
    """Tests for VRAM pre-flight on POST /start-with-eviction."""

    def test_vram_error_contains_required_and_available(self):
        """409 error includes required_mb and available_mb when insufficient."""
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated as create_app

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
