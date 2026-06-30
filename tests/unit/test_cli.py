"""Tests for llauncher CLI (Typer-based command-line interface).

Uses typer.testing.CliRunner to invoke the CLI without subprocess.
Covers all four subcommand groups: model, server, node, config.
"""

import json
import pytest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

import typer
from typer.testing import CliRunner

from llauncher import cli
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
    """Starting a non-existent model should error.

    --port is required (ADR-010 / issue #58); supplying an arbitrary port
    here lets the test reach the model-not-found error path.
    """
    _dir, _path = mock_config_store

    with patch("llauncher.operations.start") as mock_start:
        from llauncher.operations import StartResult

        mock_start.return_value = StartResult(
            success=False,
            action="error",
            port=9999,
            model="unknown-model",
            message="Model not found: unknown-model",
        )

        result = runner.invoke(
            app, ["server", "start", "unknown-model", "--port", "9999"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


def test_start_without_port_errors(mock_config_store):
    """Omitting --port must fail at arg-parse time (ADR-010 / issue #58).

    Previously the CLI fell back to the ``DEFAULT_PORT`` env var; that
    fallback is removed. ``operations.start`` must never be called when
    --port is missing.
    """
    _dir, _path = mock_config_store

    with patch("llauncher.operations.start") as mock_start:
        result = runner.invoke(app, ["server", "start", "test-model"])

    assert result.exit_code != 0
    # Typer surfaces the missing-option error in stderr; just verify the
    # operation was never invoked.
    mock_start.assert_not_called()


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


def test_server_cancel_delivered(mock_config_store):
    """ADR-014: cancel reports marker_existed=True when a marker existed."""
    with patch("llauncher.core.marker.request_cancel", return_value=True) as mock_req:
        result = runner.invoke(app, ["server", "cancel", "8081"])
    assert result.exit_code == 0
    mock_req.assert_called_once_with(8081)
    assert "8081" in result.stdout


def test_server_cancel_no_op_when_no_marker(mock_config_store):
    """ADR-014: 'nothing to cancel' is a successful no-op (exit 0)."""
    with patch("llauncher.core.marker.request_cancel", return_value=False):
        result = runner.invoke(app, ["server", "cancel", "9999"])
    assert result.exit_code == 0
    assert "nothing to cancel" in result.stdout.lower()


def test_server_cancel_json_output(mock_config_store):
    with patch("llauncher.core.marker.request_cancel", return_value=True):
        result = runner.invoke(app, ["server", "cancel", "8081", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {"cancelled": True, "marker_existed": True, "port": 8081}


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
# server delegation gate (#203, mirrors MCP/UI #200 delegation tests)
# ---------------------------------------------------------------------------
#
# The autouse ``_deterministic_delegation`` fixture (tests/conftest.py) pins
# ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=0`` and clears the agent stamp, so the
# CLI's gate takes the in-process path unless a test overrides it. These tests
# mirror ``TestDelegationRouting`` (mcp) / ``test_model_card_delegation`` (ui):
# the gate decision is patched (or env-forced), the local-agent-node factory is
# mocked, and we assert delegate → HTTP via the node, in-process → ``ops.*``.


@contextmanager
def _cli_delegate(node, *, enabled=True):
    """Patch the gate decision and the local-agent-node factory the CLI uses.

    ``cli.start_server`` / ``cli.stop_server`` import ``delegation`` and
    ``local_agent_node`` at call time, so patch them at their definition
    sites (``llauncher.core.delegation.should_delegate`` and
    ``llauncher.remote.node.local_agent_node``).
    """
    with patch(
        "llauncher.core.delegation.should_delegate", return_value=enabled
    ), patch(
        "llauncher.remote.node.local_agent_node", return_value=node
    ) as factory:
        yield factory


class TestCliStartDelegation:
    def test_start_delegates_over_http_when_agent_present(self, mock_config_store):
        node = MagicMock()
        node.start_server.return_value = {
            "success": True,
            "action": "started",
            "port": 8080,
            "message": "Started m on port 8080",
        }
        with patch("llauncher.operations.start") as mock_ops_start, _cli_delegate(node):
            result = runner.invoke(app, ["server", "start", "m", "--port", "8080"])

        assert result.exit_code == 0
        node.start_server.assert_called_once_with("m", 8080)
        mock_ops_start.assert_not_called()

    def test_start_in_process_when_no_agent(self, mock_config_store):
        from llauncher.operations import StartResult

        result_obj = StartResult(
            success=True, action="started", port=8080, model="m",
            pid=7, message="Started m on port 8080",
        )
        with patch(
            "llauncher.operations.start", return_value=result_obj
        ) as mock_ops_start, _cli_delegate(MagicMock(), enabled=False) as factory:
            result = runner.invoke(app, ["server", "start", "m", "--port", "8080"])

        assert result.exit_code == 0
        mock_ops_start.assert_called_once_with("m", 8080, caller="cli")
        factory.assert_not_called()

    def test_start_env_override_forces_delegation(self, mock_config_store, monkeypatch):
        """Real gate: ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=1`` → HTTP, no probe."""
        monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "1")
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        node = MagicMock()
        node.start_server.return_value = {"success": True, "message": "ok"}
        with patch("llauncher.operations.start") as mock_ops_start, patch(
            "llauncher.remote.node.local_agent_node", return_value=node
        ):
            result = runner.invoke(app, ["server", "start", "m", "--port", "8080"])

        assert result.exit_code == 0
        node.start_server.assert_called_once_with("m", 8080)
        mock_ops_start.assert_not_called()

    def test_start_delegated_failure_exits_nonzero(self, mock_config_store):
        node = MagicMock()
        node.start_server.return_value = {
            "success": False, "error": "agent refused", "port": 8080,
        }
        with patch("llauncher.operations.start"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "start", "m", "--port", "8080"])

        assert result.exit_code == 1
        assert "agent refused" in result.stdout

    def test_start_delegated_none_result_is_safe(self, mock_config_store):
        """A ``None`` delegated body must surface as an error, not raise."""
        node = MagicMock()
        node.start_server.return_value = None
        with patch("llauncher.operations.start"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "start", "m", "--port", "8080"])

        assert result.exit_code == 1
        assert "empty response" in result.stdout.lower()


class TestCliStopDelegation:
    def test_stop_delegates_over_http_when_agent_present(self, mock_config_store):
        node = MagicMock()
        node.stop_server.return_value = {
            "success": True, "action": "stopped", "port": 8080,
            "message": "Stopped server on port 8080",
        }
        with patch("llauncher.operations.stop") as mock_ops_stop, _cli_delegate(node):
            result = runner.invoke(app, ["server", "stop", "8080"])

        assert result.exit_code == 0
        node.stop_server.assert_called_once_with(8080)
        mock_ops_stop.assert_not_called()

    def test_stop_in_process_when_no_agent(self, mock_config_store):
        from llauncher.operations import StopResult

        result_obj = StopResult(
            success=True, action="stopped", port=8080,
            message="Stopped server on port 8080",
        )
        with patch(
            "llauncher.operations.stop", return_value=result_obj
        ) as mock_ops_stop, _cli_delegate(MagicMock(), enabled=False) as factory:
            result = runner.invoke(app, ["server", "stop", "8080"])

        assert result.exit_code == 0
        mock_ops_stop.assert_called_once_with(8080, caller="cli")
        factory.assert_not_called()

    def test_stop_delegated_failure_exits_nonzero(self, mock_config_store):
        node = MagicMock()
        node.stop_server.return_value = {
            "success": False, "error": "No server running on port 8080",
        }
        with patch("llauncher.operations.stop"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "stop", "8080"])

        assert result.exit_code == 1
        assert "no server running" in result.stdout.lower()

    def test_stop_delegated_message_fallback(self, mock_config_store):
        """Envelope lacking message/error/success → synthesized 'stop on port'."""
        node = MagicMock()
        node.stop_server.return_value = {"port": 8080}
        with patch("llauncher.operations.stop"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "stop", "8080"])

        # success defaults False (no 'success' key) → exit 1, synthesized msg.
        assert result.exit_code == 1
        assert "stop on port 8080" in result.stdout


def test_delegated_outcome_none_seam():
    """Unit-level guard on the dict|None reducer (mirrors MCP ``_delegated_or_error``)."""
    ok, msg = cli._delegated_outcome(None, "start", 8080)
    assert ok is False
    assert "empty response" in msg.lower()


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


# ---------------------------------------------------------------------------
# INTERFACE coverage close-out (issue: coverage close-out, INTERFACE cluster)
# ---------------------------------------------------------------------------


def test_print_table_colours_status_keywords():
    """``_print_table`` styles ``online``/``serving`` and ``stopped`` cells.

    Exercises the per-cell status-keyword branches (cli.py:75-80): a value
    of ``online`` (or ``running``/``serving``) takes the green branch and a
    value of ``stopped`` takes the yellow branch. We render directly rather
    than through a command so every status keyword is hit deterministically
    regardless of live node/server state.
    """
    # No assertion on ANSI styling itself (Rich owns that); reaching the
    # branches without raising is the coverage target. ``serving`` and
    # ``online`` both flow through the first branch (cli.py:76); ``stopped``
    # through the second (cli.py:78); ``offline`` through the third.
    cli._print_table(
        ["STATUS"],
        [["online"], ["serving"], ["running"], ["stopped"], ["offline"], ["other"]],
        title="Styling",
    )


def test_server_status_json_with_running_server(mock_config_store):
    """``server status --json`` exports each running server via ``to_dict``.

    Covers the JSON export loop body (cli.py:270): with at least one entry
    in ``state.running`` the command serializes ``srv.to_dict()`` keyed by
    port string.
    """
    _dir, _path = mock_config_store

    srv = MagicMock()
    srv.to_dict.return_value = {"port": 8080, "config_name": "m", "pid": 7}

    with patch("llauncher.cli.LauncherState") as MockState:
        instance = MagicMock()
        instance.running = {8080: srv}
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == {"8080": {"port": 8080, "config_name": "m", "pid": 7}}


def test_server_status_table_uptime_boundaries(mock_config_store):
    """``server status`` table renders hour/minute/second uptime formats.

    Covers the uptime-formatting boundaries (cli.py:283 hours branch, 285
    minutes branch; the sub-minute branch is already covered elsewhere). We
    seed three running servers whose ``uptime_seconds`` land in each band.
    """
    _dir, _path = mock_config_store

    def _srv(name, pid, secs):
        s = MagicMock()
        s.config_name = name
        s.pid = pid
        s.uptime_seconds.return_value = secs
        return s

    running = {
        8001: _srv("hours", 11, 7265),    # >= 3600 → "2h 1m"  (line 283)
        8002: _srv("minutes", 12, 125),   # >= 60   → "2m 5s"  (line 285)
        8003: _srv("seconds", 13, 42),    # < 60    → "42s"
    }

    with patch("llauncher.cli.LauncherState") as MockState:
        instance = MagicMock()
        instance.running = running
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "status"])
        assert result.exit_code == 0
        # Rich may wrap the table, but the model names anchor the rows.
        assert "hours" in result.stdout
        assert "minutes" in result.stdout
        assert "seconds" in result.stdout


def test_list_nodes_json(node_config_file):
    """``node list --json`` emits ``registry.to_dict()`` (cli.py:371-372)."""
    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance.to_dict.return_value = {"nodeA": {"host": "1.2.3.4", "port": 8765}}
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == {"nodeA": {"host": "1.2.3.4", "port": 8765}}
        reg_instance.to_dict.assert_called_once()


def _mock_node(host, port, status_value):
    node = MagicMock()
    node.host = host
    node.port = port
    node.status.value = status_value
    return node


def test_node_status_table_online_only(node_config_file):
    """``node status`` (no ``--all``) renders only online nodes (cli.py:427-433,439)."""
    online = _mock_node("10.0.0.1", 8765, "online")
    offline = _mock_node("10.0.0.2", 8765, "offline")

    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance._nodes = {"up": online, "down": offline}
        # get_node(...).ping() is a no-op MagicMock — the ping loop's try
        # branch runs without raising.
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "status"])
        assert result.exit_code == 0
        assert "up" in result.stdout
        # Offline node filtered out of the default (online-only) view.
        assert "down" not in result.stdout


def test_node_status_table_all_includes_offline(node_config_file):
    """``node status --all`` includes offline/error nodes (cli.py:427 True branch)."""
    online = _mock_node("10.0.0.1", 8765, "online")
    offline = _mock_node("10.0.0.2", 8765, "offline")

    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance._nodes = {"up": online, "down": offline}
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "status", "--all"])
        assert result.exit_code == 0
        assert "up" in result.stdout
        assert "down" in result.stdout


def test_node_status_table_empty_roster(node_config_file):
    """``node status`` with no registered nodes prints the empty notice (cli.py:435-436)."""
    with patch("llauncher.cli.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance._nodes = {}
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "status"])
        assert result.exit_code == 0
        assert "no nodes registered" in result.stdout.lower()


def test_config_validate_schema_exception(mock_config_store):
    """``config validate`` reports a schema failure on the exception path.

    Covers cli.py:472-474: ``get_model`` returns a config (so we pass the
    not-found guard) but the re-validation via ``ModelConfig.model_validate``
    raises, taking the ``except`` branch that prints the failure and exits 1.
    """
    _dir, _path = mock_config_store

    cfg = ModelConfig.from_dict_unvalidated({
        "name": "broken-model",
        "model_path": "/fake/path/model.gguf",
    })

    with patch("llauncher.cli.ConfigStore.get_model", return_value=cfg):
        with patch(
            "llauncher.cli.ModelConfig.model_validate",
            side_effect=ValueError("schema boom"),
        ):
            result = runner.invoke(app, ["config", "validate", "broken-model"])

    assert result.exit_code == 1
    assert "validation failed" in result.stdout.lower()
    assert "schema boom" in result.stdout
