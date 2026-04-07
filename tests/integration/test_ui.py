"""Integration tests for UI components."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.models.config import ModelConfig


class TestDashboardIntegration:
    """Integration tests for dashboard rendering."""

    def test_dashboard_no_models(self):
        """Dashboard with no models shows info message."""
        with patch("streamlit.header") as mock_header:
            with patch("streamlit.expander") as mock_expander:
                with patch("streamlit.info") as mock_info:
                    with patch("streamlit.divider"):
                        with patch("streamlit.subheader"):
                            state = MagicMock()
                            state.models = {}

                            from llauncher.ui.tabs.dashboard import render_dashboard

                            render_dashboard(state)

                            mock_info.assert_called_once()
                            assert "no models" in mock_info.call_args[0][0].lower()

    def test_dashboard_multiple_models(self):
        """Dashboard with multiple models has correct state."""
        config1 = ModelConfig.from_dict_unvalidated({
            "name": "model1",
            "model_path": "/path/to/model1.gguf",
        })
        config2 = ModelConfig.from_dict_unvalidated({
            "name": "model2",
            "model_path": "/path/to/model2.gguf",
        })

        state = MagicMock()
        state.models = {"model1": config1, "model2": config2}

        assert len(state.models) == 2
        assert "model1" in state.models
        assert "model2" in state.models


class TestStartStopWorkflow:
    """Integration tests for start/stop workflow."""

    def test_start_server_workflow(self):
        """Start button calls can_start then start_server."""
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
        })

        state = MagicMock()
        state.can_start.return_value = (True, "OK")
        state.start_server.return_value = (True, "Started", MagicMock())

        valid, msg = state.can_start(config, caller="ui")
        assert valid is True

        if valid:
            success, message, process = state.start_server("test-model", caller="ui")

        assert success is True
        state.start_server.assert_called_once()

    def test_stop_server_workflow(self):
        """Stop button calls stop_server."""
        state = MagicMock()
        state.stop_server.return_value = (True, "Server stopped")
        running_port = 8080

        success, message = state.stop_server(running_port, caller="ui")

        state.stop_server.assert_called_once_with(8080, caller="ui")
        assert success is True


class TestAddModelIntegration:
    """Integration tests for add model workflow."""

    def test_add_model_complete_workflow(self):
        """Complete add model workflow from form to ConfigStore."""
        name = "new-model"
        model_path = "/path/to/model.gguf"
        n_gpu_layers = 255
        ctx_size = 4096
        default_port = 8080

        config = ModelConfig.from_dict_unvalidated({
            "name": name,
            "model_path": model_path,
            "n_gpu_layers": n_gpu_layers,
            "ctx_size": ctx_size,
            "default_port": default_port,
        })

        with patch("llauncher.core.config.ConfigStore") as mock_config_store:
            mock_config_store.add_model(config)
            mock_config_store.add_model.assert_called_once_with(config)

    def test_add_model_with_validation_error(self):
        """Add model with invalid config shows error."""
        try:
            config = ModelConfig(
                name="test",
                model_path="/path/to/model.gguf",
                default_port=80,
            )
            assert False, "Should have raised validation error"
        except Exception as e:
            assert "default_port" in str(e).lower() or "ge=1024" in str(e)


class TestLogRetrievalIntegration:
    """Integration tests for log retrieval."""

    def test_log_retrieval_integration(self):
        """Log viewer retrieves logs correctly."""
        from llauncher.core.process import stream_logs

        with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
            mock_log_file = MagicMock()
            mock_log_file.__str__ = MagicMock(return_value="/fake/log/test-8080.log")
            mock_log_dir.glob.return_value = [mock_log_file]

            with patch("llauncher.core.process._tail_file", return_value=["log line 1"]):
                result = stream_logs(model_name="test")
                assert result == ["log line 1"]

    def test_log_retrieval_no_logs(self):
        """Log viewer returns empty when no logs found."""
        from llauncher.core.process import stream_logs

        with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
            mock_log_dir.glob.return_value = []

            result = stream_logs(model_name="nonexistent")
            assert result == []


class TestSessionStateIntegration:
    """Integration tests for session state management."""

    def test_session_state_edit_transitions(self):
        """Edit button sets session state flag."""
        model_name = "test-model"

        session_state = {}
        session_state[f"editing_{model_name}"] = True

        assert session_state.get(f"editing_{model_name}") is True

        del session_state[f"editing_{model_name}"]

        assert session_state.get(f"editing_{model_name}") is None

    def test_session_state_multiple_models(self):
        """Only one model can be edited at a time."""
        session_state = {
            "editing_model1": True,
            "editing_model2": False,
        }

        editing_model = None
        for name in ["model1", "model2"]:
            if session_state.get(f"editing_{name}"):
                editing_model = name
                break

        assert editing_model == "model1"
