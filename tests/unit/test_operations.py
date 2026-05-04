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


# ---------------------------------------------------------------------------
# swap fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def marker_run_dir(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect marker reads/writes to the same tmp run dir as lockfiles."""
    monkeypatch.setattr("llauncher.core.marker.LAUNCHER_RUN_DIR", run_dir)
    return run_dir


# Opt-out kwargs for tests that synthesize fake-path ModelConfigs and
# don't want swap()'s default model-health / VRAM pre-flight to reject.
# Tests that exercise pre-flight rejection paths pass their own callables
# explicitly instead of using this.
_NO_PREFLIGHT = {"model_health_check": None, "vram_check": None}


def _make_config(name: str, **overrides) -> ModelConfig:
    base = {
        "name": name,
        "model_path": f"/fake/path/{name}.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    }
    base.update(overrides)
    return ModelConfig.from_dict_unvalidated(base)


def _config_lookup(*configs: ModelConfig):
    """Build a ``side_effect`` callable for ``ConfigStore.get_model`` patches."""
    by_name = {c.name: c for c in configs}

    def side_effect(name: str):
        return by_name.get(name)

    return side_effect


# ---------------------------------------------------------------------------
# swap — pre-flight rejections
# ---------------------------------------------------------------------------


def test_swap_on_empty_port(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_empty"
    assert result.port_state == "unchanged"
    assert result.model is None

    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.SWAPPED
    assert entries[0].result == AuditResult.REJECTED_EMPTY


def test_swap_with_stale_lockfile_treated_as_empty(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    # Dead pid in lockfile.
    lf.write_lockfile(8081, "ghost", 2**31 - 1, run_dir=run_dir)

    result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_empty"
    # Stale lockfile cleaned up.
    assert lf.read_lockfile(8081, run_dir=run_dir) is None

    actions = [(e.action, e.result) for e in al.read_entries(path=audit_path)]
    assert (AuditAction.OBSERVED_STOPPED, AuditResult.SUCCESS) in actions
    assert (AuditAction.SWAPPED, AuditResult.REJECTED_EMPTY) in actions


def test_swap_same_model_short_circuits_already_running(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "mistral-7b", os.getpid(), run_dir=run_dir)

    with patch("llauncher.operations.proc.stop_server_by_port") as stop_proc, \
         patch("llauncher.operations.proc.start_server") as start_proc:
        result = ops.swap("mistral-7b", 8081, caller="test")

    assert result.success is True
    assert result.action == "already_running"
    assert result.port_state == "serving"
    assert result.model == "mistral-7b"
    assert result.previous_model == "mistral-7b"
    assert result.pid == os.getpid()
    # No teardown / relaunch on same-model swap.
    stop_proc.assert_not_called()
    start_proc.assert_not_called()
    # Marker was taken and released — no stray file left over.
    assert (run_dir / "8081.swap").exists() is False
    # Idempotent no-op: no audit entries.
    assert al.read_entries(path=audit_path) == []


def test_swap_rejects_when_new_model_not_in_config(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old")),
    ):
        result = ops.swap("ghost-model", 8081, caller="test")

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert result.port_state == "unchanged"
    assert result.model == "old"  # unchanged

    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.SWAPPED
    assert entries[0].result == AuditResult.REJECTED_PREFLIGHT


def test_swap_rejects_when_old_model_config_missing(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """Lockfile claims X but config X has been deleted — rollback impossible."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    # Only the new model is present in config (old is missing).
    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("new-model")),
    ):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert "rollback would be impossible" in result.message.lower()


def test_swap_rejects_when_model_health_check_fails(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ):
        result = ops.swap(
            "new-model",
            8081,
            caller="test",
            model_health_check=lambda cfg: (False, "file missing"),
        )

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert "file missing" in result.message.lower()


def test_swap_rejects_when_vram_check_fails(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ):
        result = ops.swap(
            "new-model",
            8081,
            caller="test",
            model_health_check=None,  # opt out so the vram check is reached
            vram_check=lambda cfg: (False, "insufficient headroom"),
        )

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert "insufficient headroom" in result.message.lower()


def test_swap_health_check_exception_treated_as_failure(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    def boom(cfg):
        raise RuntimeError("collector exploded")

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ):
        result = ops.swap(
            "new-model", 8081, caller="test", model_health_check=boom
        )

    assert result.success is False
    assert result.action == "rejected_preflight"


# ---------------------------------------------------------------------------
# swap — in-flight marker
# ---------------------------------------------------------------------------


def test_swap_rejects_when_marker_already_present(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os
    from llauncher.core import marker as mk

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)
    # A live marker (current pid as holder).
    mk.take_marker(
        8081, caller="other", from_model="old", to_model="other-model"
    )

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port") as stop_proc:
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_in_progress"
    # Stop must not run when an in-flight marker exists.
    stop_proc.assert_not_called()
    # The original marker is still there (live holder).
    assert (run_dir / "8081.swap").exists()


def test_swap_clears_stale_marker_then_rejects(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """Stale marker (dead holder) is reconciled away, but this call still rejects.

    Caller is expected to retry on a follow-up call. This avoids a swap
    silently jumping the queue mid-call.
    """
    import os
    import json
    from llauncher.core import marker as mk

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    # Hand-craft a stale marker (dead llauncher_pid).
    run_dir.mkdir(parents=True, exist_ok=True)
    stale = mk.SwapMarker(
        port=8081,
        caller="defunct",
        started_at="2026-05-02T14:30:00+00:00",
        llauncher_pid=2**31 - 1,
        from_model="old",
        to_model="other",
    )
    (run_dir / "8081.swap").write_text(json.dumps(stale.to_dict()))

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_in_progress"
    # Stale marker was cleaned up; subsequent retry will be free to proceed.
    assert (run_dir / "8081.swap").exists() is False

    actions = [(e.action, e.result) for e in al.read_entries(path=audit_path)]
    assert (AuditAction.SWAP_ABORTED, AuditResult.SUCCESS) in actions
    assert (AuditAction.SWAPPED, AuditResult.REJECTED_IN_PROGRESS) in actions


# ---------------------------------------------------------------------------
# swap — phase 3 (stop) failure
# ---------------------------------------------------------------------------


def test_swap_rejected_when_stop_fails(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=False), \
         patch("llauncher.operations.proc.start_server") as start_proc:
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rejected_stop_failed"
    assert result.port_state == "unchanged"
    assert result.model == "old"  # old still running
    # New model never launched.
    start_proc.assert_not_called()
    # Old lockfile preserved (we never reached lf.remove_lockfile).
    assert lf.read_lockfile(8081, run_dir=run_dir) is not None
    # Marker released.
    assert (run_dir / "8081.swap").exists() is False

    entries = al.read_entries(path=audit_path)
    assert any(
        e.action == AuditAction.SWAPPED and e.result == AuditResult.REJECTED_STOP_FAILED
        for e in entries
    )


# ---------------------------------------------------------------------------
# swap — happy path
# ---------------------------------------------------------------------------


def test_swap_full_success(
    run_dir: Path, marker_run_dir: Path, audit_path: Path, mock_popen: MagicMock
) -> None:
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", return_value=mock_popen), \
         patch(
             "llauncher.operations.proc.wait_for_server_ready",
             return_value=(True, ["loading", "listening"]),
         ):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is True
    assert result.action == "swapped"
    assert result.port_state == "serving"
    assert result.model == "new-model"
    assert result.previous_model == "old"
    assert result.pid == 99999

    # Lockfile reflects the new claim.
    written = lf.read_lockfile(8081, run_dir=run_dir)
    assert written is not None
    assert written.model == "new-model"
    assert written.pid == 99999
    # Marker released.
    assert (run_dir / "8081.swap").exists() is False

    actions = [(e.action, e.result) for e in al.read_entries(path=audit_path)]
    # Three commanded events: STOPPED, STARTED, SWAPPED.
    assert (AuditAction.STOPPED, AuditResult.SUCCESS) in actions
    assert (AuditAction.STARTED, AuditResult.SUCCESS) in actions
    assert (AuditAction.SWAPPED, AuditResult.SUCCESS) in actions


# ---------------------------------------------------------------------------
# swap — rollback paths
# ---------------------------------------------------------------------------


def test_swap_rollback_on_phase4_launch_failure(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """New model's process never starts; rollback restores the old model."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    new_popen = MagicMock(); new_popen.pid = 0
    rollback_popen = MagicMock(); rollback_popen.pid = 77777

    # First start_server raises (new model launch fails); second succeeds (rollback).
    start_calls = [FileNotFoundError("binary missing"), rollback_popen]

    def start_side_effect(*args, **kwargs):
        nxt = start_calls.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", side_effect=start_side_effect), \
         patch("llauncher.operations.proc.wait_for_server_ready", return_value=(True, [])):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rolled_back"
    assert result.port_state == "restored"
    assert result.model == "old"
    assert result.previous_model == "old"
    assert result.pid == 77777

    written = lf.read_lockfile(8081, run_dir=run_dir)
    assert written is not None
    assert written.model == "old"
    # Marker released.
    assert (run_dir / "8081.swap").exists() is False

    actions = [(e.action, e.result) for e in al.read_entries(path=audit_path)]
    assert (AuditAction.SWAPPED, AuditResult.ROLLED_BACK) in actions


def test_swap_rollback_on_readiness_timeout(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """New model starts but never reaches ready; rollback restores the old."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    new_popen = MagicMock(); new_popen.pid = 88888
    rollback_popen = MagicMock(); rollback_popen.pid = 77777

    # Two readiness polls: first (new) times out, second (rollback) succeeds.
    ready_returns = [(False, ["timeout"]), (True, ["ready"])]

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch(
             "llauncher.operations.proc.start_server",
             side_effect=[new_popen, rollback_popen],
         ), \
         patch("llauncher.operations.proc.wait_for_server_ready", side_effect=ready_returns), \
         patch("llauncher.operations.proc.stop_server_by_pid"):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "rolled_back"
    assert result.model == "old"

    written = lf.read_lockfile(8081, run_dir=run_dir)
    assert written is not None
    assert written.model == "old"


def test_swap_failed_when_rollback_also_fails(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """Both new launch and rollback launch fail — port is dead."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch(
             "llauncher.operations.proc.start_server",
             side_effect=FileNotFoundError("binary gone"),
         ):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "failed"
    assert result.port_state == "unavailable"
    assert result.model is None
    assert result.previous_model == "old"
    assert "manual intervention" in result.message.lower()

    # Lockfile cleared (no model is on the port).
    assert lf.read_lockfile(8081, run_dir=run_dir) is None
    # Marker released.
    assert (run_dir / "8081.swap").exists() is False

    actions = [(e.action, e.result) for e in al.read_entries(path=audit_path)]
    assert (AuditAction.SWAPPED, AuditResult.UNAVAILABLE) in actions


# ---------------------------------------------------------------------------
# swap — marker lifecycle invariants
# ---------------------------------------------------------------------------


def test_swap_uses_snapshot_config_for_rollback(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """A mid-swap config edit doesn't poison rollback (ADR-011 §Rollback)."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    snapshot_old = _make_config("old", ctx_size=4096)
    edited_old = _make_config("old", ctx_size=999999)  # mid-swap edit
    new_config = _make_config("new-model")

    # Track the order of get_model calls.
    call_log: list[str] = []

    def get_model(name: str):
        call_log.append(name)
        # First call for "old" gets the snapshot; later calls (if any) would
        # reflect the edit. We assert below that the rollback launches
        # against the snapshot, not a fresh re-read.
        if name == "old":
            return snapshot_old if call_log.count("old") == 1 else edited_old
        if name == "new-model":
            return new_config
        return None

    captured_configs: list[ModelConfig] = []

    def start_server(config, port, server_bin=None):
        captured_configs.append(config)
        if config.name == "new-model":
            raise FileNotFoundError("nope")
        # Rollback launch — return a popen-shaped mock.
        m = MagicMock(); m.pid = 77777
        return m

    with patch("llauncher.operations.ConfigStore.get_model", side_effect=get_model), \
         patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", side_effect=start_server), \
         patch("llauncher.operations.proc.wait_for_server_ready", return_value=(True, [])):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.action == "rolled_back"
    # The rollback should have used snapshot_old (ctx_size=4096), not the edit.
    rollback_launches = [c for c in captured_configs if c.name == "old"]
    assert len(rollback_launches) == 1
    assert rollback_launches[0].ctx_size == 4096


def test_swap_result_to_dict_envelope() -> None:
    result = ops.SwapResult(
        success=True,
        action="swapped",
        port_state="serving",
        port=8081,
        model="new",
        previous_model="old",
        pid=12345,
        message="ok",
        startup_logs=["a", "b"],
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["action"] == "swapped"
    assert d["port_state"] == "serving"
    assert d["port"] == 8081
    assert d["model"] == "new"
    assert d["previous_model"] == "old"
    assert d["pid"] == 12345
    assert d["startup_logs"] == ["a", "b"]


# ---------------------------------------------------------------------------
# swap — default pre-flight adapters wired (slice 2)
# ---------------------------------------------------------------------------


def test_swap_default_model_health_check_rejects_when_file_invalid(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """The default model-health adapter is invoked when no override is given."""
    import os
    from llauncher.core.model_health import ModelHealthResult

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    invalid = ModelHealthResult(valid=False, exists=False, reason="not found")

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=invalid,
    ), patch(
        # VRAM check would also fire on the real path; force it to pass so
        # the rejection comes specifically from model-health.
        "llauncher.operations.preflight.gpu_mod.GPUHealthCollector.get_health",
        return_value={"backends": [], "devices": []},
    ):
        result = ops.swap("new-model", 8081, caller="test")

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert "not found" in result.message.lower()


def test_swap_default_vram_check_rejects_when_insufficient(
    run_dir: Path, marker_run_dir: Path, audit_path: Path
) -> None:
    """The default VRAM adapter is invoked when no override is given."""
    import os
    from llauncher.core.model_health import ModelHealthResult

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    healthy = ModelHealthResult(valid=True, exists=True, readable=True)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(
            _make_config("old"),
            _make_config("llama-70b", model_path="/m/llama-70b.gguf"),
        ),
    ), patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=healthy,
    ), patch(
        "llauncher.operations.preflight.gpu_mod.GPUHealthCollector.get_health",
        return_value={
            "backends": ["nvidia"],
            "devices": [{"index": 0, "name": "RTX 4090", "free_vram_mb": 24000}],
        },
    ):
        result = ops.swap("llama-70b", 8081, caller="test")

    assert result.success is False
    assert result.action == "rejected_preflight"
    assert "vram" in result.message.lower()


def test_swap_default_preflight_proceeds_when_both_pass(
    run_dir: Path,
    marker_run_dir: Path,
    audit_path: Path,
    mock_popen: MagicMock,
) -> None:
    """End-to-end swap with default adapters wired: both pass, swap succeeds."""
    import os
    from llauncher.core.model_health import ModelHealthResult

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)

    healthy = ModelHealthResult(valid=True, exists=True, readable=True)

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=healthy,
    ), patch(
        "llauncher.operations.preflight.gpu_mod.GPUHealthCollector.get_health",
        return_value={
            "backends": ["nvidia"],
            "devices": [{"index": 0, "name": "big", "free_vram_mb": 24000}],
        },
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", return_value=mock_popen), \
         patch(
             "llauncher.operations.proc.wait_for_server_ready",
             return_value=(True, ["ready"]),
         ):
        # Note: no _NO_PREFLIGHT — exercising the real default adapter chain.
        result = ops.swap("new-model", 8081, caller="test")

    assert result.success is True
    assert result.action == "swapped"


def test_startup_logs_capped_at_max(
    run_dir: Path, marker_run_dir: Path, audit_path: Path, mock_popen: MagicMock
) -> None:
    """Startup logs are capped (ADR-011 open question 2 — preserve ADR-002 cap)."""
    import os

    lf.write_lockfile(8081, "old", os.getpid(), run_dir=run_dir)
    long_logs = [f"line {i}" for i in range(500)]

    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", return_value=mock_popen), \
         patch(
             "llauncher.operations.proc.wait_for_server_ready",
             return_value=(True, long_logs),
         ):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert len(result.startup_logs) == ops.STARTUP_LOG_TAIL_MAX
    # Tail preserved.
    assert result.startup_logs[-1] == "line 499"
