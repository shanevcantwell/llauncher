"""Integration coverage for /server-metrics/{port} + /server-slots/{port}
(ADR-LLNCH-019, issue #179 SP-6).

Two tiers of proof, matching the ``test_self_swap.py`` / ``test_swap.py``
convention:

* **Stub-mode (default, runs in CI).** A real subprocess on a real port,
  driven through the same in-process MCP dispatch + agent ``TestClient``
  the production stack uses. The stub binary listens but never speaks
  HTTP, so the pinned, deterministic outcome is the "unreachable"
  degraded envelope — this proves every hop (agent routing →
  ``core.server_metrics`` → lockfile read → live probe) is wired,
  without needing a real ``llama-server`` binary or GGUF.
* **Real-mode (opt-in via ``LLAUNCHER_INTEGRATION_REAL=1`` +
  ``LLAMA_SMALL_GGUF``, `` LLAMA_SERVER_PATH``).** Starts an actual
  ``llama-server``, polls ``/server-metrics/{port}`` until the aggregate
  tier reports ``available``, and asserts ``phase``/tok-s are
  populated — the ADR Testing section's "poll, assert phase/rate"
  acceptance criterion. Skips gracefully (``real_binary_env`` fixture)
  when not opted in, per the SP-6 "skips gracefully" requirement.
"""

from __future__ import annotations

import socket
import time

import pytest

from llauncher.core import server_metrics


pytestmark = pytest.mark.integration


def _free_port() -> int:
    """Return a likely-free local port (bind+close is good enough in tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(autouse=True)
def _clear_metrics_state():
    server_metrics.clear_cache()
    yield
    server_metrics.clear_cache()


# ─────────────────────────────────────────────────────────────────────
# Stub-mode: end-to-end wiring proof (runs in CI)
# ─────────────────────────────────────────────────────────────────────


async def test_server_metrics_wiring_against_stub(
    mcp_env, register_model, mcp_dispatch, agent_client
):
    """Full agent-HTTP → core.server_metrics → live-port round trip.

    The stub accepts the connection and closes it without sending a
    response, so httpx surfaces a transport error which
    ``core.server_metrics`` maps to the "unreachable" degraded envelope
    — the deterministic, CI-safe proof that the wiring is intact.
    """
    del mcp_env  # fixture wires the isolated run/config dirs; not read directly
    register_model("alpha")
    port = _free_port()

    start_result = await mcp_dispatch(
        "start_server", {"model_name": "alpha", "port": port}
    )
    assert start_result["success"] is True, start_result

    try:
        response = agent_client.get(f"/server-metrics/{port}")
        assert response.status_code == 200
        assert response.json() == {"available": False, "reason": "unreachable"}

        slots_response = agent_client.get(f"/server-slots/{port}")
        assert slots_response.status_code == 200
        assert slots_response.json() == {"available": False, "reason": "unreachable"}
    finally:
        await mcp_dispatch("stop_server", {"port": port})


async def test_server_metrics_empty_port_is_degraded_not_crash(agent_client):
    """A port with no server at all degrades cleanly, matching PARSE-AT-THE-DOOR."""
    port = _free_port()
    response = agent_client.get(f"/server-metrics/{port}")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "unreachable"}


# ─────────────────────────────────────────────────────────────────────
# Real-mode: canonical live proof (opt-in, skips gracefully otherwise)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.integration_real
async def test_server_metrics_live_reports_phase_and_rate(
    real_binary_env, mcp_dispatch, agent_client
):
    """Real llama-server: poll until the aggregate tier is available.

    Also grounds the SP-1 ``--no-slots`` default live: with
    ``ModelConfig.slots`` at its default (``False``), ``/server-slots``
    must 404 ``slots_disabled``.
    """
    from llauncher.core.config import ConfigStore
    from llauncher.models.config import ModelConfig

    gguf = real_binary_env["gguf"]
    cfg = ModelConfig.from_dict_unvalidated(
        {
            "name": "alpha",
            "model_path": str(gguf),
            "n_gpu_layers": 0,
            "ctx_size": 512,
            "threads_batch": 1,
            "ubatch_size": 1,
            "flash_attn": "off",
        }
    )
    ConfigStore.add_model(cfg, caller="issue-179-live-test")

    port = _free_port()
    start = await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    assert start["success"], start

    try:
        deadline = time.monotonic() + 30.0
        snapshot = None
        while time.monotonic() < deadline:
            response = agent_client.get(f"/server-metrics/{port}")
            assert response.status_code == 200
            snapshot = response.json()
            if snapshot.get("available"):
                break
            time.sleep(0.5)

        assert snapshot is not None and snapshot["available"] is True, snapshot
        assert snapshot["state"] == "ok"
        assert snapshot["phase"] in ("idle", "prompt", "generating")
        assert isinstance(snapshot["gen_tok_s"], (int, float))
        assert isinstance(snapshot["prompt_tok_s"], (int, float))
        assert snapshot["slots_total"] == cfg.parallel
        assert snapshot["canonical_name"] == "alpha"

        slots_response = agent_client.get(f"/server-slots/{port}")
        assert slots_response.status_code == 404
        assert slots_response.json() == {"detail": "slots_disabled"}
    finally:
        await mcp_dispatch("stop_server", {"port": port})
