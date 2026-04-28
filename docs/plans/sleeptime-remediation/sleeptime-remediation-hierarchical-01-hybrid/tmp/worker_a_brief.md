## Worker Brief: ADR-003 Agent API Authentication

Implement ALL tasks from Phase 1 of `/tmp/llauncher_implementation_plan.md`. These are the specific tasks you must complete:

### Implementation Tasks (Code)
| # | Type | File Path | Description |
|---|------|-----------|-------------|
| 1.1 | impl | `llauncher/core/settings.py` | Add `AGENT_API_KEY: str | None = os.getenv("LAUNCHER_AGENT_TOKEN")`. Validate non-empty if set. Log warning on module load when unset and host will bind 0.0.0.0 (check DEFAULT_HOST setting). |
| 1.2 | impl | `llauncher/agent/middleware.py` (NEW) | Create `AuthenticationMiddleware` extending FastAPI's `BaseHTTPMiddleware`. Override dispatch to check X-Api-Key header against AGENT_API_KEY settings value. Return 401 if header missing, 403 if wrong. Skip auth for /health, /docs, /openapi.json paths when token is configured. |
| 1.4 | impl | `llauncher/agent/server.py` | Wire middleware into FastAPI app in create_app(). If AGENT_API_KEY set → add_middleware(AuthenticationMiddleware). Conditionally disable OpenAPI docs: pass docs_url=None, redoc_url=None to FastAPI() constructor when token is configured. Log startup message accordingly. |
| 1.8 | impl | `llauncher/remote/node.py` | Add optional api_key field to RemoteNode data class/model. Modify HTTP request methods (ping, get_status) to include X-Api-Key header if api_key is set on the node. |
| 1.9 | impl | `llauncher/remote/registry.py` | Extend NodeRegistry.add_node() with optional api_key parameter. Persist key in nodes.json. Update any existing UI helpers that create nodes. |
| 1.11 | impl | `llauncher/agent/server.py` | Add startup logging: if token unset AND host is "0.0.0.0" → WARNING log. If set → INFO log that auth is active with bind address. |

### Test Tasks (Unit Tests)
| # | Type | File Path | Description |
|---|------|-----------|-------------|
| 1.2a | test | `tests/unit/test_core_settings_auth.py` (NEW) | test_default_api_key_is_none, test_api_key_from_env, test_empty_token_rejected |
| 1.2b | test | `tests/unit/test_agent_middleware.py` (NEW) | test_no_token_allows_all_requests, test_with_token_rejects_unauthenticated_returns_401, test_with_token_accepts_valid_key, test_with_token_rejects_wrong_key_returns_403, test_openapi_docs_excluded_from_auth |
| 1.2c | test | `tests/unit/test_remote_node_auth.py` (NEW) | test_node_with_api_key_includes_header, test_node_without_api_key_no_extra_headers, test_node_empty_api_key_treated_as_none |

### Implementation Details from Plan

**Settings addition** (`core/settings.py`):
```python
AGENT_API_KEY: str | None = os.getenv("LAUNCHER_AGENT_TOKEN")
# If set but empty string, normalize to None
if AGENT_API_KEY == "":
    AGENT_API_KEY = None
```

**Middleware signature** (`agent/middleware.py`):
```python
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, Response

class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expected_token: str):
        super().__init__(app)
        self.expected_token = expected_token
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        
        api_key = request.headers.get("X-Api-Key")
        if not api_key or api_key != self.expected_token:
            status_code = 401 if not api_key else 403
            return JSONResponse(status_code=status_code, content={"detail": "Authentication required"})
        
        response = await call_next(request)
        return response
```

**FastAPI wiring** (`agent/server.py`): 
In create_app(), read AGENT_API_KEY from settings. If set:
- docs_url=None, redoc_url=None in FastAPI() constructor
- add_middleware(AuthenticationMiddleware, expected_token=AGENT_API_KEY)
- Log startup message

### Git Commit Message
```
feat(agent): add API key authentication middleware

- Add AGENT_API_KEY env var to core settings (LAUNCHER_AGENT_TOKEN)
- Implement AuthenticationMiddleware with X-Api-Key header check
- Wire middleware into FastAPI app in agent/server.py
- Use 401 for missing key, 403 for wrong key, skip auth for /health and docs
- Disable OpenAPI docs (/docs, /redoc) when auth is active
- Extend RemoteNode to carry api_key for authenticated node pings
- Update NodeRegistry.add_node() with optional api_key parameter
- Add unit tests for middleware, settings, and remote auth flow

Refs: ADR-003
```

Read the full plan at `/tmp/llauncher_implementation_plan.md` for any additional context. Execute all tasks above, then commit and push to origin/main.
