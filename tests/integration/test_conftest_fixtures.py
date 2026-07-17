"""Regression coverage for the integration-test harness itself.

``mcp_env`` and ``real_binary_env`` both isolate the same disk-bound seams
(run/log/config dirs) onto the same function-scoped ``tmp_path``. A test
that requests both — directly, or transitively via ``mcp_dispatch`` /
``agent_client``, which depend on ``mcp_env`` — used to hit two independent
``mkdir(exist_ok=False)`` calls on the identical path, raising
``FileExistsError`` at fixture setup before the test body ever ran. That bug
blocked every ``integration_real`` live test (``test_self_swap.py::
test_self_swap_live_completion_against_new_model``,
``test_server_metrics_live.py::test_server_metrics_live_reports_phase_and_rate``).

This module exercises the same fixture composition WITHOUT a real binary or
GPU, so the regression is caught by the fast non-live suite rather than only
by the opt-in live run. It works by satisfying ``_real_mode_available()``
with a fake-but-existing binary/gguf pair, which is enough to drive
``real_binary_env`` past its skip and into the dir-setup path under test.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


@pytest.fixture
def fake_real_mode(tmp_path, stub_binary, monkeypatch):
    """Satisfy ``_real_mode_available()`` without a real llama-server/GGUF.

    Points ``LLAMA_SERVER_PATH`` / ``LLAMA_SMALL_GGUF`` at files that merely
    need to *exist* — the stub binary and a throwaway placeholder file — so
    ``real_binary_env`` proceeds past its skip guard into the dir-setup and
    monkeypatch logic this test is actually probing.
    """
    fake_gguf = tmp_path / "fake.gguf"
    fake_gguf.write_bytes(b"\x00")
    monkeypatch.setenv("LLAUNCHER_INTEGRATION_REAL", "1")
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(stub_binary))
    monkeypatch.setenv("LLAMA_SMALL_GGUF", str(fake_gguf))
    return fake_gguf


def test_real_binary_env_composes_with_mcp_env_without_collision(
    fake_real_mode, mcp_env, real_binary_env
):
    """Requesting both fixtures on one ``tmp_path`` must not raise.

    This is the direct regression test for the double-``mkdir(exist_ok=False)``
    collision: before the fix, resolving both fixtures for one test raised
    ``FileExistsError`` at setup. Here both are requested explicitly (rather
    than transitively through ``mcp_dispatch``) so the failure — if
    reintroduced — surfaces at fixture setup, before any assertion runs.
    """
    assert mcp_env["run_dir"].is_dir()
    assert mcp_env["log_dir"].is_dir()
    assert mcp_env["config_dir"].is_dir()

    # real_binary_env must resolve to the SAME run context mcp_env created —
    # not a second, independently-created directory tree.
    assert real_binary_env["run_dir"] == mcp_env["run_dir"]
    assert real_binary_env["run_dir"].is_dir()


async def test_real_binary_env_composes_with_mcp_dispatch_without_collision(
    fake_real_mode, real_binary_env, mcp_dispatch
):
    """The exact composition the live tests use: ``real_binary_env`` +
    ``mcp_dispatch`` (which transitively depends on ``mcp_env``).

    ``test_self_swap.py::test_self_swap_live_completion_against_new_model``
    and ``test_server_metrics_live.py::test_server_metrics_live_reports_phase_and_rate``
    both request this exact pair. Reaching this point without a
    ``FileExistsError`` is the regression guard; the ``server_status`` call
    is a cheap liveness check that the harness is actually usable afterward.
    ``server_status`` does a live OS process scan (``LauncherState.refresh``),
    so it may report servers already running on the host outside this test's
    isolated dirs — we only assert the call succeeds and is well-shaped.
    """
    status = await mcp_dispatch("server_status", {})
    assert set(status) == {"running_servers", "count"}
    assert status["count"] == len(status["running_servers"])
