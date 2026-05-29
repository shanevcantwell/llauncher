"""Integration tests for security hardening §3 C1 + C2.

Covers:

- **C1-d**: ``run_agent`` refuses to start on a non-loopback host with no
  token configured (exit code 2, stderr names the remediation paths).
- **C1-e**: On a loopback start with no env token and no existing file,
  the agent writes a fresh token to ``$HOME/.llauncher/agent.token``
  with mode 0600 (parent dir 0700) and that token authenticates the
  resulting app.
- **C1-reuse**: On a second loopback start with the file already
  present, the agent reads it rather than regenerating.
- **C1-stdin**: With ``LLAUNCHER_AGENT_TOKEN=-`` the token is read from
  stdin and used to authenticate the resulting app.
- **C2-default**: With no env overrides, ``AgentConfig.from_env()``
  binds to ``127.0.0.1``. ``0.0.0.0`` remains a valid explicit override.

These tests deliberately do not bind a real socket — they exercise the
token-resolution + create_app wiring against the FastAPI TestClient, and
the refuse-to-start guard at the ``run_agent`` entry. Real-bind coverage
is out of scope (the C2-a hook in the plan calls for socket
introspection in a real-binary environment, which we leave to manual
or follow-up work).
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.integration


# ─────── C2-default: from_env loopback default ─────────────────────────────


def test_from_env_default_host_is_loopback(monkeypatch):
    """C2: no env overrides → bind to 127.0.0.1."""
    from llauncher.agent.config import AgentConfig

    monkeypatch.delenv("LLAUNCHER_AGENT_HOST", raising=False)
    monkeypatch.delenv("LLAUNCHER_AGENT_PORT", raising=False)
    monkeypatch.delenv("LLAUNCHER_AGENT_NODE_NAME", raising=False)

    cfg = AgentConfig.from_env()
    assert cfg.host == "127.0.0.1"


def test_from_env_accepts_explicit_0_0_0_0_override(monkeypatch):
    """C2: 0.0.0.0 remains a valid explicit value (just not the default)."""
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LLAUNCHER_AGENT_HOST", "0.0.0.0")
    cfg = AgentConfig.from_env()
    assert cfg.host == "0.0.0.0"


# ─────── C1-d: refuse to start on non-loopback without token ───────────────


def test_run_agent_refuses_non_loopback_without_token(monkeypatch, tmp_path):
    """C1-d: agent exits non-zero with a clear error when binding
    to a non-loopback host without ``LLAUNCHER_AGENT_TOKEN``."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv
    from llauncher.agent import auth as agent_auth

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    # Force token-file lookup at a missing path so the operator's real
    # ~/.llauncher/agent.token cannot accidentally satisfy the guard.
    monkeypatch.setattr(
        agent_auth, "default_token_path",
        lambda: tmp_path / "definitely-missing.token",
    )

    # uvicorn.run must never be reached.
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: called.append((a, kw)))

    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    cfg = AgentConfig(host="192.168.1.50", port=8765)

    with pytest.raises(SystemExit) as excinfo:
        agent_srv.run_agent(cfg)

    assert excinfo.value.code == 2
    assert not called
    err = buf.getvalue()
    assert "non-loopback" in err
    assert "LLAUNCHER_AGENT_TOKEN" in err
    assert "127.0.0.1" in err or "loopback" in err


# ─────── C1-e: auto-generate token file on first loopback start ────────────


def _isolate_home(monkeypatch, tmp_path):
    """Point HOME at a tmp dir and patch default_token_path() to honor it.

    ``Path.home()`` consults HOME on POSIX; we also patch the module's
    ``default_token_path`` to defeat any import-time caching.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Cover non-POSIX call sites that read USERPROFILE.
    monkeypatch.setenv("USERPROFILE", str(home))

    from llauncher.agent import auth as agent_auth
    monkeypatch.setattr(
        agent_auth, "default_token_path",
        lambda: home / ".llauncher" / "agent.token",
    )
    return home


def test_loopback_first_run_generates_token_file(monkeypatch, tmp_path):
    """C1-e: first loopback start with no env token writes 0600 file."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    home = _isolate_home(monkeypatch, tmp_path)

    # Capture the token-handed-to-create_app by intercepting uvicorn.run
    # AND by re-running create_app with the same env state.
    captured: dict = {}
    def fake_uvicorn_run(app, host=None, port=None, log_level="info", lifespan="auto"):
        captured["app"] = app
        captured["host"] = host
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    cfg = AgentConfig(host="127.0.0.1", port=9000)
    agent_srv.run_agent(cfg)

    # Token file exists at the expected path.
    token_file = home / ".llauncher" / "agent.token"
    assert token_file.exists(), "agent.token must be auto-generated on first run"

    # Mode 0600 on the file.
    file_mode = stat.S_IMODE(token_file.stat().st_mode)
    assert file_mode == 0o600, f"expected 0600, got {oct(file_mode)}"

    # Parent dir 0700.
    parent_mode = stat.S_IMODE(token_file.parent.stat().st_mode)
    assert parent_mode == 0o700, f"expected 0700 on parent, got {oct(parent_mode)}"

    # File content is a non-trivial secret.
    token = token_file.read_text(encoding="utf-8").strip()
    assert len(token) >= 32

    # Stderr announced the token exactly once.
    err = buf.getvalue()
    assert token in err
    assert err.count(token) == 1


def test_loopback_second_run_reuses_existing_token(monkeypatch, tmp_path):
    """C1-reuse: an existing agent.token is honored rather than rewritten."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    home = _isolate_home(monkeypatch, tmp_path)

    # Pre-seed an existing token file.
    seeded_path = home / ".llauncher" / "agent.token"
    seeded_path.parent.mkdir(parents=True)
    seeded_path.parent.chmod(0o700)
    seeded_path.write_text("preexisting-token-value\n", encoding="utf-8")
    seeded_path.chmod(0o600)

    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    cfg = AgentConfig(host="127.0.0.1", port=9000)
    agent_srv.run_agent(cfg)

    # File content unchanged.
    assert seeded_path.read_text(encoding="utf-8").strip() == "preexisting-token-value"
    # Stderr did NOT announce a new generated token (the announcement
    # message string is unique to the generate-and-write path).
    assert "Generated new auth token" not in buf.getvalue()


# ─────── C1-stdin: LLAUNCHER_AGENT_TOKEN=- reads from stdin ─────────────────


def test_stdin_token_trigger(monkeypatch, tmp_path):
    """C1-stdin: ``LLAUNCHER_AGENT_TOKEN=-`` reads the token from stdin."""
    from llauncher.agent import auth as agent_auth

    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "-")
    monkeypatch.setattr("sys.stdin", io.StringIO("piped-secret-token-12345\n"))

    token = agent_auth.resolve_agent_token()
    assert token == "piped-secret-token-12345"


def test_stdin_token_empty_raises(monkeypatch, tmp_path):
    """C1-stdin: empty stdin with the trigger set is a fatal config error."""
    from llauncher.agent import auth as agent_auth

    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "-")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    with pytest.raises(RuntimeError, match="stdin"):
        agent_auth.resolve_agent_token()


# ─────── End-to-end: auto-generated token authenticates the live app ───────


def test_autogenerated_token_authenticates_app(monkeypatch, tmp_path, mcp_env):
    """C1-e (end-to-end): the auto-generated token authenticates
    requests against the real ``create_app`` middleware chain.
    """
    from llauncher.agent import auth as agent_auth
    from llauncher.agent.server import create_app

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    home = _isolate_home(monkeypatch, tmp_path)

    # Use the resolver directly (so we don't need uvicorn).
    token = agent_auth.resolve_agent_token()
    assert token

    app = create_app(auth_token=token)
    with TestClient(app) as client:
        # Wrong key → 403
        resp = client.get("/status", headers={"X-Api-Key": "wrong"})
        assert resp.status_code == 403
        # Right key → 2xx (or at worst, a routed response — definitely not 401/403)
        resp = client.get("/status", headers={"X-Api-Key": token})
        assert resp.status_code not in (401, 403)
        # /health remains exempt.
        resp = client.get("/health")
        assert resp.status_code == 200
