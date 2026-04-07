"""Tests for MCP server dispatch logic."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

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
