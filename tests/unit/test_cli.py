"""Tests for llauncher CLI (Typer-based command-line interface).

Uses typer.testing.CliRunner to invoke the CLI without subprocess.
Covers all four subcommand groups: model, server, node, config.
"""

import io
import json
import pytest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

import typer
from rich.console import Console
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
    """Mock ConfigStore with temporary path.

    Module-local (shadows the ``tests/conftest.py`` fixture of the same
    name) because callers here destructure and assert against the returned
    ``(config_dir, config_path)`` tuple directly — that's still this
    fixture's job and it stays.

    Issue #463: this fixture historically had NO ``LAUNCHER_AUDIT_PATH``
    patch, so ``ConfigStore.add_model``/``remove_model`` calls under it
    wrote real audit lines to the operator's ``~/.llauncher/audit.jsonl``
    (the incident's exact anchor — see ``test_model_remove_happy_path``,
    ``test_model_remove_without_yes_aborts_on_no``,
    ``test_config_validate_valid`` below). That gap is now closed
    structurally by the autouse ``_isolate_state_dir`` fixture in
    ``tests/conftest.py`` (one seam, one owner) — do NOT add a redundant
    audit-path patch here; the autouse fixture is the single enforcement
    surface and ``tests/conftest.py::_forbid_real_state_writes`` fails any
    test by name if that surface is ever bypassed.
    """
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
    assert "default_port" not in data  # ADR-LLNCH-010: not a model attribute


# ---------------------------------------------------------------------------
# model remove (#276)
# ---------------------------------------------------------------------------


def test_model_remove_rejected_in_use(mock_config_store):
    """``model remove`` refuses (non-zero exit) when the model is in use.

    Patches ``ops.delete_model`` at ``llauncher.operations.delete_model`` —
    ``cli.remove_model`` does ``from llauncher import operations as ops``
    lazily inside the function body, so the module-level attribute on
    ``llauncher.operations`` is what's resolved at call time.
    """
    from llauncher.operations import DeleteModelResult

    envelope = DeleteModelResult(
        success=False,
        action="rejected_in_use",
        name="busy-model",
        in_use_port=8080,
        message="Model 'busy-model' is running on port 8080 (pid 123); stop it before deleting.",
    )
    with patch("llauncher.operations.delete_model", return_value=envelope) as mock_delete:
        result = runner.invoke(app, ["model", "remove", "busy-model", "--yes"])

    assert result.exit_code != 0
    assert "running on port 8080" in result.stdout
    mock_delete.assert_called_once_with("busy-model", caller="cli")


def test_model_remove_happy_path(mock_config_store):
    """``model remove --yes`` deletes an existing, not-in-use model end to end.

    Uses the real ``ConfigStore`` (via ``mock_config_store``) rather than
    mocking ``ops.delete_model``, so the CLI-to-operations wiring is
    genuinely exercised for at least one case.
    """
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "removable", "model_path": "/fake/removable.gguf",
    }))
    assert "removable" in ConfigStore.list_models()

    result = runner.invoke(app, ["model", "remove", "removable", "--yes"])

    assert result.exit_code == 0
    assert "removable" not in ConfigStore.list_models()


def test_model_remove_without_yes_aborts_on_no(mock_config_store):
    """Omitting ``--yes`` and answering ``n`` aborts without deleting."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "keep-me", "model_path": "/fake/keep-me.gguf",
    }))

    result = runner.invoke(app, ["model", "remove", "keep-me"], input="n\n")

    assert result.exit_code != 0
    assert "aborted" in result.stdout.lower()
    assert "keep-me" in ConfigStore.list_models()


# ---------------------------------------------------------------------------
# model validate (#475, ADR-LLNCH-027)
# ---------------------------------------------------------------------------


def _validation_report(*, ok: bool, models: list):
    from llauncher.models.validation import ValidationReport
    from datetime import datetime, timezone

    return ValidationReport(checked_at=datetime.now(timezone.utc), ok=ok, models=models)


def _model_validation(
    name: str,
    *,
    ok: bool,
    gating_reason: str = "",
    check: str = "weights",
    advisory_check: str = "",
    advisory_reason: str = "",
):
    from llauncher.models.validation import ModelValidation, ValidationVerdict

    verdicts = [
        ValidationVerdict(check=check, ok=ok, reason="" if ok else gating_reason)
    ]
    if advisory_check:
        verdicts.append(
            ValidationVerdict(
                check=advisory_check,
                ok=False,
                reason=advisory_reason,
                advisory=True,
            )
        )
    return ModelValidation(
        name=name,
        model_path=f"/fake/{name}.gguf",
        exists=ok,
        verdicts=verdicts,
        ok=ok,
    )


def test_model_validate_all_ok_exit_zero(mock_config_store):
    """``model validate`` with no argument exits 0 when every entry passes."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "healthy", "model_path": "/fake/healthy.gguf",
    }))

    report = _validation_report(ok=True, models=[_model_validation("healthy", ok=True)])
    with patch("llauncher.operations.validate_models", return_value=report) as mocked:
        result = runner.invoke(app, ["model", "validate"])

    assert result.exit_code == 0
    assert "OK" in result.stdout
    mocked.assert_called_once_with(names=None, vram=True)


def test_model_validate_one_missing_exit_two(mock_config_store):
    """``model validate`` exits 2 when at least one entry fails a gating check."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "gone", "model_path": "/fake/gone.gguf",
    }))

    report = _validation_report(
        ok=False, models=[_model_validation("gone", ok=False, gating_reason="not found")]
    )
    with patch("llauncher.operations.validate_models", return_value=report):
        result = runner.invoke(app, ["model", "validate"])

    assert result.exit_code == 2
    assert "MISSING" in result.stdout
    assert "not found" in result.stdout


def test_model_validate_unknown_name_exit_one(mock_config_store):
    """Validating an unconfigured name exits 1, matching ``model info``."""
    _dir, _path = mock_config_store

    result = runner.invoke(app, ["model", "validate", "no-such-model"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_model_validate_json_shape(mock_config_store):
    """``--json`` emits a ``ValidationReport``-shaped payload."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "healthy", "model_path": "/fake/healthy.gguf",
    }))

    report = _validation_report(ok=True, models=[_model_validation("healthy", ok=True)])
    with patch("llauncher.operations.validate_models", return_value=report):
        result = runner.invoke(app, ["model", "validate", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["models"][0]["name"] == "healthy"
    assert "verdicts" in data["models"][0]


def test_model_validate_single_name_delegates(mock_config_store):
    """Naming a single model calls ``validate_models(names=[name])``."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "healthy", "model_path": "/fake/healthy.gguf",
    }))

    report = _validation_report(ok=True, models=[_model_validation("healthy", ok=True)])
    with patch("llauncher.operations.validate_models", return_value=report) as mocked:
        result = runner.invoke(app, ["model", "validate", "healthy"])

    assert result.exit_code == 0
    mocked.assert_called_once_with(names=["healthy"], vram=True)


def test_model_validate_no_vram_flag_suppresses_the_shellout(mock_config_store):
    """``--no-vram`` reaches the op as ``vram=False`` (no nvidia-smi at all)."""
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "healthy", "model_path": "/fake/healthy.gguf",
    }))

    report = _validation_report(ok=True, models=[_model_validation("healthy", ok=True)])
    with patch("llauncher.operations.validate_models", return_value=report) as mocked:
        result = runner.invoke(app, ["model", "validate", "--no-vram"])

    assert result.exit_code == 0
    mocked.assert_called_once_with(names=None, vram=False)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"ok": True}, "OK"),
        ({"ok": False, "gating_reason": "not found"}, "MISSING"),
        ({"ok": False, "gating_reason": "unreadable"}, "UNREADABLE"),
        ({"ok": False, "gating_reason": "too small"}, "TOO_SMALL"),
        ({"ok": False, "check": "gguf_magic", "gating_reason": "bad magic bytes"}, "BAD_MAGIC"),
        (
            {"ok": True, "advisory_check": "lockfile", "advisory_reason": "stale lockfile on port 8081"},
            "STALE_LOCK",
        ),
        (
            {"ok": True, "advisory_check": "vram", "advisory_reason": "insufficient VRAM"},
            "VRAM?",
        ),
    ],
)
def test_model_validate_status_tokens_are_distinguishable(
    mock_config_store, kwargs, expected
):
    """Every gating failure is NOT ``MISSING`` (ADR-LLNCH-027 status vocabulary).

    Collapsing them sent the operator hunting for weights that are on disk
    but unreadable, truncated, or corrupt.
    """
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "subject", "model_path": "/fake/subject.gguf",
    }))

    entry = _model_validation("subject", **kwargs)
    report = _validation_report(ok=entry.ok, models=[entry])
    with patch("llauncher.operations.validate_models", return_value=report):
        result = runner.invoke(app, ["model", "validate"])

    assert not isinstance(result.exception, UnicodeEncodeError)
    assert expected in result.stdout


def test_model_validate_renders_on_cp1252_stdout(mock_config_store):
    """The whole rendered table survives a cp1252 console (#471-class guard).

    This invokes the CLI for real against a cp1252-encoded stdout, so it
    covers the whole ``_print_table`` render — including the box frame, whose
    default Rich glyphs are U+2500-range and not cp1252-encodable — not just
    the status tokens. Re-deriving the status expression inside the test body would
    pass unchanged if ``cli.py`` started emitting emoji, which is exactly
    what this guard must catch.
    """
    _dir, _path = mock_config_store
    ConfigStore.add_model(ModelConfig.from_dict_unvalidated({
        "name": "healthy", "model_path": "/fake/healthy.gguf",
    }))

    report = _validation_report(
        ok=False,
        models=[
            _model_validation("healthy", ok=True),
            _model_validation("gone", ok=False, gating_reason="not found"),
        ],
    )
    cp1252_runner = CliRunner(charset="cp1252")
    with patch("llauncher.operations.validate_models", return_value=report):
        result = cp1252_runner.invoke(app, ["model", "validate"])

    assert not isinstance(result.exception, UnicodeEncodeError), (
        f"CLI failed to encode its own table on a cp1252 console: {result.exception!r}"
    )
    assert result.exit_code == 2
    # Every byte the console emitted must round-trip through cp1252.
    result.stdout.encode("cp1252")
    assert "OK" in result.stdout and "MISSING" in result.stdout


# ---------------------------------------------------------------------------
# server subcommands
# ---------------------------------------------------------------------------

def test_server_status_no_servers(mock_config_store, sample_model_config):
    """Server status with no running servers should show informational message."""
    _dir, _path = mock_config_store

    with patch("llauncher.state.LauncherState") as MockState:
        instance = MagicMock()
        instance.running = {}
        MockState.return_value = instance

        result = runner.invoke(app, ["server", "status"])
        assert result.exit_code == 0
        assert "no server" in result.stdout.lower() or "No server" in result.stdout


def test_server_status_json_empty(mock_config_store):
    """Server status --json with no servers should return empty JSON object."""
    _dir, _path = mock_config_store

    with patch("llauncher.state.LauncherState") as MockState:
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

    --port is required (ADR-LLNCH-010 / issue #58); supplying an arbitrary port
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
    """Omitting --port must fail at arg-parse time (ADR-LLNCH-010 / issue #58).

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
    """ADR-LLNCH-014: cancel reports marker_existed=True when a marker existed."""
    with patch("llauncher.core.marker.request_cancel", return_value=True) as mock_req:
        result = runner.invoke(app, ["server", "cancel", "8081"])
    assert result.exit_code == 0
    mock_req.assert_called_once_with(8081)
    assert "8081" in result.stdout


def test_server_cancel_no_op_when_no_marker(mock_config_store):
    """ADR-LLNCH-014: 'nothing to cancel' is a successful no-op (exit 0)."""
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
    """Stopping a non-running server is now idempotent (per ADR-LLNCH-010)."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.stop") as mock_stop:
        from llauncher.operations import StopResult

        # Per ADR-LLNCH-010, stop on empty port is success-with-already_empty.
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
# server swap (#337 — parity with start/stop; ADR-LLNCH-010/ADR-LLNCH-011 envelope)
# ---------------------------------------------------------------------------


def test_swap_without_port_errors(mock_config_store):
    """Omitting --port must fail at arg-parse time, mirroring ``start`` (ADR-LLNCH-010)."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.swap") as mock_swap:
        result = runner.invoke(app, ["server", "swap", "test-model"])

    assert result.exit_code != 0
    mock_swap.assert_not_called()


def test_swap_with_explicit_port(mock_config_store):
    """Swapping a model with --port should call operations.swap with that port."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.swap") as mock_swap:
        from llauncher.operations import SwapResult

        mock_swap.return_value = SwapResult(
            success=True,
            action="swapped",
            port_state="serving",
            port=9999,
            model="test-model",
            previous_model="old-model",
            pid=42,
            message="Swapped to test-model on port 9999",
        )

        result = runner.invoke(app, ["server", "swap", "test-model", "--port", "9999"])
        assert result.exit_code == 0
        mock_swap.assert_called_once_with("test-model", 9999, caller="cli")
        assert "Swapped to test-model" in result.stdout


def test_swap_rejected_empty_port(mock_config_store):
    """Swap on an empty port is rejected per ADR-LLNCH-010 (use start instead)."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.swap") as mock_swap:
        from llauncher.operations import SwapResult

        mock_swap.return_value = SwapResult(
            success=False,
            action="rejected_empty",
            port_state="unavailable",
            port=9999,
            message="Port 9999 is empty; use start to launch a new server",
        )

        result = runner.invoke(app, ["server", "swap", "test-model", "--port", "9999"])
        assert result.exit_code == 1
        assert "empty" in result.stdout.lower()


def test_swap_rolled_back_surfaces_previous_model(mock_config_store):
    """A rolled-back swap renders both the message and the restored model (ADR-LLNCH-011)."""
    _dir, _path = mock_config_store

    with patch("llauncher.operations.swap") as mock_swap:
        from llauncher.operations import SwapResult

        mock_swap.return_value = SwapResult(
            success=False,
            action="rolled_back",
            port_state="restored",
            port=9999,
            model="old-model",
            previous_model="old-model",
            message="new model failed readiness",
        )

        result = runner.invoke(app, ["server", "swap", "test-model", "--port", "9999"])
        assert result.exit_code == 1
        assert "new model failed readiness" in result.stdout
        assert "rolled back to old-model" in result.stdout


class TestCliSwapDelegation:
    def test_swap_delegates_over_http_when_agent_present(self, mock_config_store):
        node = MagicMock()
        node.swap_server.return_value = {
            "success": True,
            "action": "swapped",
            "port": 8080,
            "message": "Swapped to m on port 8080",
        }
        with patch("llauncher.operations.swap") as mock_ops_swap, _cli_delegate(node):
            result = runner.invoke(app, ["server", "swap", "m", "--port", "8080"])

        assert result.exit_code == 0
        node.swap_server.assert_called_once_with("m", 8080)
        mock_ops_swap.assert_not_called()

    def test_swap_in_process_when_no_agent(self, mock_config_store):
        from llauncher.operations import SwapResult

        result_obj = SwapResult(
            success=True, action="swapped", port_state="serving", port=8080,
            model="m", message="Swapped to m on port 8080",
        )
        with patch(
            "llauncher.operations.swap", return_value=result_obj
        ) as mock_ops_swap, _cli_delegate(MagicMock(), enabled=False) as factory:
            result = runner.invoke(app, ["server", "swap", "m", "--port", "8080"])

        assert result.exit_code == 0
        mock_ops_swap.assert_called_once_with("m", 8080, caller="cli")
        factory.assert_not_called()

    def test_swap_delegated_failure_exits_nonzero(self, mock_config_store):
        node = MagicMock()
        node.swap_server.return_value = {
            "success": False, "error": "agent refused", "port": 8080,
        }
        with patch("llauncher.operations.swap"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "swap", "m", "--port", "8080"])

        assert result.exit_code == 1
        assert "agent refused" in result.stdout

    def test_swap_delegated_none_result_is_safe(self, mock_config_store):
        """A ``None`` delegated body must surface as an error, not raise."""
        node = MagicMock()
        node.swap_server.return_value = None
        with patch("llauncher.operations.swap"), _cli_delegate(node):
            result = runner.invoke(app, ["server", "swap", "m", "--port", "8080"])

        assert result.exit_code == 1
        assert "empty response" in result.stdout.lower()


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

    with patch("llauncher.remote.registry.NodeRegistry", spec=NodeRegistry) as MockReg:
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
    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        MockReg.return_value = reg_instance
        reg_instance.remove_node.return_value = (False, "Node 'ghost' not found")

        result = runner.invoke(app, ["node", "remove", "ghost"])
        assert result.exit_code == 1


def test_node_status_json(node_config_file):
    """Node status --json should return valid JSON with node details."""
    # Add a node first
    runner.invoke(app, ["node", "add", "jstatus-node", "--host", "9.8.7.6"])

    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
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

    with patch("llauncher.core.config.CONFIG_PATH", cfg_path):
        result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(cfg_path) in result.stdout


def test_config_path_not_wrapped_when_wider_than_console():
    """Path output must not be soft-wrapped: a long path on a narrow console
    must still emit as a single unbroken atom (#256).

    Regression guard — with a forced 80-column, non-TTY console (Rich's default
    under pytest) and a path longer than 80 chars, the pre-fix code inserted a
    mid-path newline, so the full path was no longer a substring of stdout.
    """
    long_path = Path(
        "/tmp/pytest-of-someuser/pytest-999999/"
        "test_config_path_not_wrapped0/.llauncher/config.json"
    )
    assert len(str(long_path)) > 80  # must exceed the default console width to bite

    with patch("llauncher.core.config.CONFIG_PATH", long_path), \
            patch.object(cli, "console", Console(width=80)):
        result = runner.invoke(app, ["config", "path"])

    assert result.exit_code == 0
    assert str(long_path) in result.stdout


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
    """``_print_table`` routes status keywords through ``_color``, others through ``Text``.

    Exercises the per-cell status-keyword branches (cli.py:75-80) *and*
    asserts the routing so a branch swap or a misspelled keyword would fail:
    every recognised status (``online``/``serving``/``running`` → green
    branch line 76; ``stopped`` → yellow branch line 78; ``offline`` → line
    79-80) must be handed to ``_color`` with its lowercased status, while a
    non-status value must NOT be (it falls to the plain-``Text`` else arm).
    """
    from unittest.mock import patch as _patch

    rows = [["online"], ["serving"], ["running"], ["stopped"], ["offline"], ["other"]]
    with _patch("llauncher.cli._color", wraps=cli._color) as color_spy:
        cli._print_table(["STATUS"], rows, title="Styling")

    routed = {call.args for call in color_spy.call_args_list}
    # Each recognised keyword reached its colour branch with (value, status).
    for kw in ("online", "serving", "running", "stopped", "offline"):
        assert (kw, kw) in routed, f"{kw} did not route through _color"
    # The non-status value took the plain-Text else branch, never _color.
    assert all(
        call.args[0] != "other" for call in color_spy.call_args_list
    ), "non-status value should not be colourised"


def test_node_status_ping_failure_is_swallowed(node_config_file):
    """A node whose ``ping()`` raises does not abort the status render (cli.py:407-409).

    The ping refresh loop swallows a transient failure and keeps the node's
    prior status so one flaky node cannot blank the whole status table.
    """
    node = _mock_node("10.0.0.9", 8765, "online")

    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance._nodes = {"flaky": node}
        pinger = MagicMock()
        pinger.ping.side_effect = RuntimeError("transient transport hiccup")
        reg_instance.get_node.return_value = pinger
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "status"])

    # The swallow kept the loop alive; the node still renders from its prior
    # (online) status rather than the command aborting.
    assert result.exit_code == 0
    assert "flaky" in result.stdout
    pinger.ping.assert_called_once()


def test_server_status_json_with_running_server(mock_config_store):
    """``server status --json`` exports each running server via ``to_dict``.

    Covers the JSON export loop body (cli.py:270): with at least one entry
    in ``state.running`` the command serializes ``srv.to_dict()`` keyed by
    port string.
    """
    _dir, _path = mock_config_store

    srv = MagicMock()
    srv.to_dict.return_value = {"port": 8080, "config_name": "m", "pid": 7}

    with patch("llauncher.state.LauncherState") as MockState:
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

    with patch("llauncher.state.LauncherState") as MockState:
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
    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
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

    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
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

    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
        reg_instance = MagicMock()
        reg_instance._nodes = {"up": online, "down": offline}
        MockReg.return_value = reg_instance

        result = runner.invoke(app, ["node", "status", "--all"])
        assert result.exit_code == 0
        assert "up" in result.stdout
        assert "down" in result.stdout


def test_node_status_table_empty_roster(node_config_file):
    """``node status`` with no registered nodes prints the empty notice (cli.py:435-436)."""
    with patch("llauncher.remote.registry.NodeRegistry") as MockReg:
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

    with patch("llauncher.core.config.ConfigStore.get_model", return_value=cfg):
        with patch(
            "llauncher.models.config.ModelConfig.model_validate",
            side_effect=ValueError("schema boom"),
        ):
            result = runner.invoke(app, ["config", "validate", "broken-model"])

    assert result.exit_code == 1
    assert "validation failed" in result.stdout.lower()
    assert "schema boom" in result.stdout


# ---------------------------------------------------------------------------
# audit command (issue #338)
# ---------------------------------------------------------------------------
#
# Mirrors ``TestAuditEndpoint`` in ``tests/unit/test_agent.py`` — same
# ``LAUNCHER_AUDIT_PATH`` monkeypatch, same filter/limit semantics, since
# both surfaces wrap ``core.audit_log.read_entries`` identically.


def test_audit_empty_prints_notice(tmp_path, monkeypatch):
    """Empty/missing audit log prints the empty-state notice, exit 0."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0
    assert "no audit entries" in result.stdout.lower()


def test_audit_empty_json_returns_empty_list(tmp_path, monkeypatch):
    """``--json`` with no entries prints an empty JSON array."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    result = runner.invoke(app, ["audit", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_audit_json_returns_serialized_entries(tmp_path, monkeypatch):
    """Populated audit log serializes to a list of JSON-safe entry dicts."""
    from llauncher.core import audit_log

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    audit_log.record(
        audit_log.AuditAction.STARTED,
        audit_log.AuditResult.SUCCESS,
        caller="test",
        port=8080,
        model="m",
        message="started m",
    )
    audit_log.record(
        audit_log.AuditAction.STOPPED,
        audit_log.AuditResult.SUCCESS,
        caller="test",
        port=8080,
        model="m",
        message="stopped m",
    )

    result = runner.invoke(app, ["audit", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 2
    assert data[0]["action"] == "started"
    assert data[0]["result"] == "success"
    assert data[1]["action"] == "stopped"
    assert data[0]["message"] == "started m"
    assert data[1]["message"] == "stopped m"


def test_audit_table_renders_entries(tmp_path, monkeypatch):
    """Default (non-JSON) rendering prints a table with the entry fields."""
    from llauncher.core import audit_log

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    audit_log.record(
        audit_log.AuditAction.STARTED,
        audit_log.AuditResult.SUCCESS,
        caller="cli",
        port=8080,
        model="m",
        message="started m",
    )

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0
    assert "started" in result.stdout
    assert "success" in result.stdout
    assert "8080" in result.stdout


def test_audit_action_filter(tmp_path, monkeypatch):
    """``--action`` narrows the result to entries with that action."""
    from llauncher.core import audit_log

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t")
    audit_log.record(audit_log.AuditAction.STOPPED, audit_log.AuditResult.SUCCESS, caller="t")

    result = runner.invoke(app, ["audit", "--action", "stopped", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["action"] == "stopped"


def test_audit_result_filter(tmp_path, monkeypatch):
    """``--result`` narrows the result to entries with that result."""
    from llauncher.core import audit_log

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t")
    audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.ERROR, caller="t")

    result = runner.invoke(app, ["audit", "--result", "error", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["result"] == "error"


def test_audit_limit_bounds_tail(tmp_path, monkeypatch):
    """``--limit`` caps the number of entries returned to the newest N."""
    from llauncher.core import audit_log

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    for i in range(5):
        audit_log.record(
            audit_log.AuditAction.STARTED,
            audit_log.AuditResult.SUCCESS,
            caller="t",
            message=f"entry-{i}",
        )

    result = runner.invoke(app, ["audit", "--limit", "2", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 2
    assert data[0]["message"] == "entry-3"
    assert data[1]["message"] == "entry-4"


# ---------------------------------------------------------------------------
# Issue #471: encoding-safe console output on consoles that can't encode unicode
# ---------------------------------------------------------------------------
#
# On a cp1252 (or otherwise non-UTF-8) console, printing a character the
# console cannot encode raises ``UnicodeEncodeError`` deep inside rich's
# Windows console handling -- *after* the underlying operation (e.g. ``server
# start``) already succeeded, so the crash reads as a phantom failure.
#
# Review follow-up on this PR: the original fix whitelisted only the two
# glyphs the traceback happened to show, which left ``server swap``'s SUCCESS
# message crashing on its U+2192. The guarantee now lives at a single print
# boundary (``cli._emit`` / ``cli._ascii_safe``) so text minted anywhere --
# operations layer included -- is downgraded before it reaches the terminal.
#
# ``CliRunner(charset="cp1252")`` is what makes these fail-pre-fix: click
# builds ``sys.stdout`` with strict errors under it (only ``<stderr>`` gets
# ``backslashreplace``), so an un-encodable character really does raise.

def test_glyph_falls_back_to_ascii_on_cp1252_console(monkeypatch):
    """``_glyph`` returns ASCII when the console can't encode the glyph."""
    monkeypatch.setattr(cli.console, "_file", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    assert cli._glyph(True) == "OK"
    assert cli._glyph(False) == "X"


def test_glyph_uses_unicode_on_utf8_console(monkeypatch):
    """``_glyph`` keeps the unicode glyph when the console can encode it."""
    monkeypatch.setattr(cli.console, "_file", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))

    assert cli._glyph(True) == "✓"
    assert cli._glyph(False) == "✗"


def test_ascii_safe_transliterates_arrow_on_cp1252_console(monkeypatch):
    """The seam downgrades *any* un-encodable character, not just check/cross.

    U+2192 is the review blocker's exact character: ``operations.swap`` mints
    it into its **success** message, so a glyph whitelist never sees it.
    """
    monkeypatch.setattr(cli.console, "_file", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    assert cli._ascii_safe("Swapped a → b on port 9999") == "Swapped a -> b on port 9999"
    # An un-mapped character still degrades rather than raising.
    assert cli._ascii_safe("中") == "?"
    # Characters cp1252 *can* encode survive untouched (em dash is 0x97 there).
    assert cli._ascii_safe("a — b") == "a — b"


def test_ascii_safe_is_identity_on_utf8_console(monkeypatch):
    """A UTF-8 console keeps the real characters."""
    monkeypatch.setattr(cli.console, "_file", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))

    assert cli._ascii_safe("Swapped a → b") == "Swapped a → b"


def test_ascii_safe_passes_through_unknown_codec(monkeypatch):
    """An unrecognised codec name is not a reason to mangle output."""
    class _Weird:
        encoding = "not-a-real-codec"

    monkeypatch.setattr(cli.console, "_file", _Weird())

    assert cli._ascii_safe("Swapped a → b") == "Swapped a → b"


def test_config_validate_success_exits_zero_under_cp1252_stdout(mock_config_store):
    """A *success* path that actually prints a glyph must not crash on cp1252.

    ``config validate``'s success line is ``[green]{_glyph(True)}[/green] ...``
    (``cli.py``), so this is fail-pre-fix in a way the old ``server start``
    success test was not: that path prints only the operation's message and
    never emitted a glyph at all.
    """
    _dir, _path = mock_config_store

    fake_model_path = str(_dir / "real_model.gguf")
    _dir.mkdir(parents=True, exist_ok=True)
    Path(fake_model_path).touch()

    cfg = ModelConfig.from_dict_unvalidated({
        "name": "valid-model",
        "model_path": fake_model_path,
    })
    ConfigStore.add_model(cfg)

    cp1252_runner = CliRunner(charset="cp1252")
    result = cp1252_runner.invoke(app, ["config", "validate", "valid-model"])

    assert result.exception is None
    assert result.exit_code == 0
    assert "OK" in result.stdout
    assert "valid" in result.stdout.lower()

    Path(fake_model_path).unlink(missing_ok=True)


def test_server_swap_success_exits_zero_under_cp1252_stdout(mock_config_store):
    """``server swap``'s SUCCESS message carries U+2192 from the operations layer.

    ``operations/swap.py`` mints ``f"Swapped {prev} -> {new} on port {port}"``
    with a real U+2192, and ``cli.py`` prints it verbatim. cp1252 cannot encode
    that code point and rich does not transliterate arbitrary text
    (``ascii_only`` only substitutes box/spinner glyphs), so before the ``_emit``
    seam this raised ``UnicodeEncodeError`` *after* the swap had already
    succeeded -- the exact phantom failure #471 exists to kill.
    """
    from llauncher.operations import SwapResult

    cp1252_runner = CliRunner(charset="cp1252")

    with patch("llauncher.core.delegation.should_delegate", return_value=False):
        with patch("llauncher.operations.swap") as mock_swap:
            mock_swap.return_value = SwapResult(
                success=True,
                action="swapped",
                port_state="serving",
                port=9999,
                model="new-model",
                previous_model="old-model",
                pid=42,
                message="Swapped old-model → new-model on port 9999",
            )

            result = cp1252_runner.invoke(
                app, ["server", "swap", "new-model", "--port", "9999"]
            )

    assert not isinstance(result.exception, UnicodeEncodeError)
    assert result.exception is None
    assert result.exit_code == 0
    assert "Swapped old-model -> new-model on port 9999" in result.stdout


def test_server_swap_delegated_success_exits_zero_under_cp1252_stdout(mock_config_store):
    """The delegated branch prints the agent's message verbatim too."""
    cp1252_runner = CliRunner(charset="cp1252")

    fake_node = MagicMock()
    fake_node.swap_server.return_value = {
        "success": True,
        "message": "Swapped old-model → new-model on port 9999",
    }

    with patch("llauncher.core.delegation.should_delegate", return_value=True):
        with patch("llauncher.remote.node.local_agent_node", return_value=fake_node):
            result = cp1252_runner.invoke(
                app, ["server", "swap", "new-model", "--port", "9999"]
            )

    assert not isinstance(result.exception, UnicodeEncodeError)
    assert result.exit_code == 0
    assert "->" in result.stdout


def test_no_unguarded_non_ascii_string_literals_in_cli_module():
    """Lock the seam in: no runtime string in ``cli.py`` may carry a raw glyph.

    Two literals are legitimately non-ASCII and both are *inputs to the
    downgrade*, never output: ``_TRANSLITERATIONS``' keys, and the check/cross
    pair ``_glyph`` hands to ``_ascii_safe``. Anything else -- a 9th glyph site
    added later, a message f-string with an arrow in it -- fails here, which is
    what the review blocker on this PR would have tripped had the message been
    minted in ``cli.py`` rather than in ``operations/swap.py``.

    Docstrings are exempt: typer renders them as ``--help`` text through
    click, not through ``_emit``, and they are outside this bug's boundary.
    """
    import ast

    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    exempt: set[int] = set()

    for node in ast.walk(tree):
        # ``_TRANSLITERATIONS = {...}`` -- the map of what gets downgraded.
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_TRANSLITERATIONS"
            for t in node.targets
        ):
            exempt.update(map(id, ast.walk(node)))
        # ``_ascii_safe("...")`` -- a literal explicitly routed through the seam.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_ascii_safe"
        ):
            for arg in node.args:
                exempt.update(map(id, ast.walk(arg)))
        # Docstrings.
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                exempt.add(id(node.body[0].value))

    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in exempt
        and not node.value.isascii()
    ]

    assert offenders == [], (
        "non-ASCII string literals in llauncher/cli.py must route through "
        f"_ascii_safe/_TRANSLITERATIONS: {offenders}"
    )


def test_operations_result_messages_route_through_the_emit_seam(monkeypatch):
    """A generalisation of the blocker: operations-minted text is safe to print.

    The result messages this CLI prints are produced elsewhere (``operations/``,
    ``core/state``) and can contain any character. The guarantee is not that
    those layers stay ASCII -- it is that ``_emit`` downgrades whatever they
    hand over.
    """
    raw = io.BytesIO()
    buf = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    monkeypatch.setattr(cli.console, "_file", buf)

    cli._emit(cli._color("Swapped a → b on port 9999", "running"))
    cli._emit("[green]plain → markup[/green]")
    buf.flush()

    out = raw.getvalue().decode("cp1252")
    assert "Swapped a -> b on port 9999" in out
    assert "plain -> markup" in out
    assert "→" not in out
