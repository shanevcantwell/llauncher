"""Live-socket auth: a real ``X-Api-Key`` over a real TCP socket (#317).

Every existing auth test is either in-process (FastAPI ``TestClient``,
which never opens a socket) or mocked — see the issue for the full audit.
This suite closes that gap with a real ``uvicorn`` server bound to a real
``127.0.0.1:<port>``, driven by a real :class:`~llauncher.remote.node.RemoteNode`
using real ``httpx`` over the wire, and asserts:

- (a) correct token → 200 on an authed endpoint (``/node-info``);
- (b) wrong/missing token → 403/401;
- (c) a CRLF+BOM-contaminated ``agent.env`` token file still authenticates
  correctly over the wire — the exact header-framing regression #310 fixed
  (a raw ``\\r`` tail on the token corrupts the ``X-Api-Key`` header and the
  request fails at the transport level before the app ever sees it).

The server is a real ``uvicorn.Server`` bound via ``asyncio`` in a background
thread (a real bind/listen/accept socket, not a mock) rather than a
subprocess: this keeps the fixture fast and dependency-free while still
exercising every seam the issue calls out (real socket, real header framing,
real ``httpx`` client, real ``create_app``/``resolve_agent_token`` code
paths) — a subprocess would add process-management complexity without
covering any additional seam, since the CRLF/BOM defect class lives in
*token parsing + header framing*, not in process boundaries.

Marked ``@pytest.mark.live_agent_socket`` (declared in ``pytest.ini``) rather
than the existing ``integration_real`` — that marker's documented contract
is "requires a real llama-server binary + GGUF" (``_real_mode_available()``
in ``tests/integration/conftest.py``), which this suite does not need: it
never spawns llama-server, only a real ``uvicorn`` bind. Reusing
``integration_real`` would either mislabel the requirement or (since these
tests do not call ``real_binary_env``) silently defeat that marker's skip
gate and run unconditionally. Skips by default, opt in with
``LLAUNCHER_LIVE_AGENT_SOCKET=1`` — consistent with the repo's opt-in-real
convention (``LLAUNCHER_INTEGRATION_REAL=1``) even though no external binary
is required, so a real socket bind is never a surprise in a constrained CI
sandbox.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

pytestmark = pytest.mark.live_agent_socket


def _live_socket_opt_in() -> bool:
    return os.environ.get("LLAUNCHER_LIVE_AGENT_SOCKET") == "1"


@pytest.fixture(autouse=True)
def _require_opt_in():
    if not _live_socket_opt_in():
        pytest.skip("set LLAUNCHER_LIVE_AGENT_SOCKET=1 to opt in (real TCP bind)")


def _free_port() -> int:
    """Return a likely-free local port (bind+close is good enough in tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until_listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.25)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        finally:
            s.close()
        time.sleep(0.05)
    raise RuntimeError(f"agent never started listening on 127.0.0.1:{port}")


@dataclass
class _LiveAgent:
    port: int
    token: str


class _ServerThread(threading.Thread):
    """Runs a ``uvicorn.Server`` on a real socket until told to stop."""

    def __init__(self, server: uvicorn.Server):
        super().__init__(daemon=True)
        self.server = server

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=5.0)


@pytest.fixture
def live_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_LiveAgent]:
    """Bind a real ``llauncher`` agent app on a real loopback socket.

    Builds the token via :func:`llauncher.core.agent_token.resolve_agent_token`
    against an isolated ``agent.env`` (never the operator's real
    ``~/.llauncher``) and serves the real ``create_app`` ASGI app with a real
    ``uvicorn.Server`` bound to ``127.0.0.1:<free_port>`` — a real listening
    TCP socket, not a mock or a ``TestClient`` in-process transport.
    """
    from llauncher.agent.server import create_app
    from llauncher.core.agent_token import resolve_agent_token

    # ``env_value=None`` means resolve_agent_token reads the env at call
    # time (precedence 1 beats the file read) — clear any ambient token so
    # the fixture always resolves against the isolated tmp_path env file,
    # never an unrelated real token. Suite-wide convention (see
    # test_agent_security_c1_c2.py, test_agent_env_single_source.py).
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    env_path = tmp_path / "agent.env"
    token = resolve_agent_token(env_value=None, env_path=env_path, allow_generate=True)
    assert token

    app = create_app(auth_token=token)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()
    try:
        _wait_until_listening(port)
        yield _LiveAgent(port=port, token=token)
    finally:
        thread.stop()


@pytest.fixture
def live_agent_crlf_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_LiveAgent]:
    """Same as ``live_agent``, but the token is read back from a
    CRLF+BOM-contaminated ``agent.env`` — the Windows-authored-file shape
    #310 fixed. Exercises the real read-from-disk path
    (``resolve_agent_token`` → ``_read_env_file_token`` →
    ``parse_env_file``) rather than a hand-built clean string, so a
    regression in the ``utf-8-sig`` + line-ending-agnostic decode would
    reintroduce a raw ``\\r`` into the token and break the ``X-Api-Key``
    header over the real wire.
    """
    from llauncher.agent.server import create_app
    from llauncher.core.agent_token import resolve_agent_token

    # Critical for AC(c): an ambient LLAUNCHER_AGENT_TOKEN would win the
    # precedence chain (env beats file) and the test would pass without
    # ever exercising the CRLF/BOM decode path this fixture exists to pin.
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    env_path = tmp_path / "agent.env"
    clean_token = "windows-issued-token-abc123"
    text = (
        "# agent.env -- written by scripts/windows/install.ps1\r\n"
        "LLAUNCHER_AGENT_HOST=127.0.0.1\r\n"
        f"LLAUNCHER_AGENT_TOKEN={clean_token}\r\n"
    )
    env_path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))

    # Resolve exactly as the real agent would on startup: no env-var
    # override, read the (contaminated) file.
    token = resolve_agent_token(env_value=None, env_path=env_path, allow_generate=False)
    assert token == clean_token, "sanity: resolver must strip the CRLF/BOM before this fixture serves it"

    app = create_app(auth_token=token)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = _ServerThread(server)
    thread.start()
    try:
        _wait_until_listening(port)
        yield _LiveAgent(port=port, token=token)
    finally:
        thread.stop()


# ─────────────────────────── (a) correct token → 200 ────────────────────────


def test_correct_token_over_real_socket_gets_200(live_agent: _LiveAgent) -> None:
    """(a) A real ``RemoteNode`` with the correct token gets 200 on ``/node-info``."""
    from llauncher.remote.node import RemoteNode

    node = RemoteNode("live-agent", "127.0.0.1", port=live_agent.port, api_key=live_agent.token)
    info = node.get_node_info()

    assert info is not None
    assert "node_name" in info


# ─────────────────────── (b) wrong/missing token → 401/403 ──────────────────


def test_wrong_token_over_real_socket_gets_403(live_agent: _LiveAgent) -> None:
    """(b) A wrong (but present) ``X-Api-Key`` gets 403 over the real socket."""
    import httpx

    resp = httpx.get(
        f"http://127.0.0.1:{live_agent.port}/node-info",
        headers={"X-Api-Key": "definitely-the-wrong-token"},
        timeout=5.0,
    )
    assert resp.status_code == 403


def test_missing_token_over_real_socket_gets_401(live_agent: _LiveAgent) -> None:
    """(b) No ``X-Api-Key`` header at all gets 401 over the real socket."""
    import httpx

    resp = httpx.get(f"http://127.0.0.1:{live_agent.port}/node-info", timeout=5.0)
    assert resp.status_code == 401


def test_wrong_token_via_remote_node_yields_no_info(live_agent: _LiveAgent) -> None:
    """(b) ``RemoteNode`` surfaces auth failure as ``None`` (its documented
    contract on a non-200 response), never as a silently-successful read."""
    from llauncher.remote.node import RemoteNode

    node = RemoteNode("live-agent", "127.0.0.1", port=live_agent.port, api_key="wrong-token")
    assert node.get_node_info() is None


# ────── (c) CRLF/BOM-contaminated token file still frames correctly ─────────


def test_crlf_bom_token_file_authenticates_over_real_socket(
    live_agent_crlf_bom: _LiveAgent,
) -> None:
    """(c) A token round-tripped through a CRLF+BOM ``agent.env`` still
    authenticates cleanly over the real wire — the #310 regression guard,
    exercised end-to-end (disk → resolver → header → real socket) rather
    than at the parser unit-test level alone."""
    from llauncher.remote.node import RemoteNode

    node = RemoteNode(
        "live-agent", "127.0.0.1", port=live_agent_crlf_bom.port, api_key=live_agent_crlf_bom.token
    )
    info = node.get_node_info()

    assert info is not None
    assert "node_name" in info


def test_crlf_bom_token_has_no_stray_carriage_return(live_agent_crlf_bom: _LiveAgent) -> None:
    """(c) Sanity anchor: if #310 regressed, the resolved token would carry a
    trailing ``\\r``, which a raw socket write would frame as an invalid
    header line (pre-#310: ``400 Bad Request`` from the ASGI server, not a
    clean 401/403/200) rather than merely mismatching. Assert directly on
    the in-memory value so this test fails on the exact defect shape rather
    than only on its downstream symptom."""
    assert "\r" not in live_agent_crlf_bom.token
    assert "﻿" not in live_agent_crlf_bom.token
