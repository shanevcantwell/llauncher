"""Extended tests for MCP server module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMainAsyncFullRun:
    """Tests for main_async with full server execution."""

    @pytest.mark.asyncio
    async def test_main_async_full_run(self):
        """Test main_async creates server and runs it completely."""
        with patch("llauncher.mcp_server.server.Server") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            mock_server.list_tools = MagicMock(return_value=lambda x: x)
            mock_server.call_tool = MagicMock(return_value=lambda x: x)

            async def mock_run(*args, **kwargs):
                assert len(args) >= 3
                assert args[2] is not None
                return None

            mock_server.run = AsyncMock(side_effect=mock_run)

            with patch("llauncher.mcp_server.server.stdio_server") as mock_stdio:
                mock_read_stream = MagicMock()
                mock_write_stream = MagicMock()
                mock_stdio.return_value.__aenter__.return_value = (
                    mock_read_stream,
                    mock_write_stream,
                )

                from llauncher.mcp_server.server import main_async
                await main_async()

                # Verify server was created with correct name
                mock_server_class.assert_called_once_with("llauncher")

                # Verify handlers were registered
                mock_server.list_tools.assert_called_once()
                mock_server.call_tool.assert_called_once()

                # Verify server.run was called with correct arguments
                mock_server.run.assert_called_once()
                args, kwargs = mock_server.run.call_args
                assert args[0] == mock_read_stream
                assert args[1] == mock_write_stream


class TestMainFunctionCallsAsyncioRun:
    """Tests for main function calling asyncio.run."""

    @pytest.mark.asyncio
    async def test_main_function_calls_asyncio_run(self):
        """Test that main function calls asyncio.run with main_async."""
        from llauncher.mcp_server.server import main, main_async

        with patch("asyncio.run") as mock_asyncio_run:
            main()
            mock_asyncio_run.assert_called_once()
            args, _ = mock_asyncio_run.call_args
            assert asyncio.iscoroutine(args[0]) or hasattr(args[0], "_coro")


class TestDispatchToolAllTools:
    """Tests for _dispatch_tool function covering all tools.

    NOTE: After Phase 1 implementation (get_mcp_state lazy init), these tests
    must additionally patch get_mcp_state to return a MagicMock. See TODOs below.
    
    Import path fixed: was llauncher.mcp.server → corrected to llaunchermcp_server.
    """

    @pytest.mark.asyncio
    async def test_dispatch_tool_all_tools(self):
        """Test dispatching to all available tools."""
        from llauncher.mcp_server.server import _dispatch_tool

        tool_tests = [
            ("list_models", "llauncher.mcp_server.server.models_tools.list_models"),
            ("get_model_config", "llauncher.mcp_server.server.models_tools.get_model_config"),
            ("start_server", "llauncher.mcp_server.server.servers_tools.start_server"),
            ("stop_server", "llauncher.mcp_server.server.servers_tools.stop_server"),
            ("swap_server", "llauncher.mcp_server.server.servers_tools.swap_server"),
            ("server_status", "llauncher.mcp_server.server.servers_tools.server_status"),
            ("get_server_logs", "llauncher.mcp_server.server.servers_tools.get_server_logs"),
            ("update_model_config", "llauncher.mcp_server.server.config_tools.update_model_config"),
            ("validate_config", "llauncher.mcp_server.server.config_tools.validate_config"),
            ("add_model", "llauncher.mcp_server.server.config_tools.add_model"),
            ("remove_model", "llauncher.mcp_server.server.config_tools.remove_model"),
        ]

        for tool_name, module_path in tool_tests:
            # TODO (post Phase 1): add outer wrapper:
            #   with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            #       mock_get.return_value = MagicMock()
            expected_result = f"{tool_name}_result"
            with patch(module_path, return_value=expected_result) as mock_func:
                result = await _dispatch_tool(tool_name, {"test_arg": "test_value"})
                assert result == expected_result
                mock_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_tool_unknown_tool(self):
        """Test that unknown tool raises ValueError."""
        from llauncher.mcp_server.server import _dispatch_tool

        # TODO (post Phase 1): add get_mcp_state() mock wrapper
        with pytest.raises(ValueError, match="Unknown tool"):
            await _dispatch_tool("unknown_tool", {})
