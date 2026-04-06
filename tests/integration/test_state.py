import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig, RunningServer
from llauncher.core.config import ConfigStore

def test_state_refresh(mock_config_store, sample_model_config):
    """Test that refresh() correctly populates models and running servers."""
    # 1. Setup: Add a model to ConfigStore
    ConfigStore.add_model(sample_model_config)

    # 2. Mock discovery to return a second model
    discovered_config = sample_model_config.model_copy(update={"name": "discovered"})

    # 3. Mock running servers
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.cmdline.return_value = ["llama-server", "--port", "8081", "-m", "/path/to/model.gguf"]

    with patch('llauncher.core.discovery.discover_scripts', return_value=[discovered_config]):
        with patch('llauncher.core.process.find_all_llama_servers', return_value=[mock_proc]):
            state = LauncherState()

            # Check models (merged)
            assert sample_model_config.name in state.models
            assert discovered_config.name in state.models

            # Check running (the one we mocked)
            assert 8081 in state.running
            assert state.running[8081].pid == 1234
            # Since 'discovered' is in state.models, it should find it
            assert state.running[8081].config_name == "discovered"

def test_can_start_validation(launcher_state, sample_model_config):
    """Test the validation logic in can_start."""
    # Test: Port in use by state
    running_server = RunningServer(pid=1, port=sample_model_config.port, config_name="other", start_time=datetime.now())
    launcher_state.running[sample_model_config.port] = running_server

    valid, msg = launcher_state.can_start(sample_model_config)
    assert not valid
    assert "already in use" in msg.lower()

    # Test: Port in use by system (mock is_port_in_use)
    del launcher_state.running[sample_model_config.port]
    with patch('llauncher.core.process.is_port_in_use', return_value=True):
        valid, msg = launcher_state.can_start(sample_model_config)
        assert not valid
        assert "already in use" in msg.lower()

    # Test: Model path doesn't exist
    with patch('llauncher.core.process.is_port_in_use', return_value=False):
        # We need to patch Path.exists for the model path
        with patch('llauncher.state.Path.exists', return_value=False):
            valid, msg = launcher_state.can_start(sample_model_config)
            assert not valid
            assert "path does not exist" in msg.lower()

    # Test: Success
    with patch('llauncher.core.process.is_port_in_use', return_value=False):
        with patch('llauncher.state.Path.exists', return_value=True):
            valid, msg = launcher_state.can_start(sample_model_config)
            assert valid
            assert msg == "OK"

def test_start_server_success(launcher_state, sample_model_config):
    """Test starting a server successfully."""
    launcher_state.models[sample_model_config.name] = sample_model_config

    mock_proc = MagicMock()
    mock_proc.pid = 5678

    with patch('llauncher.core.process.is_port_in_use', return_value=False):
        with patch('llauncher.state.Path.exists', return_value=True):
            with patch('llauncher.core.process.start_server', return_value=mock_proc) as mock_start:
                success, msg, proc = launcher_state.start_server(sample_model_config.name)

                assert success is True
                assert proc == mock_proc
                assert sample_model_config.port in launcher_state.running
                assert launcher_state.running[sample_model_config.port].pid == 5678

def test_stop_server_success(launcher_state, sample_model_config):
    """Test stopping a server successfully."""
    # Setup: model is running
    launcher_state.models[sample_model_config.name] = sample_model_config
    launcher_state.running[sample_model_config.port] = RunningServer(
        pid=5678, port=sample_model_config.port, config_name=sample_model_config.name, start_time=datetime.now()
    )

    with patch('llauncher.core.process.stop_server_by_port', return_value=True) as mock_stop:
        success, msg = launcher_state.stop_server(sample_model_config.port)

        assert success is True
        assert sample_model_config.port not in launcher_state.running
        mock_stop.assert_called_once_with(sample_model_config.port)

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
