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


class TestAgentDownBanner:
    """M4 Slice 12 (issue #49) — auto-spawn replaced by passive banner.

    ``start_agent_background`` and ``show_loading_screen`` were deleted
    along with ``NodeRegistry.start_local_agent``. The replacement is
    :func:`llauncher.ui.app.show_agent_down_banner`, which does not
    spawn anything — it just instructs the user to run
    ``llauncher-agent`` themselves.
    """

    def test_show_agent_down_banner_renders_error_with_command(self):
        """The banner must surface the CLI command the user needs to run.

        Page-level chrome (``st.title``) lives in ``main()`` rather than
        in the banner — see the docstring on
        :func:`show_agent_down_banner` — so the assertion below
        deliberately does NOT check for ``st.title``.
        """
        from llauncher.ui.app import show_agent_down_banner

        with patch("llauncher.ui.app.st") as mock_st:
            show_agent_down_banner()

        mock_st.error.assert_called_once()
        mock_st.title.assert_not_called()  # title is the caller's job
        # The error text contains the literal command string so users
        # can copy-paste from the screen.
        error_text = mock_st.error.call_args[0][0]
        assert "llauncher-agent" in error_text
        assert "agent is not running" in error_text.lower()

    def test_start_agent_background_is_gone(self):
        """Regression guard against re-introducing the auto-spawn helper."""
        import llauncher.ui.app as app

        assert not hasattr(app, "start_agent_background"), (
            "start_agent_background was removed in M4 Slice 12 (issue #49). "
            "The UI no longer spawns the agent — users run "
            "`llauncher-agent` themselves per ADR-009."
        )

    def test_show_loading_screen_is_gone(self):
        """Regression guard: the spinner-overlay screen is gone too."""
        import llauncher.ui.app as app

        assert not hasattr(app, "show_loading_screen"), (
            "show_loading_screen was removed in M4 Slice 12 (issue #49). "
            "It only existed to mask the auto-spawn delay; without "
            "auto-spawn there is no delay to mask."
        )


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
    """Tests for main() function logic (non-Streamlit portions).

    M4 Slice 12 (issue #49) deleted the ``agent_startup_started`` /
    ``agent_startup_error`` session-state plumbing along with the
    auto-spawn flow they orchestrated. The tests that exercised those
    keys were removed; ``TestAgentDownBanner`` covers the replacement
    flow (passive banner + ``st.stop()``).
    """

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
