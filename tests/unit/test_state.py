"""Tests for state management (llauncher/state.py)."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from pathlib import Path

from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig, RunningServer


class TestStartWithEviction:
    """Tests for start_with_eviction method (backward-compat wrapper)."""

    @pytest.fixture
    def mock_state(self, tmp_path):
        """Create a state instance with mocked dependencies and temp model files."""
        # Create temporary model files for validation
        self.tmp_path = tmp_path
        (tmp_path / "new_model.gguf").write_text("mock")
        (tmp_path / "old_model.gguf").write_text("mock")
        (tmp_path / "existing_model.gguf").write_text("mock")

        state = LauncherState.__new__(LauncherState)
        state.models = {}
        state.running = {}
        state.audit = []
        state.rules = MagicMock()
        state.rules.validate_start.return_value = (True, "OK")
        state.rules.validate_stop.return_value = (True, "OK")
        return state

    def make_model(self, name: str, port: int) -> ModelConfig:
        """Create a model config with temp file path."""
        model_path = str(self.tmp_path / f"{name}.gguf")
        # Use from_dict_unvalidated to bypass path validation during tests
        return ModelConfig.from_dict_unvalidated({
            "name": name,
            "model_path": model_path,
            "default_port": port,
            "n_gpu_layers": 255,
            "ctx_size": 4096,
        })

    def test_start_with_eviction_successful(self, mock_state):
        """Test successful eviction and start (compat wrapper)."""
        # Setup: Model exists, port occupied by another server
        mock_state.models = {
            "new_model": self.make_model("new_model", 8080),
        }

        # Existing server on port 8080
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="old_model",
                start_time=datetime.now(),
            )
        }

        # Mock process_start_server, process_stop_server, wait_for_server_ready,
        # AND refresh_running_servers (which would otherwise wipe self.running)
        with patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.wait_for_server_ready", return_value=True), \
             patch.object(mock_state, "refresh_running_servers"):

            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            # Execute via compat wrapper
            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        # Assert: compat wrapper returns (success, message)
        assert success is True, f"Expected success, got: {message}"
        assert "Started new_model on port 8080" in message
        assert 8080 in mock_state.running
        assert mock_state.running[8080].config_name == "new_model"
        # Verify evict action was logged with success
        evict_entries = [e for e in mock_state.audit if e.action == "evict"]
        assert len(evict_entries) == 1
        assert evict_entries[0].result == "success"
        start_entries = [e for e in mock_state.audit if e.action == "start"]
        assert len(start_entries) == 1

    def test_start_with_eviction_model_not_found(self, mock_state):
        """Test eviction when model does not exist."""
        mock_state.models = {
            "other_model": self.make_model("other_model", 8081),
        }

        success, message = mock_state.start_with_eviction(
            model_name="nonexistent_model",
            port=8080,
            caller="test",
        )

        assert success is False
        assert "not found" in message.lower()
        assert mock_state.audit[-1].result == "error"

    def test_start_with_eviction_port_not_occupied(self, mock_state):
        """Test eviction when port is free (no eviction needed)."""
        mock_state.models = {
            "new_model": self.make_model("new_model", 8080),
        }

        # Port is not occupied
        mock_state.running = {}

        with patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.wait_for_server_ready", return_value=True), \
             patch.object(mock_state, "refresh_running_servers"):

            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        assert success is True
        # No eviction happened; compat message shows what was started
        assert "Started new_model on port 8080" in message

    def test_start_with_eviction_stop_fails(self, mock_state):
        """Test eviction when stopping existing server fails."""
        mock_state.models = {
            "new_model": self.make_model("new_model", 8080),
        }

        # Existing server on port
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="old_model",
                start_time=datetime.now(),
            )
        }

        # Mock process_stop_server to fail
        with patch("llauncher.state.process_stop_server", return_value=False):
            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        assert success is False
        assert "Cannot evict" in message
        assert "Failed to stop" in message
        # Verify evict action was logged with error
        evict_entries = [e for e in mock_state.audit if e.action == "evict"]
        assert len(evict_entries) == 1
        assert evict_entries[0].result == "error"

    def test_start_with_eviction_start_fails(self, mock_state):
        """Test eviction when starting new server fails (no rollback in non-strict mode)."""
        mock_state.models = {
            "new_model": self.make_model("new_model", 8080),
        }

        # Existing server on port
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="old_model",
                start_time=datetime.now(),
            )
        }

        # Mock stop_server to succeed, but process_start_server to fail
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.process_start_server", side_effect=Exception("Failed to start server")):

            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        assert success is False
        assert "Failed to start" in message

    def test_start_with_eviction_same_model_running(self, mock_state):
        """Test eviction when same model is already running (stops and restarts)."""
        mock_state.models = {
            "existing_model": self.make_model("existing_model", 8080),
        }

        # Same model already running on port
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="existing_model",
                start_time=datetime.now(),
            )
        }

        with patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.wait_for_server_ready", return_value=True), \
             patch.object(mock_state, "refresh_running_servers"):

            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            success, message = mock_state.start_with_eviction(
                model_name="existing_model",
                port=8080,
                caller="test",
            )

        # Should succeed (stops and restarts the same model)
        assert success is True
        assert "Started existing_model" in message
        assert 8080 in mock_state.running
        assert mock_state.running[8080].config_name == "existing_model"


class TestEvictionRollback:
    """Tests for the 5-phase eviction rollback decision tree (ADR-002).

    These tests validate _start_with_eviction_impl directly, covering:
    - Pre-flight failures (model not found)
    - Evict+start failure with strict_rollback=True → port_state=restored
    - Readiness timeout → rollback succeeds → port_state=restored
    - Non-strict mode with old config missing → port_state=unavailable
    - Both start and rollback failing → port_state=unavailable
    - Empty port (no eviction) → simple success path
    """

    def _make_mock_state(self, tmp_path):
        """Create a bare LauncherState with models and running servers set up manually."""
        state = LauncherState.__new__(LauncherState)
        state.models = {}
        state.running = {}
        state.audit = []
        state.rules = MagicMock()
        state.rules.validate_start.return_value = (True, "OK")
        state.rules.validate_stop.return_value = (True, "OK")
        self._tmp_path = tmp_path
        return state

    def _make_model(self, name: str, port: int) -> ModelConfig:
        """Create a valid ModelConfig for testing."""
        gguf_path = str(self._tmp_path / f"{name}.gguf")
        Path(gguf_path).touch()
        return ModelConfig.from_dict_unvalidated({
            "name": name,
            "model_path": gguf_path,
            "default_port": port,
            "n_gpu_layers": 255,
            "ctx_size": 4096,
        })

    def _make_running_server(self, config: ModelConfig, pid: int = 12345) -> RunningServer:
        """Create a RunningServer for testing."""
        return RunningServer(
            pid=pid,
            port=config.default_port,
            config_name=config.name,
            start_time=datetime.now(),
        )

    # ─── Test 1: Pre-flight fails — model not found, port state unchanged ───
    def test_pre_flight_model_not_found_untouched(self, tmp_path):
        """Phase 1: Model lookup fails; nothing changed."""
        state = self._make_mock_state(tmp_path)
        # No models at all — model NOT in config
        result = state._start_with_eviction_impl("nonexistent_model", 8080, caller="test")

        assert result.success is False
        assert result.port_state == "unchanged"
        assert "not found" in result.error.lower()
        assert len(state.running) == 0  # nothing changed
        assert len(state.audit) == 1
        assert state.audit[0].action == "start"
        assert state.audit[0].result == "error"

    # ─── Test 2: Evict + start fails, strict_rollback succeeds → port_state=restored ───
    def test_evict_start_fail_strict_rollback_succeeds(self, tmp_path):
        """Phase 3 start exception + Phase 3 rollback → restored."""
        state = self._make_mock_state(tmp_path)

        # Set up: old model running on port 8080
        old_config = self._make_model("old_model", 8080)
        new_config = self._make_model("new_model", 9999)
        state.models = {"old_model": old_config, "new_model": new_config}
        state.running[8080] = self._make_running_server(old_config, pid=12345)

        # Mock: stop succeeds, new start fails, rollback succeeds + ready
        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.wait_for_server_ready", return_value=True):
            # First call = new model start (fails), second call = rollback start (succeeds)
            mock_start.side_effect = [Exception("OOM kill"), MagicMock(pid=54321)]

            result = state._start_with_eviction_impl(
                "new_model", 8080, caller="test", strict_rollback=True,
            )

        assert result.success is False
        assert result.port_state == "restored"
        assert result.rolled_back is True
        assert result.restored_model == "old_model"

    # ─── Test 3: New starts but readiness times out → rollback succeeds → restored ───
    def test_evict_start_success_readiness_timeout_rollback_succeeds(self, tmp_path):
        """Phase 4 readiness failure + Phase 4 rollback → restored."""
        state = self._make_mock_state(tmp_path)

        old_config = self._make_model("old_model", 8080)
        new_config = self._make_model("new_model", 9999)
        state.models = {"old_model": old_config, "new_model": new_config}
        state.running[8080] = self._make_running_server(old_config, pid=12345)

        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready") as mock_ready:
            # Phase 4 readiness returns False (timeout) → rollback → ready=True
            mock_ready.side_effect = [False, True]
            mock_start.side_effect = [MagicMock(pid=111), MagicMock(pid=222)]

            result = state._start_with_eviction_impl(
                "new_model", 8080, caller="test",
                readiness_timeout=5, strict_rollback=True,
            )

        assert result.success is False
        assert result.port_state == "restored"
        assert result.rolled_back is True
        assert "Readiness timeout" in result.error

    # ─── Test 4: Non-strict mode, old config missing → port_state=unavailable (no rollback) ───
    def test_evict_start_success_then_ready_timeout_no_rollback(self, tmp_path):
        """Non-strict rollback: readiness fails, no rollback attempt → unavailable."""
        state = self._make_mock_state(tmp_path)

        new_config = self._make_model("new_model", 9999)
        state.models = {"new_model": new_config}
        # Old model is running but NOT in config (deleted from disk)
        old_deleted_config = ModelConfig.from_dict_unvalidated({
            "name": "old_deleted",
            "model_path": "/nonexistent/old.gguf",
            "default_port": 8080,
            "n_gpu_layers": 255,
            "ctx_size": 4096,
        })
        state.running[8080] = RunningServer(
            pid=12345, port=8080, config_name="old_deleted",
            start_time=datetime.now(),
        )

        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.process_start_server", return_value=MagicMock(pid=111)), \
             patch("llauncher.state.stop_server_by_pid"), \
             patch("llauncher.state.wait_for_server_ready", return_value=False):

            result = state._start_with_eviction_impl(
                "new_model", 8080, caller="test",
                readiness_timeout=5, strict_rollback=False,
            )

        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Readiness timeout" in result.error

    # ─── Test 5: Both new start and rollback fail → port_state=unavailable ───
    def test_both_fail_unavailable(self, tmp_path):
        """Start fails + rollback also raises → unavailable."""
        state = self._make_mock_state(tmp_path)

        old_config = self._make_model("old_model", 8080)
        new_config = self._make_model("new_model", 9999)
        state.models = {"old_model": old_config, "new_model": new_config}
        state.running[8080] = self._make_running_server(old_config, pid=12345)

        with patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.process_start_server",
                   side_effect=Exception("start failed")):

            result = state._start_with_eviction_impl(
                "new_model", 8080, caller="test", strict_rollback=True,
            )

        assert result.success is False
        assert result.port_state == "unavailable"
        assert result.rolled_back is False
        assert "Rollback failed" in result.error

    # ─── Test 6: Empty port — simple start, no eviction, no regression ───
    def test_empty_port_no_eviction(self, tmp_path):
        """No servers running → simple start path."""
        state = self._make_mock_state(tmp_path)

        config = self._make_model("test_model", 9999)
        state.models = {"test_model": config}
        # No servers running — port is free

        with patch("llauncher.state.process_start_server", return_value=MagicMock(pid=789)), \
             patch("llauncher.state.wait_for_server_ready", return_value=True):

            result = state._start_with_eviction_impl("test_model", 9999, caller="test")

        assert result.success is True
        assert result.port_state == "serving"
        assert result.previous_model == ""
        assert len(state.audit) == 1
        assert state.audit[0].action == "start"
        assert state.audit[0].result == "success"


class TestLauncherStateBase:
    """Base tests for LauncherState core functionality."""

    @pytest.fixture
    def mock_state(self, tmp_path):
        """Create a state instance with mocked dependencies."""
        # Create temp model file for validation
        (tmp_path / "test_model.gguf").write_text("mock")
        model_path = str(tmp_path / "test_model.gguf")

        state = LauncherState.__new__(LauncherState)
        state.models = {
            "test_model": ModelConfig(
                name="test_model",
                model_path=model_path,
                default_port=8080,
            )
        }
        state.running = {}
        state.audit = []
        state.rules = MagicMock()
        state.rules.validate_start.return_value = (True, "OK")
        state.rules.validate_stop.return_value = (True, "OK")
        return state

    def test_can_start_port_in_use(self, mock_state):
        """Test can_start when port is already in use."""
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="other_model",
                start_time=datetime.now(),
            )
        }

        config = mock_state.models["test_model"]

        valid, message = mock_state.can_start(config, caller="test", port=8080)

        assert valid is False
        assert "already in use" in message.lower()

    def test_can_start_port_free(self, mock_state):
        """Test can_start when port is free."""
        mock_state.running = {}

        config = mock_state.models["test_model"]

        # Mock is_port_in_use to return False
        with patch("llauncher.state.is_port_in_use", return_value=False):
            valid, message = mock_state.can_start(config, caller="test", port=8080)

        assert valid is True
        assert message == "OK"

    def test_can_start_port_not_blacklisted(self, mock_state):
        """Test can_start with blacklisted port."""
        mock_state.running = {}
        mock_state.rules.validate_start.return_value = (False, "Port 8080 is blacklisted")

        config = mock_state.models["test_model"]

        with patch("llauncher.state.is_port_in_use", return_value=False):
            valid, message = mock_state.can_start(config, caller="test", port=8080)

        assert valid is False
        assert "blacklisted" in message.lower()

    def test_can_stop_success(self, mock_state):
        """Test can_stop when server is running."""
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="test_model",
                start_time=datetime.now(),
            )
        }

        valid, message = mock_state.can_stop(port=8080, caller="test")

        assert valid is True
        assert message == "OK"

    def test_can_stop_not_running(self, mock_state):
        """Test can_stop when server is not running."""
        mock_state.running = {}

        valid, message = mock_state.can_stop(port=9999, caller="test")

        assert valid is False
        assert "No server running" in message

    def test_start_server_success(self, mock_state, tmp_path):
        """Test successful server start."""
        with patch("llauncher.state.process_start_server") as mock_start:
            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            with patch("llauncher.state.is_port_in_use", return_value=False):
                success, message, process = mock_state.start_server(
                    model_name="test_model",
                    caller="test",
                )

        assert success is True
        assert 8080 in mock_state.running
        assert mock_state.running[8080].config_name == "test_model"

    def test_stop_server_success(self, mock_state):
        """Test successful server stop."""
        mock_state.running = {
            8080: RunningServer(
                pid=1234,
                port=8080,
                config_name="test_model",
                start_time=datetime.now(),
            )
        }

        with patch("llauncher.state.process_stop_server", return_value=True):
            success, message = mock_state.stop_server(port=8080, caller="test")

        assert success is True
        assert 8080 not in mock_state.running
        assert "Stopped" in message

    def test_stop_server_not_running(self, mock_state):
        """Test stopping a server that is not running."""
        mock_state.running = {}

        # can_stop returns False when port not in running, so stop_server should fail
        success, message = mock_state.stop_server(port=9999, caller="test")

        assert success is False
        assert "No server running" in message


class TestUptimeFormatting:
    """Tests for format_uptime function."""

    def test_format_uptime_hours_minutes_seconds(self):
        """Format uptime with hours, minutes, and seconds."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(9245)  # 2h 34m 5s
        assert result == "2h 34m 5s"

    def test_format_uptime_hours_minutes(self):
        """Format uptime with hours and minutes only."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(9000)  # 2h 30m 0s
        assert result == "2h 30m"

    def test_format_uptime_seconds_only(self):
        """Format uptime with seconds only."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(45)
        assert result == "45s"


class TestRunningServer:
    """Tests for RunningServer dataclass."""

    def test_running_server_creation(self):
        """Test Creating RunningServer with keyword arguments."""
        server = RunningServer(
            pid=1234,
            port=8080,
            config_name="test_model",
            start_time=datetime.now(),
        )

        assert server.pid == 1234
        assert server.port == 8080
        assert server.config_name == "test_model"

    def test_running_server_uptime_seconds(self):
        """Test uptime_seconds calculation."""
        past_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        server = RunningServer(
            pid=1234,
            port=8080,
            config_name="test_model",
            start_time=past_time,
        )

        uptime = server.uptime_seconds()
        assert uptime > 0

    def test_running_server_to_dict(self):
        """Test to_dict serialization."""
        start_time = datetime.now()
        server = RunningServer(
            pid=1234,
            port=8080,
            config_name="test_model",
            start_time=start_time,
        )

        result = server.to_dict()

        assert result["pid"] == 1234
        assert result["port"] == 8080
        assert result["config_name"] == "test_model"
        assert "start_time" in result


class TestModelConfig:
    """Tests for ModelConfig class."""

    def test_model_config_creation(self, tmp_path):
        """Test creating ModelConfig."""
        model_path = str(tmp_path / "test.gguf")
        (tmp_path / "test.gguf").write_text("mock")

        config = ModelConfig(
            name="test_model",
            model_path=model_path,
            default_port=8080,
        )

        assert config.name == "test_model"
        assert config.default_port == 8080

    def test_model_config_to_dict(self, tmp_path):
        """Test ModelConfig serialization."""
        model_path = str(tmp_path / "test.gguf")
        (tmp_path / "test.gguf").write_text("mock")

        config = ModelConfig(
            name="test_model",
            model_path=model_path,
            default_port=8080,
            n_gpu_layers=255,
        )

        result = config.to_dict()

        assert result["name"] == "test_model"
        assert result["default_port"] == 8080
        assert result["n_gpu_layers"] == 255
