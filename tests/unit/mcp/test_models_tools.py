"""Tests for MCP models tools."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.mcp_server.tools.models import list_models, get_model_config, get_tools
from llauncher.models.config import ModelConfig, RunningServer
from datetime import datetime


@pytest.fixture
def mock_state():
    """Mock LauncherState with test data."""
    state = MagicMock()

    # Create test models. Per ADR-010, port is supplied at the call site,
    # not stored on the config — legacy default_port keys are silently dropped.
    running_config = ModelConfig.from_dict_unvalidated({
        "name": "running-model",
        "model_path": "/path/to/running.gguf",
        "ctx_size": 32768,
        "np": 4,
    })

    stopped_config = ModelConfig.from_dict_unvalidated({
        "name": "stopped-model",
        "model_path": "/path/to/stopped.gguf",
        "ctx_size": 65536,
        "np": None,
    })

    state.models = {
        "running-model": running_config,
        "stopped-model": stopped_config,
    }

    # Mock get_model_status to return appropriate status
    def get_status(name):
        if name == "running-model":
            return {"status": "running", "port": 8080, "pid": 12345}
        return {"status": "stopped"}

    state.get_model_status = get_status

    return state


class TestListModels:
    """Tests for list_models tool."""

    @pytest.mark.asyncio
    async def test_list_models_empty(self):
        """Empty state returns empty list."""
        mock_state = MagicMock()
        mock_state.models = {}

        result = await list_models(mock_state, {})

        assert result == {"models": [], "count": 0}

    @pytest.mark.asyncio
    async def test_list_models_running(self, mock_state):
        """Running model shows port and status in structured format."""
        result = await list_models(mock_state, {})

        assert len(result["models"]) == 2
        running = next(m for m in result["models"] if m["identification"]["name"] == "running-model")
        assert running["status"]["state"] == "running"
        assert running["status"]["port"] == 8080
        assert running["status"]["pid"] == 12345

    @pytest.mark.asyncio
    async def test_list_models_stopped(self, mock_state):
        """Stopped model shows auto-allocate in structured format."""
        result = await list_models(mock_state, {})

        stopped = next(m for m in result["models"] if m["identification"]["name"] == "stopped-model")
        assert stopped["status"]["state"] == "stopped"
        assert stopped["status"]["port"] is None  # Stopped → no port assigned

    @pytest.mark.asyncio
    async def test_list_models_multiple(self, mock_state):
        """Multiple models with mixed status in structured format."""
        result = await list_models(mock_state, {})

        assert len(result["models"]) == 2
        names = [m["identification"]["name"] for m in result["models"]]
        assert "running-model" in names
        assert "stopped-model" in names

    @pytest.mark.asyncio
    async def test_list_models_omits_default_port(self, mock_state):
        """Per ADR-010, default_port is no longer in the response."""
        result = await list_models(mock_state, {})

        for model in result["models"]:
            assert "default_port" not in model["status"]

    @pytest.mark.asyncio
    async def test_get_model_config_returns_full_config(self, mock_state):
        """get_model_config returns configuration dict with ctx_size and np."""
        result = await get_model_config(mock_state, {"name": "running-model"})

        config = result["configuration"]
        assert "ctx_size" in config
        assert "np" in config
        assert config["ctx_size"] == 32768
        assert config["np"] == 4

    @pytest.mark.asyncio
    async def test_get_model_config_np_none(self, mock_state):
        """get_model_config handles np=None correctly."""
        result = await get_model_config(mock_state, {"name": "stopped-model"})

        config = result["configuration"]
        assert config["np"] is None


class TestGetModelConfig:
    """Tests for get_model_config tool."""

    @pytest.mark.asyncio
    async def test_get_model_config_success(self, mock_state):
        """Returns full config and status in structured format."""
        result = await get_model_config(mock_state, {"name": "running-model"})

        assert result["identification"]["name"] == "running-model"
        assert "configuration" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_get_model_config_missing_name(self):
        """Returns error for missing name argument."""
        mock_state = MagicMock()
        result = await get_model_config(mock_state, {})

        assert "error" in result
        assert "name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_get_model_config_not_found(self):
        """Returns error for unknown model."""
        mock_state = MagicMock()
        mock_state.models = {}

        result = await get_model_config(mock_state, {"name": "unknown"})

        assert "error" in result
        assert "not found" in result["error"].lower()


class TestGetTools:
    """Tests for get_tools function."""

    def test_get_tools_returns_two_tools(self):
        """get_tools returns list_models and get_model_config."""
        tools = get_tools()

        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert "list_models" in tool_names
        assert "get_model_config" in tool_names
