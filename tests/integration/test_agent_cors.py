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


def _assert_no_cors_signal(resp, label: str) -> None:
    """Assert no ``Access-Control-*`` headers AND no ``Vary: Origin`` echo.

    The ``Vary: Origin`` check (issue #115) catches a CORS middleware
    that is mounted but happens to decline echoing on a given request
    — Starlette's ``CORSMiddleware`` sets ``Vary: Origin`` defensively
    whenever it is in the stack, even when the request shape causes it
    to suppress the ``Access-Control-Allow-Origin`` header. Asserting
    only the ``Access-Control-*`` absence would miss that "middleware
    is mounted, just declining today" regression shape.
    """
    leaked = _cors_headers(resp.headers)
    assert leaked == {}, (
        f"{label} leaked Access-Control-* headers: {leaked}. "
        "Plan §3 C4 forbids any Access-Control-* response headers."
    )
    vary = resp.headers.get("vary", "").lower()
    assert "origin" not in vary, (
        f"{label} emitted Vary: {vary!r} — 'origin' in Vary is the "
        "smoking-gun signal that CORSMiddleware is mounted in the "
        "stack even when it declines to echo. Plan §3 C4 / issue #115."
    )


# ─────── C4-a: OPTIONS /status emits no Access-Control-* headers ───────────


def test_options_status_no_cors_headers(agent_client):
    """Plan §4 assertion C4-a, no-auth flavor.

    A bare ``OPTIONS /status`` request — the canonical CORS preflight
    shape — must not echo any ``Access-Control-*`` headers. We do not
    care whether the response status is 200, 405, or anything else;
    only that no CORS posture is signalled.
    """
    resp = agent_client.options("/status")
    _assert_no_cors_signal(resp, "OPTIONS /status (no Origin)")
    # Belt-and-suspenders: the specific header named in the plan.
    assert "access-control-allow-origin" not in {
        k.lower() for k in resp.headers
    }


def test_options_status_no_cors_headers_with_origin_request(agent_client):
    """Same as above, but with an ``Origin:`` header and a state-changing preflight.

    A misconfigured CORS middleware would typically only echo
    ``Access-Control-Allow-Origin`` when the client supplied an
    ``Origin``. CORS-bypass concerns center on state-changing
    requests (POST/PUT/DELETE), so the preflight names ``POST`` for
    its ``Access-Control-Request-Method`` (issue #114) — the threat
    shape a real attacker page would emit before issuing a credentialed
    state-changing call. This pins absence under the realistic
    preflight, not just the trivial GET preflight.
    """
    resp = agent_client.options(
        "/status",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Api-Key, Content-Type",
        },
    )
    _assert_no_cors_signal(resp, "OPTIONS /status (POST preflight)")


# ─────── GET flavor: /health emits no Access-Control-* headers ─────────────


def test_get_health_no_cors_headers_with_origin(agent_client):
    """Representative GET endpoint check (real 2xx success path).

    ``/health`` is exempt from auth and so reachable in every config
    flavor — it's the most leak-prone endpoint if a CORS middleware
    were silently added in front of the auth layer. This is also a
    deterministic 2xx response, so the absence assertion exercises the
    success-path code in every middleware (a future CORS middleware
    that only attaches headers on 2xx would be caught here).
    """
    resp = agent_client.get(
        "/health",
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 200
    _assert_no_cors_signal(resp, "GET /health (2xx with Origin)")


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
    _assert_no_cors_signal(resp, "POST /start/9999 (auth-rejected)")


def test_post_with_origin_header_no_cors_headers_authed_2xx(agent_client_with_token):
    """Authed POST that reaches a real 2xx handler response (issue #113).

    ``/stop/{port}`` is idempotent and returns ``200 action=already_empty``
    when nothing is running on the port (ADR-LLNCH-010). We use it as the
    cheapest authed POST that exercises a true success-path handler
    response — no spawn, no port binding, no side effects — so the
    absence assertion covers the case where a hypothetical future
    ``CORSMiddleware(add_only_on_2xx=...)`` would attach headers only
    on successful state-changing requests.
    """
    client, token = agent_client_with_token
    resp = client.post(
        "/stop/9999",
        headers={
            "X-Api-Key": token,
            "Origin": "https://evil.example",
        },
    )
    assert resp.status_code == 200, (
        f"Expected 200 from idempotent /stop on idle port, got "
        f"{resp.status_code}: {resp.text!r}"
    )
    _assert_no_cors_signal(resp, "POST /stop/9999 (authed 2xx)")
