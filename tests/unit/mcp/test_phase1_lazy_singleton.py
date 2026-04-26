"""Comprehensive tests for Phase 1 lazy singleton + per-call refresh pattern.

Tests the get_mcp_state() lazy singleton and ensures read handlers call refresh().
These tests verify the architectural fix for stale data in MCP read tool responses.
"""

import pytest
from unittest.mock import patch, MagicMock


def _reset_mcp_state():
    """Reset the global _mcp_state to None so get_mcp_state reinitializes."""
    import llauncher.mcp_server.server as server_mod

    server_mod._mcp_state = None  # type: ignore[attr-defined]


class TestGetMcpState:
    """Tests for the get_mcp_state() lazy singleton pattern."""

    def test_get_mcp_state_returns_instance(self):
        """First call creates an instance (not None)."""
        _reset_mcp_state()
        from llauncher.mcp_server.server import get_mcp_state

        result = get_mcp_state()

        assert result is not None
        assert hasattr(result, "models")
        assert hasattr(result, "running")

    def test_get_mcp_state_caches_singleton(self):
        """Second call returns the same object (identity check)."""
        _reset_mcp_state()
        from llauncher.mcp_server.server import get_mcp_state

        first = get_mcp_state()
        second = get_mcp_state()

        assert first is second, "get_mcp_state must return the cached singleton"

    def test_get_mcp_state_first_call_refreshes(self):
        """On first access, state is fresh (configs loaded from disk)."""
        import llauncher.mcp_server.server as server_mod

        with patch.object(
            server_mod, "LauncherState", wraps=server_mod.LauncherState
        ) as MockLauncherState:
            _reset_mcp_state()
            from llauncher.mcp_server.server import get_mcp_state

            result = get_mcp_state()

            # LauncherState was instantiated (triggers __post_init__ → refresh)
            MockLauncherState.assert_called_once()
            # Returned the same cached instance
            assert result is not None


class TestReadHandlersCallRefresh:
    """Tests that every read handler calls state.refresh()."""

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_list_models(self):
        """list_models calls .refresh() on state."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_state = MagicMock()
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.models import list_models

            result = await list_models(mock_state, {})

            # refresh must have been called (sync call inside async def)
            mock_state.refresh.assert_called_once()
            assert "models" in result

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_get_model_config(self):
        """get_model_config calls .refresh() on state."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_state = MagicMock()
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.models import get_model_config

            result = await get_model_config(mock_state, {"name": "test"})

            # Note: the handler calls state.refresh() directly (via state param)
            mock_state.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_server_status(self):
        """server_status calls .refresh() on state."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_state = MagicMock()
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.servers import server_status

            result = await server_status(mock_state, {})

            mock_state.refresh.assert_called_once()
            assert "running_servers" in result

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_get_server_logs(self):
        """get_server_logs calls .refresh() on state."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_state = MagicMock()
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.servers import get_server_logs

            result = await get_server_logs(mock_state, {"port": 8081})

            # refresh is called before port validation
            mock_state.refresh.assert_called_once()


class TestMutateHandlersNoExternalRefresh:
    """Document/verify that mutate handlers don't need explicit refresh.

    Mutate handlers (start_server, stop_server, etc.) modify state directly
    and are self-consistent — they write the exact changes they make.
    A subsequent read will get fresh data via per-call refresh on the read side.
    """

    @pytest.mark.asyncio
    async def test_mutate_handlers_no_external_refresh_needed(self):
        """start_server modifies state.running without needing explicit refresh()."""
        from llauncher.mcp_server.tools.servers import start_server

        mock_state = MagicMock()
        # Simulate a successful server start
        mock_state.start_server.return_value = (True, "started", MagicMock(pid=12345))

        result = await start_server(mock_state, {"model_name": "test_model"})

        assert result["success"] is True
        # start_server does NOT call state.refresh() — it directly writes to
        # state.running, making the mutation self-consistent.
        mock_state.refresh.assert_not_called()


class TestDispatchIntegration:
    """Tests verifying dispatch layer uses get_mcp_state correctly."""

    @pytest.mark.asyncio
    async def test_dispatch_uses_get_mcp_state(self):
        """Patch get_mcp_state to return MagicMock; dispatch list_models.

        Verifies that _dispatch_tool calls get_mcp_state() (and not the old
        global state variable).
        """
        with patch(
            "llauncher.mcp_server.server.get_mcp_state"
        ) as mock_get:
            mock_get.return_value = MagicMock()

            from llauncher.mcp_server.server import _dispatch_tool

            # Patch the handler to avoid real execution
            with patch(
                "llauncher.mcp_server.server.models_tools.list_models",
                return_value={"models": []},
            ):
                result = await _dispatch_tool("list_models", {})

            assert mock_get.called, "_dispatch_tool must call get_mcp_state()"
            assert result == {"models": []}

    @pytest.mark.asyncio
    async def test_list_models_passes_lazy_state_to_handler(self):
        """Patch get_mcp_state, call dispatch, verify state from get_mcp_state is passed to handler."""
        with patch(
            "llauncher.mcp_server.server.get_mcp_state"
        ) as mock_get:
            captured_state = MagicMock()
            mock_get.return_value = captured_state

            from llauncher.mcp_server.server import _dispatch_tool

            with patch(
                "llauncher.mcp_server.server.models_tools.list_models",
                return_value={"models": []},
            ) as mock_handler:
                await _dispatch_tool("list_models", {})

                # The handler should have been called with the state from get_mcp_state()
                mock_handler.assert_called_once_with(captured_state, {})


class TestGetModelConfigValidation:
    """Tests for get_model_config validation and error handling."""

    @pytest.mark.asyncio
    async def test_get_model_config_missing_name_returns_error(self):
        """Call get_model_config without 'name' arg; should return error dict."""
        with patch(
            "llauncher.mcp_server.server.get_mcp_state"
        ) as mock_get:
            mock_state = MagicMock()
            mock_state.models = {}  # empty models
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.models import get_model_config

            result = await get_model_config(mock_state, {})

            assert "error" in result
            assert "Missing required argument: name" in result["error"]

    @pytest.mark.asyncio
    async def test_get_model_config_unknown_model_returns_error(self):
        """Provide nonexistent model name; should return error."""
        with patch(
            "llauncher.mcp_server.server.get_mcp_state"
        ) as mock_get:
            mock_state = MagicMock()
            mock_state.models = {}  # no models registered
            mock_get.return_value = mock_state

            from llauncher.mcp_server.tools.models import get_model_config

            result = await get_model_config(mock_state, {"name": "nonexistent"})

            assert "error" in result
            assert "nonexistent" in result["error"]
