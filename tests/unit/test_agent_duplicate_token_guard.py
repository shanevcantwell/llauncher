"""Startup guard: refuse to run with a duplicate token line (issue #293).

The recurring UI-403 ("403s keep coming back") traced to two
``LLAUNCHER_AGENT_TOKEN=`` lines in one ``agent.env``. Every resolver is
last-wins (#284/d5f83b9), so a duplicate does not by itself change which
value wins — but it is the split-brain footgun a later hand-edit reordering
the lines trips into a server/client mismatch. ``run_agent`` fails loud
(``SystemExit(2)``) on more than one token line rather than run with the
latent hazard. This is the enforcement surface for "one canonical token
line" at the runtime door, paired with the installer-side dedupe (#285) and
the rewrite-in-place persist (#293).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import llauncher.agent.server as agent_server
from llauncher.agent.config import AgentConfig
from llauncher.core.agent_token import count_env_file_token_lines


def _silence_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_server.logger, "info", lambda *a, **k: None)
    monkeypatch.setattr(agent_server.logger, "warning", lambda *a, **k: None)


def test_count_token_lines_missing_file_is_zero(tmp_path: Path) -> None:
    assert count_env_file_token_lines(tmp_path / "nope.env") == 0


def test_count_token_lines_counts_only_real_key_lines(tmp_path: Path) -> None:
    """Comments and same-substring values do not count; leading ws does."""
    env = tmp_path / "agent.env"
    env.write_text(
        "# LLAUNCHER_AGENT_TOKEN=commented\n"
        "  LLAUNCHER_AGENT_TOKEN=leading-ws-still-counts\n"
        "LLAUNCHER_AGENT_HOST=x LLAUNCHER_AGENT_TOKEN=not-a-key\n"
        "LLAUNCHER_AGENT_TOKEN=real\n"
    )
    assert count_env_file_token_lines(env) == 2


def test_run_agent_fails_loud_on_duplicate_token_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two token lines in the live env file → SystemExit(2), loud message,
    uvicorn never started."""
    env = tmp_path / "agent.env"
    env.write_text(
        "LLAUNCHER_AGENT_TOKEN=first\nLLAUNCHER_AGENT_TOKEN=second\n"
    )
    monkeypatch.setattr(agent_server, "default_env_path", lambda: env)

    started = {"uvicorn": False}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        started["uvicorn"] = True

    monkeypatch.setattr(agent_server.uvicorn, "run", _fake_uvicorn_run)
    _silence_logs(monkeypatch)
    # Ensure the legacy-env guard above it does not pre-empt this one.
    monkeypatch.delenv("LAUNCHER_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "envwins")

    with pytest.raises(SystemExit) as exc:
        agent_server.run_agent(AgentConfig(host="127.0.0.1", port=8000))

    assert exc.value.code == 2
    assert started["uvicorn"] is False
    err = capsys.readouterr().err
    assert "2 LLAUNCHER_AGENT_TOKEN=" in err
    assert str(env) in err
    # #298: the guard only ever fires on two CANONICAL lines (the counter
    # only counts double-L lines), so "re-run the installer" is never a
    # correct remediation here — the message must say so and point at a
    # hand-edit instead.
    assert "will not fix this" in err
    assert "hand-edit" in err
    assert "re-run the installer" not in err


def test_run_agent_allows_single_token_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one token line does not trip the guard; uvicorn starts."""
    env = tmp_path / "agent.env"
    env.write_text("LLAUNCHER_AGENT_TOKEN=only-one\n")
    monkeypatch.setattr(agent_server, "default_env_path", lambda: env)

    started = {"uvicorn": False}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        started["uvicorn"] = True

    monkeypatch.setattr(agent_server.uvicorn, "run", _fake_uvicorn_run)
    _silence_logs(monkeypatch)
    monkeypatch.delenv("LAUNCHER_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "envwins")

    agent_server.run_agent(AgentConfig(host="127.0.0.1", port=8000))

    assert started["uvicorn"] is True
