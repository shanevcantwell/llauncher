"""Tests for MCP config tools."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.mcp_server.tools.config import (
    update_model_config,
    validate_config,
    add_model,
    remove_model,
    get_tools,
)
from llauncher.models.config import ModelConfig


@pytest.fixture
def mock_state():
    """Mock LauncherState with test data."""
    state = MagicMock()

    config = ModelConfig.from_dict_unvalidated({
        "name": "existing-model",
        "model_path": "/path/to/model.gguf",
    })

    state.models = {"existing-model": config}
    state.running = {}

    return state


class TestUpdateModelConfig:
    """Tests for update_model_config tool."""

    @pytest.mark.asyncio
    async def test_update_model_config_missing_name(self):
        """Returns error for missing name argument."""
        mock_state = MagicMock()
        result = await update_model_config(mock_state, {})

        assert result["success"] is False
        assert "name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_update_model_config_not_found(self):
        """Returns error for unknown model."""
        mock_state = MagicMock()
        mock_state.models = {}

        result = await update_model_config(mock_state, {"name": "unknown", "config": {}})

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_update_model_config_success(self, mock_state):
        """Updates model and saves to ConfigStore."""
        with patch("llauncher.core.config.ConfigStore") as mock_config_store:
            result = await update_model_config(
                mock_state,
                {"name": "existing-model", "config": {"n_gpu_layers": 100}},
            )

            assert result["success"] is True
            mock_config_store.update_model.assert_called_once()


class TestUpdateModelConfigValidation:
    """Tests for update_model_config edge cases and validation errors (#34-G)."""

    @pytest.mark.asyncio
    async def test_update_model_config_pydantic_validation_error(self):
        """update_model_config returns error when updated config fails Pydantic validation.

        This closes the uncovered exception path in update_model_config (lines 129-147 of config.py).
        We mock model_validate to raise so we exercise the try/except block around it.
        """
        from llauncher.models.config import ModelConfig

        mock_state = MagicMock()
        existing = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/dev/null/test.gguf",
            "default_port": 8081,
            "ctx_size": 4096,
            "n_gpu_layers": 255
        })
        mock_state.models = {"test-model": existing}

        with patch.object(ModelConfig, "model_validate", side_effect=ValueError("Field 'threads' must be > 0")):
            result = await update_model_config(mock_state, {
                "name": "test-model",
                "config": {"ctx_size": -1}
            })

        assert result["success"] is False
        assert "Validation error" in result.get("error", "")


class TestValidateConfig:
    """Tests for validate_config tool."""

    @pytest.mark.asyncio
    async def test_validate_config_missing_config(self):
        """Returns error for missing config argument."""
        mock_state = MagicMock()
        result = await validate_config(mock_state, {})

        assert result["valid"] is False
        assert "config" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_validate_config_valid(self):
        """Returns valid=True for valid config."""
        mock_state = MagicMock()
        valid_config = {"name": "new-model", "model_path": "/path/to/model.gguf"}

        with patch("llauncher.mcp_server.tools.config.ModelConfig") as mock_model_config:
            mock_model_config.model_validate.return_value = ModelConfig.from_dict_unvalidated(valid_config)

            result = await validate_config(mock_state, {"config": valid_config})

            assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_config_invalid(self):
        """Returns valid=False for invalid config."""
        mock_state = MagicMock()
        invalid_config = {"name": "test"}  # Missing model_path

        with patch("llauncher.mcp_server.tools.config.ModelConfig") as mock_model_config:
            mock_model_config.model_validate.side_effect = ValueError("model_path required")

            result = await validate_config(mock_state, {"config": invalid_config})

            assert result["valid"] is False
            assert "error" in result


class TestAddModel:
    """Tests for add_model tool."""

    @pytest.mark.asyncio
    async def test_add_model_missing_config(self):
        """Returns error for missing config argument."""
        mock_state = MagicMock()
        result = await add_model(mock_state, {})

        assert result["success"] is False
        assert "config" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_add_model_validation_error(self):
        """Returns error for invalid config."""
        mock_state = MagicMock()
        with patch("llauncher.mcp_server.tools.config.ModelConfig") as mock_model_config:
            mock_model_config.model_validate.side_effect = ValueError("Invalid config")

            result = await add_model(mock_state, {"config": {"name": "test"}})

            assert result["success"] is False
            assert "validation" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_add_model_exists(self, mock_state):
        """Returns error for duplicate model name."""
        with patch("llauncher.mcp_server.tools.config.ModelConfig") as mock_model_config:
            mock_model_config.model_validate.return_value = mock_state.models["existing-model"]

            result = await add_model(mock_state, {"config": {"name": "existing-model"}})

            assert result["success"] is False
            assert "already exists" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_add_model_success(self, mock_state):
        """Adds model to ConfigStore."""
        new_config = ModelConfig.from_dict_unvalidated({
            "name": "new-model",
            "model_path": "/path/to/new.gguf",
        })

        with patch("llauncher.core.config.ConfigStore") as mock_config_store:
            with patch("llauncher.mcp_server.tools.config.ModelConfig") as mock_model_config:
                mock_model_config.model_validate.return_value = new_config

                result = await add_model(mock_state, {"config": {"name": "new-model", "model_path": "/path/to/new.gguf"}})

                assert result["success"] is True
                mock_config_store.add_model.assert_called_once()


class TestRemoveModel:
    """Tests for remove_model tool."""

    @pytest.mark.asyncio
    async def test_remove_model_missing_name(self):
        """Returns error for missing name argument."""
        mock_state = MagicMock()
        result = await remove_model(mock_state, {})

        assert result["success"] is False
        assert "name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_remove_model_not_found(self):
        """Returns error for unknown model."""
        mock_state = MagicMock()
        mock_state.models = {}

        result = await remove_model(mock_state, {"name": "unknown"})

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_remove_model_server_running(self, mock_state):
        """Returns error if server is running."""
        mock_state.running = {8080: MagicMock(config_name="existing-model")}

        result = await remove_model(mock_state, {"name": "existing-model"})

        assert result["success"] is False
        assert "running" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_remove_model_success(self, mock_state):
        """Removes from ConfigStore and state."""
        with patch("llauncher.core.config.ConfigStore") as mock_config_store:
            result = await remove_model(mock_state, {"name": "existing-model"})

            assert result["success"] is True
            mock_config_store.remove_model.assert_called_once_with("existing-model")


class TestGetTools:
    """Tests for get_tools function."""

    def test_get_tools_returns_four_tools(self):
        """get_tools returns four config tools."""
        tools = get_tools()

        assert len(tools) == 4
        tool_names = [t.name for t in tools]
        assert "update_model_config" in tool_names
        assert "validate_config" in tool_names
        assert "add_model" in tool_names
        assert "remove_model" in tool_names
