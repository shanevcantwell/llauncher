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


def test_create_app_wires_lifespan() -> None:
    """``create_app()`` constructs a FastAPI app with the lifespan handler bound."""
    app = agent_server.create_app()
    # FastAPI stores the lifespan context manager on the router.
    assert app.router.lifespan_context is not None


def test_run_agent_passes_lifespan_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_agent()`` must pass ``lifespan="on"`` so the handler actually fires."""
    from llauncher.agent.config import AgentConfig

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(agent_server.uvicorn, "run", _fake_uvicorn_run)
    # Silence the startup banner log calls.
    monkeypatch.setattr(agent_server.logger, "info", lambda *a, **k: None)
    monkeypatch.setattr(agent_server.logger, "warning", lambda *a, **k: None)

    agent_server.run_agent(AgentConfig(host="127.0.0.1", port=8000))

    assert captured.get("lifespan") == "on"
