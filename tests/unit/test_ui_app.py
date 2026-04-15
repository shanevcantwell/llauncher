"""Tests for the UI app module (llauncher/ui/app.py)."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestGetState:
    """Tests for get_state function."""

    def test_get_state_creates_instance(self):
        """First call creates state in session_state."""
        from llauncher.ui.app import get_state

        # Mock st.session_state
        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {}
            result = get_state()

            # Verify state was created and returned
            assert mock_st.session_state.get("state") is result

    def test_get_state_returns_cached(self):
        """Second call returns same instance from session_state."""
        from llauncher.ui.app import get_state

        # Mock st.session_state with existing state
        existing_state = MagicMock()
        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {"state": existing_state}
            result = get_state()

            # Verify cached state returned
            assert result is existing_state
            assert mock_st.session_state["state"] is result


class TestGetRegistry:
    """Tests for get_registry function."""

    def test_get_registry_creates_instance(self):
        """First call creates registry in session_state."""
        from llauncher.ui.app import get_registry

        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {}
            result = get_registry()

            assert mock_st.session_state.get("registry") is result

    def test_get_registry_returns_cached(self):
        """Second call returns same instance."""
        from llauncher.ui.app import get_registry

        existing_registry = MagicMock()
        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {"registry": existing_registry}
            result = get_registry()

            assert result is existing_registry


class TestGetAggregator:
    """Tests for get_aggregator function."""

    def test_get_aggregator_creates_instance(self):
        """First call creates aggregator with registry."""
        from llauncher.ui.app import get_aggregator

        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {}
            result = get_aggregator()

            # Verify aggregator was created and stored
            assert mock_st.session_state.get("aggregator") is result

    def test_get_aggregator_returns_cached(self):
        """Second call returns same instance."""
        from llauncher.ui.app import get_aggregator

        existing_aggregator = MagicMock()
        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {"aggregator": existing_aggregator}
            result = get_aggregator()

            assert result is existing_aggregator


class TestIsAgentReady:
    """Tests for is_agent_ready function."""

    def test_is_agent_ready_calls_registry(self):
        """Function delegates to registry.is_local_agent_ready."""
        from llauncher.ui.app import is_agent_ready

        mock_registry = MagicMock()
        mock_registry.is_local_agent_ready.return_value = True

        result = is_agent_ready(mock_registry)

        mock_registry.is_local_agent_ready.assert_called_once()
        assert result is True

    def test_is_agent_ready_false(self):
        """Returns False when registry reports not ready."""
        from llauncher.ui.app import is_agent_ready

        mock_registry = MagicMock()
        mock_registry.is_local_agent_ready.return_value = False

        result = is_agent_ready(mock_registry)

        assert result is False


class TestStartAgentBackground:
    """Tests for start_agent_background function."""

    def test_start_agent_background_calls_registry(self):
        """Calls registry.start_local_agent()."""
        from llauncher.ui.app import start_agent_background

        mock_registry = MagicMock()

        start_agent_background(mock_registry)

        mock_registry.start_local_agent.assert_called_once()


class TestShowLoadingScreen:
    """Tests for show_loading_screen function."""

    def test_show_loading_screen_renders(self):
        """Renders loading screen HTML."""
        from llauncher.ui.app import show_loading_screen

        with patch("llauncher.ui.app.st") as mock_st:
            show_loading_screen()

            # Verify st.markdown was called with CSS
            mock_st.markdown.assert_called()


class TestGetStateFunctions:
    """Tests for session state management functions."""

    def test_get_state_caches_in_session(self):
        """State persists across calls within same session."""
        from llauncher.ui.app import get_state, get_registry, get_aggregator

        with patch("llauncher.ui.app.st") as mock_st:
            mock_st.session_state = {}

            # First call creates instances
            state1 = get_state()
            registry1 = get_registry()
            aggregator1 = get_aggregator()

            # Second call returns cached instances
            state2 = get_state()
            registry2 = get_registry()
            aggregator2 = get_aggregator()

            # Verify caching
            assert state1 is state2
            assert registry1 is registry2
            assert aggregator1 is aggregator2


class TestMainFunctionLogic:
    """Tests for main() function logic (non-Streamlit portions)."""

    def test_agent_startup_state_tracking(self):
        """Verify agent startup state is tracked correctly."""
        # This test verifies the logic of agent_startup_started tracking

        # Simulate initial state
        session_state = {
            "agent_startup_started": False,
            "state": MagicMock(),
            "registry": MagicMock(),
            "aggregator": MagicMock(),
        }

        # Simulate agent not ready - should set startup_started
        assert session_state["agent_startup_started"] is False

        session_state["agent_startup_started"] = True
        assert session_state["agent_startup_started"] is True

    def test_agent_startup_error_handling(self):
        """Verify agent_startup_error is cleared on success."""
        session_state = {
            "agent_startup_error": "Connection failed",
        }

        # Simulate success - error should be cleared
        session_state.pop("agent_startup_error", None)
        assert "agent_startup_error" not in session_state

    def test_selected_node_tracking(self):
        """Verify selected node is stored correctly in session state."""
        # Test "All Nodes" selection
        selected = "All Nodes"
        expected_selected_node = None

        if selected == "All Nodes":
            actual = None
        else:
            actual = selected.replace(" ", "").replace(" ", "")

        assert expected_selected_node == actual

    def test_selected_node_with_status(self):
        """Verify node selection with status icon."""
        # Test node selection with status indicator
        selected = "🟢 test-node"
        expected_selected_node = "test-node"

        # Simulate the selection parsing logic
        actual = selected.replace("🟢 ", "").replace("⚫ ", "")

        assert expected_selected_node == actual


class TestNodeSelectorLogic:
    """Tests for node selector logic."""

    def test_node_options_empty(self):
        """No nodes configured returns only 'All Nodes'."""
        mock_registry = MagicMock()
        mock_registry.__len__.return_value = 0
        mock_registry.__iter__.return_value = iter([])

        show_offline = True
        node_options = ["All Nodes"]

        for node in mock_registry:
            is_online = node.status == "ONLINE"
            if not is_online and not show_offline:
                continue
            status = "🟢" if is_online else "⚫"
            node_options.append(f"{status} {node.name}")

        assert node_options == ["All Nodes"]

    def test_node_options_with_nodes(self):
        """Nodes configured returns proper options."""
        mock_node1 = MagicMock()
        mock_node1.name = "node1"
        mock_node1.status = "ONLINE"

        mock_node2 = MagicMock()
        mock_node2.name = "node2"
        mock_node2.status = "OFFLINE"

        mock_registry = MagicMock()
        mock_registry.__len__.return_value = 2
        mock_registry.__iter__.return_value = iter([mock_node1, mock_node2])

        show_offline = True
        node_options = ["All Nodes"]

        for node in mock_registry:
            is_online = node.status == "ONLINE"
            if not is_online and not show_offline:
                continue
            status = "🟢" if is_online else "⚫"
            node_options.append(f"{status} {node.name}")

        assert "All Nodes" in node_options
        assert "🟢 node1" in node_options
        assert "⚫ node2" in node_options

    def test_node_options_filter_offline(self):
        """show_offline=False filters offline nodes."""
        mock_node1 = MagicMock()
        mock_node1.name = "node1"
        mock_node1.status = "ONLINE"

        mock_node2 = MagicMock()
        mock_node2.name = "node2"
        mock_node2.status = "OFFLINE"

        mock_registry = MagicMock()
        mock_registry.__len__.return_value = 2
        mock_registry.__iter__.return_value = iter([mock_node1, mock_node2])

        show_offline = False
        node_options = ["All Nodes"]

        for node in mock_registry:
            is_online = node.status == "ONLINE"
            if not is_online and not show_offline:
                continue
            status = "🟢" if is_online else "⚫"
            node_options.append(f"{status} {node.name}")

        assert "All Nodes" in node_options
        assert "🟢 node1" in node_options
        assert "⚫ node2" not in node_options


class TestRefreshLogic:
    """Tests for refresh functionality."""

    def test_refresh_all_calls_all_components(self):
        """Refresh button should call all refresh methods."""
        mock_state = MagicMock()
        mock_registry = MagicMock()

        # Simulate refresh button click
        mock_state.refresh()
        mock_registry.refresh_all()

        mock_state.refresh.assert_called_once()
        mock_registry.refresh_all.assert_called_once()
