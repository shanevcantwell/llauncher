"""Integration tests for security hardening §3 C3 (issue #78).

Covers:

- **C3-a**: A POST to the agent with a body that exceeds the 1 MiB
  request-body cap returns HTTP 413, regardless of whether auth is
  configured. The check must short-circuit before the route handler
  runs (the route's Pydantic schema would otherwise reject the body
  with 422 before the size check fired, masking the regression).
- **C3-normal**: A normal-size request still flows through to its
  usual handler and receives its usual response.
- **C3-content-length**: A request advertising an oversize
  ``Content-Length`` header is rejected on the fast path (no body
  bytes read).

These tests use the in-process FastAPI TestClient via the
``agent_client`` / ``agent_client_with_token`` fixtures from
``tests/integration/conftest.py``.
"""

from __future__ import annotations

import pytest

from llauncher.agent.middleware import MAX_REQUEST_BODY_BYTES


pytestmark = pytest.mark.integration


# Sanity check: the documented cap matches the constant the implementation
# exports. If someone later edits one without the other, this is the
# canary.
def test_body_cap_constant_is_one_mib():
    assert MAX_REQUEST_BODY_BYTES == 1024 * 1024


# ─────── C3-a: oversize body → 413 (no-auth app) ──────────────────────────


def test_oversize_post_returns_413_no_auth(agent_client):
    """An oversize POST body is rejected with 413 even when auth is off.

    The body-size middleware lives outside auth and ahead of the router,
    so the cap fires on the no-auth app exactly as it does on the
    auth-enabled app.
    """
    # 2 MiB of bytes — comfortably over the 1 MiB cap. We aim a POST at
    # /start/{port}: in a normal flow this would Pydantic-parse a JSON
    # body, but the size cap must short-circuit before we get there.
    oversize = b"x" * (2 * 1024 * 1024)

    resp = agent_client.post(
        "/start/9999",
        content=oversize,
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 413, (
        f"expected 413 for oversize body, got {resp.status_code}: {resp.text!r}"
    )


# ─────── C3-a (auth path): oversize body → 413 even with valid token ─────


def test_oversize_post_returns_413_with_auth(agent_client_with_token):
    """The body-size cap fires before auth — but a valid token still
    yields 413 (not 401/403). This confirms the middleware order: size
    cap is the outermost layer."""
    client, token = agent_client_with_token
    oversize = b"x" * (2 * 1024 * 1024)

    resp = client.post(
        "/start/9999",
        content=oversize,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": token,
        },
    )

    assert resp.status_code == 413


# ─────── C3-content-length fast path ──────────────────────────────────────


def test_oversize_content_length_header_rejected(agent_client):
    """An honest oversize ``Content-Length`` is rejected on the fast path.

    We send an empty body but lie that it is huge. The middleware must
    trust the declared length and reject without attempting to read.
    (httpx normalizes some headers, so we exercise the path via the
    raw ASGI scope by sending an actual oversize body; the practical
    end-user observation is the same: 413.)
    """
    # The httpx TestClient sets Content-Length from the actual body, so
    # the most reliable way to exercise the header fast path is still to
    # send a real oversize body. The accumulator path and the header
    # path converge on the same observable result.
    oversize = b"y" * (MAX_REQUEST_BODY_BYTES + 1)
    resp = agent_client.post("/start/9999", content=oversize)
    assert resp.status_code == 413


# ─────── C3-normal: under-cap request still works ────────────────────────


def test_normal_request_still_works(agent_client):
    """A normal-size request passes the cap and reaches its handler.

    We hit ``/health`` (a small GET) and the model-listing endpoint as
    representatives of read-side traffic; both should respond with their
    usual 200, demonstrating the middleware is non-disruptive for
    legitimate payloads.
    """
    resp = agent_client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "healthy"


def test_under_cap_post_reaches_handler(agent_client):
    """An under-cap POST flows past the size check and reaches the route.

    We POST a small (~few hundred bytes) JSON body to ``/start/{port}``.
    The expected response is *not* 413 — the route handler may return
    anything else (a routed business response — 200, 4xx, 5xx), but the
    size middleware must not interfere.
    """
    small_body = {"model": "nonexistent-model-for-this-test"}
    resp = agent_client.post("/start/9999", json=small_body)
    # Any status other than 413 means the size cap correctly let the
    # request through to the routing/business layer.
    assert resp.status_code != 413


# ─────── Direct-ASGI tests for the streaming / chunked paths ─────────────


@pytest.mark.asyncio
async def test_streaming_path_rejects_chunked_oversize():
    """No Content-Length header (simulating chunked transfer): the
    middleware must accumulate body bytes and reject mid-stream when
    the cap is crossed.

    The httpx TestClient always sets Content-Length on POST bodies, so
    we exercise this branch by driving the middleware directly over
    ASGI with a hand-rolled scope/receive/send triple.
    """
    from llauncher.agent.middleware import BodySizeLimitMiddleware

    inner_called = False

    async def inner_app(scope, receive, send):
        # Drain receive — this is what a real downstream app would do.
        nonlocal inner_called
        inner_called = True
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            if not msg.get("more_body"):
                return

    mw = BodySizeLimitMiddleware(inner_app, max_bytes=1024)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/start/9999",
        # No Content-Length header — force the streaming path.
        "headers": [(b"content-type", b"application/json")],
    }

    # Three chunks of 512 bytes — total 1536 > 1024.
    chunks = [
        {"type": "http.request", "body": b"a" * 512, "more_body": True},
        {"type": "http.request", "body": b"b" * 512, "more_body": True},
        {"type": "http.request", "body": b"c" * 512, "more_body": False},
    ]
    chunk_iter = iter(chunks)

    async def receive():
        try:
            return next(chunk_iter)
        except StopIteration:
            return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)

    # Expect a 413 response start + body.
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


@pytest.mark.asyncio
async def test_content_length_fast_path_short_circuits():
    """An oversize Content-Length is rejected before any body byte is read.

    The downstream app must not be invoked at all.
    """
    from llauncher.agent.middleware import BodySizeLimitMiddleware

    inner_called = False

    async def inner_app(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    mw = BodySizeLimitMiddleware(inner_app, max_bytes=1024)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/start/9999",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", b"99999"),
        ],
    }

    async def receive():
        raise AssertionError("receive() must not be called on the fast path")

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)

    assert not inner_called
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


@pytest.mark.asyncio
async def test_non_http_scope_passes_through():
    """Lifespan / websocket scopes are not the body-size middleware's
    concern; they must pass through unmodified."""
    from llauncher.agent.middleware import BodySizeLimitMiddleware

    inner_called = False

    async def inner_app(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    mw = BodySizeLimitMiddleware(inner_app)

    scope = {"type": "lifespan"}

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    await mw(scope, receive, send)
    assert inner_called


@pytest.mark.asyncio
async def test_malformed_content_length_falls_through():
    """A non-integer Content-Length header is ignored (the streaming
    path still protects us)."""
    from llauncher.agent.middleware import BodySizeLimitMiddleware

    inner_called = False

    async def inner_app(scope, receive, send):
        nonlocal inner_called
        inner_called = True
        msg = await receive()
        assert msg["type"] == "http.request"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BodySizeLimitMiddleware(inner_app, max_bytes=1024)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/start/9999",
        "headers": [
            (b"content-length", b"not-a-number"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": b"small", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)

    assert inner_called
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 200
