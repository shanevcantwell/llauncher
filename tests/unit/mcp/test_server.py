"""Tests for MCP server dispatch logic."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import llauncher.mcp.server
from llauncher.mcp.server import list_tools_handler, call_tool_handler, _dispatch_tool


class TestListTools:
    """Tests for list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self):
        """list_tools returns all tools from all modules."""
        with patch("llauncher.mcp.server.models_tools.get_tools", return_value=["model1", "model2"]):
            with patch("llauncher.mcp.server.servers_tools.get_tools", return_value=["server1", "server2", "server3", "server4"]):
                with patch("llauncher.mcp.server.config_tools.get_tools", return_value=["config1", "config2", "config3", "config4"]):
                    tools = await list_tools_handler()

                    # 2 models + 4 servers + 4 config = 10 total
                    assert len(tools) == 10
                    assert "model1" in tools
                    assert "server1" in tools
                    assert "config1" in tools


class TestCallTool:
    """Tests for call_tool handler."""

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Successful tool dispatch returns correct JSON response."""
        with patch("llauncher.mcp.server._dispatch_tool", return_value={"status": "success", "data": "test"}):
            result = await call_tool_handler("test_tool", {"arg1": "value1"})

            assert len(result) == 1
            assert "success" in result[0].text

    @pytest.mark.asyncio
    async def test_call_tool_exception(self):
        """Tool exception returns error JSON response."""
        with patch("llauncher.mcp.server._dispatch_tool", side_effect=Exception("Test error")):
            result = await call_tool_handler("test_tool", {"arg1": "value1"})

            assert len(result) == 1
            assert "error" in result[0].text
            assert "Test error" in result[0].text


class TestDispatchTool:
    """Tests for _dispatch_tool function."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_models(self):
        """Dispatch to models tools."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.models_tools.list_models", return_value="models_result"):
            result = await _dispatch_tool("list_models", {})
            assert result == "models_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_servers(self):
        """Dispatch to servers tools."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.servers_tools.start_server", return_value="server_result"):
            result = await _dispatch_tool("start_server", {})
            assert result == "server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_config(self):
        """Dispatch to config tools."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.config_tools.add_model", return_value="config_result"):
            result = await _dispatch_tool("add_model", {})
            assert result == "config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_unknown(self):
        """Unknown tool raises ValueError."""
        with pytest.raises(ValueError, match="Unknown tool"):
            await _dispatch_tool("unknown_tool", {})

    @pytest.mark.asyncio
    async def test_dispatch_tool_get_model_config(self):
        """Dispatch to get_model_config tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.models_tools.get_model_config", return_value="get_model_config_result"):
            result = await _dispatch_tool("get_model_config", {})
            assert result == "get_model_config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_stop_server(self):
        """Dispatch to stop_server tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.servers_tools.stop_server", return_value="stop_server_result"):
            result = await _dispatch_tool("stop_server", {})
            assert result == "stop_server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_swap_server(self):
        """Dispatch to swap_server tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.servers_tools.swap_server", return_value="swap_server_result"):
            result = await _dispatch_tool("swap_server", {})
            assert result == "swap_server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_server_status(self):
        """Dispatch to server_status tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.servers_tools.server_status", return_value="server_status_result"):
            result = await _dispatch_tool("server_status", {})
            assert result == "server_status_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_get_server_logs(self):
        """Dispatch to get_server_logs tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.servers_tools.get_server_logs", return_value="get_server_logs_result"):
            result = await _dispatch_tool("get_server_logs", {})
            assert result == "get_server_logs_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_update_model_config(self):
        """Dispatch to update_model_config tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.config_tools.update_model_config", return_value="update_model_config_result"):
            result = await _dispatch_tool("update_model_config", {})
            assert result == "update_model_config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_validate_config(self):
        """Dispatch to validate_config tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.config_tools.validate_config", return_value="validate_config_result"):
            result = await _dispatch_tool("validate_config", {})
            assert result == "validate_config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_remove_model(self):
        """Dispatch to remove_model tool."""
        mock_state = MagicMock()
        with patch("llauncher.mcp.server.config_tools.remove_model", return_value="remove_model_result"):
            result = await _dispatch_tool("remove_model", {})
            assert result == "remove_model_result"


class TestMainFunctions:
    """Tests for main functions in llauncher.mcp.server."""

    @pytest.mark.asyncio
    async def test_main_async(self):
        """Test main_async function."""
        with patch("llauncher.mcp.server.Server") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            # Mock the decorators to return a function that returns the original function
            mock_server.list_tools = MagicMock(return_value=lambda x: x)
            mock_server.call_tool = MagicMock(return_value=lambda x: x)

            # Mock server.run to return an async function when called
            async def mock_run(*args, **kwargs):
                pass
            mock_server.run.return_value = mock_run()

            with patch("llauncher.mcp.server.stdio_server") as mock_stdio:
                mock_read_stream = MagicMock()
                mock_write_stream = MagicMock()
                mock_stdio.return_value.__aenter__.return_value = (mock_read_stream, mock_write_stream)

                await llauncher.mcp.server.main_async()

                # Verify server was created with correct name
                mock_server_class.assert_called_once_with("llauncher")

                # Verify handlers were registered
                mock_server.list_tools.assert_called_once()
                mock_server.call_tool.assert_called_once()

                # Verify server.run was called
                mock_server.run.assert_called_once()

    def test_main(self):
        """Test main function."""
        with patch("llauncher.mcp.server.asyncio.run") as mock_asyncio_run:
            llauncher.mcp.server.main()
            mock_asyncio_run.assert_called_once()

    def test_main_entry_point(self):
        """Test the if __name__ == "__main__" block."""
        # We can't easily test the actual block without importing the module as main
        # But we can test that main function exists and is callable
        assert callable(llauncher.mcp.server.main)
