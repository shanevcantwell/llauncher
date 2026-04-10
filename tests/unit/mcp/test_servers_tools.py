"""Tests for MCP servers tools."""

import pytest
from unittest.mock import patch, MagicMock

from llauncher.mcp.tools.servers import (
    start_server,
    stop_server,
    swap_server,
    server_status,
    get_server_logs,
    get_tools,
)
from llauncher.models.config import RunningServer, ModelConfig
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


class TestSwapServer:
    """Tests for swap_server tool."""

    @pytest.mark.asyncio
    async def test_swap_server_missing_port(self):
        """Returns error for missing port argument."""
        mock_state = MagicMock()
        result = await swap_server(mock_state, {"model_name": "test-model"})

        assert result["success"] is False
        assert "port" in result["error"].lower()
        assert result["port_state"] == "unchanged"

    @pytest.mark.asyncio
    async def test_swap_server_missing_model_name(self):
        """Returns error for missing model_name argument."""
        mock_state = MagicMock()
        result = await swap_server(mock_state, {"port": 8080})

        assert result["success"] is False
        assert "model_name" in result["error"].lower()
        assert result["port_state"] == "unchanged"

    @pytest.mark.asyncio
    async def test_swap_server_model_not_found(self):
        """Returns error if new model doesn't exist."""
        mock_state = MagicMock()
        mock_state.models = {}

        result = await swap_server(mock_state, {"port": 8080, "model_name": "nonexistent"})

        assert result["success"] is False
        assert "not found" in result["error"].lower()
        assert result["port_state"] == "unchanged"

    @pytest.mark.asyncio
    async def test_swap_server_new_model_already_running(self):
        """Returns error if new model is already running elsewhere."""
        mock_state = MagicMock()
        mock_state.models = {"new-model": MagicMock(model_path="/exists")}
        mock_state.running = {
            8081: RunningServer(
                pid=11111,
                port=8081,
                config_name="new-model",
                start_time=datetime.now(),
            )
        }

        with patch("llauncher.mcp.tools.servers.Path") as mock_path:
            mock_path.exists.return_value = True
            result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model"})

        assert result["success"] is False
        assert "already running" in result["error"].lower()
        assert result["port_state"] == "unchanged"

    @pytest.mark.asyncio
    async def test_swap_server_old_model_no_persisted_config(self):
        """Returns error if old model has no persisted config for rollback."""
        mock_state = MagicMock()
        mock_state.models = {"new-model": MagicMock(model_path="/exists")}
        mock_state.running = {
            8080: RunningServer(
                pid=12345,
                port=8080,
                config_name="script-only-model",  # Not in state.models
                start_time=datetime.now(),
            )
        }

        with patch("llauncher.mcp.tools.servers.Path") as mock_path:
            mock_path.exists.return_value = True
            result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model"})

        assert result["success"] is False
        assert "persisted config" in result["error"].lower()
        assert result["port_state"] == "unchanged"

    @pytest.mark.asyncio
    async def test_swap_server_success_no_previous_model(self):
        """Successful swap when port is empty."""
        mock_state = MagicMock()
        mock_state.models = {"new-model": MagicMock(model_path="/exists")}
        mock_state.running = {}

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_state.start_server.return_value = (True, "Started", mock_process)

        with patch("llauncher.mcp.tools.servers.wait_for_server_ready", return_value=(True, [])):
            with patch("llauncher.mcp.tools.servers.Path") as mock_path:
                mock_path.exists.return_value = True
                result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model"})

        assert result["success"] is True
        assert result["port"] == 8080
        assert result["previous_model"] is None
        assert result["new_model"] == "new-model"
        assert result["port_state"] == "serving"
        assert result["rolled_back"] is False

    @pytest.mark.asyncio
    async def test_swap_server_success_with_previous_model(self):
        """Successful swap when replacing existing model."""
        mock_state = MagicMock()
        old_config = MagicMock(model_path="/old/model.gguf")
        new_config = MagicMock(model_path="/new/model.gguf")
        mock_state.models = {
            "old-model": old_config,
            "new-model": new_config,
        }
        mock_state.running = {
            8080: RunningServer(
                pid=11111,
                port=8080,
                config_name="old-model",
                start_time=datetime.now(),
            )
        }

        mock_process = MagicMock()
        mock_process.pid = 22222
        mock_state.start_server.return_value = (True, "Started", mock_process)
        mock_state.stop_server.return_value = (True, "Stopped")

        with patch("llauncher.mcp.tools.servers.wait_for_server_ready", return_value=(True, [])):
            with patch("llauncher.mcp.tools.servers.Path") as mock_path:
                mock_path.exists.return_value = True
                result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model"})

        assert result["success"] is True
        assert result["previous_model"] == "old-model"
        assert result["new_model"] == "new-model"
        assert result["port_state"] == "serving"

    @pytest.mark.asyncio
    async def test_swap_server_rollback_on_start_failure(self):
        """Rolls back to old model if new model fails to start."""
        mock_state = MagicMock()
        old_config = MagicMock(model_path="/old/model.gguf")
        new_config = MagicMock(model_path="/new/model.gguf")
        mock_state.models = {
            "old-model": old_config,
            "new-model": new_config,
        }
        mock_state.running = {
            8080: RunningServer(
                pid=11111,
                port=8080,
                config_name="old-model",
                start_time=datetime.now(),
            )
        }

        # First start (new model) fails, second start (rollback) succeeds
        mock_old_process = MagicMock()
        mock_old_process.pid = 11111
        mock_state.start_server.side_effect = [
            (False, "Failed to start", None),  # New model fails
            (True, "Rolled back", mock_old_process),  # Old model succeeds
        ]
        mock_state.stop_server.return_value = (True, "Stopped")

        with patch("llauncher.mcp.tools.servers.wait_for_server_ready", return_value=(True, [])):
            with patch("llauncher.mcp.tools.servers.Path") as mock_path:
                mock_path.exists.return_value = True
                result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model"})

        assert result["success"] is False
        assert result["rolled_back"] is True
        assert result["port_state"] == "restored"
        assert result["restored_model"] == "old-model"

    @pytest.mark.asyncio
    async def test_swap_server_rollback_on_timeout(self):
        """Rolls back to old model if new model times out."""
        mock_state = MagicMock()
        old_config = MagicMock(model_path="/old/model.gguf")
        new_config = MagicMock(model_path="/new/model.gguf")
        mock_state.models = {
            "old-model": old_config,
            "new-model": new_config,
        }
        mock_state.running = {
            8080: RunningServer(
                pid=11111,
                port=8080,
                config_name="old-model",
                start_time=datetime.now(),
            )
        }

        mock_new_process = MagicMock()
        mock_new_process.pid = 22222
        mock_old_process = MagicMock()
        mock_old_process.pid = 11111

        # New model starts but times out, old model restarts successfully
        mock_state.start_server.side_effect = [
            (True, "Started", mock_new_process),  # New model starts
            (True, "Rolled back", mock_old_process),  # Old model restarts
        ]
        mock_state.stop_server.return_value = (True, "Stopped")

        with patch("llauncher.mcp.tools.servers.wait_for_server_ready", side_effect=[
            (False, ["log1", "log2"]),  # Timeout
            (True, []),  # Rollback succeeds
        ]):
            with patch("llauncher.mcp.tools.servers.stop_server_by_pid"):
                with patch("llauncher.mcp.tools.servers.Path") as mock_path:
                    mock_path.exists.return_value = True
                    result = await swap_server(mock_state, {"port": 8080, "model_name": "new-model", "timeout": 30})

        assert result["success"] is False
        assert result["rolled_back"] is True
        assert result["port_state"] == "restored"
        assert "timeout" in result["error"].lower() or "ready" in result["error"].lower()
        assert "startup_logs" in result


class TestGetTools:
    """Tests for get_tools function."""

    def test_get_tools_returns_five_tools(self):
        """get_tools returns five server tools."""
        tools = get_tools()

        assert len(tools) == 5
        tool_names = [t.name for t in tools]
        assert "start_server" in tool_names
        assert "stop_server" in tool_names
        assert "swap_server" in tool_names
        assert "server_status" in tool_names
        assert "get_server_logs" in tool_names
