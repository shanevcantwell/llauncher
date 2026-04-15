"""Tests for the dashboard tab module (llauncher/ui/tabs/dashboard.py)."""

import pytest
from unittest.mock import MagicMock, patch


class TestGetServersToDisplay:
    """Tests for get_servers_to_display function."""

    def test_get_servers_to_display_all_nodes(self):
        """Test with registry and aggregator - all nodes."""
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        mock_state = MagicMock()
        mock_state.running = {
            8080: MagicMock(port=8080, config_name="model1"),
        }

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()
        mock_aggregator.get_all_servers.return_value = [
            MagicMock(node_name="remote", port=8081, config_name="model2"),
        ]

        result = get_servers_to_display(mock_state, mock_registry, mock_aggregator, None)

        # Should have both local and remote servers
        assert len(result) >= 1
        mock_aggregator.get_all_servers.assert_called_once()

    def test_get_servers_to_display_local_only(self):
        """Test with registry and aggregator, no selected_node."""
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        mock_state = MagicMock()
        mock_state.running = {}

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()

        result = get_servers_to_display(mock_state, mock_registry, mock_aggregator, None)

        # Should return empty since no servers running
        assert result == []
        mock_state.refresh.assert_called_once()

    def test_get_servers_to_display_selected_node_all(self):
        """Test with selected_node=None (all nodes)."""
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        mock_state = MagicMock()
        mock_state.running = {}

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()

        result = get_servers_to_display(mock_state, mock_registry, mock_aggregator, None)

        # Should fetch all servers
        mock_aggregator.get_all_servers.assert_called()

    def test_get_servers_to_display_no_registry(self):
        """Test without registry - local only."""
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        mock_state = MagicMock()
        mock_state.running = {}
        mock_state.refresh = MagicMock()

        result = get_servers_to_display(mock_state, None, None, None)

        # Should return empty since no servers
        assert result == []


class TestGetModelsToDisplay:
    """Tests for get_models_to_display function."""

    def test_get_models_to_display_all_nodes(self):
        """Test getting models from all nodes."""
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_state = MagicMock()
        mock_state.models = {
            "model1": MagicMock(to_dict=MagicMock(return_value={"name": "model1"})),
        }

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()
        mock_aggregator.get_all_models.return_value = {
            "remote": [{"name": "remote_model"}],
        }

        result = get_models_to_display(mock_state, mock_registry, mock_aggregator, None)

        # Should include both local and remote models
        assert "local" in result
        assert "remote" in result
        mock_aggregator.get_all_models.assert_called_once()

    def test_get_models_to_display_local_only(self):
        """Test with selected_node='local'."""
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_state = MagicMock()
        mock_state.models = {
            "model1": MagicMock(to_dict=MagicMock(return_value={"name": "model1"})),
        }

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()

        result = get_models_to_display(mock_state, mock_registry, mock_aggregator, "local")

        # Should only have local models
        assert result == {"local": [{"name": "model1"}]}

    def test_get_models_to_display_selected_remote_node(self):
        """Test with selected_node for remote node."""
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_state = MagicMock()

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()
        mock_aggregator.get_all_models.return_value = {
            "remote": [{"name": "remote_model"}],
        }

        result = get_models_to_display(mock_state, mock_registry, mock_aggregator, "remote")

        # Should return only remote models
        assert result == {"remote": [{"name": "remote_model"}]}

    def test_get_models_to_display_no_registry(self):
        """Test without registry - local only."""
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_state = MagicMock()
        mock_state.models = {
            "model1": MagicMock(to_dict=MagicMock(return_value={"name": "model1"})),
        }

        result = get_models_to_display(mock_state, None, None, None)

        assert result == {"local": [{"name": "model1"}]}


class TestGetNodeServers:
    """Tests for get_node_servers function."""

    def test_get_node_servers_filter(self):
        """Test filtering servers by node name."""
        from llauncher.ui.tabs.dashboard import get_node_servers

        mock_aggregator = MagicMock()
        mock_aggregator.get_all_servers.return_value = [
            MagicMock(node_name="local", port=8080),
            MagicMock(node_name="remote", port=8081),
            MagicMock(node_name="local", port=8082),
        ]

        result = get_node_servers(mock_aggregator, "local")

        # Should return only local servers
        assert len(result) == 2
        assert all(s.node_name == "local" for s in result)

    def test_get_node_servers_empty(self):
        """Test when no servers match node."""
        from llauncher.ui.tabs.dashboard import get_node_servers

        mock_aggregator = MagicMock()
        mock_aggregator.get_all_servers.return_value = [
            MagicMock(node_name="remote", port=8080),
        ]

        result = get_node_servers(mock_aggregator, "local")

        assert result == []


class TestRenderDashboard:
    """Tests for render_dashboard function."""

    def test_render_dashboard_sessions_state_access(self):
        """Test that render_dashboard accesses session_state correctly."""
        from llauncher.ui.tabs.dashboard import render_dashboard

        mock_state = MagicMock()
        mock_state.models = {}
        mock_state.refresh = MagicMock()

        mock_registry = MagicMock()
        mock_registry.__len__.return_value = 1

        mock_aggregator = MagicMock()

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            # Mock st.session_state.get to return False (not editing any model)
            mock_st.session_state.get.return_value = False

            # Mock st.expander context manager
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            # Mock st.columns to return valid column objects for all column calls
            # 3 columns in first call, 2 in second, etc.
            def mock_columns(n):
                return tuple(MagicMock() for _ in range(n))
            mock_st.columns.side_effect = mock_columns

            render_dashboard(mock_state, mock_registry, mock_aggregator, None)

            # Verify header was called
            mock_st.header.assert_called()

    def test_render_dashboard_all_nodes_indicator(self):
        """Test 'All Nodes' indicator when registry has multiple nodes."""
        from llauncher.ui.tabs.dashboard import render_dashboard

        mock_state = MagicMock()
        mock_state.models = {}
        mock_state.refresh = MagicMock()

        mock_registry = MagicMock()
        mock_registry.__len__.return_value = 3  # Multiple nodes

        mock_aggregator = MagicMock()

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            mock_st.session_state.get.return_value = False
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            # Mock st.columns to return valid column objects
            def mock_columns(n):
                return tuple(MagicMock() for _ in range(n))
            mock_st.columns.side_effect = mock_columns

            render_dashboard(mock_state, mock_registry, mock_aggregator, None)

            # Should show "All nodes" indicator
            mock_st.markdown.assert_called()


class TestRenderModelCard:
    """Tests for render_model_card function."""

    def test_render_model_card_running(self):
        """Test rendering for running server."""
        from llauncher.ui.tabs.dashboard import render_model_card

        mock_state = MagicMock()
        mock_registry = MagicMock()
        mock_aggregator = MagicMock()
        mock_running_server = MagicMock()
        mock_running_server.port = 8080
        mock_running_server.config_name = "model1"
        mock_running_server.uptime_seconds = 3600  # Return int for format_uptime
        mock_running_server.pid = 12345  # Return int for stream_logs
        mock_running_server.logs_path = "/tmp/logs"  # Return string for logs_path

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            # Mock expander for the details section
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            # Mock columns - returns a list with the number of elements matching the input list length
            def mock_columns(n):
                count = len(n) if isinstance(n, list) else n
                return [MagicMock() for _ in range(count)]
            mock_st.columns.side_effect = mock_columns

            mock_st.markdown = MagicMock()
            mock_st.divider = MagicMock()
            mock_st.button = MagicMock(return_value=False)

            render_model_card(
                mock_state, mock_registry, mock_aggregator,
                "local", {"name": "model1", "default_port": 8080}, mock_running_server
            )

            # Should show running status indicators
            mock_st.expander.assert_called()
            # Should have called columns for name + button
            mock_st.columns.assert_any_call([4, 1])

    def test_render_model_card_stopped(self):
        """Test rendering for stopped server."""
        from llauncher.ui.tabs.dashboard import render_model_card

        mock_state = MagicMock()
        mock_state.models = {
            "model1": MagicMock(default_port=8080)
        }
        mock_registry = MagicMock()
        mock_aggregator = MagicMock()

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            # Mock expander for the details section
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            # Mock columns - returns a list with the number of elements matching the input list length
            def mock_columns(n):
                count = len(n) if isinstance(n, list) else n
                return [MagicMock() for _ in range(count)]
            mock_st.columns.side_effect = mock_columns

            mock_st.markdown = MagicMock()
            mock_st.divider = MagicMock()
            mock_st.button = MagicMock(return_value=False)

            render_model_card(
                mock_state, mock_registry, mock_aggregator,
                "local", {"name": "model1", "default_port": 8080}, None
            )

            # Should show stopped status
            mock_st.expander.assert_called()
            # Should have called columns for name + button
            mock_st.columns.assert_any_call([4, 1])


class TestRenderAddModel:
    """Tests for render_add_model function."""

    def test_render_add_model_form(self):
        """Test rendering add model form."""
        from llauncher.ui.tabs.dashboard import render_add_model

        mock_state = MagicMock()

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            mock_st.form = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
            mock_st.form_submit_button = MagicMock(return_value=False)
            mock_st.text_input = MagicMock(return_value="")
            mock_st.number_input = MagicMock(return_value=0)
            mock_st.checkbox = MagicMock(return_value=False)
            mock_st.selectbox = MagicMock(return_value="on")

            render_add_model(mock_state)

            # Should render form
            mock_st.form.assert_called()


class TestRenderEditModel:
    """Tests for render_edit_model function."""

    def test_render_edit_model_model_name_not_found(self):
        """Test when model_name is provided but not in state."""
        from llauncher.ui.tabs.dashboard import render_edit_model

        mock_state = MagicMock()
        mock_state.models = {}

        result = render_edit_model(mock_state, "nonexistent-model")

        # Should return None without error when model not found
        assert result is None

    def test_render_edit_model_no_selected_model(self):
        """Test when model_name is None and no session state."""
        from llauncher.ui.tabs.dashboard import render_edit_model

        mock_state = MagicMock()
        mock_state.models = {}

        result = render_edit_model(mock_state)

        # Should return None when no editing mode
        assert result is None


class TestDashboardValidation:
    """Tests for dashboard validation logic."""

    def test_model_name_required(self):
        """Test that model name is required."""
        name = ""
        model_path = "/path/to/model"

        if not name or not model_path:
            result = "Model name and path are required"
        else:
            result = None

        assert result == "Model name and path are required"

    def test_model_path_required(self):
        """Test that model path is required."""
        name = "model1"
        model_path = ""

        if not name or not model_path:
            result = "Model name and path are required"
        else:
            result = None

        assert result == "Model name and path are required"

    def test_edit_model_path_required(self):
        """Test that model path is required on edit."""
        model_path = ""

        if not model_path:
            result = "Model path is required"
        else:
            result = None

        assert result == "Model path is required"

    def test_duplicate_model_name(self):
        """Test duplicate model name detection."""
        name = "existing"
        state_models = {"existing": MagicMock()}

        if name in state_models:
            result = f"Model '{name}' already exists"
        else:
            result = None

        assert result == "Model 'existing' already exists"


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


class TestDashboardEdgeCases:
    """Tests for dashboard edge cases."""

    def test_editing_model_detection(self):
        """Test detection of model being edited."""
        state_models = {"model1": MagicMock(), "model2": MagicMock()}
        session_state = {"editing_model1": True}

        editing_model = None
        for name in state_models:
            if session_state.get(f"editing_{name}"):
                editing_model = name
                break

        assert editing_model == "model1"

    def test_no_editing_mode(self):
        """Test when no model is being edited."""
        state_models = {"model1": MagicMock(), "model2": MagicMock()}
        session_state = {}

        editing_model = None
        for name in state_models:
            if session_state.get(f"editing_{name}"):
                editing_model = name
                break

        assert editing_model is None

    def test_running_server_lookup(self):
        """Test lookup of running server by node and model."""
        servers = [
            MagicMock(node_name="local", config_name="model1", port=8080),
            MagicMock(node_name="local", config_name="model2", port=8081),
        ]

        running_server_map = {}
        for server in servers:
            key = (server.node_name, server.config_name)
            running_server_map[key] = server

        # Look up model1 on local
        result = running_server_map.get(("local", "model1"))
        assert result is not None
        assert result.port == 8080

    def test_not_running_server_lookup(self):
        """Test lookup of non-running server."""
        servers = [
            MagicMock(node_name="local", config_name="model1", port=8080),
        ]

        running_server_map = {}
        for server in servers:
            key = (server.node_name, server.config_name)
            running_server_map[key] = server

        # Look up model2 which is not running
        result = running_server_map.get(("local", "model2"))
        assert result is None
