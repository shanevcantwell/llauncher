import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig, RunningServer
from llauncher.core.config import ConfigStore

def test_state_refresh(mock_config_store, sample_model_config):
    """Test that refresh() correctly populates models and running servers."""
    # 1. Setup: Add a model to ConfigStore
    ConfigStore.add_model(sample_model_config)

    # 2. Mock running servers - use the model's path so _find_model_by_path matches
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.cmdline.return_value = ["llama-server", "--port", "8081", "-m", sample_model_config.model_path]

    with patch('llauncher.state.find_all_llama_servers', return_value=[mock_proc]):
        state = LauncherState()

        # Check models (only from ConfigStore, no discovery)
        assert sample_model_config.name in state.models
        # No discovered models should be present

        # Check running (the one we mocked)
        assert 8081 in state.running
        assert state.running[8081].pid == 1234
        # Since the model is in state.models, it should find it
        assert state.running[8081].config_name == sample_model_config.name

def test_can_start_validation(launcher_state, sample_model_config):
    """Test the validation logic in can_start."""
    # Test: Port in use by state
    running_server = RunningServer(pid=1, port=sample_model_config.default_port, config_name="other", start_time=datetime.now())
    launcher_state.running[sample_model_config.default_port] = running_server

    valid, msg = launcher_state.can_start(sample_model_config)
    assert not valid
    assert "already in use" in msg.lower()

    # Test: Port in use by system (mock is_port_in_use)
    del launcher_state.running[sample_model_config.default_port]
    with patch('llauncher.state.is_port_in_use', return_value=True):
        valid, msg = launcher_state.can_start(sample_model_config)
        assert not valid
        assert "already in use" in msg.lower()

    # Test: Model path doesn't exist
    with patch('llauncher.state.is_port_in_use', return_value=False):
        # We need to patch Path.exists for the model path
        with patch('llauncher.state.Path.exists', return_value=False):
            valid, msg = launcher_state.can_start(sample_model_config)
            assert not valid
            assert "path does not exist" in msg.lower()

    # Test: Success
    with patch('llauncher.state.is_port_in_use', return_value=False):
        with patch('llauncher.state.Path.exists', return_value=True):
            valid, msg = launcher_state.can_start(sample_model_config)
            assert valid
            assert msg == "OK"

def test_start_server_success(launcher_state, sample_model_config):
    """Test starting a server successfully."""
    launcher_state.models[sample_model_config.name] = sample_model_config

    # Make sure port isn't already running
    if sample_model_config.default_port in launcher_state.running:
        del launcher_state.running[sample_model_config.default_port]

    mock_proc = MagicMock()
    mock_proc.pid = 5678

    with patch('llauncher.state.find_available_port', return_value=(True, 8081, "Using preferred port 8081")):
        with patch('llauncher.state.is_port_in_use', return_value=False):
            with patch('llauncher.state.Path.exists', return_value=True):
                with patch('llauncher.state.process_start_server', return_value=mock_proc) as mock_start:
                    success, msg, proc = launcher_state.start_server(sample_model_config.name)

                    assert success is True
                    assert proc == mock_proc
                    assert 8081 in launcher_state.running
                    assert launcher_state.running[8081].pid == 5678

def test_stop_server_success(launcher_state, sample_model_config):
    """Test stopping a server successfully."""
    # Setup: model is running
    launcher_state.models[sample_model_config.name] = sample_model_config
    launcher_state.running[sample_model_config.default_port] = RunningServer(
        pid=5678, port=sample_model_config.default_port, config_name=sample_model_config.name, start_time=datetime.now()
    )

    with patch('llauncher.state.process_stop_server', return_value=True) as mock_stop:
        success, msg = launcher_state.stop_server(sample_model_config.default_port)

        assert success is True
        assert sample_model_config.default_port not in launcher_state.running
        mock_stop.assert_called_once_with(sample_model_config.default_port)

def test_record_action(launcher_state):
    """Test audit log recording."""
    launcher_state.record_action("test_action", "test_model", "test_caller", "success", "test_msg")

    assert len(launcher_state.audit) == 1
    entry = launcher_state.audit[0]
    assert entry.action == "test_action"
    assert entry.model == "test_model"
    assert entry.caller == "test_caller"
    assert entry.result == "success"
    assert entry.message == "test_msg"


class TestLauncherStateEdgeCases:
    """Tests for LauncherState edge cases and error handling."""

    @pytest.fixture
    def state_with_models(self):
        """Create a state with some models configured."""
        state = LauncherState()
        # Add a test model
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": str(Path.home() / "test.model"),
            "default_port": 8080,
        })
        state.models["test-model"] = config
        return state

    def test_refresh_running_servers_empty_cmdline(self, state_with_models):
        """Processes with empty cmdline are skipped."""
        import psutil
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = []

        with patch("llauncher.state.find_all_llama_servers", return_value=[mock_proc]):
            state_with_models.refresh_running_servers()
            # Should not crash, should have no running servers
            assert len(state_with_models.running) == 0

    def test_refresh_running_servers_no_such_process(self, state_with_models):
        """NoSuchProcess exception during refresh is handled."""
        import psutil
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.NoSuchProcess(12345, None)

        with patch("llauncher.state.find_all_llama_servers", return_value=[mock_proc]):
            state_with_models.refresh_running_servers()
            # Should not crash
            assert len(state_with_models.running) == 0

    def test_refresh_running_servers_access_denied(self, state_with_models):
        """AccessDenied exception during refresh is handled."""
        import psutil
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.AccessDenied(12345)

        with patch("llauncher.state.find_all_llama_servers", return_value=[mock_proc]):
            state_with_models.refresh_running_servers()
            # Should not crash
            assert len(state_with_models.running) == 0

    def test_find_model_by_path_none(self, state_with_models):
        """_find_model_by_path returns None for None path."""
        result = state_with_models._find_model_by_path(None)
        assert result is None

    def test_can_stop_no_server_running(self, state_with_models):
        """can_stop returns False when no server on port."""
        valid, msg = state_with_models.can_stop(9999, caller="test")
        assert valid is False
        assert "No server running" in msg

    def test_start_server_model_not_found(self, state_with_models):
        """start_server returns error for unknown model."""
        success, msg, process = state_with_models.start_server("unknown-model", caller="test")
        assert success is False
        assert "Model not found" in msg
        assert process is None

    def test_start_server_port_allocation_failure(self, state_with_models):
        """start_server handles port allocation failure."""
        with patch("llauncher.state.find_available_port", return_value=(False, 0, "No ports available")):
            success, msg, process = state_with_models.start_server("test-model", caller="test")
            assert success is False
            assert "Cannot allocate port" in msg
            assert process is None

    def test_start_server_exception_during_start(self, state_with_models):
        """start_server handles exception during process start."""
        # Use a port that's not blacklisted (8080 is in default blacklist)
        with patch("llauncher.state.find_available_port", return_value=(True, 9000, "OK")):
            with patch("llauncher.state.Path.exists", return_value=True):
                with patch("llauncher.state.process_start_server", side_effect=Exception("Failed to start")):
                    success, msg, process = state_with_models.start_server("test-model", caller="test")
                    assert success is False
                    assert "Failed to start" in msg
                    assert process is None

    def test_stop_server_process_not_found(self, state_with_models):
        """stop_server handles case where process is not found."""
        # Add a running server
        running = RunningServer(pid=12345, port=8080, config_name="test-model", start_time=datetime.now())
        state_with_models.running[8080] = running

        with patch("llauncher.state.process_stop_server", return_value=False):
            success, msg = state_with_models.stop_server(8080, caller="test")
            assert success is False
            assert "Failed to stop" in msg

    def test_get_model_status_not_found(self, state_with_models):
        """get_model_status returns unknown for non-existent model."""
        status = state_with_models.get_model_status("nonexistent")
        assert status["status"] == "unknown"
        assert "not found" in status["message"].lower()

    def test_get_model_status_stopped(self, state_with_models):
        """get_model_status returns stopped for non-running model."""
        status = state_with_models.get_model_status("test-model")
        assert status["status"] == "stopped"
        assert "default_port" in status
