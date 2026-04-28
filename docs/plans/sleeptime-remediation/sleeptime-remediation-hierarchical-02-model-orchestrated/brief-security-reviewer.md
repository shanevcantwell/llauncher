# Brief: Security Hardening (Worker)

**Source Review:** plan-sleeptime-remediation-00-review-opus-4.7-complete-review.md, security-reviewer agent output  
**Related Coordinator:** brief-python-reviewer.md (auth key redaction C1 shared)  
**Scope Limitation:** Only modify files under `/home/node/github/llauncher/`. No filesystem edits outside the codebase.

---

## Objective

Fix all CRITICAL and HIGH security findings from the security audit of ADR-003 authentication implementation. This brief is **tighter in scope than the python-reviewer brief** — it focuses exclusively on auth middleware, API key handling, startup warnings, OpenAPI exposure, and file permissions for credential storage. Non-authentication Python quality issues (thread safety, error handling patterns, etc.) are handled by the python-reviewer brief to avoid dual-modification conflicts.

---

## Coordination Notes with Other Briefs

| Security Issue | Coordinated With | Responsibility |
|----------------|------------------|----------------|
| B1: Timing attack (`hmac.compare_digest`) | Python Reviewer → **Security owns this fix exclusively** | This brief implements the entire change; python-reviewer removes any conflicting `!=` patterns in middleware as a cleanup pass after this is done |
| C1 (B2/B5): API key plaintext/redaction + chmod 0o600 | Python Reviewer — **CONSOLIDATED: Security owns this entire atomic change** | Key redaction contract (`has_api_key`: bool) AND file permissions (chmod 0o600 in `_save()`). Not split between briefs. python-reviewer brief updated to NOT touch `to_dict()`. |
| B4: OpenAPI schema exposure | Independent — **Security owns** | Single-file change to server.py + middleware exempt path removal |
| H5: Startup warning gap | Independent — **Security owns** | Modify `server.py` startup logging |

---

## Fixed Issue List

### CRITICAL — Timing Attack (B1)

**File:** `agent/middleware.py`, line 54  
**Current code:** `if api_key is None or api_key != self.expected_token:`  
**Fix:**
```python
import hmac  # add to module imports

# In dispatch(), replace:
if api_key is None or not hmac.compare_digest(api_key, self.expected_token):
    return JSONResponse(status_code=401, content={"detail": "Missing or invalid API key"})
elif self.expected_token is not None:
    return JSONResponse(status_code=403, content={"detail": "Invalid API key"})
```

**Verification:** The existing test suite (`test_agent_middleware.py`) uses `hmac.compare_digest` assertions internally — those tests must continue to pass. Additionally add one new test that validates the constant-time path is actually taken (import-level smoke test).

### HIGH — OpenAPI Schema Leakage (B4)

**Files:** `agent/server.py` lines 128-129; `agent/middleware.py` line 13  
**Current code in server.py:**
```python
docs_url=None if auth_active else "/docs",
redoc_url=None if auth_active else "/redoc",
# openapi_url is NOT set — defaults to "/openapi.json"
```

**Fix:** Add `openapi_url` suppression when auth is active:
```python
app = FastAPI(
    ...
    docs_url=None if auth_active else "/docs",
    redoc_url=None if auth_active else "/redoc",
    openapi_url=None if auth_active else "/openapi.json",  # ADD THIS LINE
)
```

Then remove `/openapi.json` from `_AUTH_EXEMPT_PATHS` in `middleware.py`. The route will no longer exist when auth is active, so the exemption becomes moot.

**Verification:** Start server with LAUNCHER_AGENT_TOKEN set → GET /openapi.json should return 404 (route doesn't exist), not 200 with full schema. TestClient tests that assert `/openapi.json` is exempt must be removed or updated to reflect new behavior.

### HIGH — nodes.json File Permissions + Plaintext Keys (B2)

**Files:** `remote/registry.py` lines 56-63; `remote/node.py` line 280  
**Current code in registry.py _save():**
```python
NODES_FILE = Path(registry_path).expanduser()
NODES_FILE.write_text(json.dumps(data, indent=2))
# No chmod — defaults to process umask (typically 664)
```

**Fix:**
```python
NODES_FILE.write_text(json.dumps(data, indent=2), mode="w")
NODES_FILE.chmod(0o600)  # owner-read/write only — no group or world access
```

Additionally emit a startup warning when the first api_key is persisted (see logging below).

**Note:** Per strategic-planner finding #6, C1 key redaction + chmod 0o600 are consolidated into this brief exclusively. The python-reviewer brief does NOT touch `to_dict()` or `_save()`. Single atomic change.

### HIGH — Startup Warning Gap (H5)

**File:** `agent/server.py`, lines 168-173  
**Current behavior:** Warning only fires when `host == "0.0.0.0"`. Binding to any specific interface (e.g., `192.168.1.50:8765`) silently allows all traffic without logging anything.

**Fix:** Always emit a startup warning when `AGENT_API_KEY` is None, regardless of bind address:
```python
if not api_key:
    if host == "0.0.0.0":
        logger.warning("CRITICAL: No API key configured and bound to 0.0.0.0 — all network interfaces are accessible without authentication")
    else:
        logger.warning("WARNING: No API key configured — server is accessible on %s without authentication", host)
```

**Verification:** Start server with no LAUNCHER_AGENT_TOKEN → verify warning appears in logs for both 0.0.0.0 and specific-IP bindings.

---

## MEDIUM Findings (Acceptable as Known Issues, Noted for ADR Update)

### M4 — ADR Documentation Correction

**File:** `docs/adrs/003-agent-api-authentication.md`, lines 38-43  
**Issue:** ADR claims `/status` and `/models` are unauthenticated. Implementation protects them (with only `/health`, `/docs`, `/redoc` exempted). ADR is wrong but implementation is *more* secure than documented — this is a documentation-only fix, not a code change.

**Action for ADR Brief:** This will be addressed in the ADR restructure brief — update ADR-003 to accurately reflect which paths are authenticated and which are exempt. The current state (protecting everything except explicit exemptions) should become the documented standard going forward.

---

## Verification Requirements

1. All 7 auth-related tests (`test_agent_middleware.py`, `test_core_settings_auth.py`, `test_remote_node_auth.py`) must pass — verify with: `pytest tests/unit/test_agent_middleware.py -v` and `pytest tests/unit/test_core_settings_auth.py -v` and `pytest tests/unit/test_remote_node_auth.py -v`
2. Timing comparison must use `hmac.compare_digest` — verify with grep for zero remaining naive comparisons: `grep -n '!= self.expected_token\|== self.expected_token' agent/middleware.py` should return empty
3. OpenAPI schema route returns 404 when auth is active (TestClient verification)
4. Verify file permissions: after adding a node with api_key, `os.stat(nodes.json).st_mode & 0o777 == 0o600`
5. `node.to_dict()` output contains no raw API key string — verify via: inspect dict output in any test or CLI call
6. Startup warning fires for both 0.0.0.0 and specific-IP bindings when LAUNCHER_AGENT_TOKEN is unset

---

## Acceptance Criteria (Issue-Tag Checklist per Strategic-Planner Finding #11/#12)

### Specific checks for each issue tag:
| Tag | Verification Method |
|-----|--------------------|
| B1 | `grep -n 'hmac.compare_digest' agent/middleware.py` — present. `grep -c '!= self.expected_token\|== self.expected_token' agent/middleware.py` — zero results expected (all replaced) |
| C1/B2+B5 | `grep -A3 '_save' remote/registry.py` — must contain `.chmod(0o600)`. `grep '"api_key"' remote/node.py remote/registry.py` — zero raw api_key serialization in output dicts |
| B4 | Start server with LAUNCHER_AGENT_TOKEN set → curl localhost:8765/openapi.json returns 404. Grep for openapi_url=None in server.py when auth_active is True |
| H5 | Start server without token bound to specific IP (e.g., 127.0.0.1) — verify startup logs contain WARNING about missing auth |

### Post-change verification:
- Run full auth test suite: `pytest tests/unit/test_agent_middleware.py -v && pytest tests/unit/test_core_settings_auth.py -v && pytest tests/unit/test_remote_node_auth.py -v` — all 7+ tests must pass with visible output showing each test name
- No remaining naive string comparisons on tokens anywhere in codebase:
  `grep -rn '!= self.expected_token\|== self.expected_token' agent/` → zero results expected
- Changes are atomic: one commit for B1 (timing), one for C1/B4 (OpenAPI + redaction + chmod)
