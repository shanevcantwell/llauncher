import pytest
from unittest.mock import patch
from llauncher.models.config import ModelConfig

def test_model_config_validation():
    """Test that ModelConfig validates input correctly."""
    # Using from_dict_unvalidated to skip path validation for this unit test
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "default_port": 8080,
        "n_gpu_layers": 32,
        "ctx_size": 2048,
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.name == "test-model"
    assert config.default_port == 8080

def test_model_config_invalid_default_port():
    """Test that invalid default_port values raise validation errors."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "default_port": 10,  # Invalid port (below 1024)
        "n_gpu_layers": 32,
        "ctx_size": 2048,
    }
    with pytest.raises(ValueError):
        ModelConfig.from_dict_unvalidated(data)


def test_model_config_extra_args_migration():
    """Test that extra_args is migrated from list[str] to str."""
    # Old format with list
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": ["--flag1", "value1", "--flag2"],
    }
    config = ModelConfig.from_dict_unvalidated(data)
    # Should be migrated to space-separated string
    assert config.extra_args == "--flag1 value1 --flag2"


def test_model_config_extra_args_string_format():
    """Test that extra_args works with string format."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": "--mcp-config /path/to/.mcp.json --flag value",
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == "--mcp-config /path/to/.mcp.json --flag value"


def test_model_config_extra_args_empty():
    """Test that empty extra_args defaults to empty string."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == ""


def test_model_config_extra_args_empty_list_migration():
    """Test that extra_args empty list [] is migrated to empty string "".

    Regression test for GitHub issue: UI crash when editing models with
    old-format config entries containing "extra_args": [].
    """
    # Old format with empty list (common in existing configs before the str migration)
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": [],  # Empty list from old format
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == "", "Empty list should migrate to empty string"
