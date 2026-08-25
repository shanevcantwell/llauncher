"""Tests for MCP server dispatch logic."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Import path fixed: was llauncher.mcp.server — corrected to llaunchermcp_server.
# main/main_async live in server submodule, not the package __init__.py
import llauncher.mcp_server
from llauncher.mcp_server.server import (
    list_tools_handler,
    call_tool_handler,
    _dispatch_tool,
    main,
    main_async,
)


class TestListTools:
    """Tests for list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self):
        """list_tools returns all tools from all modules."""
        with patch("llauncher.mcp_server.server.models_tools.get_tools", return_value=["model1", "model2"]):
            with patch("llauncher.mcp_server.server.servers_tools.get_tools", return_value=["server1", "server2", "server3", "server4"]):
                with patch("llauncher.mcp_server.server.config_tools.get_tools", return_value=["config1", "config2", "config3", "config4"]):
                    with patch("llauncher.mcp_server.server.audit_tools.get_tools", return_value=["audit1"]):
                        tools = await list_tools_handler()

                        # 2 models + 4 servers + 4 config + 1 audit = 11 total
                        assert len(tools) == 11
                        assert "model1" in tools
                        assert "server1" in tools
                        assert "audit1" in tools


class TestCallTool:
    """Tests for call_tool handler."""

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Successful tool dispatch returns correct JSON response."""
        with patch("llauncher.mcp_server.server._dispatch_tool", return_value={"status": "success"}):
            result = await call_tool_handler("test_tool", {"arg1": "value1"})

            assert len(result) == 1
            assert hasattr(result[0], 'text')

    @pytest.mark.asyncio
    async def test_call_tool_exception(self):
        """Tool exception returns error JSON response."""
        with patch("llauncher.mcp_server.server._dispatch_tool", side_effect=Exception("Test error")):
            result = await call_tool_handler("test_tool", {"arg1": "value1"})

            assert len(result) == 1
            assert hasattr(result[0], 'text')


class TestDispatchTool:
    """Tests for _dispatch_tool function.

    NOTE: After Phase 1 implementation (get_mcp_state lazy init), these tests
    must additionally patch get_mcp_state to return a MagicMock, otherwise the
    real state creation + config loading will interfere with test isolation.
    
    See post-phase-1 TODO in this file for required additions.
    """

    @pytest.mark.asyncio
    async def test_dispatch_tool_models(self):
        """Dispatch to models tools."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.models_tools.list_models", return_value="models_result"):
                result = await _dispatch_tool("list_models", {})
                assert result == "models_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_servers(self):
        """Dispatch to servers tools."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.servers_tools.start_server", return_value="server_result"):
                result = await _dispatch_tool("start_server", {})
                assert result == "server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_config(self):
        """Dispatch to config tools."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.config_tools.add_model", return_value="config_result"):
                result = await _dispatch_tool("add_model", {})
                assert result == "config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_unknown(self):
        """Unknown tool raises ValueError."""
        # get_mcp_state mock prevents real LauncherState creation.
        # ValueError still propagates because it's raised by _dispatch_tool,
        # not by any mocked handler.
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with pytest.raises(ValueError, match="Unknown tool"):
                await _dispatch_tool("unknown_tool", {})

    @pytest.mark.asyncio
    async def test_dispatch_tool_get_model_config(self):
        """Dispatch to get_model_config tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.models_tools.get_model_config", return_value="get_model_config_result"):
                result = await _dispatch_tool("get_model_config", {})
                assert result == "get_model_config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_stop_server(self):
        """Dispatch to stop_server tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.servers_tools.stop_server", return_value="stop_server_result"):
                result = await _dispatch_tool("stop_server", {})
                assert result == "stop_server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_swap_server(self):
        """Dispatch to swap_server tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.servers_tools.swap_server", return_value="swap_server_result"):
                result = await _dispatch_tool("swap_server", {})
                assert result == "swap_server_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_server_status(self):
        """Dispatch to server_status tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.servers_tools.server_status", return_value="server_status_result"):
                result = await _dispatch_tool("server_status", {})
                assert result == "server_status_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_get_server_logs(self):
        """Dispatch to get_server_logs tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.servers_tools.get_server_logs", return_value="get_server_logs_result"):
                result = await _dispatch_tool("get_server_logs", {})
                assert result == "get_server_logs_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_update_model_config(self):
        """Dispatch to update_model_config tool."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("llauncher.mcp_server.server.config_tools.update_model_config", return_value="update_model_config_result"):
                result = await _dispatch_tool("update_model_config", {})
                assert result == "update_model_config_result"

    @pytest.mark.asyncio
    async def test_dispatch_tool_validate_config(self):
        """Dispatch to validate_config — should bypass get_mcp_state entirely via early return (#34-G)."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            with patch("llauncher.mcp_server.server.config_tools.validate_config", return_value="validate_config_result"):
                result = await _dispatch_tool("validate_config", {})
                assert result == "validate_config_result"
                # KEY ASSERTION: get_mcp_state was NOT called — validate_config bypasses lazy init (#33/#34-G)
                mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_tool_delete_model(self):
        """Dispatch to delete_model tool — stateless per ADR-LLNCH-008."""
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            with patch(
                "llauncher.mcp_server.server.config_tools.delete_model",
                return_value="delete_model_result",
            ):
                result = await _dispatch_tool("delete_model", {})
                assert result == "delete_model_result"
                # ADR-LLNCH-008: stateless verb must not touch the singleton
                mock_get.assert_not_called()


class TestMainFunctions:
    """Tests for main functions in llauncher.mcp_server."""

    @pytest.mark.asyncio
    async def test_main_async(self):
        """Test main_async function."""
        with patch("llauncher.mcp_server.server.Server") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            mock_server.list_tools = MagicMock(return_value=lambda x: x)
            mock_server.call_tool = MagicMock(return_value=lambda x: x)

            async def mock_run(*args, **kwargs):
                pass
            mock_server.run.return_value = mock_run()

            with patch("llauncher.mcp_server.server.stdio_server") as mock_stdio:
                mock_read_stream = MagicMock()
                mock_write_stream = MagicMock()
                mock_stdio.return_value.__aenter__.return_value = (mock_read_stream, mock_write_stream)

                await main_async()

                # Verify server was created with correct name
                mock_server_class.assert_called_once_with("llauncher")

                # Verify handlers were registered
                mock_server.list_tools.assert_called_once()
                mock_server.call_tool.assert_called_once()

                # Verify server.run was called
                mock_server.run.assert_called_once()

    def test_main(self):
        """Test main function."""
        with patch("asyncio.run") as mock_asyncio_run:
            main()
            mock_asyncio_run.assert_called_once()
            # asyncio.run is mocked, so the main_async() coroutine handed to
            # it is never awaited. Close it explicitly to avoid a
            # ``RuntimeWarning: coroutine 'main_async' was never awaited``.
            args, _ = mock_asyncio_run.call_args
            import asyncio as _asyncio
            if _asyncio.iscoroutine(args[0]):
                args[0].close()

    def test_main_entry_point(self):
        """Test the if __name__ == '__main__' block."""
        assert callable(main)


class TestInterfaceCloseout:
    """INTERFACE coverage close-out for the MCP dispatch + handler wiring."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_cancel_server(self):
        """Dispatch to cancel_server — stateless verb, bypasses the singleton.

        Covers server.py:83 — the ``cancel_server`` arm of ``_dispatch_tool``
        in the stateless verb group (ADR-LLNCH-010/ADR-LLNCH-014), reached before
        ``get_mcp_state``.
        """
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            with patch(
                "llauncher.mcp_server.server.servers_tools.cancel_server",
                return_value="cancel_server_result",
            ):
                result = await _dispatch_tool("cancel_server", {"port": 8080})
                assert result == "cancel_server_result"
                # Stateless verb must not touch the lazy LauncherState singleton.
                mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_tool_read_audit(self):
        """Dispatch to read_audit (issue #338) — stateless read, bypasses the singleton.

        Mirrors ``test_dispatch_tool_cancel_server``: ``read_audit`` reads
        local disk directly (like ``server_metrics``/``server_slots``), so
        it must be reached before ``get_mcp_state`` and never touch the
        lazy ``LauncherState`` singleton.
        """
        with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
            with patch(
                "llauncher.mcp_server.server.audit_tools.read_audit",
                return_value="read_audit_result",
            ):
                result = await _dispatch_tool("read_audit", {})
                assert result == "read_audit_result"
                mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_async_registered_handlers_invoke_dispatch(self):
        """The decorated ``list_tools``/``call_tool`` callbacks delegate to the handlers.

        Covers server.py:120 and :124 — the existing ``main_async`` test
        registers the handlers but never *invokes* the inner decorated
        functions, so their one-line bodies stayed uncovered. Here we capture
        what ``@server.list_tools()`` / ``@server.call_tool()`` register and
        then call them, confirming each delegates to its module-level handler.
        """
        captured: dict = {}

        def list_tools_decorator():
            def register(fn):
                captured["list_tools"] = fn
                return fn
            return register

        def call_tool_decorator():
            def register(fn):
                captured["call_tool"] = fn
                return fn
            return register

        with patch("llauncher.mcp_server.server.Server") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server
            mock_server.list_tools = MagicMock(side_effect=list_tools_decorator)
            mock_server.call_tool = MagicMock(side_effect=call_tool_decorator)

            # AsyncMock so ``server.run`` is awaitable on every call — avoids
            # the single-use coroutine footgun of a pre-created coroutine.
            mock_server.run = AsyncMock()

            with patch("llauncher.mcp_server.server.stdio_server") as mock_stdio:
                mock_stdio.return_value.__aenter__.return_value = (
                    MagicMock(),
                    MagicMock(),
                )
                with patch(
                    "llauncher.mcp_server.server.list_tools_handler",
                    new=AsyncMock(return_value=["a-tool"]),
                ) as mock_lt, patch(
                    "llauncher.mcp_server.server.call_tool_handler",
                    new=AsyncMock(return_value=["a-result"]),
                ) as mock_ct:
                    await main_async()

                    # Invoke the captured decorated callbacks (server.py:120, :124).
                    assert await captured["list_tools"]() == ["a-tool"]
                    assert await captured["call_tool"]("some_tool", {"k": "v"}) == [
                        "a-result"
                    ]

                    mock_lt.assert_awaited_once()
                    mock_ct.assert_awaited_once_with("some_tool", {"k": "v"})
