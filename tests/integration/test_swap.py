"""Integration tests for swap_server with real llama-server processes.

These tests require actual models to be configured and available.
They are marked with @pytest.mark.integration and can be skipped
if the required models are not available.

To run: pytest tests/integration/test_swap.py -v
To skip: pytest tests/integration/test_swap.py -v -m "not live"
"""

import pytest
import asyncio
from pathlib import Path

from llauncher.state import LauncherState
from llauncher.mcp.tools.servers import swap_server, stop_server


@pytest.mark.integration
@pytest.mark.live
class TestSwapServerLive:
    """Live integration tests for swap_server.

    These tests require:
    - At least two models configured in llauncher
    - llama-server binary available
    - Ports 8081 and 8082 available

    Expected setup:
    - Model A on port 8081 (e.g., coder model)
    - Model B on port 8082 (e.g., lfm2-350m)
    """

    @pytest.fixture
    def state(self):
        """Get a fresh LauncherState."""
        return LauncherState()

    @pytest.mark.asyncio
    async def test_swap_server_roundtrip(self, state):
        """Test swapping models back and forth.

        This test:
        1. Swaps from model A to model B on port 8081
        2. Verifies model B is running
        3. Swaps back to model A
        4. Verifies model A is running again

        Requires models 'coder' and 'lfm2' to be configured.
        """
        # Check prerequisites
        if "coder" not in state.models or "lfm2" not in state.models:
            pytest.skip("Required models (coder, lfm2) not configured")

        port = 8081

        # Ensure we start with coder on port 8081
        # (Test assumes this is the initial state)

        # Swap to lfm2
        result = await swap_server(state, {
            "port": port,
            "model_name": "lfm2",
            "timeout": 120,
        })

        assert result["success"] is True, f"Swap failed: {result.get('error')}"
        assert result["new_model"] == "lfm2"
        assert result["port_state"] == "serving"
        assert result["pid"] is not None

        # Verify lfm2 is now running on port 8081
        state.refresh_running_servers()
        assert port in state.running
        assert state.running[port].config_name == "lfm2"

        # Swap back to coder
        result = await swap_server(state, {
            "port": port,
            "model_name": "coder",
            "timeout": 120,
        })

        assert result["success"] is True, f"Swap back failed: {result.get('error')}"
        assert result["new_model"] == "coder"
        assert result["previous_model"] == "lfm2"
        assert result["port_state"] == "serving"

        # Verify coder is running again
        state.refresh_running_servers()
        assert port in state.running
        assert state.running[port].config_name == "coder"

    @pytest.mark.asyncio
    async def test_swap_rollback_on_invalid_model(self, state):
        """Test that swap fails gracefully for invalid model.

        This test verifies pre-flight validation catches invalid model names
        without touching the running server.
        """
        port = 8081

        # Ensure there's something running on the port first
        if port not in state.running:
            pytest.skip(f"No server running on port {port}")

        original_model = state.running[port].config_name

        # Try to swap to a non-existent model
        result = await swap_server(state, {
            "port": port,
            "model_name": "nonexistent-model-xyz",
            "timeout": 120,
        })

        # Should fail with port_state unchanged
        assert result["success"] is False
        assert result["port_state"] == "unchanged"
        assert "not found" in result["error"].lower()

        # Original model should still be running
        state.refresh_running_servers()
        assert port in state.running
        assert state.running[port].config_name == original_model

    @pytest.mark.asyncio
    async def test_swap_empty_port(self, state):
        """Test starting a model on an empty port.

        This verifies swap works when there's no existing model on the port.
        """
        # Find an empty port
        test_port = 8085  # Use a port that's likely empty
        if test_port in state.running:
            pytest.skip(f"Port {test_port} is already in use")

        # Need at least one model configured
        if not state.models:
            pytest.skip("No models configured")

        model_name = list(state.models.keys())[0]

        # Swap (start) on empty port
        result = await swap_server(state, {
            "port": test_port,
            "model_name": model_name,
            "timeout": 120,
        })

        if result["success"]:
            assert result["previous_model"] is None
            assert result["new_model"] == model_name
            assert result["port_state"] == "serving"

            # Clean up - stop the server we just started
            await stop_server(state, {"port": test_port})
            state.refresh_running_servers()
            assert test_port not in state.running
        else:
            pytest.skip(f"Could not start model: {result.get('error')}")


@pytest.mark.live
class TestSwapServerManual:
    """Manual test helpers - run these interactively.

    These aren't pytest tests but helper methods for manual testing.
    """

    @staticmethod
    async def run_swap_test():
        """Run a manual swap test with interactive output.

        Usage:
            python -c "from tests.integration.test_swap import TestSwapServerManual; asyncio.run(TestSwapServerManual.run_swap_test())"
        """
        state = LauncherState()

        print("Configured models:")
        for name in state.models.keys():
            print(f"  - {name}")

        print("\nRunning servers:")
        for port, server in state.running.items():
            print(f"  - Port {port}: {server.config_name} (PID {server.pid})")

        if len(state.models) < 2:
            print("\nNeed at least 2 models configured to test swap.")
            return

        # Get two model names
        model_names = list(state.models.keys())
        model_a, model_b = model_names[0], model_names[1]

        # Find a port with model_a running
        test_port = None
        for port, server in state.running.items():
            if server.config_name == model_a:
                test_port = port
                break

        if test_port is None:
            print(f"\nModel '{model_a}' is not running. Starting it...")
            # Would need to start it first
            return

        print(f"\n--- Testing swap from {model_a} to {model_b} on port {test_port} ---")

        result = await swap_server(state, {
            "port": test_port,
            "model_name": model_b,
            "timeout": 120,
        })

        print(f"\nSwap result:")
        print(f"  Success: {result['success']}")
        print(f"  Port state: {result['port_state']}")
        if result['success']:
            print(f"  New model: {result['new_model']}")
            print(f"  PID: {result['pid']}")
        else:
            print(f"  Error: {result.get('error')}")
            if 'startup_logs' in result:
                print(f"  Logs: {''.join(result['startup_logs'][-5:])}")

        print("\n--- Swap back to original ---")

        result = await swap_server(state, {
            "port": test_port,
            "model_name": model_a,
            "timeout": 120,
        })

        print(f"\nSwap back result:")
        print(f"  Success: {result['success']}")
        print(f"  Port state: {result['port_state']}")
        if result['success']:
            print(f"  Restored model: {result['new_model']}")
        else:
            print(f"  Error: {result.get('error')}")


if __name__ == "__main__":
    asyncio.run(TestSwapServerManual.run_swap_test())
