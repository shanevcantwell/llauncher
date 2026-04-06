import pytest
from pathlib import Path
from unittest.mock import patch
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig

@pytest.fixture
def tmp_config_dir(tmp_path):
    """Temporary directory for config files."""
    return tmp_path / ".llauncher"

@pytest.fixture
def mock_config_store(tmp_config_dir):
    """Mock ConfigStore with temporary path."""
    # We need to patch the constants in the config module
    with patch('llauncher.core.config.CONFIG_DIR', tmp_config_dir) as mock_dir:
        with patch('llauncher.core.config.CONFIG_PATH', tmp_config_dir / 'config.json') as mock_path:
            yield mock_dir, mock_path

@pytest.fixture
def sample_model_config():
    """Sample model configuration for tests."""
    # Use from_dict_unvalidated to bypass the path existence check during tests
    return ModelConfig.from_dict_unvalidated({
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "port": 8081,
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    })

@pytest.fixture
def launcher_state(mock_config_store):
    """LauncherState with mocked dependencies."""
    # Mock discovery and process management to avoid real side effects
    with patch('llauncher.core.discovery.discover_scripts', return_value=[]):
        with patch('llauncher.core.process.find_all_llama_servers', return_value=[]):
            yield LauncherState()
