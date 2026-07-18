"""ADR-LLNCH-016 — canonical self-swap worked example, as an executable proof.

The worked example: an agent harness, talking to the llauncher MCP child
over stdio, calls ``swap_server(port=P, model_name=B)`` to replace the
model it is currently inferencing against. The interesting property is
that the MCP control channel survives the swap unchanged — only the
underlying ``llama-server`` inference process is replaced.

This file drives the swap through the *same* in-process dispatch table
the real MCP server uses (``llauncher.mcp_server.server._dispatch_tool``)
and asserts on the four properties ADR-LLNCH-016 §5 enumerates:

1. The ``SwapResult`` envelope conforms to the ADR-LLNCH-016 §3 contract — every
   harness-facing field is present, has the documented type, and has the
   value expected for a ``swapped`` outcome.
2. The MCP dispatch callable is the *same Python object* before and after
   the swap (the MCP child / its in-process stand-in did not get torn down).
3. Post-swap the TCP port is bound — the inference endpoint survived,
   with a different process behind it.
4. The lockfile records a different PID after the swap than before —
   the old inference proc died, the new one lives.

Stub-mode is the default; a real-binary mode can be opted-in via
``LLAUNCHER_INTEGRATION_REAL=1`` for the canonical live proof — that
variant is marked ``@pytest.mark.live`` matching the convention in
``test_swap.py``.

References:
- ADR-LLNCH-016 (this ADR), §§1–5
- ADR-LLNCH-011 (swap semantics v2): the five-phase mechanic under test
- ADR-LLNCH-010 (port at the call site): the verb shape the test drives
- ADR-LLNCH-014 (cancellation): the recovery branch (covered separately in
  ``test_mcp_flows.py``; this file documents the link but does not
  duplicate the assertion)
- Issue #56: the M5 task this test closes
"""

from __future__ import annotations

import socket
import time

import pytest

from llauncher.core import lockfile as lf


pytestmark = pytest.mark.integration


# ── ADR-LLNCH-016 §3 contract: fields the harness depends on ─────────────────────
#
# Edits here are a contract change and must update ADR-LLNCH-016 in lockstep.
HARNESS_CONTRACT_FIELDS: dict[str, type | tuple[type, ...]] = {
    "success": bool,
    "action": str,
    "port_state": str,
    "model": (str, type(None)),
    "previous_model": (str, type(None)),
    "pid": (int, type(None)),
}


def _free_port() -> int:
    """Return a likely-free local port (bind+close is good enough in tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_is_listening(port: int, timeout: float = 2.0) -> bool:
    """True iff a TCP connect to ``127.0.0.1:port`` succeeds within ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.25)
        try:
            err = s.connect_ex(("127.0.0.1", port))
        finally:
            s.close()
        if err == 0:
            return True
        time.sleep(0.05)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Canonical self-swap — stub-mode (default, runs in CI)
# ─────────────────────────────────────────────────────────────────────────────


async def test_self_swap_canonical_worked_example(
    mcp_env, register_model, mcp_dispatch
):
    """ADR-LLNCH-016 worked example, executable form.

    Drives the swap through the MCP dispatch table and asserts the four
    properties enumerated in ADR-LLNCH-016 §5.
    """
    register_model("alpha")  # model A, the harness's current "brain"
    register_model("beta")   # model B, the harness's desired new brain
    port = _free_port()

    # T0-prereq: the harness is already running with alpha on port P.
    start_result = await mcp_dispatch(
        "start_server", {"model_name": "alpha", "port": port}
    )
    assert start_result["success"] is True, start_result
    assert start_result["action"] == "started"

    pre_swap_claim = lf.read_lockfile(port, run_dir=mcp_env["run_dir"])
    assert pre_swap_claim is not None
    assert pre_swap_claim.model == "alpha"
    pre_swap_pid = pre_swap_claim.pid

    # Property (2) baseline: capture the live MCP state object that was
    # lazily initialised on the start_server call above.  If a refactor
    # were to tear down and recreate srv._mcp_state mid-swap (e.g. by
    # restarting the whole MCP child) the identity assertion below would
    # catch it even though the fixture closure itself never changes.
    import llauncher.mcp_server.server as _srv_mod
    mcp_state_before = _srv_mod._mcp_state

    try:
        # T0 → T4: self-swap from alpha to beta on the same port. The
        # harness, in the worked example, is also the caller — but at the
        # integration-test layer we only need to drive the verb.
        result = await mcp_dispatch(
            "swap_server", {"port": port, "model_name": "beta"}
        )

        # ── Property (1): ADR-LLNCH-016 §3 envelope contract ──────────────────
        for field, expected_type in HARNESS_CONTRACT_FIELDS.items():
            assert field in result, (
                f"ADR-LLNCH-016 §3 violation: contracted field {field!r} "
                f"missing from swap_server response: {result!r}"
            )
            assert isinstance(result[field], expected_type), (
                f"ADR-LLNCH-016 §3 violation: field {field!r} has type "
                f"{type(result[field]).__name__}, expected {expected_type}; "
                f"value={result[field]!r}"
            )

        assert result["success"] is True, result
        assert result["action"] == "swapped"
        assert result["port_state"] == "serving"
        assert result["model"] == "beta"
        assert result["previous_model"] == "alpha"
        assert result["pid"] is not None

        # ── Property (2): MCP control channel survived ──────────────────
        #
        # The MCP control-channel state object (srv._mcp_state) must be the
        # *same Python object* before and after the swap.  This is the
        # in-process proxy for "the MCP child was not torn down during the
        # swap."  A refactor that reset or recreated _mcp_state mid-swap
        # would be caught here even though the fixture's _dispatch closure
        # is always the same reference.
        assert _srv_mod._mcp_state is mcp_state_before, (
            "ADR-LLNCH-016 §2 violation: srv._mcp_state identity changed across "
            "swap — the MCP control-channel state was torn down and "
            "recreated, meaning the control channel did not survive"
        )

        # And the dispatch table itself still answers — issuing a read-only
        # tool call post-swap exercises the same routing path the harness
        # would use for its next interaction.
        status = await mcp_dispatch("server_status", {})
        assert isinstance(status, dict)
        assert "running_servers" in status
        beta_rows = [
            r for r in status["running_servers"]
            if r.get("port") == port and r.get("config_name") == "beta"
        ]
        assert beta_rows, (
            f"server_status post-swap does not show beta on port {port}: "
            f"{status!r}"
        )

        # ── Property (3): inference endpoint is bound on the same port ──
        #
        # The stub closes accepted connections immediately, so we only
        # assert TCP-level bindability; a real-binary variant (below)
        # exercises an actual completion request.
        assert _port_is_listening(port), (
            f"post-swap: port {port} is not accepting TCP — inference "
            "endpoint did not come back up"
        )

        # ── Property (4): different PID behind the same port ────────────
        post_swap_claim = lf.read_lockfile(port, run_dir=mcp_env["run_dir"])
        assert post_swap_claim is not None
        assert post_swap_claim.model == "beta"
        assert post_swap_claim.pid == result["pid"], (
            "lockfile PID and SwapResult.pid disagree — would confuse a "
            "post-mortem"
        )
        assert post_swap_claim.pid != pre_swap_pid, (
            f"post-swap lockfile PID {post_swap_claim.pid} matches "
            f"pre-swap PID — old inference proc was not replaced"
        )

    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Negative envelope contract — failure paths still honor ADR-LLNCH-016 §3
# ─────────────────────────────────────────────────────────────────────────────


async def test_self_swap_envelope_contract_on_rejected_preflight(
    mcp_env, register_model, mcp_dispatch
):
    """Even on rejected-preflight the §3 contract holds.

    The harness branches on ``success`` / ``action`` / ``port_state`` before
    inspecting anything else, so those fields must be present and well-typed
    on every outcome, not just the happy path. ADR-LLNCH-016 §3 pins the contract
    as universal across SwapResult outcomes.
    """
    register_model("alpha")
    port = _free_port()

    await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    try:
        result = await mcp_dispatch(
            "swap_server",
            {"port": port, "model_name": "nonexistent-model-xyz"},
        )

        for field, expected_type in HARNESS_CONTRACT_FIELDS.items():
            assert field in result, (
                f"ADR-LLNCH-016 §3 violation on failure path: {field!r} missing"
            )
            assert isinstance(result[field], expected_type), (
                f"ADR-LLNCH-016 §3 violation on failure path: {field!r} has "
                f"type {type(result[field]).__name__}"
            )

        assert result["success"] is False
        assert result["action"] == "rejected_preflight"
        assert result["port_state"] == "unchanged"
        # previous_model echoes the still-running model, per ADR-LLNCH-011's
        # rejected_preflight semantics — the harness can keep using it.
        assert result["previous_model"] == "alpha"
        assert result["model"] == "alpha"
    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Live variant — real llama-server + real GGUF + a real completion request
#
# Marked ``@pytest.mark.live`` per the convention in test_swap.py. Skips
# unless LLAUNCHER_INTEGRATION_REAL=1 and the two GGUF / binary env vars
# are wired. This is the canonical end-to-end proof of ADR-LLNCH-016 — the
# stub-mode test above proves the orchestration and the contract; this
# proves the actual inference channel cuts over.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.integration_real
async def test_self_swap_live_completion_against_new_model(
    real_binary_env, mcp_dispatch
):
    """End-to-end self-swap with a real llama-server and a completion call.

    Requires two GGUFs (set ``LLAMA_SMALL_GGUF`` and either reuse it for
    both names or set ``LLAMA_SMALL_GGUF_B`` to a second small model). If
    only one GGUF is configured, both registered models point at it — the
    swap still exercises stop-old / start-new and the completion proves
    the new process is the one serving.
    """
    import os

    import httpx

    from llauncher.core.config import ConfigStore
    from llauncher.models.config import ModelConfig

    gguf_a = real_binary_env["gguf"]
    gguf_b_env = os.environ.get("LLAMA_SMALL_GGUF_B")
    gguf_b = gguf_b_env if gguf_b_env else str(gguf_a)

    def _register(name: str, model_path: str) -> None:
        cfg = ModelConfig.from_dict_unvalidated(
            {
                "name": name,
                "model_path": model_path,
                "n_gpu_layers": 0,
                "ctx_size": 512,
                "threads_batch": 1,
                "ubatch_size": 1,
                "flash_attn": "off",
            }
        )
        ConfigStore.add_model(cfg, caller="adr-016-live-test")

    _register("alpha", str(gguf_a))
    _register("beta", str(gguf_b))

    port = _free_port()

    try:
        start = await mcp_dispatch(
            "start_server", {"model_name": "alpha", "port": port}
        )
        assert start["success"], start

        # The real readiness-poll already established a listening socket; give
        # the model load itself a moment for /completion to be live.
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{base}/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)

        try:
            result = await mcp_dispatch(
                "swap_server", {"port": port, "model_name": "beta"}
            )
            assert result["success"] is True, result
            assert result["action"] == "swapped"
            assert result["port_state"] == "serving"
            assert result["model"] == "beta"

            # Wait for the new model to answer /health.
            deadline = time.monotonic() + 30.0
            ready = False
            while time.monotonic() < deadline:
                try:
                    r = httpx.get(f"{base}/health", timeout=1.0)
                    if r.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            assert ready, "new model never served /health after swap"

            # The whole point: an HTTP completion request, sent over the same
            # port the harness has been using, hits a model that is now beta.
            # We do not assert anything about the completion text — we assert
            # that the call returns 200 with a non-empty content body, which
            # is "the inference channel works against the new process."
            completion = httpx.post(
                f"{base}/completion",
                json={"prompt": "Hello, ", "n_predict": 4, "stream": False},
                timeout=30.0,
            )
            assert completion.status_code == 200, completion.text
            body = completion.json()
            assert "content" in body, body
            assert isinstance(body["content"], str)
            # Any non-empty completion proves the new process is the one
            # answering — a torn-down old process would have refused the TCP
            # connect; a not-yet-loaded new process would have 503'd.
            assert len(body["content"]) > 0, body
        finally:
            await mcp_dispatch("stop_server", {"port": port})
    finally:
        ConfigStore.remove_model("alpha", caller="adr-016-live-test")
        ConfigStore.remove_model("beta", caller="adr-016-live-test")
