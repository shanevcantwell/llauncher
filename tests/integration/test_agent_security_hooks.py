"""Phase C — agent HTTP security hooks from the hardening plan (#70).

Picks the assertions from ``docs/plans/security-hardening-plan.md §4`` that
fit an MCP/HTTP integration harness. UI-only hooks (C11) and
filesystem-mode hooks (C8, C10) are out of scope here — they belong with
unit tests or follow-up tickets.

Hooks covered:

- C1-a  POST /start without ``X-Api-Key`` returns 401 when token is set.
- C1-b  Wrong key returns 403 (not 401).
- C1-c  /health is exempt and returns 200 without a key.
- §4-17 MCP error envelope is structured, never a stack trace.
- §4-16 Auth comparison uses ``hmac.compare_digest`` (code-path proof).

Note: The plan uses ``Authorization``; the implementation uses
``X-Api-Key`` (see ``llauncher/agent/middleware.py``). We assert against
the implemented header — the plan's wording is the stale half.
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.integration


# ─────── C1-a/b/c: token enforcement on the wired-up agent app ──────────────


def test_start_without_api_key_returns_401(agent_client_with_token):
    client, _token = agent_client_with_token
    resp = client.post("/start/9999", json={"model": "alpha"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_start_with_wrong_api_key_returns_403(agent_client_with_token):
    client, _token = agent_client_with_token
    resp = client.post(
        "/start/9999", json={"model": "alpha"}, headers={"X-Api-Key": "nope"}
    )
    assert resp.status_code == 403


def test_health_exempt_without_api_key(agent_client_with_token):
    client, _token = agent_client_with_token
    resp = client.get("/health")
    assert resp.status_code == 200


# ─────── §4-17: MCP error envelope is structured, never raw exception ───────


async def test_mcp_invalid_model_returns_structured_error(mcp_env, mcp_dispatch):
    """Unknown model surfaces as ADR-010 error envelope, no stack trace."""
    result = await mcp_dispatch(
        "start_server", {"model_name": "definitely-not-a-model", "port": 19999}
    )
    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["action"] == "error"
    msg = result.get("message") or result.get("error") or ""
    assert "Traceback" not in msg
    assert "definitely-not-a-model" in msg


async def test_mcp_unknown_tool_returns_structured_error(mcp_env, mcp_dispatch):
    """Unknown tool name routes through call_tool_handler's except — but
    we're calling _dispatch_tool directly, which raises. The handler one
    layer up wraps it. Assert the handler shape too."""
    import json
    from llauncher.mcp_server.server import call_tool_handler

    out = await call_tool_handler("not_a_tool_name", {})
    assert len(out) == 1
    payload = json.loads(out[0].text)
    assert "error" in payload
    assert "Traceback" not in payload["error"]


# ─────── §4-16: constant-time auth comparison is in place ──────────────────


def test_auth_uses_hmac_compare_digest():
    """Source-level proof; no microbench. Hardening plan §4.16."""
    from llauncher.agent import middleware

    src = inspect.getsource(middleware.AuthenticationMiddleware.dispatch)
    assert "compare_digest" in src
