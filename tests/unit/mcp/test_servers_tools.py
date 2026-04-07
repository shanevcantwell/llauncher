"""Tests for MCP servers tools."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.mcp.tools.servers import (
    start_server,
    stop_server,
    server_status,
    get_server_logs,
    get_tools,
)
from llauncher.models.config import RunningServer
from datetime import datetime


@pytest.fixture
def mock_state():
    """Mock LauncherState with test data."""
    state = MagicMock()
    state.running = {
        8080: RunningServer(
            pid=12345,
            port=8080,
            config_name="test-model",
            start_time=datetime.now(),
        )
    }
    return state


class TestStartServer:
    """Tests for start_server tool."""

    @pytest.mark.asyncio
    async def test_start_server_missing_model_name(self):
        """Returns error for missing model_name argument."""
        mock_state = MagicMock()
        result = await start_server(mock_state, {})

        assert result["success"] is False
        assert "model_name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_start_server_success(self, mock_state):
        """Wraps state.start_server() success."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_state.start_server.return_value = (True, "Server started", mock_process)

        result = await start_server(mock_state, {"model_name": "test-model"})

        assert result["success"] is True
        assert result["pid"] == 12345

    @pytest.mark.asyncio
    async def test_start_server_failure(self, mock_state):
        """Wraps state.start_server() failure."""
        mock_state.start_server.return_value = (False, "Port already in use", None)

        result = await start_server(mock_state, {"model_name": "test-model"})

        assert result["success"] is False
        assert "Port already in use" in result["message"]


class TestStopServer:
    """Tests for stop_server tool."""

    @pytest.mark.asyncio
    async def test_stop_server_missing_port(self):
        """Returns error for missing port argument."""
        mock_state = MagicMock()
        result = await stop_server(mock_state, {})

        assert result["success"] is False
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_stop_server_success(self, mock_state):
        """Wraps state.stop_server() success."""
        mock_state.stop_server.return_value = (True, "Server stopped")

        result = await stop_server(mock_state, {"port": 8080})

        assert result["success"] is True
        assert "stopped" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_stop_server_failure(self, mock_state):
        """Wraps state.stop_server() failure."""
        mock_state.stop_server.return_value = (False, "No server on port")

        result = await stop_server(mock_state, {"port": 8080})

        assert result["success"] is False
        assert "No server on port" in result["message"]


class TestServerStatus:
    """Tests for server_status tool."""

    @pytest.mark.asyncio
    async def test_server_status_empty(self):
        """No running servers returns empty list."""
        mock_state = MagicMock()
        mock_state.running = {}

        result = await server_status(mock_state, {})

        assert result["running_servers"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_server_status_multiple(self, mock_state):
        """Multiple running servers returned."""
        result = await server_status(mock_state, {})

        assert result["count"] == 1
        server = result["running_servers"][0]
        assert server["pid"] == 12345
        assert server["port"] == 8080


class TestGetServerLogs:
    """Tests for get_server_logs tool."""

    @pytest.mark.asyncio
    async def test_get_server_logs_missing_port(self):
        """Returns error for missing port argument."""
        mock_state = MagicMock()
        result = await get_server_logs(mock_state, {})

        assert "error" in result
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_get_server_logs_not_found(self, mock_state):
        """Returns error for unknown port."""
        result = await get_server_logs(mock_state, {"port": 9999})

        assert "error" in result
        assert "no server" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_get_server_logs_success(self, mock_state):
        """Returns logs from stream_logs()."""
        with patch("llauncher.mcp.tools.servers.stream_logs", return_value=["log line 1", "log line 2"]):
            result = await get_server_logs(mock_state, {"port": 8080})

            assert result["logs"] == ["log line 1", "log line 2"]

    @pytest.mark.asyncio
    async def test_get_server_logs_custom_lines(self, mock_state):
        """Custom lines parameter passed through."""
        with patch("llauncher.mcp.tools.servers.stream_logs") as mock_stream:
            await get_server_logs(mock_state, {"port": 8080, "lines": 500})

            mock_stream.assert_called_once()
            call_args = mock_stream.call_args[0]
            assert call_args[1] == 500


class TestGetTools:
    """Tests for get_tools function."""

    def test_get_tools_returns_four_tools(self):
        """get_tools returns four server tools."""
        tools = get_tools()

        assert len(tools) == 4
        tool_names = [t.name for t in tools]
        assert "start_server" in tool_names
        assert "stop_server" in tool_names
        assert "server_status" in tool_names
        assert "get_server_logs" in tool_names
