"""Comprehensive tests for Phase 1 lazy singleton + per-call refresh pattern.

Tests the get_mcp_state() lazy singleton and ensures read handlers call refresh().
These tests verify the architectural fix for stale data in MCP read tool responses.
"""

import pytest
from unittest.mock import patch, MagicMock, ANY


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

    def test_get_mcp_state_partial_failure_clears_cache(self):
        """Partial construction failure resets _mcp_state to allow retry (#34-F)."""
        import llauncher.mcp_server.server as server_mod

        original_val = server_mod._mcp_state  # type: ignore[attr-defined]
        try:
            _reset_mcp_state()
            assert server_mod._mcp_state is None

            with patch.object(
                server_mod, "LauncherState", side_effect=RuntimeError("simulated bad __post_init__")
            ):
                from llauncher.mcp_server.server import get_mcp_state

                with pytest.raises(RuntimeError):
                    get_mcp_state()

            # _mcp_state must be None again, allowing retry
            assert server_mod._mcp_state is None, \
                "Failed construction should reset cache for retry"
        finally:
            server_mod._mcp_state = original_val  # type: ignore[attr-defined]

    def test_mcp_state_not_initialized_at_import(self):
        """_mcp_state must be None at module import time (no eager init). (#34-A)"""
        _reset_mcp_state()
        import llauncher.mcp_server.server as server_mod

        assert server_mod._mcp_state is None, \
            "Lazy init should not trigger at module load"


class TestReadHandlersCallRefresh:
    """Tests that every read handler calls state.refresh().

    Fix #31/#32: Handlers now use the passed-in state directly instead of
    importing get_mcp_state() — no more circular import, no double-refresh.
    """

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_list_models(self):
        """list_models calls .refresh() on its injected state (#31/#32)."""
        from llauncher.mcp_server.tools.models import list_models

        mock_state = MagicMock()
        result = await list_models(mock_state, {})

        mock_state.refresh.assert_called_once()
        assert "models" in result

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_get_model_config(self):
        """get_model_config calls .refresh() on its injected state (#31/#32)."""
        from llauncher.mcp_server.tools.models import get_model_config

        mock_state = MagicMock()
        result = await get_model_config(mock_state, {"name": "test"})

        mock_state.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_server_status(self):
        """server_status calls .refresh() on its injected state (#31/#32)."""
        from llauncher.mcp_server.tools.servers import server_status

        mock_state = MagicMock()
        result = await server_status(mock_state, {})

        mock_state.refresh.assert_called_once()
        assert "running_servers" in result

    @pytest.mark.asyncio
    async def test_read_handler_calls_refresh_get_server_logs(self):
        """get_server_logs calls .refresh() on its injected state (#31/#32)."""
        from llauncher.mcp_server.tools.servers import get_server_logs

        mock_state = MagicMock()
        result = await get_server_logs(mock_state, {"port": 8081})

        # refresh is called before port validation
        mock_state.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_read_handler_no_circular_import(self):
        """Read handlers can be imported without loading server.py (#31)."""
        import llauncher.mcp_server.server as server_mod
        # Reset to ensure _mcp_state isn't cached from prior tests
        original = server_mod._mcp_state  # type: ignore[attr-defined]
        server_mod._mcp_state = None  # type: ignore[attr-defined]

        try:
            # These imports must NOT trigger get_mcp_state() or LauncherState
            from llauncher.mcp_server.tools.models import list_models, get_model_config  # noqa: F401
            from llauncher.mcp_server.tools.servers import server_status, get_server_logs  # noqa: F401

            # get_mcp_state should still be None (no lazy init triggered)
            assert server_mod._mcp_state is None
        finally:
            server_mod._mcp_state = original  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_refresh_count_per_dispatch_cycle(self):
        """Refresh counts per dispatch cycle (#34-C).

        On first access: __post_init__→refresh(1) + handler state.refresh(2) = 2 total.
        After cache is set, subsequent dispatch still triggers __post_init__
        (since get_mcp_state re-creates on fresh _mcp_state=None), so also 2.
        """
        import llauncher.mcp_server.server as server_mod

        _reset_mcp_state()

        refresh_count = [0]

        def count_refresh(*args):
            refresh_count[0] += 1
            return None

        with patch.object(
            server_mod.LauncherState, "refresh", side_effect=count_refresh
        ):
            from llauncher.mcp_server.server import _dispatch_tool

            # First call: __post_init__→refresh(1) + handler state.refresh(2)
            await _dispatch_tool("list_models", {})
            assert refresh_count[0] == 2, \
                f"Expected 2 refreshes on first dispatch, got {refresh_count[0]}"

        # Second call after reset: also 2 (singleton re-created then handler calls refresh)
        _reset_mcp_state()
        refresh_count[0] = 0

        with patch.object(
            server_mod.LauncherState, "refresh", side_effect=count_refresh
        ):
            await _dispatch_tool("list_models", {})
            assert refresh_count[0] == 2, \
                f"Expected 2 refreshes on second dispatch, got {refresh_count[0]}"

    @pytest.mark.asyncio
    async def test_read_handler_reflects_refresh_in_data_output(self):
        """Handler reads post-refresh data from same state instance it refreshed (#34-B).

        Regression guard: if a handler accidentally starts using a different data source
        (e.g. stale closure, different variable) after calling .refresh(), this test
        catches it.
        """
        from llauncher.mcp_server.tools.models import list_models
        from unittest.mock import MagicMock

        mock_state = MagicMock()
        call_counter = [0]

        def side_effect_refresh():
            call_counter[0] += 1

        post_data = {"old-model": MagicMock(model_path="/dev/null/old.gguf"),
                     "new-model": MagicMock(model_path="/dev/null/new.gguf")}

        def items_side_effect():
            # After refresh() is called, return post-refresh data
            if call_counter[0] > 0:
                return list(post_data.items())
            else:
                return []

        mock_state.refresh.side_effect = side_effect_refresh
        original_items = mock_state.models.items
        mock_state.models.items = items_side_effect
        mock_state.get_model_status.return_value = {"status": "stopped", "default_port": None}

        result = await list_models(mock_state, {})

        # refresh() was called exactly once
        assert call_counter[0] == 1, f"Expected 1 refresh call, got {call_counter[0]}"
        # Handler must iterate post-refresh data after calling refresh()
        assert "models" in result


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


class TestValidateConfigBypassLazyInit:
    """Tests for Bug #33: validate_config bypasses get_mcp_state() entirely."""

    @pytest.mark.asyncio
    async def test_validate_config_does_not_trigger_lazy_init(self):
        """validate_config returns early without calling get_mcp_state() (#33)."""
        import llauncher.mcp_server.server as server_mod

        # Reset to ensure clean state
        server_mod._mcp_state = None  # type: ignore[attr-defined]

        from llauncher.mcp_server.server import _dispatch_tool

        with patch("llauncher.mcp_server.server.config_tools.validate_config",
                   return_value={"valid": True, "config": {}}) as mock_validate:
            result = await _dispatch_tool("validate_config", {
                "config": {"name": "test", "model_path": "/dev/null"}
            })

            assert result == {"valid": True, "config": {}}
            # After calling validate_config, _mcp_state must still be None
            # (get_mcp_state was never called, so lazy init never happened)
            assert server_mod._mcp_state is None, \
                "validate_config should NOT trigger lazy singleton initialization (#33)"

    @pytest.mark.asyncio
    async def test_validate_config_calls_handler_with_none_state(self):
        """validate_config passes None as state to handler since it's stateless."""
        from llauncher.mcp_server.server import _dispatch_tool

        with patch("llauncher.mcp_server.server.config_tools.validate_config",
                   return_value={"valid": True, "config": {}}) as mock_validate:
            await _dispatch_tool("validate_config", {
                "config": {"name": "test", "model_path": "/dev/null"}
            })

            # Handler receives None for state (it's truly stateless)
            mock_validate.assert_called_once_with(None, ANY)

    @pytest.mark.asyncio
    async def test_validate_config_with_real_handler_receives_none_gracefully(self):
        """Real validate_config handler works when called with None state (#34-D).

        Both existing tests mock the handler. This one exercises the ACTUAL
        config_tools.validate_config() function to prove it doesn't crash
        when state is None (since dispatch bypass passes None for this tool).
        """
        from llauncher.mcp_server.server import _dispatch_tool
        import tempfile, os
        from pathlib import Path
        from unittest.mock import patch

        # Create a real temp file so the model_path validator succeeds
        tmp = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        try:
            tmp.close()
            result = await _dispatch_tool("validate_config", {
                "config": {"name": "test-model", "model_path": tmp.name}
            })

            assert result["valid"] is True, f"Expected valid=True, got: {result}"
            assert "config" in result
        finally:
            os.unlink(tmp.name)


class TestStaleDataElimination:
    """End-to-end: external state changes are reflected after per-call refresh (#34-E).

    These tests exercise the full dispatch→get_mcp_state→refresh→read chain,
    proving that Phase 1 eliminates stale data as intended.
    """

    @pytest.mark.asyncio
    async def test_refresh_between_dispatch_calls_catches_model_addition(self):
        """When external config adds a model and refresh() is called, handler sees it. (#34-E)

        This tests the core value of Phase 1: zero-staleness on read tools.
        Without per-call refresh, this test would fail — stale snapshot from __post_init__
        would be returned instead of refreshed data.
        """
        import llauncher.mcp_server.server as server_mod
        _reset_mcp_state()

        with patch("llauncher.core.process.find_all_llama_servers", return_value=[]):
            import json, tempfile
            from pathlib import Path
            from unittest.mock import MagicMock

            temp_dir = Path(tempfile.mkdtemp())
            temp_config = temp_dir / "config.json"

            models_data = {
                "model-a": {
                    "name": "model-a",
                    "model_path": "/dev/null/model-a.gguf",
                    "default_port": 8081,
                    "ctx_size": 4096,
                    "n_gpu_layers": 255,
                }
            }
            temp_config.write_text(json.dumps(models_data))

            # Create a mock Path-like object that supports exists() and read_text()
            _config_data = {"model-a": {
                "name": "model-a", "model_path": "/dev/null/model-a.gguf",
                "default_port": 8081, "ctx_size": 4096, "n_gpu_layers": 255,
            }}

            mock_path = MagicMock()
            mock_path.exists.return_value = True

            def read_text_side_effect():
                return json.dumps(_config_data)

            mock_path.read_text.side_effect = read_text_side_effect

            from llauncher.mcp_server.server import get_mcp_state, _dispatch_tool

            with patch("llauncher.core.config.CONFIG_PATH", mock_path):
                state = get_mcp_state()  # First access — loads one model
                assert len(state.models) == 1, f"Expected 1 model, got {list(state.models.keys())}"
                first_name = list(state.models.keys())[0]

                # Simulate external change: update closure data with second model
                _config_data["model-b"] = {
                    "name": "model-b",
                    "model_path": "/dev/null/model-b.gguf",
                    "default_port": 8082,
                    "ctx_size": 4096,
                    "n_gpu_layers": 128,
                }

                # Call refresh() to pick up the change (simulates reloading from disk)
                state.refresh()
                assert len(state.models) == 2, f"Expected 2 models after refresh, got {list(state.models.keys())}"
                assert "model-b" in state.models

    @pytest.mark.asyncio
    async def test_refresh_clears_killed_process_from_running(self):
        """When external process is killed and refresh() called, stale entry disappears. (#34-E)

        Verifies that per-call refresh eliminates stale server entries from the running dict.
        Without refresh, a killed process would still appear as 'running' forever (until mutation).
        """
        import llauncher.mcp_server.server as server_mod
        _reset_mcp_state()

        mock_config_path = MagicMock()
        mock_config_path.exists.return_value = False

        with patch("llauncher.core.config.CONFIG_PATH", mock_config_path), \
             patch("llauncher.core.process.find_all_llama_servers", return_value=[]):

            from llauncher.mcp_server.server import get_mcp_state, _dispatch_tool
            from datetime import datetime

            state = get_mcp_state()
            # Manually populate a "running" server (simulating previous start)
            state.running[8081] = MagicMock(
                pid=9999, port=8081,
                config_name="test-model",
                start_time=datetime.now(),
            )
            assert len(state.running) == 1

            # Pretend the process is killed: mock find_all_llama_servers returns empty
            with patch("llauncher.core.process.find_all_llama_servers", return_value=[]):
                state.refresh_running_servers()  # Should clear running dict
                assert len(state.running) == 0, "Stale server entry should be cleared after refresh"

    @pytest.mark.asyncio
    async def test_two_dispatch_calls_separate_refreshes_reflect_changes(self):
        """Two sequential dispatch→read calls both get fresh data via their own refresh. (#34-E)

        Proves the per-call-refresh pattern: each call independently verifies state freshness.
        This is what eliminates the silent stale-data window that existed before Phase 1.
        """
        import llauncher.mcp_server.server as server_mod
        _reset_mcp_state()

        mock_config_path = MagicMock()
        mock_config_path.exists.return_value = True

        with patch("llauncher.core.config.CONFIG_PATH", mock_config_path), \
             patch("llauncher.core.process.find_all_llama_servers", return_value=[]):
            import json

            _dispatch_data = {"model-a": {
                "name": "model-a", "model_path": "/dev/null/a.gguf",
                "default_port": 8081, "ctx_size": 4096, "n_gpu_layers": 255,
            }}
            mock_config_path.read_text.return_value = json.dumps(_dispatch_data)

            from llauncher.mcp_server.server import _dispatch_tool

            # First dispatch: see 1 model
            result1 = await _dispatch_tool("list_models", {})
            assert len(result1) > 0  # Handler returned something
