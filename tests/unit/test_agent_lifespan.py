"""Unit tests for the agent FastAPI lifespan shutdown handler.

Per Issue #65 (Phase 2 of the v2 phased plan). Verifies that the agent
reaps llauncher-managed llama-server children on SIGTERM/SIGINT via the
FastAPI lifespan shutdown hook. The handler dispatches each managed port
through :func:`operations.stop`, which already owns audit emission,
psutil-based termination, and lockfile removal.

Behavior change called out in ``docs/v2-handoff.md`` §What NOT To Do:
both SIGTERM and SIGINT now reap children symmetrically; the previous
bare ``KeyboardInterrupt`` path orphaned them silently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from llauncher.agent import server as agent_server
from llauncher.operations.stop import StopResult


@dataclass(frozen=True)
class _FakeLockfile:
    """Minimal stand-in for ``llauncher.core.lockfile.Lockfile``."""

    port: int
    model: str = "mistral-7b"
    pid: int = 12345


def _drive_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the lifespan context manager start→shutdown to completion."""

    async def _run() -> None:
        # FastAPI lifespan signature takes the ``app`` argument; we don't
        # need a real FastAPI instance for the shutdown path under test.
        async with agent_server.lifespan(app=None):  # type: ignore[arg-type]
            pass

    asyncio.run(_run())


def test_lifespan_shutdown_no_lockfiles_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero managed children → enumerate and exit cleanly without calling stop()."""
    monkeypatch.setattr(agent_server.lf, "list_lockfiles", lambda: [])

    stop_calls: list[int] = []

    def _fake_stop(port: int, *, caller: str = "unknown") -> StopResult:
        stop_calls.append(port)
        return StopResult(success=True, action="already_empty", port=port)

    monkeypatch.setattr(agent_server.ops, "stop", _fake_stop)

    _drive_lifespan(monkeypatch)

    assert stop_calls == []


def test_lifespan_shutdown_reaps_each_lockfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each enumerated lockfile dispatches one ``ops.stop(port)`` call."""
    monkeypatch.setattr(
        agent_server.lf,
        "list_lockfiles",
        lambda: [
            _FakeLockfile(port=8081, model="mistral-7b"),
            _FakeLockfile(port=8082, model="qwen-2.5-coder"),
            _FakeLockfile(port=8083, model="llama-3"),
        ],
    )

    stop_calls: list[tuple[int, str]] = []

    def _fake_stop(port: int, *, caller: str = "unknown") -> StopResult:
        stop_calls.append((port, caller))
        return StopResult(
            success=True, action="stopped", port=port, model="x", pid=999
        )

    monkeypatch.setattr(agent_server.ops, "stop", _fake_stop)

    _drive_lifespan(monkeypatch)

    assert [p for p, _ in stop_calls] == [8081, 8082, 8083]
    # Caller string is the documented marker for shutdown-reaped stops; the
    # audit log will distinguish them from user-initiated stop() calls.
    assert all(caller == "agent-shutdown" for _, caller in stop_calls)


def test_lifespan_shutdown_coalesces_with_inflight_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port whose in-flight background stop completed is skipped (PR #161).

    The reaper joins any in-flight ``stop_in_background`` thread first
    (bounded by the full grace budget) and only drives the blocking
    ``ops.stop`` for ports with no in-flight stop — otherwise two
    threads race SIGTERM/SIGKILL on the same process.
    """
    monkeypatch.setattr(
        agent_server.lf,
        "list_lockfiles",
        lambda: [
            _FakeLockfile(port=8081, model="mistral-7b"),
            _FakeLockfile(port=8082, model="qwen-2.5-coder"),
        ],
    )
    # Pin the grace settings so the joined timeout is assertable.
    monkeypatch.setattr(
        agent_server.settings, "LLAUNCHER_STOP_CHILD_GRACE_S", 0.25
    )
    monkeypatch.setattr(agent_server.settings, "LLAUNCHER_STOP_GRACE_S", 0.5)

    join_calls: list[tuple[int, float]] = []

    def _fake_join(port: int, timeout: float | None = None) -> bool:
        join_calls.append((port, timeout))
        return port == 8081  # 8081's in-flight stop completed; 8082 has none

    monkeypatch.setattr(agent_server.ops, "join_inflight_stop", _fake_join)

    stop_calls: list[int] = []

    def _fake_stop(port: int, *, caller: str = "unknown") -> StopResult:
        stop_calls.append(port)
        return StopResult(success=True, action="stopped", port=port)

    monkeypatch.setattr(agent_server.ops, "stop", _fake_stop)

    _drive_lifespan(monkeypatch)

    # Every port was offered the coalesce, bounded by the grace budget sum.
    assert join_calls == [(8081, 0.75), (8082, 0.75)]
    # Only the non-coalesced port fell through to the blocking stop.
    assert stop_calls == [8082]


def test_lifespan_shutdown_tolerates_per_port_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``ops.stop`` for one port does not abort the loop."""
    monkeypatch.setattr(
        agent_server.lf,
        "list_lockfiles",
        lambda: [
            _FakeLockfile(port=8081),
            _FakeLockfile(port=8082),
            _FakeLockfile(port=8083),
        ],
    )

    stop_calls: list[int] = []

    def _fake_stop(port: int, *, caller: str = "unknown") -> StopResult:
        stop_calls.append(port)
        if port == 8082:
            raise OSError("simulated psutil failure")
        return StopResult(success=True, action="stopped", port=port)

    monkeypatch.setattr(agent_server.ops, "stop", _fake_stop)

    # Should not raise — the OSError on 8082 is caught and logged.
    _drive_lifespan(monkeypatch)

    assert stop_calls == [8081, 8082, 8083]


def test_lifespan_shutdown_tolerates_lockfile_enumeration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the lockfile directory is unreadable, shutdown exits without raising."""

    def _boom() -> Any:
        raise OSError("simulated FS failure")

    monkeypatch.setattr(agent_server.lf, "list_lockfiles", _boom)

    stop_called = False

    def _fake_stop(port: int, *, caller: str = "unknown") -> StopResult:
        nonlocal stop_called
        stop_called = True
        return StopResult(success=True, action="already_empty", port=port)

    monkeypatch.setattr(agent_server.ops, "stop", _fake_stop)

    # Should not raise — the OSError from list_lockfiles is caught and logged.
    _drive_lifespan(monkeypatch)

    assert stop_called is False


def test_lifespan_startup_provisions_run_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup self-provisions LAUNCHER_RUN_DIR (issue #201 Part 1).

    After a fresh system-mode install the migrated state dir carries logs/
    and audit.jsonl but not run/, so the first lockfile/marker write could
    fail before llama-server spawns. The lifespan startup creates it.
    """
    run_dir = tmp_path / "run"
    assert not run_dir.exists()
    monkeypatch.setattr(agent_server.settings, "LAUNCHER_RUN_DIR", run_dir)
    # No managed children to reap on shutdown.
    monkeypatch.setattr(agent_server.lf, "list_lockfiles", lambda: [])

    _drive_lifespan(monkeypatch)

    assert run_dir.is_dir()


def test_lifespan_startup_run_dir_is_idempotent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-present run dir is fine (exist_ok=True) — no raise."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(agent_server.settings, "LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr(agent_server.lf, "list_lockfiles", lambda: [])

    _drive_lifespan(monkeypatch)

    assert run_dir.is_dir()


def test_create_app_wires_lifespan() -> None:
    """``create_app()`` constructs a FastAPI app with the lifespan handler bound."""
    app = agent_server.create_app(auth_token="test-token")
    # FastAPI stores the lifespan context manager on the router.
    assert app.router.lifespan_context is not None


def test_run_agent_passes_lifespan_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run_agent()`` must pass ``lifespan="on"`` so the handler actually fires."""
    from llauncher.agent.config import AgentConfig

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(agent_server.uvicorn, "run", _fake_uvicorn_run)
    # Silence the startup banner log calls.
    monkeypatch.setattr(agent_server.logger, "info", lambda *a, **k: None)
    monkeypatch.setattr(agent_server.logger, "warning", lambda *a, **k: None)
    # Issue #128: run_agent configures a FileHandler under
    # LAUNCHER_LOG_DIR. Redirect it to tmp_path so the test never
    # touches the real ~/.llauncher/logs.
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "test-token")

    agent_server.run_agent(AgentConfig(host="127.0.0.1", port=8000))

    assert captured.get("lifespan") == "on"
