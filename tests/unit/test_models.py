import pytest
from unittest.mock import patch
from llauncher.models.config import ModelConfig

def test_model_config_validation():
    """Test that ModelConfig validates input correctly."""
    # Using from_dict_unvalidated to skip path validation for this unit test
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "port": 8080,
        "n_gpu_layers": 32,
        "ctx_size": 2048,
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.name == "test-model"
    assert config.port == 8080

def test_model_config_invalid_port():
    """Test that invalid port values raise validation errors."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "port": 10,  # Invalid port
        "n_gpu_layers": 32,
        "ctx_size": 2048,
    }
    with pytest.raises(ValueError):
        ModelConfig.from_dict_unvalidated(data)
