"""Extended unit tests for ``llauncher.state.LauncherState`` legacy helpers.

Targets uncovered branches:
- ``start_with_eviction_compat`` legacy tuple-return wrapper (lines 563-598)
- ``record_action`` audit entry path
- ``get_model_status`` running/stopped/unknown branches
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from llauncher.state import LauncherState, EvictionResult
from llauncher.models.config import ModelConfig, RunningServer


@pytest.fixture
def state(tmp_path):
    """Bare LauncherState with mocked rules and one model installed."""
    (tmp_path / "m.gguf").write_text("mock")
    s = LauncherState.__new__(LauncherState)
    s.models = {}
    s.running = {}
    s.audit = []
    s.rules = MagicMock()
    s.rules.validate_start.return_value = (True, "OK")
    s.rules.validate_stop.return_value = (True, "OK")
    s.models["m"] = ModelConfig.from_dict_unvalidated({
        "name": "m",
        "model_path": str(tmp_path / "m.gguf"),
    })
    return s


class TestStartWithEvictionCompat:
    def test_success_returns_true_message(self, state):
        fake = EvictionResult(
            success=True,
            port_state="serving",
            error="",
            new_model_attempted="m",
            previous_model="",
        )
        with patch.object(state, "_start_with_eviction_impl", return_value=fake):
            ok, msg = state.start_with_eviction_compat("m", 8081, caller="cli")
        assert ok is True
        assert "8081" in msg

    def test_failure_returns_false_error(self, state):
        fake = EvictionResult(
            success=False,
            port_state="unavailable",
            error="kaboom",
        )
        with patch.object(state, "_start_with_eviction_impl", return_value=fake):
            ok, msg = state.start_with_eviction_compat("m", 8081, caller="cli")
        assert ok is False
        assert msg == "kaboom"

    def test_rolled_back_appends_restored_model(self, state):
        fake = EvictionResult(
            success=False,
            port_state="restored",
            error="boom",
            rolled_back=True,
            restored_model="old-m",
            previous_model="old-m",
        )
        with patch.object(state, "_start_with_eviction_impl", return_value=fake):
            ok, msg = state.start_with_eviction_compat("m", 8081, caller="cli")
        assert ok is False
        assert "rolled back to old-m" in msg

    def test_start_with_eviction_alias_points_to_compat(self, state):
        """Backward-compat alias must resolve to the same callable."""
        assert state.start_with_eviction.__func__ is state.start_with_eviction_compat.__func__


class TestRecordAction:
    def test_record_action_appends_audit_entry(self, state):
        state.record_action("start", "m", "cli", "success", "boot ok")
        assert len(state.audit) == 1
        entry = state.audit[0]
        assert entry.action == "start"
        assert entry.model == "m"
        assert entry.result == "success"
        assert entry.message == "boot ok"

    def test_record_action_default_message_none(self, state):
        state.record_action("stop", "m", "cli", "error")
        assert state.audit[0].message is None


class TestGetModelStatus:
    def test_unknown_model(self, state):
        out = state.get_model_status("does-not-exist")
        assert out == {"status": "unknown", "message": "Model not found"}

    def test_stopped_model(self, state):
        assert state.get_model_status("m") == {"status": "stopped"}

    def test_running_model_reports_port_and_pid(self, state):
        state.running[8081] = RunningServer(
            pid=4321,
            port=8081,
            config_name="m",
            start_time=datetime.now(),
        )
        out = state.get_model_status("m")
        assert out == {"status": "running", "port": 8081, "pid": 4321}
