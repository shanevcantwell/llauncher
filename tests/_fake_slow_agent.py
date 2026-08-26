"""Shared test double: a real HTTP server whose ``/start``/``/swap`` block.

Issue #503 — the RemoteNode client used a flat 5 s ``httpx`` timeout for
every verb, including ``/start`` and ``/swap``, which the agent can
legitimately take well past 5 s to answer (readiness ceiling
``DEFAULT_READINESS_TIMEOUT_S`` = 120 s). This produced a false-negative
failure toast/CLI exit for a start/swap the agent went on to complete.

This fixture is a real ``ThreadingHTTPServer`` bound to a real loopback
socket (not a mocked ``httpx.Client``) so the tests that use it exercise the
actual wall-clock timeout behavior of the ``httpx`` client built by
``RemoteNode._get_client`` — a mocked transport would not reproduce a real
socket-level timeout at all. Kept dependency-free (stdlib ``http.server``
only, no FastAPI/uvicorn) since the fake agent only needs to accept a POST
and sleep before answering.
"""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_handler(delay_s: float, body: dict) -> type[BaseHTTPRequestHandler]:
    payload = json.dumps(body).encode("utf-8")

    class _SlowHandler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            import time

            time.sleep(delay_s)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            self._respond()

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            self._respond()

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib method name
            self._respond()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence stdlib's default stderr access log

    return _SlowHandler


@contextmanager
def slow_fake_agent(
    delay_s: float = 6.0,
    body: dict | None = None,
) -> Iterator[int]:
    """Bind a real loopback HTTP server whose POST handler sleeps ``delay_s``
    seconds before answering 200 with ``body`` (default a start/swap-shaped
    success envelope). Yields the bound port.

    ``delay_s`` defaults to 6.0 — comfortably past the pre-fix 5.0 s
    ``RemoteNode`` default timeout, so a test against this fixture fails
    loudly (``httpx.RequestError``/``ReadTimeout``) without the #503 fix and
    passes with it, without paying the full 150 s readiness-ceiling wait.
    """
    response_body = body if body is not None else {
        "success": True,
        "action": "started",
        "message": "ok",
    }
    port = _free_port()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), _make_handler(delay_s, response_body)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
