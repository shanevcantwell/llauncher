# Enhancement: Add Authentication to Agent HTTP API

**Type:** Security / Enhancement  
**Priority:** Critical  
**Component:** `agent/server.py`, `agent/routing.py`

---

## Problem

The llauncher agent binds to `0.0.0.0:8765` by default with **zero authentication** on any endpoint. Any caller on the network can:

- **Start models** consuming GPU/CPU resources (`POST /start/{model_name}`, `POST /start-with-eviction/{model_name}`)
- **Stop running servers** causing denial-of-service (`POST /stop/{port}`)
- **Read server logs** leaking internal state (`GET /logs/{port}`)

A warning is logged at `agent/server.py:183` when binding to `0.0.0.0`, but this does not enforce any restriction — it merely informs the operator.

---

## Impact

An attacker on the same network (or internet, if port-forwarded) can fully control the agent's serving infrastructure with no barrier. This is the highest-impact vulnerability in the codebase.

---

## Proposed Solution

Add a shared secret authentication mechanism via environment variable:

1. **New env var:** `LAUNCHER_AGENT_TOKEN` (optional; if unset, behavior is unchanged for backward compatibility)
2. **FastAPI middleware** validates the `Authorization: Bearer <token>` header on every request to `/start/`, `/stop/`, `/start-with-eviction/`, and `/logs/`.
3. **Fallback:** If the token is not set, accept requests without auth (preserves current behavior) but log a warning on startup.

### Design Sketch

```python
# agent/server.py — within create_app()

token: str | None = os.environ.get("LAUNCHER_AGENT_TOKEN")
if token:
    app.add_middleware(AuthMiddleware, expected_token=token)
else:
    print("[WARNING] LAUNCHER_AGENT_TOKEN not set. Agent API is unauthenticated.")
```

### Alternative: APIKeyHeader (simpler, FastAPI-native)

```python
from fastapi.security import APIKeyHeader

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(
    request: Request,
    key: str | None = Depends(api_key_header),
):
    expected = os.environ.get("LAUNCHER_AGENT_TOKEN")
    if expected and key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return request
```

---

## Related Issues from Code Review (2026-04-25)

| ID | Finding |
|----|---------|
| W12 | OpenAPI docs (`/docs`, `/redoc`) exposed on `0.0.0.0` — should also be gated behind auth or disabled when token is set |
| W8  | Bare `except Exception` in remote node code — same hardening mindset applies |

---

## Acceptance Criteria

- [ ] `LAUNCHER_AGENT_TOKEN` env var is recognized and enforced
- [ ] Requests without valid token receive `403 Forbidden`
- [ ] All agent endpoints are covered (start, stop, swap, logs, status, models)
- [ ] When `LAUNCHER_AGENT_TOKEN` is not set, a warning logs at startup but behavior is unchanged
- [ ] Documentation updated (`README.md`, `docs/`) to describe the auth mechanism
