"""Tests for state management (llauncher/state.py)."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from pathlib import Path

from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig, RunningServer


class TestStartWithEviction:
    """Tests for start_with_eviction method."""

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
        """Test successful eviction and start."""
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

        # Mock both process_start_server and process_stop_server
        with patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.process_stop_server", return_value=True), \
             patch("llauncher.state.is_port_in_use", return_value=False):

            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            # Execute
            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        # Assert
        assert success is True, f"Expected success, got: {message}"
        assert "evicted" in message.lower()
        assert 8080 in mock_state.running
        assert mock_state.running[8080].config_name == "new_model"
        # Verify stop was called for old server
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
        assert "Model not found" in message
        assert mock_state.audit[-1].result == "error"

    def test_start_with_eviction_port_not_occupied(self, mock_state):
        """Test eviction when port is free (no eviction needed)."""
        mock_state.models = {
            "new_model": self.make_model("new_model", 8080),
        }

        # Port is not occupied
        mock_state.running = {}

        with patch("llauncher.state.process_start_server") as mock_start, \
             patch("llauncher.state.is_port_in_use", return_value=False):

            mock_process = MagicMock()
            mock_process.pid = 5678
            mock_start.return_value = mock_process

            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        assert success is True
        # Should not mention eviction when port was free
        assert "evicted" not in message.lower()
        assert "Started" in message

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
        with patch("llauncher.state.process_stop_server", return_value=False), \
             patch("llauncher.state.is_port_in_use", return_value=False):
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
        """Test eviction when starting new server fails."""
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
             patch("llauncher.state.process_start_server", side_effect=Exception("Failed to start server")), \
             patch("llauncher.state.is_port_in_use", return_value=False):

            success, message = mock_state.start_with_eviction(
                model_name="new_model",
                port=8080,
                caller="test",
            )

        assert success is False
        assert "Failed to start" in message

    def test_start_with_eviction_same_model_running(self, mock_state):
        """Test eviction when same model is already running (idempotent)."""
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
             patch("llauncher.state.is_port_in_use", return_value=False):

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
        assert 8080 in mock_state.running
        assert mock_state.running[8080].config_name == "existing_model"


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
