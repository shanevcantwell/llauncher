"""Tests for MCP servers tools.

Per ADR-010, the verb tools (start/stop/swap) are thin wrappers around
:mod:`llauncher.operations` and return its result envelope verbatim.
The read tools (server_status, get_server_logs) still go through
LauncherState for per-call refresh.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from llauncher.mcp_server.tools.servers import (
    get_server_logs,
    get_tools,
    server_metrics,
    server_slots,
    server_status,
    start_server,
    stop_server,
    swap_server,
)
from llauncher.models.config import RunningServer
from llauncher.operations.start import StartResult
from llauncher.operations.stop import StopResult
from llauncher.operations.swap import SwapResult


@pytest.fixture
def mock_state():
    """Mock LauncherState with one running server, for read-side tools."""
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


# ───────────────────────── start_server ──────────────────────────


class TestStartServer:
    """Verifies start_server is a thin wrapper over ops.start."""

    @pytest.mark.asyncio
    async def test_missing_model_name(self):
        """Returns error envelope when model_name is absent (no ops call)."""
        result = await start_server({"port": 8080})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "model_name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_port(self):
        """Returns error envelope when port is absent (no ops call)."""
        result = await start_server({"model_name": "test-model"})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_started(self):
        """Returns ops.start() envelope on a successful start."""
        envelope = StartResult(
            success=True,
            action="started",
            port=8080,
            model="test-model",
            pid=12345,
        )
        with patch(
            "llauncher.mcp_server.tools.servers.ops.start",
            return_value=envelope,
        ) as mock_op:
            result = await start_server({"model_name": "test-model", "port": 8080})

        mock_op.assert_called_once_with("test-model", 8080, caller="mcp")
        assert result["success"] is True
        assert result["action"] == "started"
        assert result["port"] == 8080
        assert result["pid"] == 12345

    @pytest.mark.asyncio
    async def test_rejected_occupied(self):
        """Surfaces rejected_occupied unchanged."""
        envelope = StartResult(
            success=False,
            action="rejected_occupied",
            port=8080,
            model="test-model",
            message="port 8080 occupied by other-model",
        )
        with patch(
            "llauncher.mcp_server.tools.servers.ops.start",
            return_value=envelope,
        ):
            result = await start_server({"model_name": "test-model", "port": 8080})

        assert result["success"] is False
        assert result["action"] == "rejected_occupied"


# ───────────────────────── stop_server ───────────────────────────


class TestStopServer:
    """Verifies stop_server is a thin wrapper over ops.stop."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        """Returns error envelope when port is absent (no ops call)."""
        result = await stop_server({})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_stopped(self):
        """Returns ops.stop() envelope on a successful stop."""
        envelope = StopResult(success=True, action="stopped", port=8080)
        with patch(
            "llauncher.mcp_server.tools.servers.ops.stop",
            return_value=envelope,
        ) as mock_op:
            result = await stop_server({"port": 8080})

        mock_op.assert_called_once_with(8080, caller="mcp")
        assert result["success"] is True
        assert result["action"] == "stopped"

    @pytest.mark.asyncio
    async def test_already_empty_is_idempotent(self):
        """Idempotent stop: action='already_empty', success=True."""
        envelope = StopResult(success=True, action="already_empty", port=9999)
        with patch(
            "llauncher.mcp_server.tools.servers.ops.stop",
            return_value=envelope,
        ):
            result = await stop_server({"port": 9999})

        assert result["success"] is True
        assert result["action"] == "already_empty"


# ───────────────────────── swap_server ───────────────────────────


class TestSwapServer:
    """Verifies swap_server is a thin wrapper over ops.swap."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        """Returns error envelope when port is absent (no ops call)."""
        result = await swap_server({"model_name": "test-model"})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_model_name(self):
        """Returns error envelope when model_name is absent (no ops call)."""
        result = await swap_server({"port": 8080})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "model_name" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_swapped(self):
        """Returns ops.swap() envelope on a successful swap."""
        envelope = SwapResult(
            success=True,
            action="swapped",
            port_state="serving",
            port=8080,
            model="new-model",
            previous_model="old-model",
        )
        with patch(
            "llauncher.mcp_server.tools.servers.ops.swap",
            return_value=envelope,
        ) as mock_op:
            result = await swap_server({"port": 8080, "model_name": "new-model"})

        mock_op.assert_called_once_with("new-model", 8080, caller="mcp")
        assert result["success"] is True
        assert result["action"] == "swapped"
        assert result["previous_model"] == "old-model"

    @pytest.mark.asyncio
    async def test_rolled_back(self):
        """Surfaces rolled_back action unchanged."""
        envelope = SwapResult(
            success=False,
            action="rolled_back",
            port_state="restored",
            port=8080,
            model="old-model",
            previous_model="old-model",
            message="new-model failed readiness; rolled back",
        )
        with patch(
            "llauncher.mcp_server.tools.servers.ops.swap",
            return_value=envelope,
        ):
            result = await swap_server({"port": 8080, "model_name": "new-model"})

        assert result["success"] is False
        assert result["action"] == "rolled_back"
        assert result["port_state"] == "restored"
        assert result["model"] == "old-model"

    @pytest.mark.asyncio
    async def test_rejected_empty(self):
        """Empty port surfaces rejected_empty (caller should use start)."""
        envelope = SwapResult(
            success=False,
            action="rejected_empty",
            port_state="unchanged",
            port=8080,
            model=None,
        )
        with patch(
            "llauncher.mcp_server.tools.servers.ops.swap",
            return_value=envelope,
        ):
            result = await swap_server({"port": 8080, "model_name": "new-model"})

        assert result["success"] is False
        assert result["action"] == "rejected_empty"


# ─────────────────────── server_status ────────────────────────────


class TestServerStatus:
    """Read tool — still LauncherState-backed."""

    @pytest.mark.asyncio
    async def test_empty(self):
        """No running servers returns empty list and refreshes state."""
        mock_state = MagicMock()
        mock_state.running = {}

        result = await server_status(mock_state, {})

        mock_state.refresh.assert_called_once()
        assert result["running_servers"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_one_running(self, mock_state):
        """A running server is serialized via to_dict()."""
        result = await server_status(mock_state, {})

        assert result["count"] == 1
        server = result["running_servers"][0]
        assert server["pid"] == 12345
        assert server["port"] == 8080


# ─────────────────────── get_server_logs ──────────────────────────


class TestGetServerLogs:
    """Read tool — still LauncherState-backed."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        """Returns error for missing port argument."""
        mock_state = MagicMock()
        mock_state.running = {}

        result = await get_server_logs(mock_state, {})

        assert "error" in result
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_port(self, mock_state):
        """Returns error for a port with no live server."""
        result = await get_server_logs(mock_state, {"port": 9999})

        assert "error" in result
        assert "no server" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_success(self, mock_state):
        """Returns logs from stream_logs()."""
        with patch(
            "llauncher.mcp_server.tools.servers.stream_logs",
            return_value=["log line 1", "log line 2"],
        ):
            result = await get_server_logs(mock_state, {"port": 8080})

        assert result["logs"] == ["log line 1", "log line 2"]

    @pytest.mark.asyncio
    async def test_custom_lines_passed_through(self, mock_state):
        """The 'lines' argument is forwarded to stream_logs's ``lines``
        keyword specifically (issue #369): a positional call like
        ``stream_logs(pid, lines)`` silently lands ``lines`` in
        ``stream_logs``'s ``model_name`` slot instead, so this asserts
        the keyword binding rather than call position.
        """
        with patch(
            "llauncher.mcp_server.tools.servers.stream_logs",
            return_value=[],
        ) as mock_stream:
            await get_server_logs(mock_state, {"port": 8080, "lines": 500})

        mock_stream.assert_called_once_with(pid=mock_state.running[8080].pid, lines=500)

    @pytest.mark.asyncio
    async def test_calls_refresh_each_invocation(self, mock_state):
        """``get_server_logs`` must call ``state.refresh()`` per invocation
        (issue #59 / audit H1 regression guard).

        ``server_status`` already has an equivalent assertion in
        :class:`TestServerStatus`. This test guards the second read tool
        against accidental removal of the refresh call during the M4 tab
        restructure (#50) or any future cleanup.
        """
        with patch(
            "llauncher.mcp_server.tools.servers.stream_logs",
            return_value=[],
        ):
            await get_server_logs(mock_state, {"port": 8080})

        mock_state.refresh.assert_called_once()


# ─────────────────────────── get_tools ────────────────────────────


class TestGetTools:
    """Tool descriptors must reflect the port-keyed shape."""

    def test_returns_nine_tools(self):
        """start, stop, swap, cancel, server_status, get_server_logs,
        list_orphans (ADR-015), server_metrics + server_slots (ADR-LLNCH-019).
        """
        tools = get_tools()
        names = [t.name for t in tools]
        assert len(tools) == 9
        for expected in ("start_server", "stop_server", "swap_server",
                         "cancel_server", "server_status", "get_server_logs",
                         "list_orphans", "server_metrics", "server_slots"):
            assert expected in names

    def test_start_server_requires_model_and_port(self):
        """start_server tool schema requires both model_name and port (ADR-010)."""
        tool = next(t for t in get_tools() if t.name == "start_server")
        required = set(tool.inputSchema["required"])
        assert required == {"model_name", "port"}

    def test_swap_server_requires_port_and_model(self):
        tool = next(t for t in get_tools() if t.name == "swap_server")
        required = set(tool.inputSchema["required"])
        assert required == {"port", "model_name"}

    def test_stop_server_requires_only_port(self):
        tool = next(t for t in get_tools() if t.name == "stop_server")
        required = set(tool.inputSchema["required"])
        assert required == {"port"}

    def test_stop_server_description_matches_async_contract(self):
        """Issue #369: the delegated path (the production path) returns
        action='stopping' on a live process — an accepted, asynchronous
        termination (issue #140) — not a synchronous action='stopped'.
        The description must document the real contract, not a
        misleading synchronous-completion promise.
        """
        tool = next(t for t in get_tools() if t.name == "stop_server")
        assert "action='stopping'" in tool.description
        assert "action='stopped'" not in tool.description


# ─────────────────────── delegation gate (#200) ───────────────────────


class TestDelegationRouting:
    """start/swap/stop route through the #200 delegation gate.

    When ``should_delegate()`` is True the verb is POSTed to the local
    agent via a ``RemoteNode`` (HTTP) and ``ops.*`` is NOT called; when
    False the in-process op runs (the default the rest of this file
    exercises via the autouse ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=0`` pin).
    """

    @staticmethod
    def _patch_delegate(node):
        """Patch the gate True and the factory to return ``node``."""
        return (
            patch(
                "llauncher.mcp_server.tools.servers.delegation.should_delegate",
                return_value=True,
            ),
            patch(
                "llauncher.mcp_server.tools.servers.local_agent_node",
                return_value=node,
            ),
        )

    @pytest.mark.asyncio
    async def test_start_delegates_over_http_when_gate_true(self):
        node = MagicMock()
        node.start_server.return_value = {
            "success": True, "action": "started", "port": 8080, "model": "m",
        }
        gate, factory = self._patch_delegate(node)
        with gate, factory, patch(
            "llauncher.mcp_server.tools.servers.ops.start"
        ) as mock_ops:
            result = await start_server({"model_name": "m", "port": 8080})

        mock_ops.assert_not_called()
        node.start_server.assert_called_once_with("m", 8080)
        assert result["success"] is True
        assert result["action"] == "started"

    @pytest.mark.asyncio
    async def test_start_in_process_when_gate_false(self):
        envelope = StartResult(success=True, action="started", port=8080, model="m")
        with patch(
            "llauncher.mcp_server.tools.servers.delegation.should_delegate",
            return_value=False,
        ), patch(
            "llauncher.mcp_server.tools.servers.local_agent_node"
        ) as mock_node, patch(
            "llauncher.mcp_server.tools.servers.ops.start",
            return_value=envelope,
        ) as mock_ops:
            result = await start_server({"model_name": "m", "port": 8080})

        mock_ops.assert_called_once_with("m", 8080, caller="mcp")
        mock_node.assert_not_called()
        assert result["action"] == "started"

    @pytest.mark.asyncio
    async def test_delegated_null_body_maps_to_error_envelope(self):
        """A 200-with-null delegated result (``None``) becomes a coherent
        error dict, not a raw None handed to the MCP framework (#200 review)."""
        node = MagicMock()
        node.start_server.return_value = None
        gate, factory = self._patch_delegate(node)
        with gate, factory:
            result = await start_server({"model_name": "m", "port": 8080})

        assert result["success"] is False
        assert result["action"] == "error"
        assert result["port"] == 8080
        assert "empty response" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_stop_delegates_over_http_when_gate_true(self):
        node = MagicMock()
        node.stop_server.return_value = {"success": True, "action": "stopping"}
        gate, factory = self._patch_delegate(node)
        with gate, factory, patch(
            "llauncher.mcp_server.tools.servers.ops.stop"
        ) as mock_ops:
            result = await stop_server({"port": 8080})

        mock_ops.assert_not_called()
        node.stop_server.assert_called_once_with(8080)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_stop_in_process_when_gate_false(self):
        envelope = StopResult(success=True, action="stopped", port=8080)
        with patch(
            "llauncher.mcp_server.tools.servers.delegation.should_delegate",
            return_value=False,
        ), patch(
            "llauncher.mcp_server.tools.servers.local_agent_node"
        ) as mock_node, patch(
            "llauncher.mcp_server.tools.servers.ops.stop",
            return_value=envelope,
        ) as mock_ops:
            result = await stop_server({"port": 8080})

        mock_ops.assert_called_once_with(8080, caller="mcp")
        mock_node.assert_not_called()
        assert result["action"] == "stopped"

    @pytest.mark.asyncio
    async def test_swap_delegates_over_http_when_gate_true(self):
        node = MagicMock()
        node.swap_server.return_value = {
            "success": True, "action": "swapped", "port": 8080, "model": "new",
        }
        gate, factory = self._patch_delegate(node)
        with gate, factory, patch(
            "llauncher.mcp_server.tools.servers.ops.swap"
        ) as mock_ops:
            result = await swap_server({"port": 8080, "model_name": "new"})

        mock_ops.assert_not_called()
        node.swap_server.assert_called_once_with("new", 8080)
        assert result["action"] == "swapped"

    @pytest.mark.asyncio
    async def test_swap_in_process_when_gate_false(self):
        envelope = SwapResult(
            success=True, action="swapped", port_state="serving",
            port=8080, model="new", previous_model="old",
        )
        with patch(
            "llauncher.mcp_server.tools.servers.delegation.should_delegate",
            return_value=False,
        ), patch(
            "llauncher.mcp_server.tools.servers.local_agent_node"
        ) as mock_node, patch(
            "llauncher.mcp_server.tools.servers.ops.swap",
            return_value=envelope,
        ) as mock_ops:
            result = await swap_server({"port": 8080, "model_name": "new"})

        mock_ops.assert_called_once_with("new", 8080, caller="mcp")
        mock_node.assert_not_called()
        assert result["action"] == "swapped"


# ─────────────────────── cancel_server (ADR-014) ──────────────────────


class TestCancelServer:
    """Verifies cancel_server tool delivers cancel via the marker module."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        from llauncher.mcp_server.tools.servers import cancel_server

        result = await cancel_server({})

        assert result["success"] is False
        assert result["action"] == "error"
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_delivered_when_marker_exists(self):
        from llauncher.mcp_server.tools.servers import cancel_server

        with patch(
            "llauncher.core.marker.request_cancel", return_value=True
        ) as mock_cancel:
            result = await cancel_server({"port": 8081})

        mock_cancel.assert_called_once_with(8081)
        assert result == {
            "success": True,
            "cancelled": True,
            "marker_existed": True,
            "port": 8081,
        }

    @pytest.mark.asyncio
    async def test_no_op_when_marker_absent(self):
        """No in-flight op → marker_existed=False, still success=True."""
        from llauncher.mcp_server.tools.servers import cancel_server

        with patch("llauncher.core.marker.request_cancel", return_value=False):
            result = await cancel_server({"port": 9999})

        assert result == {
            "success": True,
            "cancelled": False,
            "marker_existed": False,
            "port": 9999,
        }


# ─── server_metrics / server_slots (ADR-LLNCH-019, issue #179 SP-5) ───


class TestServerMetricsTool:
    """Thin wrapper over ``core.server_metrics.get_aggregate_metrics``."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        result = await server_metrics({})
        assert result == {"error": "Missing required argument: port"}

    @pytest.mark.asyncio
    async def test_delegates_to_core_server_metrics(self):
        snapshot = {"available": True, "phase": "idle"}
        with patch(
            "llauncher.core.server_metrics.get_aggregate_metrics",
            return_value=snapshot,
        ) as mock_get:
            result = await server_metrics({"port": 8081})

        mock_get.assert_called_once_with(8081)
        assert result == snapshot

    @pytest.mark.asyncio
    async def test_returns_degraded_envelope_verbatim(self):
        degraded = {"available": False, "reason": "unreachable"}
        with patch(
            "llauncher.core.server_metrics.get_aggregate_metrics",
            return_value=degraded,
        ):
            result = await server_metrics({"port": 9999})

        assert result == degraded


class TestServerSlotsTool:
    """Thin wrapper over ``core.server_metrics.get_slots``."""

    @pytest.mark.asyncio
    async def test_missing_port(self):
        result = await server_slots({})
        assert result == {"error": "Missing required argument: port"}

    @pytest.mark.asyncio
    async def test_delegates_to_core_server_metrics(self):
        payload = {"available": True, "node": "n1", "slots": []}
        with patch(
            "llauncher.core.server_metrics.get_slots", return_value=payload
        ) as mock_get:
            result = await server_slots({"port": 8081})

        mock_get.assert_called_once_with(8081)
        assert result == payload

    @pytest.mark.asyncio
    async def test_returns_slots_disabled_envelope_verbatim(self):
        """No HTTP-status mapping at the MCP layer — the tool call returns
        the envelope as-is; only the agent HTTP endpoint maps this to 404.
        """
        disabled = {"available": False, "reason": "slots_disabled"}
        with patch(
            "llauncher.core.server_metrics.get_slots", return_value=disabled
        ):
            result = await server_slots({"port": 8081})

        assert result == disabled
