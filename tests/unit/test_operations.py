"""Unit tests for ``llauncher.operations`` (the v2 tool layer).

Per ADR-008 and ADR-010. Verifies start/stop verb semantics, lockfile
reconciliation behavior, and audit-log discipline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher import operations as ops
from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.models.config import ModelConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect lockfile writes to a tmp dir and inject the path into reads."""
    target = tmp_path / "run"
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", target)
    return target


@pytest.fixture
def audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect audit-log writes to a tmp file."""
    target = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", target)
    return target


@pytest.fixture
def sample_config() -> ModelConfig:
    return ModelConfig.from_dict_unvalidated(
        {
            "name": "mistral-7b",
            "model_path": "/fake/path/model.gguf",
            "n_gpu_layers": 255,
            "ctx_size": 4096,
        }
    )


@pytest.fixture
def mock_popen() -> MagicMock:
    """Stand-in for the Popen object returned by ``proc.start_server``."""
    p = MagicMock()
    p.pid = 99999
    p.terminate = MagicMock()
    return p


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_on_empty_port(
    run_dir: Path, audit_path: Path, sample_config: ModelConfig, mock_popen: MagicMock
) -> None:
    with patch("llauncher.operations.ConfigStore.get_model", return_value=sample_config), \
         patch("llauncher.operations.proc.start_server", return_value=mock_popen):
        result = ops.start("mistral-7b", 8081, caller="test")

    assert result.success is True
    assert result.action == "started"
    assert result.port == 8081
    assert result.model == "mistral-7b"
    assert result.pid == 99999

    # Lockfile written.
    written = lf.read_lockfile(8081, run_dir=run_dir)
    assert written is not None
    assert written.model == "mistral-7b"
    assert written.pid == 99999

    # Audit logged as commanded `started` with success.
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.STARTED
    assert entries[0].result == AuditResult.SUCCESS
    assert entries[0].port == 8081
    assert entries[0].model == "mistral-7b"


def test_start_idempotent_when_same_model_running(
    run_dir: Path, audit_path: Path, sample_config: ModelConfig
) -> None:
    # Pre-existing lockfile claims our pid is alive (use os.getpid).
    import os

    lf.write_lockfile(8081, "mistral-7b", os.getpid(), run_dir=run_dir)

    with patch("llauncher.operations.ConfigStore.get_model", return_value=sample_config), \
         patch("llauncher.operations.proc.start_server") as start_proc:
        result = ops.start("mistral-7b", 8081, caller="test")

    assert result.success is True
    assert result.action == "already_running"
    assert result.model == "mistral-7b"
    assert result.pid == os.getpid()
    # Process should NOT be launched.
    start_proc.assert_not_called()

    # No audit entry on idempotent no-op start (caller's intent already met).
    entries = al.read_entries(path=audit_path)
    assert entries == []


def test_start_rejected_when_different_model_running(
    run_dir: Path, audit_path: Path
) -> None:
    import os

    # Port 8081 is occupied by llama-3, alive.
    lf.write_lockfile(8081, "llama-3", os.getpid(), run_dir=run_dir)

    with patch("llauncher.operations.proc.start_server") as start_proc:
        result = ops.start("mistral-7b", 8081, caller="test")

    assert result.success is False
    assert result.action == "rejected_occupied"
    assert result.model == "llama-3"  # the occupant
    assert result.pid == os.getpid()
    start_proc.assert_not_called()

    # Audit entry shows REJECTED_OCCUPIED.
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.STARTED
    assert entries[0].result == AuditResult.REJECTED_OCCUPIED
    assert entries[0].from_model == "llama-3"
    assert entries[0].model == "mistral-7b"


def test_start_reconciles_stale_lockfile(
    run_dir: Path,
    audit_path: Path,
    sample_config: ModelConfig,
    mock_popen: MagicMock,
) -> None:
    # Lockfile claims a long-dead pid.
    lf.write_lockfile(8081, "old-model", 2**31 - 1, run_dir=run_dir)

    with patch("llauncher.operations.ConfigStore.get_model", return_value=sample_config), \
         patch("llauncher.operations.proc.start_server", return_value=mock_popen):
        result = ops.start("mistral-7b", 8081, caller="test")

    assert result.success is True
    assert result.action == "started"

    # New lockfile reflects the new claim.
    written = lf.read_lockfile(8081, run_dir=run_dir)
    assert written is not None
    assert written.model == "mistral-7b"
    assert written.pid == 99999

    # Audit log: observed_stopped (cleanup) followed by started.
    entries = al.read_entries(path=audit_path)
    actions = [e.action for e in entries]
    assert AuditAction.OBSERVED_STOPPED in actions
    assert AuditAction.STARTED in actions


def test_start_errors_when_model_not_in_config(
    run_dir: Path, audit_path: Path
) -> None:
    with patch("llauncher.operations.ConfigStore.get_model", return_value=None), \
         patch("llauncher.operations.proc.start_server") as start_proc:
        result = ops.start("ghost-model", 8081, caller="test")

    assert result.success is False
    assert result.action == "error"
    assert "not found" in result.message.lower()
    start_proc.assert_not_called()

    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].result == AuditResult.ERROR


def test_start_errors_when_process_launch_fails(
    run_dir: Path, audit_path: Path, sample_config: ModelConfig
) -> None:
    with patch("llauncher.operations.ConfigStore.get_model", return_value=sample_config), \
         patch(
             "llauncher.operations.proc.start_server",
             side_effect=FileNotFoundError("binary missing"),
         ):
        result = ops.start("mistral-7b", 8081, caller="test")

    assert result.success is False
    assert result.action == "error"

    # No lockfile was written.
    assert lf.read_lockfile(8081, run_dir=run_dir) is None

    # Audit entry is STARTED with ERROR result.
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.STARTED
    assert entries[0].result == AuditResult.ERROR


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_on_empty_port(run_dir: Path, audit_path: Path) -> None:
    result = ops.stop(8081, caller="test")

    assert result.success is True
    assert result.action == "already_empty"
    assert result.model is None

    # No audit entry on a true no-op stop.
    assert al.read_entries(path=audit_path) == []


def test_stop_running_server(run_dir: Path, audit_path: Path) -> None:
    import os

    lf.write_lockfile(8081, "mistral-7b", os.getpid(), run_dir=run_dir)

    with patch("llauncher.operations.proc.stop_server_by_port", return_value=True) as stop_proc:
        result = ops.stop(8081, caller="test")

    assert result.success is True
    assert result.action == "stopped"
    assert result.model == "mistral-7b"
    stop_proc.assert_called_once_with(8081)

    # Lockfile removed.
    assert lf.read_lockfile(8081, run_dir=run_dir) is None

    # Audit entry: STOPPED + SUCCESS.
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.STOPPED
    assert entries[0].result == AuditResult.SUCCESS
    assert entries[0].model == "mistral-7b"


def test_stop_with_stale_lockfile(run_dir: Path, audit_path: Path) -> None:
    # Dead pid claimed in lockfile.
    lf.write_lockfile(8081, "ghost", 2**31 - 1, run_dir=run_dir)

    with patch("llauncher.operations.proc.stop_server_by_port") as stop_proc:
        result = ops.stop(8081, caller="test")

    assert result.success is True
    assert result.action == "already_empty"
    assert result.model == "ghost"
    # Process termination shouldn't be called for a dead pid.
    stop_proc.assert_not_called()

    # Lockfile cleaned up.
    assert lf.read_lockfile(8081, run_dir=run_dir) is None

    # Audit shows OBSERVED_STOPPED (not commanded STOPPED).
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.OBSERVED_STOPPED


def test_stop_when_termination_fails(run_dir: Path, audit_path: Path) -> None:
    import os

    lf.write_lockfile(8081, "mistral-7b", os.getpid(), run_dir=run_dir)

    with patch("llauncher.operations.proc.stop_server_by_port", return_value=False):
        result = ops.stop(8081, caller="test")

    assert result.success is False
    assert result.action == "error"
    # Lockfile NOT removed on error — operator may need to investigate.
    assert lf.read_lockfile(8081, run_dir=run_dir) is not None

    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.STOPPED
    assert entries[0].result == AuditResult.ERROR


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------


def test_start_result_to_dict_envelope() -> None:
    result = ops.StartResult(
        success=True,
        action="started",
        port=8081,
        model="mistral-7b",
        pid=12345,
        message="ok",
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["action"] == "started"
    assert d["port"] == 8081
    assert d["model"] == "mistral-7b"
    assert d["pid"] == 12345
    assert d["message"] == "ok"


def test_stop_result_to_dict_envelope() -> None:
    result = ops.StopResult(
        success=True,
        action="stopped",
        port=8081,
        model="mistral-7b",
        pid=12345,
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["action"] == "stopped"
    assert d["port"] == 8081
