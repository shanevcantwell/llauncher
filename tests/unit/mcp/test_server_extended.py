"""Extended tests for MCP server module."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llauncher.mcp.server import main, main_async


class TestMainAsyncFullRun:
    """Tests for main_async with full server execution."""

    @pytest.mark.asyncio
    async def test_main_async_full_run(self):
        """Test main_async creates server and runs it completely."""
        with patch("llauncher.mcp.server.Server") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            # Mock the decorators to return a function that returns the original function
            # This is how the existing test does it
            mock_server.list_tools = MagicMock(return_value=lambda x: x)
            mock_server.call_tool = MagicMock(return_value=lambda x: x)

            # Mock server.run to be an async function that we can await
            async def mock_run(*args, **kwargs):
                # Simulate server running
                # Check that initialization_options was passed as third positional argument
                assert len(args) >= 3
                assert args[2] is not None  # Should be the result of create_initialization_options()
                return None

            mock_server.run = AsyncMock(side_effect=mock_run)

            with patch("llauncher.mcp.server.stdio_server") as mock_stdio:
                mock_read_stream = MagicMock()
                mock_write_stream = MagicMock()
                mock_stdio.return_value.__aenter__.return_value = (
                    mock_read_stream,
                    mock_write_stream,
                )

                # Call main_async
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
                assert len(args) >= 3
                assert args[2] is not None  # Should be the result of create_initialization_options()


class TestMainFunctionCallsAsyncioRun:
    """Tests for main function calling asyncio.run."""

    def test_main_function_calls_asyncio_run(self):
        """Test that main function calls asyncio.run with main_async."""
        with patch("llauncher.mcp.server.asyncio.run") as mock_asyncio_run:
            main()
            mock_asyncio_run.assert_called_once()
            # Verify it's calling main_async
            args, _ = mock_asyncio_run.call_args
            assert asyncio.iscoroutine(args[0]) or hasattr(args[0], "_coro")


class TestDispatchToolAllTools:
    """Tests for _dispatch_tool function covering all tools."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_all_tools(self):
        """Test dispatching to all available tools."""
        from llauncher.mcp.server import _dispatch_tool

        # Define all tools and their expected mocks
        tool_tests = [
            # Models tools
            ("list_models", "llauncher.mcp.server.models_tools.list_models", "models_result"),
            ("get_model_config", "llauncher.mcp.server.models_tools.get_model_config", "model_config_result"),

            # Servers tools
            ("start_server", "llauncher.mcp.server.servers_tools.start_server", "server_result"),
            ("stop_server", "llauncher.mcp.server.servers_tools.stop_server", "stop_result"),
            ("swap_server", "llauncher.mcp.server.servers_tools.swap_server", "swap_result"),
            ("server_status", "llauncher.mcp.server.servers_tools.server_status", "status_result"),
            ("get_server_logs", "llauncher.mcp.server.servers_tools.get_server_logs", "logs_result"),

            # Config tools
            ("update_model_config", "llauncher.mcp.server.config_tools.update_model_config", "update_result"),
            ("validate_config", "llauncher.mcp.server.config_tools.validate_config", "validate_result"),
            ("add_model", "llauncher.mcp.server.config_tools.add_model", "add_result"),
            ("remove_model", "llauncher.mcp.server.config_tools.remove_model", "remove_result"),
        ]

        for tool_name, module_path, expected_result in tool_tests:
            with patch(module_path, return_value=expected_result) as mock_func:
                result = await _dispatch_tool(tool_name, {"test_arg": "test_value"})
                assert result == expected_result
                mock_func.assert_called_once()
                # Verify the mock was called with the global state and arguments
                call_args = mock_func.call_args
                # First argument should be the global state instance
                assert hasattr(call_args[0][0], 'models')  # LauncherState has models attribute
                assert call_args[0][1] == {"test_arg": "test_value"}  # Second arg should be arguments

    @pytest.mark.asyncio
    async def test_dispatch_tool_unknown_tool(self):
        """Test that unknown tool raises ValueError."""
        from llauncher.mcp.server import _dispatch_tool

        with pytest.raises(ValueError, match="Unknown tool: unknown_tool"):
            await _dispatch_tool("unknown_tool", {})