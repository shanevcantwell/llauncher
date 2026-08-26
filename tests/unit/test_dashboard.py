"""Tests for the dashboard tab module (llauncher/ui/tabs/dashboard.py).

Stage 2 of M4 Slice 13 (#50) made the dashboard view-only and
target-string-only. The legacy ``selected_node=None`` "All Nodes"
branch and the inline add/edit forms have moved to the Models tab —
their tests live in ``test_models_tab.py`` now.
"""

from unittest.mock import MagicMock, patch


class TestGetServersToDisplay:
    """Tests for get_servers_to_display function."""

    def test_local_target_returns_local_servers(self):
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        mock_state = MagicMock()
        mock_state.running = {}

        mock_registry = MagicMock()
        mock_aggregator = MagicMock()

        result = get_servers_to_display(mock_state, mock_registry, mock_aggregator, "local")

        assert result == []
        # #497: the per-run refresh is hoisted to app.py; this helper
        # reads state as already fresh and must not refresh again.
        mock_state.refresh.assert_not_called()
        # Aggregator must NOT be queried for the local target.
        mock_aggregator.get_all_servers.assert_not_called()

    def test_local_target_includes_running_servers(self):
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        from datetime import datetime

        mock_state = MagicMock()
        running_server = MagicMock()
        running_server.pid = 1234
        running_server.port = 8080
        running_server.config_name = "model1"
        running_server.start_time = datetime.now()
        running_server.uptime_seconds.return_value = 60
        running_server.logs_path = "/tmp/logs"
        mock_state.running = {8080: running_server}

        result = get_servers_to_display(mock_state, MagicMock(), MagicMock(), "local")

        assert len(result) == 1
        assert result[0].node_name == "local"
        assert result[0].port == 8080
        assert result[0].config_name == "model1"

    def test_remote_target_filters_aggregator(self):
        from llauncher.ui.tabs.dashboard import get_servers_to_display

        local = MagicMock(node_name="local", port=8080, config_name="m1")
        remote = MagicMock(node_name="gpu-rig", port=8081, config_name="m2")

        mock_aggregator = MagicMock()
        mock_aggregator.get_all_servers.return_value = [local, remote]

        result = get_servers_to_display(MagicMock(), MagicMock(), mock_aggregator, "gpu-rig")

        assert result == [remote]


class TestGetModelsToDisplay:
    """Tests for get_models_to_display function."""

    def test_local_target_returns_local_model_dicts(self):
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_state = MagicMock()
        mock_state.models = {
            "m1": MagicMock(to_dict=MagicMock(return_value={"name": "m1"})),
        }

        result = get_models_to_display(mock_state, MagicMock(), MagicMock(), "local")

        assert result == [{"name": "m1"}]

    def test_remote_target_pulls_from_aggregator(self):
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_aggregator = MagicMock()
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [{"name": "remote_m"}],
        }

        result = get_models_to_display(MagicMock(), MagicMock(), mock_aggregator, "gpu-rig")

        assert result == [{"name": "remote_m"}]

    def test_remote_target_missing_returns_empty(self):
        from llauncher.ui.tabs.dashboard import get_models_to_display

        mock_aggregator = MagicMock()
        mock_aggregator.get_all_models.return_value = {}

        result = get_models_to_display(MagicMock(), MagicMock(), mock_aggregator, "gpu-rig")

        assert result == []


class TestRenderDashboard:
    """Tests for the view-only render_dashboard."""

    def test_render_dashboard_local_no_servers(self):
        from llauncher.ui.tabs.dashboard import render_dashboard

        mock_state = MagicMock()
        mock_state.models = {}
        mock_state.running = {}

        with patch("llauncher.ui.tabs.dashboard.st") as mock_st:
            render_dashboard(mock_state, MagicMock(), MagicMock(), "local")

            mock_st.header.assert_called()
            mock_st.info.assert_called()  # "No servers running on local"

    def test_render_dashboard_does_not_call_forms(self):
        """Add/edit forms moved to Models tab; dashboard must not import them."""
        from llauncher.ui.tabs import dashboard

        # The dashboard module no longer imports the form helpers — those
        # are intentionally constrained to the Models tab.
        assert not hasattr(dashboard, "render_add_model")
        assert not hasattr(dashboard, "render_edit_model")


class TestUptimeFormatting:
    """Tests for format_uptime function."""

    def test_format_uptime_hours_minutes_seconds(self):
        from llauncher.ui.utils import format_uptime

        result = format_uptime(9245)
        assert result == "2h 34m 5s"

    def test_format_uptime_hours_minutes(self):
        from llauncher.ui.utils import format_uptime

        result = format_uptime(9000)
        assert result == "2h 30m"

    def test_format_uptime_seconds_only(self):
        from llauncher.ui.utils import format_uptime

        result = format_uptime(45)
        assert result == "45s"


class TestRenderModelCardLegacyShim:
    """Verb surface lives on model_card.py; the dashboard no longer calls it.

    Keep a couple of smoke tests on ``render_model_card`` itself here
    (lighter than spinning up the full Models tab) to catch import-time
    breakage in the card module after the consolidation.
    """

    def test_render_model_card_running(self):
        from llauncher.ui.tabs.model_card import render_model_card

        mock_state = MagicMock()
        mock_running_server = MagicMock()
        mock_running_server.port = 8080
        mock_running_server.config_name = "model1"
        mock_running_server.uptime_seconds = 3600
        mock_running_server.pid = 12345
        mock_running_server.logs_path = "/tmp/logs"

        with patch("llauncher.ui.tabs.model_card.st") as mock_st:
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            def mock_columns(n):
                count = len(n) if isinstance(n, list) else n
                return [MagicMock() for _ in range(count)]
            mock_st.columns.side_effect = mock_columns

            mock_st.button = MagicMock(return_value=False)

            render_model_card(
                mock_state, MagicMock(), MagicMock(),
                "local", {"name": "model1"}, mock_running_server,
            )

            mock_st.expander.assert_called()

    def test_render_model_card_stopped_renders_picker(self):
        """The stopped-card path must render the port picker now."""
        from llauncher.ui.tabs.model_card import render_model_card

        mock_state = MagicMock()
        mock_state.models = {"model1": MagicMock()}
        mock_state.running = {}

        with patch("llauncher.ui.tabs.model_card.st") as mock_st, patch(
            "llauncher.ui.tabs.model_card.render_port_picker"
        ) as mock_picker:
            mock_expander = MagicMock()
            mock_expander.__enter__ = MagicMock(return_value=None)
            mock_expander.__exit__ = MagicMock(return_value=None)
            mock_st.expander.return_value = mock_expander

            def mock_columns(n):
                count = len(n) if isinstance(n, list) else n
                return [MagicMock() for _ in range(count)]
            mock_st.columns.side_effect = mock_columns

            mock_st.button = MagicMock(return_value=False)
            mock_picker.return_value = None  # user hasn't entered a port yet

            render_model_card(
                mock_state, MagicMock(), MagicMock(),
                "local", {"name": "model1"}, None,
            )

            mock_picker.assert_called_once()


class TestDashboardEdgeCases:
    """Tests for dashboard edge cases."""

    def test_running_server_lookup(self):
        servers = [
            MagicMock(node_name="local", config_name="model1", port=8080),
            MagicMock(node_name="local", config_name="model2", port=8081),
        ]

        running_server_map = {}
        for server in servers:
            key = (server.node_name, server.config_name)
            running_server_map[key] = server

        result = running_server_map.get(("local", "model1"))
        assert result is not None
        assert result.port == 8080

    def test_not_running_server_lookup(self):
        servers = [
            MagicMock(node_name="local", config_name="model1", port=8080),
        ]

        running_server_map = {}
        for server in servers:
            key = (server.node_name, server.config_name)
            running_server_map[key] = server

        result = running_server_map.get(("local", "model2"))
        assert result is None
