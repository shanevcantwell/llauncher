"""Tests for AuthenticationMiddleware in the llauncher agent service."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llauncher.agent.middleware import (
    AuthenticationMiddleware,
    _AUTH_EXEMPT_PATHS,
)


def _make_app(token=None) -> tuple[FastAPI, TestClient]:
    """Create a test app optionally wrapped with AuthenticationMiddleware."""
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/docs")
    def docs_endpoint():  # proxy for OpenAPI docs path
        return {"openapi": True}

    @app.get("/openapi.json")
    def openapi_json():
        return {"schema": {}}

    @app.get("/redoc")
    def redoc_page():
        return {}

    @app.get("/protected")
    def protected():
        return {"data": "secret"}

    if token is not None:
        app.add_middleware(AuthenticationMiddleware, expected_token=token)

    return app, TestClient(app)


def test_no_token_allows_all_requests():
    """When no auth token is configured, all requests pass through."""
    app, client = _make_app(token=None)
    assert client.get("/health").status_code == 200
    assert client.get("/protected").status_code == 200


def test_with_token_rejects_unauthenticated_returns_401():
    """Missing X-Api-Key header should return 401."""
    app, client = _make_app(token="secret")
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_with_token_accepts_valid_key():
    """Correct X-Api-Key header should allow the request."""
    app, client = _make_app(token="secret")
    response = client.get("/protected", headers={"X-Api-Key": "secret"})
    assert response.status_code == 200


def test_with_token_rejects_wrong_key_returns_403():
    """Wrong X-Api-Key value should return 403."""
    app, client = _make_app(token="secret")
    response = client.get("/protected", headers={"X-Api-Key": "wrong"})
    assert response.status_code == 403


def test_openapi_docs_excluded_from_auth():
    """Exempt paths (/health, /docs, etc.) should be accessible without auth."""
    app, client = _make_app(token="secret")

    # Even with auth active, these endpoints are free
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code in (200,)
    assert client.get("/redoc").status_code in (200,)


def test_with_empty_api_key_returns_403():
    """Empty string X-Api-Key is present but wrong — should return 403 (not 401)."""
    app, client = _make_app(token="secret")
    
    # Empty key means header was sent but value is empty → credentials provided, access denied
    response = client.get("/protected", headers={"X-Api-Key": ""})
    assert response.status_code == 403


def test_health_exempt_with_empty_key(self=None):
    """/health remains accessible even when a wrong/empty key is sent (exempt path)."""
    app, client = _make_app(token="secret")

    # Exempt paths bypass auth entirely — empty or wrong key doesn't matter
    response = client.get("/health", headers={"X-Api-Key": ""})
    assert response.status_code == 200


def test_exempt_paths_match_documented_set():
    """Pin the exempt set to ADR-003's narrowed contract (#126 drift guard).

    ADR-003 Decision §3 / docs/auth.md document exactly four auth-exempt
    paths. Every read endpoint (`/status`, `/models`, `/node-info`, …) must
    require the token because each leaks something. This guard fails loudly
    if code widens the set without a matching doc update, re-opening the
    drift that #126 closed.
    """
    assert _AUTH_EXEMPT_PATHS == frozenset(
        {"/health", "/docs", "/redoc", "/openapi.json"}
    )
    # Read endpoints that must NOT be exempt (the security-cohort posture).
    for read_path in ("/status", "/models", "/models/validate", "/node-info"):
        assert read_path not in _AUTH_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware — ASGI-level edge branches (INTERFACE close-out)
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

from llauncher.agent.middleware import BodySizeLimitMiddleware  # noqa: E402


@pytest.mark.asyncio
async def test_limited_receive_forwards_non_request_message():
    """``limited_receive`` forwards non-``http.request`` messages verbatim.

    Covers middleware.py:141: a downstream app that keeps draining
    ``receive`` past the body will eventually get a non-request message
    (e.g. ``http.disconnect``); the wrapper must return it unchanged
    without touching the byte accumulator.
    """
    seen: list[str] = []

    async def inner_app(scope, receive, send):
        # Drain until the disconnect — this pulls the non-request message
        # through ``limited_receive`` (middleware.py:140-141).
        while True:
            msg = await receive()
            seen.append(msg["type"])
            if msg["type"] == "http.disconnect":
                return

    mw = BodySizeLimitMiddleware(inner_app, max_bytes=1024)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [],  # no content-length → streaming path
    }

    messages = iter(
        [
            {"type": "http.request", "body": b"hi", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def send(message):
        pass

    await mw(scope, receive, send)

    # The under-cap request flowed through, and the non-request disconnect
    # was forwarded to the downstream app (the line-141 branch).
    assert "http.request" in seen
    assert "http.disconnect" in seen


@pytest.mark.asyncio
async def test_guarded_send_suppresses_late_response_after_rejection():
    """A send attempted *after* the cap trips is suppressed.

    Covers middleware.py:159: once ``rejected`` is set (body exceeded the
    cap, surfaced to the app as a sentinel ``http.disconnect``), a
    downstream app that still tries to emit a response has those messages
    swallowed by ``guarded_send`` — only the middleware's own 413 reaches
    the wire.
    """

    async def inner_app(scope, receive, send):
        # Consume until the cap-trip sentinel, then (mis)behave by emitting
        # a 200 anyway — guarded_send must drop it.
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"late"})

    mw = BodySizeLimitMiddleware(inner_app, max_bytes=1024)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-type", b"application/json")],  # streaming path
    }

    chunks = iter(
        [
            {"type": "http.request", "body": b"a" * 2048, "more_body": False},
        ]
    )

    async def receive():
        try:
            return next(chunks)
        except StopIteration:
            return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)

    # The downstream 200 was suppressed; the only response.start on the wire
    # is the middleware's 413.
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
