"""Tests for llauncher CLI (Typer-based command-line interface).

Uses typer.testing.CliRunner to invoke the CLI without subprocess.
Covers all four subcommand groups: model, server, node, config.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import typer
from typer.testing import CliRunner

from llauncher.cli import app, console
from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_model_config():
    """Sample model configuration for tests."""
    return ModelConfig.from_dict_unvalidated({
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    })


@pytest.fixture
def mock_config_store(tmp_path):
    """Mock ConfigStore with temporary path."""
    config_dir = tmp_path / ".llauncher"
    config_path = config_dir / "config.json"

    with patch("llauncher.core.config.CONFIG_DIR", config_dir):
        with patch("llauncher.core.config.CONFIG_PATH", config_path):
            yield config_dir, config_path


@pytest.fixture
def node_config_file(tmp_path):
    """Temporary nodes file for tests."""
    nodes_dir = tmp_path / ".llauncher"
    nodes_file = nodes_dir / "nodes.json"
    with patch("llauncher.remote.registry.NODES_FILE", nodes_file):
        yield nodes_file


# ---------------------------------------------------------------------------
# Help / overall CLI
# ---------------------------------------------------------------------------

def test_help_shows_all_command_groups():
    """CLI help should display all four subcommand groups."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("model", "server", "node", "config"):
        assert group in result.stdout


# ---------------------------------------------------------------------------
# model subcommands
# ---------------------------------------------------------------------------

def test_model_list_empty(mock_config_store):
    """Model list should be empty when no models are configured."""
    _dir, _path = mock_config_store
    # No models added — ConfigStore.load() returns {} by default
    result = runner.invoke(app, ["model", "list"])
    assert result.exit_code == 0


def test_model_list_with_models(mock_config_store):
    """Model list should show configured models in a table."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "gemma", "model_path": "/fake/gemma.gguf",
    }))
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "llama3", "model_path": "/fake/llama.gguf",
    }))

    result = runner.invoke(app, ["model", "list"])
    assert result.exit_code == 0
    assert "gemma" in result.stdout
    assert "llama3" in result.stdout


def test_model_list_json(mock_config_store):
    """Model list --json should return valid JSON."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "qwen", "model_path": "/fake/qwen.gguf",
    }))

    result = runner.invoke(app, ["model", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert "qwen" in data


def test_model_info_not_found(mock_config_store):
    """Model info for a non-existent model should error."""
    _dir, _path = mock_config_store

    result = runner.invoke(app, ["model", "info", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_model_info_json(mock_config_store):
    """Model info --json should return valid JSON with expected fields."""
    _dir, _path = mock_config_store
    cfg = ModelConfig.from_dict_unvalidated({
        "name": "phi", "model_path": "/fake/phi.gguf",
        "n_gpu_layers": 30,
    })
    ConfigStore.add_model(cfg)

    result = runner.invoke(app, ["model", "info", "phi", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["name"] == "phi"
    assert data["n_gpu_layers"] == 30
    assert "default_port" not in data  # ADR-010: not a model attribute


# ---------------------------------------------------------------------------
# server subcommands
# ---------------------------------------------------------------------------

def test_server_status_no_servers(mock_config_store, sample_model_config):
    """Server status with no running servers should show informational message."""
    _dir, _path = mock_config_store

    with patch("llauncher.cli.LauncherState") as MockState:
        instance = MagicMock()
        instance.running = {}
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "status"])
        assert result.exit_code == 0
        assert "no server" in result.stdout.lower() or "No server" in result.stdout


def test_server_status_json_empty(mock_config_store):
    """Server status --json with no servers should return empty JSON object."""
    _dir, _path = mock_config_store

    with patch("llauncher.cli.LauncherState") as MockState:
        instance = MagicMock()
        instance.running = {}
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert len(data) == 0


def test_start_missing_model(mock_config_store):
    """Starting a non-existent model should error."""
    _dir, _path = mock_config_store

    with patch("llauncher.cli.LauncherState") as MockState:
        instance = MagicMock()
        instance.start_server.return_value = (False, "Model not found: unknown-model", None)
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "start", "unknown-model"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


def test_start_with_explicit_port(mock_config_store):
    """Starting a model with --port should call operations.start with that port."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.start") as mock_start:
        from llauncher.operations import StartResult

        mock_start.return_value = StartResult(
            success=True,
            action="started",
            port=9999,
            model="test-model",
            pid=42,
            message="Started test-model on port 9999",
        )

        result = runner.invoke(app, ["server", "start", "test-model", "--port", "9999"])
        assert result.exit_code == 0
        # Verify operations.start was called with the correct port argument.
        mock_start.assert_called_once()
        args, kwargs = mock_start.call_args
        # First positional arg is name, second is port.
        assert args[0] == "test-model"
        assert args[1] == 9999


def test_stop_nonexistent_port(mock_config_store):
    """Stopping a non-running server is now idempotent (per ADR-010)."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.stop") as mock_stop:
        from llauncher.operations import StopResult

        # Per ADR-010, stop on empty port is success-with-already_empty.
        mock_stop.return_value = StopResult(
            success=True,
            action="already_empty",
            port=9000,
            message="No server claimed port 9000",
        )

        result = runner.invoke(app, ["server", "stop", "9000"])
        assert result.exit_code == 0
        mock_stop.assert_called_once_with(9000, caller="cli")


# ---------------------------------------------------------------------------
# node subcommands
# ---------------------------------------------------------------------------

def test_node_add_and_list(node_config_file):
    """Adding a node should persist it and list should show it."""
    # Add via CLI
    result = runner.invoke(app, ["node", "add", "test-node", "--host", "192.168.1.50"])
    assert result.exit_code == 0

    # List should find the node
    result = runner.invoke(app, ["node", "list"])
    assert result.exit_code == 0
    assert "test-node" in result.stdout


def test_node_add_with_api_key_persists(node_config_file):
    """Adding a node with --api-key should store the key."""
    # Add via CLI with api key
    result = runner.invoke(app, [
        "node", "add", "secure-node", "--host", "10.0.0.1",
        "--port", "8765", "--api-key", "secret-token-xyz"
    ])
    assert result.exit_code == 0

    # Verify the node was persisted with api_key
    import json as _json
    data = _json.loads(node_config_file.read_text())
    node_data = data.get("secure-node")
    assert node_data is not None
    assert node_data.get("has_api_key") is True


def test_node_add_duplicate_fails(tmp_path):
    """Adding a duplicate node name should error."""
    from llauncher.remote.registry import NodeRegistry, NODES_FILE

    nodes_file = tmp_path / ".llauncher" / "nodes.json"

    with patch("llauncher.cli.NodeRegistry", spec=NodeRegistry) as MockReg:
        reg_instance = MagicMock()
        MockReg.return_value = reg_instance
        reg_instance.add_node.return_value = (False, "Node 'my-node' already exists")

        result = runner.invoke(app, ["node", "add", "my-node", "--host", "1.2.3.4"])
        assert result.exit_code == 1
        assert "already exists" in result.stdout.lower()


def test_node_remove(node_config_file):
    """Removing a node should delete it from the registry."""
    # First add a node
    runner.invoke(app, ["node", "add", "to-delete", "--host", "5.6.7.8"])

    assert node_config_file.exists()
    initial = json.loads(node_config_file.read_text())
    assert "to-delete" in initial

    # Remove it
    result = runner.invoke(app, ["node", "remove", "to-delete"])
    assert result.exit_code == 0

    # Verify removal
    remaining = json.loads(node_config_file.read_text())
    assert "to-delete" not in remaining


def test_node_remove_not_found():
    """Removing a non-existent node should error."""
    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        MockReg.return_value = reg_instance
        reg_instance.remove_node.return_value = (False, "Node 'ghost' not found")

        result = runner.invoke(app, ["node", "remove", "ghost"])
        assert result.exit_code == 1


def test_node_status_json(node_config_file):
    """Node status --json should return valid JSON with node details."""
    # Add a node first
    runner.invoke(app, ["node", "add", "jstatus-node", "--host", "9.8.7.6"])

    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        MockReg.return_value = reg_instance

        # Configure mock node
        mock_node = MagicMock()
        mock_node.host = "9.8.7.6"
        mock_node.port = 8765
        mock_node.api_key = None
        mock_node.status.value = "online"
        mock_node.last_seen = None
        mock_node._error_message = None

        reg_instance._nodes = {"jstatus-node": mock_node}

        result = runner.invoke(app, ["node", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------

def test_config_path_printed(mock_config_store):
    """Config path should print the path to the configuration file."""
    _dir, cfg_path = mock_config_store

    with patch("llauncher.cli.CONFIG_PATH", cfg_path):
        result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(cfg_path) in result.stdout


def test_config_validate_valid(mock_config_store):
    """Valid config should pass validation."""
    _dir, _path = mock_config_store

    # Create a real temp file so ModelConfig.path_exists validator passes
    fake_model_path = str(_dir / "real_model.gguf")
    _dir.mkdir(parents=True, exist_ok=True)
    Path(fake_model_path).touch()

    cfg = ModelConfig.from_dict_unvalidated({
        "name": "valid-model",
        "model_path": fake_model_path,
    })
    ConfigStore.add_model(cfg)

    result = runner.invoke(app, ["config", "validate", "valid-model"])
    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()

    # Cleanup
    Path(fake_model_path).unlink(missing_ok=True)


def test_config_validate_not_found(mock_config_store):
    """Validating a non-existent model should error."""
    _dir, _path = mock_config_store

    result = runner.invoke(app, ["config", "validate", "missing-model"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Negative / edge cases
# ---------------------------------------------------------------------------

def test_invalid_subcommand():
    """Unknown subcommand should produce a helpful error."""
    result = runner.invoke(app, ["bogus"])
    assert result.exit_code != 0
