"""Integration tests for security hardening §3 C1 + C2.

Covers:

- **C1-d**: ``run_agent`` refuses to start on a non-loopback host with no
  token configured (exit code 2, stderr names the remediation paths).
- **C1-e**: On a loopback start with no env token and no existing
  ``agent.env``, the agent generates a fresh token and writes it into
  ``$HOME/.llauncher/agent.env`` (mode 0600, parent dir 0700) as a
  ``LLAUNCHER_AGENT_TOKEN=`` line, and that token authenticates the
  resulting app.
- **C1-reuse**: On a second loopback start with ``agent.env`` already
  carrying a token line, the agent reads it rather than regenerating.
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

Issue #284 retired the standalone ``agent.token`` mirror file; the live
source read by both the agent and the UI is now ``agent.env`` (a
``KEY=VALUE`` file), resolved via ``core.agent_token.default_env_path``.
These tests were updated in place to assert against that file/key shape
— the resolution-precedence and persistence-guard behavior under test is
unchanged.
"""

from __future__ import annotations

import io
import os
import stat
import sys
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

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    # Force env-file lookup at a missing path so the operator's real
    # ~/.llauncher/agent.env cannot accidentally satisfy the guard.
    # Token resolution was hoisted to core.agent_token (#171); patch the
    # canonical home so the implementation's internal lookup is affected.
    monkeypatch.setattr(
        "llauncher.core.agent_token.default_env_path",
        lambda: tmp_path / "definitely-missing.env",
    )

    # Issue #128: run_agent configures a FileHandler under
    # LAUNCHER_LOG_DIR before the refusal check runs. Redirect it to
    # tmp_path so the test never touches the real ~/.llauncher/logs.
    monkeypatch.setattr("llauncher.agent.server.LAUNCHER_LOG_DIR", tmp_path)

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


# ─────── #281: refuse to start on pre-#139 legacy-only env ─────────────────


def test_run_agent_refuses_legacy_only_token_env(monkeypatch, tmp_path):
    """#281: agent exits non-zero (code 2) when only the pre-#139
    single-L ``LAUNCHER_AGENT_TOKEN`` is set and ``LLAUNCHER_AGENT_TOKEN``
    is absent — even on an otherwise-healthy loopback bind. This is
    defense in depth for deployments that bypass the installers' own
    migration (scripts/windows/install.ps1, scripts/systemd/install.sh)."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv

    monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "stale-pre-rename-token")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    # Issue #128: run_agent configures a FileHandler under
    # LAUNCHER_LOG_DIR before the legacy-env check runs. Redirect it to
    # tmp_path so the test never touches the real ~/.llauncher/logs.
    monkeypatch.setattr("llauncher.agent.server.LAUNCHER_LOG_DIR", tmp_path)

    # uvicorn.run must never be reached.
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: called.append((a, kw)))

    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    # Loopback bind — proves the guard fires independent of the C1-d
    # non-loopback check, which this legacy-env guard runs ahead of.
    cfg = AgentConfig(host="127.0.0.1", port=8765)

    with pytest.raises(SystemExit) as excinfo:
        agent_srv.run_agent(cfg)

    assert excinfo.value.code == 2
    assert not called
    err = buf.getvalue()
    assert "LAUNCHER_AGENT_TOKEN" in err
    assert "LLAUNCHER_AGENT_TOKEN" in err
    assert "138" in err or "139" in err


# ─────── C1-e: auto-generate token file on first loopback start ────────────


def _isolate_home(monkeypatch, tmp_path):
    """Point HOME at a tmp dir and patch default_env_path() to honor it.

    ``Path.home()`` consults HOME on POSIX; we also patch the module's
    ``default_env_path`` to defeat any import-time caching.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Cover non-POSIX call sites that read USERPROFILE.
    monkeypatch.setenv("USERPROFILE", str(home))

    # Token resolution was hoisted to core.agent_token (#171); patch there.
    monkeypatch.setattr(
        "llauncher.core.agent_token.default_env_path",
        lambda: home / ".llauncher" / "agent.env",
    )

    # Issue #128: llauncher.core.settings.LAUNCHER_LOG_DIR is resolved
    # from the real Path.home() at import time, long before this
    # monkeypatch.setenv("HOME", ...) runs -- setting HOME alone does not
    # move it. run_agent's _configure_logging() would otherwise create
    # the real ~/.llauncher/logs/agent.log as a side effect of this test.
    monkeypatch.setattr(
        "llauncher.agent.server.LAUNCHER_LOG_DIR", home / ".llauncher" / "logs"
    )
    return home


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="NTFS has no POSIX mode bits; chmod is a no-op for read/owner bits",
)
def test_loopback_first_run_generates_token_file(monkeypatch, tmp_path):
    """C1-e: first loopback start with no env token writes 0600 agent.env."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    home = _isolate_home(monkeypatch, tmp_path)

    # Capture the token-handed-to-create_app by intercepting uvicorn.run
    # AND by re-running create_app with the same env state.
    captured: dict = {}
    def fake_uvicorn_run(app, host=None, port=None, log_level="info", lifespan="auto", log_config=None):
        captured["app"] = app
        captured["host"] = host
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    cfg = AgentConfig(host="127.0.0.1", port=9000)
    agent_srv.run_agent(cfg)

    # agent.env exists at the expected path.
    env_file = home / ".llauncher" / "agent.env"
    assert env_file.exists(), "agent.env must be auto-generated on first run"

    # Mode 0600 on the file.
    file_mode = stat.S_IMODE(env_file.stat().st_mode)
    assert file_mode == 0o600, f"expected 0600, got {oct(file_mode)}"

    # Parent dir 0700.
    parent_mode = stat.S_IMODE(env_file.parent.stat().st_mode)
    assert parent_mode == 0o700, f"expected 0700 on parent, got {oct(parent_mode)}"

    # Content is a LLAUNCHER_AGENT_TOKEN= line with a non-trivial secret.
    content = env_file.read_text(encoding="utf-8").strip()
    assert content.startswith("LLAUNCHER_AGENT_TOKEN=")
    token = content.split("=", 1)[1]
    assert len(token) >= 32

    # Stderr announced the token exactly once.
    err = buf.getvalue()
    assert token in err
    assert err.count(token) == 1


def test_loopback_second_run_reuses_existing_token(monkeypatch, tmp_path):
    """C1-reuse: an existing agent.env token line is honored, not rewritten."""
    from llauncher.agent.config import AgentConfig
    from llauncher.agent import server as agent_srv

    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    home = _isolate_home(monkeypatch, tmp_path)

    # Pre-seed an existing agent.env with a token line.
    seeded_path = home / ".llauncher" / "agent.env"
    seeded_path.parent.mkdir(parents=True)
    seeded_path.parent.chmod(0o700)
    seeded_path.write_text("LLAUNCHER_AGENT_TOKEN=preexisting-token-value\n", encoding="utf-8")
    seeded_path.chmod(0o600)

    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)

    cfg = AgentConfig(host="127.0.0.1", port=9000)
    agent_srv.run_agent(cfg)

    # File content unchanged.
    assert (
        seeded_path.read_text(encoding="utf-8").strip()
        == "LLAUNCHER_AGENT_TOKEN=preexisting-token-value"
    )
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
