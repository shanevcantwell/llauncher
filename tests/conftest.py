import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig


def pytest_configure(config):
    """Register custom markers used by the suite."""
    config.addinivalue_line(
        "markers",
        "real_model_health: opt out of the autouse "
        "``_patch_model_health`` mock and exercise the real "
        "``llauncher.core.model_health.check_model_health``.",
    )


@pytest.fixture(autouse=True)
def _patch_model_health(request):
    """Patch ``check_model_health`` to always return valid in tests.

    Prevents small test temp-files from triggering the >1 MB health gate,
    which would break existing state/eviction tests that were written before
    ADR-005 was added.

    Patch target moved (issue #57): ``state.py`` no longer imports
    ``check_model_health``; the operations layer's preflight module is the
    single consumer (wrapped via :func:`default_model_health_check`). The
    target ``llauncher.operations.preflight.mh.check_model_health`` resolves
    via attribute traversal to the same *module object* as
    ``llauncher.core.model_health``, so the patch is applied to that module
    attribute. Reach is therefore:

    - **Reached:** any caller that does attribute access against the
      module each call, e.g. ``mh.check_model_health(...)`` after
      ``from llauncher.core import model_health as mh``, or a fresh
      ``from llauncher.core.model_health import check_model_health``
      executed inside a function body (the lookup hits the module dict
      every time).
    - **Not reached:** call sites that bound the function name at module
      import time (``from llauncher.core.model_health import check_model_health``
      at module top level) — those hold a direct reference to the
      original function object and bypass the patched attribute.

    Tests that need the real implementation
    (``test_adr_cross_cutting``, ``test_agent_models_health_api``) can opt
    out by adding ``@pytest.mark.real_model_health``.
    """
    if request.node.get_closest_marker("real_model_health"):
        yield
        return

    mock_result = MagicMock()
    mock_result.valid = True
    mock_result.exists = True
    mock_result.readable = True
    mock_result.size_bytes = 1024 * 1024 + 1
    mock_result.reason = None
    mock_result.last_modified = None

    with patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=mock_result,
    ):
        yield


@pytest.fixture(autouse=True)
def _deterministic_delegation(monkeypatch):
    """Force the #200 delegation gate to in-process for the whole suite.

    A real llauncher agent may be listening on ``LLAUNCHER_AGENT_PORT``
    (8765) on the developer/CI host. Without pinning the gate, the
    auto-detect health probe would find it and the MCP/UI launch tests
    (test_mcp_flows, test_self_swap, ...) would POST real start/stop verbs
    to that live agent — spawning real models and breaking isolation.

    Pinning ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=0`` makes every front-end
    take the in-process path by default, matching the legacy behavior the
    bulk of the suite was written against. We also clear the
    ``LLAUNCHER_IS_AGENT_PROCESS`` stamp so no ambient value leaks in.
    Gate-specific tests override these via their own ``monkeypatch`` (which
    wins, being applied inside the test body after this autouse setup).
    """
    monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "0")
    monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)


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
    """Mock ConfigStore with temporary paths.

    Also redirects ``LAUNCHER_AUDIT_PATH`` so the CRUD audit entries
    added per issue #60 are written into ``tmp_path`` instead of the
    developer's real ``~/.llauncher/audit.jsonl``.
    """
    audit_target = tmp_config_dir / "audit.jsonl"
    with patch('llauncher.core.config.CONFIG_DIR', tmp_config_dir) as mock_dir, \
         patch('llauncher.core.config.CONFIG_PATH', tmp_config_dir / 'config.json') as mock_path, \
         patch('llauncher.core.audit_log.LAUNCHER_AUDIT_PATH', audit_target), \
         patch('llauncher.core.settings.LAUNCHER_AUDIT_PATH', audit_target):
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
