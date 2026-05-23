"""Integration tests for security hardening §3 C4 (issue #79).

The agent deliberately emits **no CORS headers**. Browsers cannot make
cross-origin requests to the agent from arbitrary pages — this is the
desired posture per the security-hardening plan (§3 control C4). These
tests pin that behavior so a future "let's just add CORS" PR cannot
silently broaden the surface.

Hooks covered:

- **C4-a** (plan §4 assertion 8): ``OPTIONS /status`` does not include
  ``Access-Control-Allow-Origin`` in response headers.
- A representative GET endpoint (``/health``) likewise emits no
  ``Access-Control-*`` headers.
- A representative POST endpoint, exercised with an ``Origin:`` request
  header set (so a misconfigured server *could* echo it back) likewise
  emits no ``Access-Control-*`` headers.

These tests use the in-process FastAPI TestClient via the
``agent_client`` / ``agent_client_with_token`` fixtures from
``tests/integration/conftest.py``.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


# All header names that would indicate a CORS posture leaking out of the
# agent. Asserted as a set so a future regression naming, e.g.,
# ``Access-Control-Expose-Headers`` is caught even though it's not on the
# common-cases list.
_CORS_HEADER_PREFIX = "access-control-"


def _cors_headers(headers) -> dict[str, str]:
    """Return any ``Access-Control-*`` headers from a response, lower-cased."""
    return {
        k.lower(): v
        for k, v in headers.items()
        if k.lower().startswith(_CORS_HEADER_PREFIX)
    }


# ─────── C4-a: OPTIONS /status emits no Access-Control-* headers ───────────


def test_options_status_no_cors_headers(agent_client):
    """Plan §4 assertion C4-a, no-auth flavor.

    A bare ``OPTIONS /status`` request — the canonical CORS preflight
    shape — must not echo any ``Access-Control-*`` headers. We do not
    care whether the response status is 200, 405, or anything else;
    only that no CORS posture is signalled.
    """
    resp = agent_client.options("/status")
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        f"OPTIONS /status leaked CORS headers: {leaked}. "
        "Plan §3 C4 / §4 C4-a requires no Access-Control-* headers."
    )
    # Belt-and-suspenders: the specific header named in the plan.
    assert "access-control-allow-origin" not in {
        k.lower() for k in resp.headers
    }


def test_options_status_no_cors_headers_with_origin_request(agent_client):
    """Same as above, but with an ``Origin:`` header on the request.

    A misconfigured CORS middleware would typically only echo
    ``Access-Control-Allow-Origin`` when the client supplied an
    ``Origin``. This test makes sure the absence is real, not just
    a side effect of the request shape.
    """
    resp = agent_client.options(
        "/status",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Api-Key",
        },
    )
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        "OPTIONS /status with Origin: still leaked CORS headers: "
        f"{leaked}. Plan §3 C4 forbids any Access-Control-* response "
        "headers."
    )


# ─────── GET flavor: /health emits no Access-Control-* headers ─────────────


def test_get_health_no_cors_headers_with_origin(agent_client):
    """Representative GET endpoint check.

    ``/health`` is exempt from auth and so reachable in every config
    flavor — it's the most leak-prone endpoint if a CORS middleware
    were silently added in front of the auth layer.
    """
    resp = agent_client.get(
        "/health",
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 200
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        f"GET /health leaked CORS headers: {leaked}"
    )


# ─────── POST flavor: a POST with Origin: emits no Access-Control-* ────────


def test_post_with_origin_header_no_cors_headers(agent_client_with_token):
    """Representative POST endpoint, with ``Origin:`` set on the request.

    We hit ``/start/9999`` without an auth header so the middleware
    short-circuits to 401 *before* the route handler runs. Whatever the
    body of the response is, it must not include any CORS-allow
    headers — the auth-failure path is just as much a place for a
    misconfigured CORS middleware to leak as the success path.
    """
    client, _token = agent_client_with_token
    resp = client.post(
        "/start/9999",
        json={"model": "alpha"},
        headers={"Origin": "https://evil.example"},
    )
    # We expect the auth middleware to reject this — body shape is not
    # under test, only the response headers.
    assert resp.status_code in (401, 403)
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        f"POST /start/{{port}} leaked CORS headers: {leaked}"
    )


def test_post_with_origin_header_no_cors_headers_authed(agent_client_with_token):
    """Same POST flavor, but with a valid token so the route actually runs.

    Covers the success / handler-emitted-response path: even when a
    route handler is the one writing the response, no CORS headers
    should be attached by any middleware on the way out.
    """
    client, token = agent_client_with_token
    # The model does not need to exist — any non-401/403 response is
    # fine; we only inspect headers, not the body. Use an obviously-
    # missing model so we hit a fast 4xx/5xx path with no side effects.
    resp = client.post(
        "/start/9999",
        json={"model": "definitely-not-a-real-model"},
        headers={
            "X-Api-Key": token,
            "Origin": "https://evil.example",
        },
    )
    # Sanity: auth passed (we did not get a 401/403 from the middleware).
    assert resp.status_code not in (401, 403), (
        f"Unexpected auth failure with valid token: {resp.status_code}"
    )
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        f"Authed POST /start/{{port}} leaked CORS headers: {leaked}"
    )
