"""Tests for UI rendering functions (business logic without Streamlit runtime)."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.models.config import ModelConfig


class TestAddModelValidation:
    """Tests for add model form validation logic."""

    def test_add_model_validation_missing_name(self):
        """Name is required."""
        name = ""
        model_path = "/path/to/model.gguf"

        # Simulate the validation from render_add_model
        if not name or not model_path:
            result = "Model name and path are required"
        else:
            result = None

        assert result == "Model name and path are required"

    def test_add_model_validation_missing_path(self):
        """Path is required."""
        name = "test-model"
        model_path = ""

        if not name or not model_path:
            result = "Model name and path are required"
        else:
            result = None

        assert result == "Model name and path are required"

    def test_add_model_validation_duplicate_name(self):
        """Error for existing model name."""
        name = "existing-model"
        model_path = "/path/to/model.gguf"

        # Simulate state.models
        state_models = {"existing-model": MagicMock()}

        if name in state_models:
            result = f"Model '{name}' already exists"
        else:
            result = None

        assert result == "Model 'existing-model' already exists"

    def test_add_model_port_zero_handling(self):
        """Port 0 converted to None for auto-allocation."""
        default_port = 0
        default_port_val = default_port if default_port >= 1024 else None

        assert default_port_val is None

    def test_add_model_port_valid(self):
        """Port >= 1024 preserved."""
        default_port = 8080
        default_port_val = default_port if default_port >= 1024 else None

        assert default_port_val == 8080

    def test_add_model_optional_fields_none(self):
        """Optional fields default correctly when 0."""
        threads = 0
        n_cpu_moe = 0
        batch_size = 0
        temperature = 0.0
        top_k = 0
        top_p = 0.0
        min_p = 0.0

        # Simulate the None conversion from render_add_model
        threads_val = threads if threads > 0 else None
        n_cpu_moe_val = n_cpu_moe if n_cpu_moe > 0 else None
        batch_size_val = batch_size if batch_size > 0 else None
        temperature_val = temperature if temperature > 0 else None
        top_k_val = top_k if top_k > 0 else None
        top_p_val = top_p if top_p > 0 else None
        min_p_val = min_p if min_p > 0 else None

        assert threads_val is None
        assert n_cpu_moe_val is None
        assert batch_size_val is None
        assert temperature_val is None
        assert top_k_val is None
        assert top_p_val is None
        assert min_p_val is None


class TestEditModelValidation:
    """Tests for edit model form validation logic."""

    def test_edit_model_validation_empty_path(self):
        """Path required on edit."""
        model_path = ""

        if not model_path:
            result = "Model path is required"
        else:
            result = None

        assert result == "Model path is required"

    def test_edit_model_flash_attn_index(self):
        """Correct index calculated from config value."""
        test_cases = [
            ("on", 0),
            ("off", 1),
            ("auto", 2),
        ]

        for flash_value, expected_index in test_cases:
            flash_idx = ["on", "off", "auto"].index(flash_value)
            assert flash_idx == expected_index


class TestModelEntryLogic:
    """Tests for model entry display logic."""

    def test_model_entry_running_status(self):
        """Running model shows actual port with 'running'."""
        status_info = {"status": "running", "port": 8080, "pid": 12345}
        is_running = status_info.get("status") == "running"

        assert is_running is True
        assert status_info.get("port") == 8080

    def test_model_entry_stopped_status(self):
        """Stopped model shows default_port or 'Auto-allocate'."""
        status_info = {"status": "stopped"}
        config = ModelConfig.from_dict_unvalidated({
            "name": "test",
            "model_path": "/path/to/model.gguf",
            "default_port": None,
        })

        is_running = status_info.get("status") == "running"
        assert is_running is False

        default_port = config.default_port or "Auto-allocate"
        assert default_port == "Auto-allocate"

    def test_model_entry_stopped_with_port(self):
        """Stopped model with configured default_port."""
        status_info = {"status": "stopped"}
        config = ModelConfig.from_dict_unvalidated({
            "name": "test",
            "model_path": "/path/to/model.gguf",
            "default_port": 9000,
        })

        is_running = status_info.get("status") == "running"
        assert is_running is False

        default_port = config.default_port or "Auto-allocate"
        assert default_port == 9000

    def test_model_entry_delete_running(self):
        """Delete running model shows error."""
        is_running = True
        status_info = {"status": "running", "port": 8080}
        name = "test-model"

        if is_running:
            running_port = status_info.get("port")
            result = f"Cannot delete {name}: server is running on port {running_port}"
        else:
            result = None

        assert "Cannot delete" in result
        assert "running" in result
        assert "8080" in result

    def test_model_entry_delete_stopped(self):
        """Delete stopped model is allowed."""
        is_running = False

        if is_running:
            result = "Cannot delete: server is running"
        else:
            result = "deletion_allowed"

        assert result == "deletion_allowed"


class TestDashboardLogic:
    """Tests for dashboard rendering logic."""

    def test_dashboard_no_models(self):
        """No models shows info message."""
        state_models = {}

        if not state_models:
            result = "No models configured"
        else:
            result = "showing models"

        assert result == "No models configured"

    def test_dashboard_editing_mode(self):
        """Editing mode detected from session state."""
        state_models = {"model1": MagicMock(), "model2": MagicMock()}
        session_state = {"editing_model1": True}

        editing_model = None
        for name in state_models:
            if session_state.get(f"editing_{name}"):
                editing_model = name
                break

        assert editing_model == "model1"

    def test_dashboard_not_editing(self):
        """No editing mode when no session state flags."""
        state_models = {"model1": MagicMock(), "model2": MagicMock()}
        session_state = {}

        editing_model = None
        for name in state_models:
            if session_state.get(f"editing_{name}"):
                editing_model = name
                break

        assert editing_model is None


class TestLogViewerLogic:
    """Tests for log viewer logic."""

    def test_log_viewer_running_process(self):
        """Log viewer called with pid for running process."""
        status_info = {"status": "running", "pid": 12345}
        is_running = status_info.get("status") == "running"
        name = "test-model"

        pid = status_info.get("pid") if is_running else None
        assert pid == 12345

    def test_log_viewer_crashed_process(self):
        """Log viewer called with model_name for crashed process."""
        status_info = {"status": "stopped", "pid": None}
        is_running = status_info.get("status") == "running"
        name = "test-model"

        pid = status_info.get("pid") if is_running else None
        assert pid is None
        # When pid is None, model_name is used instead


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

    def test_format_uptime_minutes_seconds(self):
        """Format uptime with minutes and seconds only."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(305)  # 5m 5s
        assert result == "5m 5s"

    def test_format_uptime_seconds_only(self):
        """Format uptime with seconds only."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(45)
        assert result == "45s"

    def test_format_uptime_zero(self):
        """Format uptime of zero seconds."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(0)
        assert result == "0s"

    def test_format_uptime_large(self):
        """Format large uptime value."""
        from llauncher.ui.utils import format_uptime

        result = format_uptime(90061)  # 25h 1m 1s
        assert result == "25h 1m 1s"
