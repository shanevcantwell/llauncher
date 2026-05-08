import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig


@pytest.fixture(autouse=True)
def _patch_model_health():
    """Patch ``check_model_health`` to always return valid in tests.

    Prevents small test temp-files from triggering the >1 MB health gate,
    which would break existing state/eviction tests that were written before
    ADR-005 was added.  Tests that specifically want real health checks can
    override this by un-patching or using their own fixture.
    """
    mock_result = MagicMock()
    mock_result.valid = True
    mock_result.exists = True
    mock_result.readable = True
    mock_result.size_bytes = 1024 * 1024 + 1
    mock_result.reason = None
    mock_result.last_modified = None

    with patch("llauncher.state.check_model_health", return_value=mock_result):
        yield


@pytest.fixture(autouse=True)
def _isolate_nodes_file(tmp_path, monkeypatch):
    """Redirect the node-registry persistence file to a per-test tmp path.

    ``llauncher.remote.registry.NODES_FILE`` is a module-level Path pointing at
    ``~/.llauncher/nodes.json``. Several tests instantiate ``NodeRegistry()``
    without a per-fixture override and call ``add_node`` / ``remove_node``,
    which historically leaked test fixtures (``node1``, ``node2``, ``custom``,
    etc.) into the developer's real registry. This autouse fixture isolates
    every test by default; opt-out tests can monkeypatch back if needed.
    """
    monkeypatch.setattr(
        "llauncher.remote.registry.NODES_FILE",
        tmp_path / "nodes.json",
    )

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
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    })

@pytest.fixture
def launcher_state(mock_config_store):
    """LauncherState with mocked dependencies."""
    # Mock process management to avoid real side effects
    with patch('llauncher.core.process.find_all_llama_servers', return_value=[]):
        yield LauncherState()
