"""CORE-RUNTIME coverage close-out — targeted hits on remaining misses.

Coverage-driven sweep (not a behavior pin) for the CORE-RUNTIME cluster.
Each test names the missing-line range it exercises in its docstring.
Companion to ``test_phase_d_coverage.py``; principle: *every uncovered
line is covered or carries an explicit justification.*

Modules:

- ``llauncher/core/process.py``: default/preferred port selection
  (61/66 — covered by reviving the shadowed ``TestFindAvailablePort`` in
  ``test_process.py``), ``_newest_log`` stat-OSError fallback (248-249),
  ``start_server`` log-open OSError re-raise (322-323),
  ``find_server_by_port`` psutil-race handler + ``return None``
  (444-447), non-numeric ``--port`` annotation (528-529), and
  ``wait_for_server_ready`` cancel-check early-abort (714-715).

- ``llauncher/state.py``: ``can_start`` host-port/path validation
  (216/225), ``can_stop`` rules-rejection (249), ``start_server``
  not-found / happy / exception (277-278, 296-312), ``stop_server``
  validation-error with a live registry entry (329), eviction
  already-running-elsewhere (399), and the eviction rollback/recovery
  paths on readiness failure and readiness error (546-548, 563-598).

NOTE (accounting deviation, surfaced to review): the CORE-RUNTIME
accounting tagged process.py 444-447 and 714-715 as
"pragma-defensible". On inspection both are deterministically coverable
with the same psutil/cancel mock idiom the codebase already uses for the
sibling ``find_all_llama_servers`` race handler and the gpu.py ``_try_*``
branches — and 714-715 is the ``cancel_check`` early-return, not the
``socket.connect_ex`` OSError the note described (that branch, 725-726,
is already covered). Covering genuinely is strictly stronger than a
pragma, so no ``# pragma: no cover`` was added.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil
import pytest

from llauncher.core.process import (
    _newest_log,
    discover_all,
    find_server_by_port,
    start_server,
    wait_for_server_ready,
)
from llauncher.models.config import ModelConfig, RunningServer
from llauncher.state import LauncherState


# ===========================================================================
# core/process.py
# ===========================================================================

class TestNewestLogStatOSError:
    """``_newest_log`` _mtime fallback when ``Path.stat`` raises (248-249)."""

    def test_stat_oserror_sorts_path_last_not_raises(self):
        gone = MagicMock(spec=Path)
        gone.stat.side_effect = OSError("vanished mid-scan")
        # Single candidate: max() still invokes the key on it, tripping the
        # OSError → -inf branch; the call must return the path, not raise.
        assert _newest_log([gone]) is gone
        gone.stat.assert_called_once()


class TestStartServerLogOpenOSError:
    """``start_server`` re-raises a log-file ``open`` OSError (322-323)."""

    def test_open_failure_reraised_as_oserror(self):
        config = ModelConfig.from_dict_unvalidated({
            "name": "open-fail",
            "model_path": "/fake/model.gguf",
            "n_gpu_layers": 255,
        })
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("llauncher.core.process.LOG_DIR") as mock_log_dir, \
             patch(
                 "llauncher.core.process.log_rotation.rotate_if_needed",
                 return_value=False,
             ), \
             patch("builtins.open", side_effect=OSError("disk full")):
            mock_log_dir.mkdir = MagicMock()
            with pytest.raises(OSError, match="Failed to create log file"):
                start_server(config, port=8080)


class TestFindServerByPortRace:
    """``find_server_by_port`` psutil-race handler + final ``return None``
    (444-447): a process that vanishes mid-scan is skipped, and an empty
    match returns ``None`` rather than raising."""

    def test_cmdline_raises_nosuchprocess_then_returns_none(self):
        proc = MagicMock()
        proc.info = {"pid": 1, "name": "llama-server"}
        proc.cmdline.side_effect = psutil.NoSuchProcess(pid=1)
        with patch("psutil.process_iter", return_value=[proc]):
            assert find_server_by_port(8081) is None

    def test_cmdline_raises_accessdenied_then_returns_none(self):
        proc = MagicMock()
        proc.info = {"pid": 2, "name": "llama-server"}
        proc.cmdline.side_effect = psutil.AccessDenied(pid=2)
        with patch("psutil.process_iter", return_value=[proc]):
            assert find_server_by_port(8081) is None


class TestAnnotatedNonNumericPort:
    """``discover_all`` non-numeric ``--port`` → None (528-529)."""

    def test_non_numeric_port_yields_none_port(self):
        proc = MagicMock()
        proc.name.return_value = "llama-server"
        proc.info = {"pid": proc.pid, "name": "llama-server"}
        proc.cmdline.return_value = ["llama-server", "--port", "not-a-number"]
        with patch("psutil.process_iter", return_value=[proc]):
            result = discover_all()
        assert len(result) == 1
        assert result[0].pid == proc.pid
        assert result[0].port is None
        assert result[0].cmdline_unreadable is False


class TestWaitForServerReadyCancel:
    """``wait_for_server_ready`` cancel-check early-abort (714-715)."""

    def test_cancel_check_true_aborts_immediately(self):
        # find_server_by_port returns None so _attempt_logs yields [] without
        # touching the network; cancel_check fires on the first poll tick.
        with patch("llauncher.core.process.find_server_by_port", return_value=None):
            ready, logs = wait_for_server_ready(
                8081, timeout=5, cancel_check=lambda: True,
            )
        assert ready is False
        assert logs == []


# ===========================================================================
# llauncher/state.py
# ===========================================================================

def _bare_state() -> LauncherState:
    """LauncherState with no disk/process side effects (skips __post_init__)."""
    s = LauncherState.__new__(LauncherState)
    s.models = {}
    s.running = {}
    s.audit = []
    s.rules = MagicMock()
    s.rules.validate_start.return_value = (True, "OK")
    s.rules.validate_stop.return_value = (True, "OK")
    return s


def _model(name: str, model_path: str) -> ModelConfig:
    return ModelConfig.from_dict_unvalidated({
        "name": name,
        "model_path": model_path,
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    })


class TestCanStart:
    """can_start host-port and model-path validation (216, 225)."""

    def test_host_port_in_use_rejected(self):
        state = _bare_state()
        config = _model("m", "/fake/m.gguf")
        with patch("llauncher.state.is_port_in_use", return_value=True):
            ok, msg = state.can_start(config, "cli", port=8081)
        assert ok is False
        assert "already in use" in msg

    def test_model_path_missing_rejected(self, tmp_path):
        state = _bare_state()
        missing = str(tmp_path / "nope.gguf")
        config = _model("m", missing)
        with patch("llauncher.state.is_port_in_use", return_value=False):
            ok, msg = state.can_start(config, "cli", port=8081)
        assert ok is False
        assert "does not exist" in msg
        assert missing in msg


class TestCanStop:
    """can_stop rules-rejection branch (249)."""

    def test_rules_reject_returns_message(self):
        state = _bare_state()
        state.running[8081] = RunningServer(
            pid=1, port=8081, config_name="m", start_time=datetime.now(),
        )
        state.rules.validate_stop.return_value = (False, "blacklisted caller")
        ok, msg = state.can_stop(8081, "evil")
        assert ok is False
        assert "blacklisted" in msg


class TestStartServer:
    """state.start_server not-found / happy / exception (277-278, 296-312)."""

    def test_model_not_found(self):
        state = _bare_state()
        ok, msg, proc = state.start_server("ghost", port=8081, caller="cli")
        assert ok is False
        assert "Model not found" in msg
        assert proc is None
        assert any(a.result == "error" and a.action == "start" for a in state.audit)

    def test_happy_path_updates_running_registry(self, tmp_path):
        state = _bare_state()
        path = tmp_path / "m.gguf"
        path.write_text("x")
        state.models = {"m": _model("m", str(path))}
        fake_proc = MagicMock(pid=4242)
        with patch("llauncher.state.is_port_in_use", return_value=False), \
             patch("llauncher.state.process_start_server", return_value=fake_proc):
            ok, msg, proc = state.start_server("m", port=8081, caller="cli")
        assert ok is True
        assert proc is fake_proc
        assert "Started m on port 8081" in msg
        assert 8081 in state.running
        assert state.running[8081].config_name == "m"
        assert state.running[8081].pid == 4242
        assert any(a.result == "success" and a.action == "start" for a in state.audit)

    def test_process_start_raises_records_error(self, tmp_path):
        state = _bare_state()
        path = tmp_path / "m.gguf"
        path.write_text("x")
        state.models = {"m": _model("m", str(path))}
        with patch("llauncher.state.is_port_in_use", return_value=False), \
             patch("llauncher.state.process_start_server",
                   side_effect=RuntimeError("spawn boom")):
            ok, msg, proc = state.start_server("m", port=8081, caller="cli")
        assert ok is False
        assert "Failed to start" in msg
        assert "spawn boom" in msg
        assert proc is None
        assert 8081 not in state.running
        assert any(a.result == "error" and a.action == "start" for a in state.audit)


class TestStopServerValidationError:
    """stop_server validation-error with a live registry entry (329)."""

    def test_validation_error_uses_running_entry_for_audit(self):
        state = _bare_state()
        # Port IS in running, but rules reject the stop → can_stop False with
        # a truthy registry entry, exercising the ``model = existing_model``
        # branch.
        state.running[8081] = RunningServer(
            pid=7, port=8081, config_name="resident", start_time=datetime.now(),
        )
        state.rules.validate_stop.return_value = (False, "stop blacklisted")
        ok, msg = state.stop_server(8081, caller="evil")
        assert ok is False
        assert "stop blacklisted" in msg
        # Audit recorded against the *running* model's name, not "unknown".
        assert any(
            a.action == "stop" and a.result == "validation_error"
            and a.model == "resident"
            for a in state.audit
        )


class TestEvictionAlreadyRunningElsewhere:
    """Eviction pre-flight: model already running on a different port (399)."""

    def test_same_model_other_port_rejected_unchanged(self, tmp_path):
        state = _bare_state()
        path = tmp_path / "m.gguf"
        path.write_text("x")
        state.models = {"m": _model("m", str(path))}
        state.running[9000] = RunningServer(
            pid=1, port=9000, config_name="m", start_time=datetime.now(),
        )
        result = state._start_with_eviction_impl("m", port=8081, caller="cli")
        assert result.success is False
        assert result.port_state == "unchanged"
        assert "already running on port 9000" in result.error


class TestEvictionRollbackRecovery:
    """Eviction rollback/recovery paths (546-548, 563-598).

    Exercises the readiness-FALSE inner rollback failure, and the
    readiness-RAISED branch in its three exits: rollback succeeds,
    rollback fails, and no-rollback (non-strict).
    """

    def _two_model_state(self, tmp_path) -> LauncherState:
        old_path = tmp_path / "old.gguf"
        old_path.write_text("x")
        new_path = tmp_path / "new.gguf"
        new_path.write_text("x")
        state = _bare_state()
        state.models = {
            "old": _model("old", str(old_path)),
            "new": _model("new", str(new_path)),
        }
        state.running[8081] = RunningServer(
            pid=100, port=8081, config_name="old", start_time=datetime.now(),
        )
        return state

    def test_readiness_false_rollback_succeeds_restored(self, tmp_path):
        """520-548: wait_for_server_ready returns a real (False, logs) tuple,
        the new process is terminated, and rollback restores the evicted
        server. Regression test for #249 — a prior version of this test
        mocked `wait_for_server_ready` with a bare `return_value=False`,
        which the real function (`tuple[bool, list[str]]`) never returns;
        that false coverage masked state.py:519 binding the tuple without
        unpacking, which made `if not ready:` permanently unreachable.
        """
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid") as mock_stop_by_pid, \
             patch("llauncher.state.wait_for_server_ready",
                   return_value=(False, ["timed out waiting for ready"])), \
             patch("llauncher.state.process_start_server") as mock_start:
            mock_start.side_effect = [MagicMock(pid=111), MagicMock(pid=222)]
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=True,
            )
        mock_stop_by_pid.assert_called_once_with(111)
        assert result.success is False
        assert result.port_state == "restored"
        assert result.rolled_back is True
        assert result.restored_model == "old"
        assert "Readiness timeout after 5s" in result.error
        assert state.running[8081].config_name == "old"
        assert state.running[8081].pid == 222

    def test_readiness_false_rollback_fails_unavailable(self, tmp_path):
        """549-558: readiness False, rollback start also raises → unavailable,
        manual intervention required."""
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready",
                   return_value=(False, [])), \
             patch("llauncher.state.process_start_server") as mock_start:
            mock_start.side_effect = [MagicMock(pid=111), RuntimeError("rollback boom")]
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=True,
            )
        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Rollback failed" in result.error
        assert 8081 not in state.running

    def test_readiness_false_no_rollback_non_strict_unavailable(self, tmp_path):
        """560-566: readiness False, strict_rollback False → no rollback
        attempted, port left unavailable."""
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready",
                   return_value=(False, [])), \
             patch("llauncher.state.process_start_server",
                   return_value=MagicMock(pid=111)):
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=False,
            )
        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Readiness timeout after 5s" in result.error
        assert result.new_model_attempted == "new"
        assert 8081 not in state.running

    def test_readiness_raises_rollback_succeeds_restored(self, tmp_path):
        """563-587: wait_for_server_ready raises, rollback restores old."""
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready",
                   side_effect=RuntimeError("poll exploded")), \
             patch("llauncher.state.process_start_server") as mock_start:
            mock_start.side_effect = [MagicMock(pid=111), MagicMock(pid=222)]
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=True,
            )
        assert result.success is False
        assert result.port_state == "restored"
        assert result.rolled_back is True
        assert result.restored_model == "old"
        assert "Readiness check failed" in result.error
        assert state.running[8081].config_name == "old"
        assert state.running[8081].pid == 222

    def test_readiness_raises_rollback_fails_unavailable(self, tmp_path):
        """588-596: wait raises, rollback start also raises → unavailable."""
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready",
                   side_effect=RuntimeError("poll exploded")), \
             patch("llauncher.state.process_start_server") as mock_start:
            mock_start.side_effect = [MagicMock(pid=111), RuntimeError("rollback boom")]
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=True,
            )
        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Rollback failed" in result.error
        assert 8081 not in state.running

    def test_readiness_raises_no_rollback_non_strict_unavailable(self, tmp_path):
        """598-604: wait raises, strict_rollback False → no rollback."""
        state = self._two_model_state(tmp_path)
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready",
                   side_effect=RuntimeError("poll exploded")), \
             patch("llauncher.state.process_start_server",
                   return_value=MagicMock(pid=111)):
            result = state._start_with_eviction_impl(
                "new", port=8081, caller="cli",
                readiness_timeout=5, strict_rollback=False,
            )
        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Readiness check failed" in result.error
        assert result.new_model_attempted == "new"
        assert 8081 not in state.running
