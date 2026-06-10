"""Phase C — canonical real-use-case flows through the in-process MCP harness.

Each test drives the same dispatch table the MCP server uses (no stdio
framing) and asserts against lockfile/marker/audit/process state.

References:
- test-coverage-plan.md Phase C
- ADR-010 (verb shape), ADR-011 (swap five-phase), ADR-013 (logs),
  ADR-014 (cancel), ADR-015 (orphan policy)
- Issues: #54 cancel, #55 orphan, #56 (this harness), #65 reap-on-shutdown
- Companion: docs/plans/security-hardening-plan.md (test hooks 1, 2, 3, 17)
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import marker as mk
from llauncher.core.process import log_stem_for


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# Flow 1: start_server happy path
# ─────────────────────────────────────────────────────────────────────────────


async def test_start_server_happy_path_via_mcp(mcp_env, register_model, mcp_dispatch):
    register_model("alpha")
    port = _free_port()

    result = await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    try:
        assert result["success"] is True, result
        assert result["action"] == "started"
        assert result["port"] == port
        assert result["model"] == "alpha"

        # Lockfile written to the isolated run-dir.
        claim = lf.read_lockfile(port, run_dir=mcp_env["run_dir"])
        assert claim is not None and claim.model == "alpha"

        # Log file appears with the stub's fixture banner. ``start`` does
        # not wait for readiness, so we poll briefly for the banner. The
        # filename comes from the one mint (core.process.log_stem_for).
        log_files = list(mcp_env["log_dir"].glob(f"{log_stem_for('alpha')}-{port}.log"))
        assert log_files, "log file not created"
        deadline = time.monotonic() + 3.0
        content = ""
        while time.monotonic() < deadline:
            content = log_files[0].read_text(encoding="utf-8", errors="replace")
            if "STUB_FIXTURE: ok" in content:
                break
            time.sleep(0.05)
        assert "STUB_FIXTURE: ok" in content, content

        # Audit emitted.
        entries = al.read_entries(path=mcp_env["audit_path"])
        assert any(e.action.value == "started" and e.port == port for e in entries)
    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Flow 2: server_status reflects start → stop
# ─────────────────────────────────────────────────────────────────────────────


async def test_server_status_reflects_state_transitions(mcp_env, register_model, mcp_dispatch):
    register_model("alpha")
    port = _free_port()

    pre = await mcp_dispatch("server_status", {})
    pre_ports = {s.get("port") for s in pre["running_servers"]}
    assert port not in pre_ports

    await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    try:
        mid = await mcp_dispatch("server_status", {})
        mid_ports = {s.get("port") for s in mid["running_servers"]}
        assert port in mid_ports, mid
    finally:
        await mcp_dispatch("stop_server", {"port": port})

    post = await mcp_dispatch("server_status", {})
    post_ports = {s.get("port") for s in post["running_servers"]}
    assert port not in post_ports


# ─────────────────────────────────────────────────────────────────────────────
# Flow 3: swap_server happy path (five-phase)
# ─────────────────────────────────────────────────────────────────────────────


async def test_swap_server_five_phase_happy(mcp_env, register_model, mcp_dispatch):
    register_model("alpha")
    register_model("beta")
    port = _free_port()

    await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    try:
        result = await mcp_dispatch("swap_server", {"port": port, "model_name": "beta"})
        assert result["success"] is True, result
        assert result["action"] == "swapped"
        assert result["port_state"] == "serving"
        assert result["model"] == "beta"
        assert result["previous_model"] == "alpha"

        claim = lf.read_lockfile(port, run_dir=mcp_env["run_dir"])
        assert claim is not None and claim.model == "beta"
    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Flow 4: cancel_server during start (pre-commit) → marker takedown, audit
# ─────────────────────────────────────────────────────────────────────────────


async def test_cancel_during_start_pre_commit(mcp_env, register_model, mcp_dispatch, monkeypatch):
    register_model("alpha")
    port = _free_port()

    # Make the stub hang before binding — start's readiness poll will spin,
    # giving us a window to fire cancel via the marker checkpoint. The
    # marker checkpoint we exploit is the post-preflight one inside start():
    # we set cancelled=True before take_marker returns by patching it.
    from llauncher.core import marker as marker_mod

    real_take = marker_mod.take_marker

    def take_then_cancel(*args, **kwargs):
        m = real_take(*args, **kwargs)
        # Immediately mark cancelled — the very next checkpoint in start()
        # is the post-preflight one before launch, so we land in the
        # "cancelled at stage=post-preflight" branch.
        marker_mod.request_cancel(m.port)
        return m

    # ``llauncher.operations.start`` (the module — re-exported in
    # ``operations/__init__.py`` as the ``start`` *function*; pull the
    # module by sys.modules) does ``from llauncher.core import marker as mk``
    # at module top. Patch the alias on that module so the ``mk.take_marker``
    # reference inside ``start()`` resolves to our wrapper.
    import sys
    start_mod = sys.modules["llauncher.operations.start"]
    monkeypatch.setattr(start_mod.mk, "take_marker", take_then_cancel)

    result = await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})

    assert result["success"] is False
    assert result["action"] == "cancelled"

    # No lockfile.
    assert lf.read_lockfile(port, run_dir=mcp_env["run_dir"]) is None
    # No marker.
    assert mk.read_marker(port, run_dir=mcp_env["run_dir"]) is None

    entries = al.read_entries(path=mcp_env["audit_path"])
    assert any(
        e.action.value == "started" and e.result.value == "cancelled"
        for e in entries
    ), [(e.action.value, e.result.value) for e in entries]


# ─────────────────────────────────────────────────────────────────────────────
# Flow 5: cancel_server post-commit during swap → ignored advisory
# ─────────────────────────────────────────────────────────────────────────────


async def test_cancel_post_commit_during_swap_is_advisory(
    mcp_env, register_model, mcp_dispatch, monkeypatch
):
    """A cancel that arrives after readiness sets cancel_ignored_post_commit=True."""
    register_model("alpha")
    register_model("beta")
    port = _free_port()

    await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})

    # Patch wait_for_server_ready so as soon as it returns ready, we fire a
    # cancel — this races into the post-commit check inside swap.
    from llauncher.core import process as proc_mod

    real_wait = proc_mod.wait_for_server_ready

    def wait_then_cancel(*args, **kwargs):
        ready, logs = real_wait(*args, **kwargs)
        if ready:
            marker_port = args[0] if args else kwargs.get("port")
            mk.request_cancel(marker_port)
        return ready, logs

    # operations.swap reaches into ``proc.wait_for_server_ready`` via the
    # ``proc`` alias imported at swap-module top level. Patch the alias
    # attribute so the ``proc.wait_for_server_ready`` lookup inside the
    # swap function resolves to our wrapper.
    import sys
    swap_mod = sys.modules["llauncher.operations.swap"]
    monkeypatch.setattr(swap_mod.proc, "wait_for_server_ready", wait_then_cancel)

    try:
        result = await mcp_dispatch("swap_server", {"port": port, "model_name": "beta"})
        assert result["success"] is True
        assert result["action"] == "swapped"
        assert result.get("cancel_ignored_post_commit") is True
    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Flow 6: list_orphans annotation + listing (ADR-015)
# ─────────────────────────────────────────────────────────────────────────────


async def test_list_orphans_annotation_and_listing(mcp_env, mcp_dispatch):
    result = await mcp_dispatch("list_orphans", {})
    assert "orphans" in result


# ─────────────────────────────────────────────────────────────────────────────
# Flow 7: stop_server graceful reaps the child (#65 regression-shape)
# ─────────────────────────────────────────────────────────────────────────────


async def test_stop_server_reaps_child(mcp_env, register_model, mcp_dispatch):
    import psutil

    register_model("alpha")
    port = _free_port()

    started = await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    pid = started["pid"]
    assert psutil.pid_exists(pid)

    stopped = await mcp_dispatch("stop_server", {"port": port})
    assert stopped["success"] is True

    # Grace window — stop_server may return before the process fully exits
    # in pathological cases; allow a short reaper poll.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and psutil.pid_exists(pid):
        time.sleep(0.05)
    assert not psutil.pid_exists(pid), f"child pid {pid} still alive"

    # Lockfile cleaned up.
    assert lf.read_lockfile(port, run_dir=mcp_env["run_dir"]) is None


# ─────────────────────────────────────────────────────────────────────────────
# Flow 8: get_server_logs honors bounded tail (ADR-013)
# ─────────────────────────────────────────────────────────────────────────────


async def test_get_server_logs_bounded_tail(mcp_env, register_model, mcp_dispatch):
    register_model("alpha")
    port = _free_port()

    await mcp_dispatch("start_server", {"model_name": "alpha", "port": port})
    try:
        result = await mcp_dispatch("get_server_logs", {"port": port, "lines": 10})
        assert "logs" in result
        # Stub emits at least the fixture banner + ready line; len(logs) is
        # bounded by the requested ceiling.
        assert isinstance(result["logs"], list)
        assert len(result["logs"]) <= 10
        joined = "\n".join(result["logs"])
        assert "STUB_FIXTURE" in joined or "rest api listening" in joined
    finally:
        await mcp_dispatch("stop_server", {"port": port})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return a likely-free local port. Bind+close is good enough for tests."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
