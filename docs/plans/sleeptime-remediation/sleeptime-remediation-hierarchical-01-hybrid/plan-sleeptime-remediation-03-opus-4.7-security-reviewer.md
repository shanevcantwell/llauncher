# Plan: Opus 4.7 Security Review Remediation — ADR-003 Findings

**Plan ID:** `PLAN-ADR003-REM-001`  
**Author:** Strategic Planner (op. 4.7 security remediation)  
**Date:** 2026-04-26  
**Related Audit:** Opus 4.7 Automated Security Review of ADR-003  
**Status:** Awaiting Implementer handoff

---

## 1. Executive Architecture Summary

Replace the naive timing-vulnerable API key comparison with constant-time `hmac.compare_digest`, suppress `/openapi.json` exposure when auth is active, harden `nodes.json` file permissions to `0o600`, mask plaintext keys in serialized output (`to_dict()`), and correct the ADR documentation — four-source-file remediation that eliminates a CRITICAL timing oracle, two HIGH information disclosure vectors (OpenAPI schema leak + plaintext key exposure on disk and via serialization), one MEDIUM-HIGH warning suppression bug, and one MEDIUM documentation drift.

---

## 2. Finding-by-Finding Analysis & Fix Design

### Finding #1 — CRITICAL: Timing Oracle in Token Comparison

| Attribute | Detail |
|-----------|--------|
| **File** | `llauncher/agent/middleware.py` |
| **Line(s)** | 54 |
| **Root Cause** | Python's `!=` string comparison short-circuits at the first differing byte. An attacker measuring response latency across many requests can statistically deduce the API key character-by-character (side-channel timing attack). OWASP explicitly flags this for secret comparisons. |

#### Chosen Fix

Replace the `!=` operator with `hmac.compare_digest()`, which performs a constant-time comparison that eliminates the timing oracle. The function already handles unequal-length inputs defensively (short-circuits on length mismatch per CPython implementation, but this is documented as acceptable by OWASP since key lengths are expected to be comparable).

**Code change — `middleware.py` lines 49–57:**

```python
import hmac  # ← Add import at top of file (line ~3 area)

# Inside dispatch():
if api_key is None:
    status_code = 401
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Authentication required"},
    )

# Now api_key is guaranteed non-None — safe to pass to compare_digest
if not hmac.compare_digest(api_key, self.expected_token):
    # api_key was present but wrong → 403 (credentials provided, access denied)
    status_code = 403
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Authentication required"},
    )

response = await call_next(request)
return response
```

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Hash the key (SHA-256) before comparing | Adds unnecessary crypto overhead; `compare_digest` is purpose-built for this. |
| Add artificial delay to equal-length wrong keys | Masking technique that can be bypassed with statistical analysis over many requests; also introduces DoS risk via slow responses. |

#### Test Impact

- **No existing test breaks.** All existing tests check status codes (401/403) and response bodies, not timing behavior.
- **New test needed:** `test_compare_digest_used_not_equality` — a unit test that mocks the middleware dispatch and verifies `hmac.compare_digest` is called with correct arguments. We don't need to prove constant-time behavior (that's the library's contract), only confirm we're using the right API.

---

### Finding #2 — HIGH → MEDIUM-HIGH: Missing-Token Warning Silently Suppressed on Non-0.0.0.0 Bind

| Attribute | Detail |
|-----------|--------|
| **File** | `llauncher/agent/server.py` |
| **Line(s)** | 168–173 (warning block at end of `run_agent()`) |
| **Root Cause** | The warning `"Agent is binding to 0.0.0.0"` fires only when both `AGENT_API_KEY is None AND config.host == "0.0.0.0"`. When auth is absent but the admin explicitly binds to, e.g., `127.0.0.1` or a specific NIC IP, no warning fires — the admin may not realize auth is missing until it's too late. |

#### Chosen Fix

Unconditionally warn on startup when auth is disabled:
- **WARNING** log whenever `AGENT_API_KEY is None` (regardless of bind address).
- Elevate to **CRITICAL** if bound to `0.0.0.0`.

**Code change — `server.py` end of `run_agent()`:**

```python
# Remove the old conditional block (lines ~168-173):
#     if AGENT_API_KEY is None and config.host == "0.0.0.0":
#         logger.warning(...)

# Replace with:
if AGENT_API_KEY is None:
    # Auth is disabled — always warn
    if config.host == "0.0.0.0":
        logger.critical(
            "⚠️  CRITICAL: Authentication is DISABLED and agent binds to 0.0.0.0 "
            "(all interfaces). This exposes all endpoints to any network peer."
        )
    else:
        logger.warning(
            "⚠️  WARNING: Authentication is DISABLED (LAUNCHER_AGENT_TOKEN not set). "
            f"Agent binds to {config.host}:{config.port}. "
            "Set LAUNCHER_AGENT_TOKEN to enable API key authentication."
        )
```

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Fatal exit when auth is disabled on network interfaces | Breaking change — existing unauthenticated deployments would fail to start. opt-in auth model requires backward compatibility. |
| Log-level configurable (INFO → DEBUG) | Would allow admins to suppress the warning entirely; a missing security control should always be visible at WARNING or above by default. |

#### Test Impact

- **No existing test breaks.** The current warning block is not exercised in any unit/integration test.
- **New test needed:** `test_auth_disabled_warning_on_startup` — patch `logging.warning` and `logging.critical`, start the agent (mocked uvicorn), verify correct log level fires based on bind address.

---

### Finding #3 — HIGH: OpenAPI Schema Leak via Exempt `/openapi.json` Endpoint

| Attribute | Detail |
|-----------|--------|
| **File(s)** | `llauncher/agent/server.py` (lines ~97–107), `llauncher/agent/middleware.py` (line 4) |
| **Root Cause** | The FastAPI constructor disables `/docs` and `/redoc` when auth is active, but **does not disable `/openapi.json`**. The OpenAPI schema endpoint remains at its default path and serves the full route specification (including all write endpoints like `/start/{model}`, `/stop/{port}`) to unauthenticated callers. The middleware exempts `/openapi.json` from authentication, making this a trivially exploitable information disclosure — an attacker maps the entire API surface before attempting brute-force or fuzzing attacks. |

#### Chosen Fix

1. **In `create_app()` (server.py):** Pass `openapi_url=None if auth_active else "/openapi.json"` to the FastAPI constructor alongside `docs_url` and `redoc_url`. This suppresses the endpoint generation entirely rather than relying on middleware-level gating.
2. **In `middleware.py`:** Remove `/openapi.json` from `_AUTH_EXEMPT_PATHS` since the route no longer exists when auth is active — keeping it as a no-op exemption is confusing and wasteful.

**Code change — `server.py`, FastAPI constructor (~line 102):**

```python
app = FastAPI(
    title="llauncher Agent",
    description="Remote management agent for llauncher nodes",
    version=__version__,
    docs_url=None if auth_active else "/docs",
    redoc_url=None if auth_active else "/redoc",
    openapi_url=None if auth_active else "/openapi.json",  # ← ADD THIS LINE
)
```

**Code change — `middleware.py`, line 4:**

```python
# Before:
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})

# After (when auth_active): only serve exempt paths when they actually exist:
# We keep all four entries for the no-auth case, but remove /openapi.json since
# it's handled by FastAPI constructor. Simpler approach: just list what stays:
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc"})

# BUT — we need a conditional exemption set because when auth is inactive,
# all docs endpoints exist; when auth is active, only /health exists.
# Cleanest approach: make _AUTH_EXEMPT_PATHS dynamic or just exempt /health always
# and let docs/redoc be handled by FastAPI's own routing (they won't match if disabled).

# Recommended simplification — since /docs and /redoc are both disabled when auth_active,
# their paths will never match any incoming request. Keeping them in the set is harmless:
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/openapi.json"})

# Actually, simplest correct approach (per task constraint): just remove openapi.json
# and keep /docs and /redoc as they are no-ops when disabled by FastAPI:
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc"})
```

**Rationale for keeping `/docs` and `/redoc` in the exemption set:** When `auth_active`, these routes don't exist (FastAPI won't register them), so no incoming request will ever match `path in _AUTH_EXEMPT_PATHS` for these paths — they are harmless dead entries. Removing them would require passing `auth_active` to the middleware, which adds coupling complexity not worth it here.

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Middleware-level auth guard on `/openapi.json` in addition to FastAPI suppression | Defense-in-depth is good but redundant — if both are present and diverge, behavior becomes undefined. One source of truth (FastAPI constructor) is cleaner. |
| Return 401 from an OpenAPI endpoint handler that checks auth | Adds a route when the intent is "no route." More code path, more surface area for bugs. FastAPI's native `openapi_url=None` parameter is zero-cost and atomic. |

#### Test Impact

- **`test_openapi_docs_excluded_from_auth` (tests/unit/test_agent_middleware.py:68):** This test asserts that `/openapi.json` returns 200 without auth when token is configured. **This will fail.** Update to assert either:
  - The endpoint is excluded from the exempt paths list, OR
  - When auth IS active and openapi_url=None, the path doesn't exist (404) — but since the route no longer exists, we should test the exemption set doesn't contain it.
- **`test_no_token_allows_all_requests`:** Still passes — when `token=None`, FastAPI constructor includes `/openapi.json` and middleware exempts it. No change in behavior for unauthenticated mode.

---

### Finding #4 — HIGH: Plaintext API Keys on Disk with World-Readable Permissions

| Attribute | Detail |
|-----------|--------|
| **File** | `llauncher/remote/registry.py` |
| **Line(s)** | `_save()` method (lines ~63–71) |
| **Root Cause** | `NODES_FILE.write_text(json.dumps(data, indent=2))` creates a file with the OS default umask-derived permissions (typically 0o644 or 0o664). This means other users on the same system can read `nodes.json` and extract plaintext API keys. Combined with Finding #5 (`to_dict()` serialization leak), keys exist in at least three places: disk file, in-memory dicts via HTTP responses/debug logs, and node registration payloads. |

#### Chosen Fix

1. **In `_save()`:** After writing the JSON data, call `NODES_FILE.chmod(0o600)` to restrict read/write to owner only.
2. **Guard against partial writes:** Wrap file operations in try/except — if chmod fails (e.g., stale symlink), log a warning but do not crash the registry.

**Code change — `registry.py`, `_save()` method:**

```python
import os  # ← Add import at top of file (alongside json and pathlib)

def _save(self) -> None:
    """Save nodes to the persistent file."""
    NODES_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    for name, node in self._nodes.items():
        data[name] = {
            "name": node.name,
            "host": node.host,
            "port": node.port,
            "timeout": node.timeout,
            "api_key": node.api_key,  # plaintext — intentional but permission-hardened
        }

    try:
        NODES_FILE.write_text(json.dumps(data, indent=2))
        os.chmod(NODES_FILE, 0o600)  # ← ADD THIS LINE
    except OSError as e:
        logger.warning(  # Add import for logging at module level
            "Failed to set secure permissions on %s: %s", NODES_FILE, e
        )
```

#### Startup Warning in `server.py`

Additionally emit a one-time startup warning when API keys are loaded from disk:

**Code change — end of `run_agent()`:**

```python
# After the auth-disabled warnings (above), add:
if AGENT_API_KEY is not None:
    # Check if nodes.json has insecure permissions
    try:
        node_perms = oct(NODES_FILE.stat().st_mode & 0o777)
        if int(node_perms, 8) != 0o600:
            logger.warning(
                "Node registry file %s has permissive permissions (%s). "
                "API keys stored there are readable by other users. Consider setting umask 077.",
                NODES_FILE, node_perms,
            )
    except OSError:
        pass  # File doesn't exist yet — not a problem at first startup
```

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Encrypt keys with OS keyring (keychain/pyca/cryptography) | Significant dependency and UX burden. On systems without a GUI desktop or running in containers, keyring backends are often unavailable. Permission hardening is the pragmatic first step; encryption can be Phase 2. |
| Separate encrypted key store from node metadata | Adds complexity for file format migration, dual-path loading/saving logic. Permission-only approach is simpler to implement and review now. |

#### Test Impact

- **`test_node_add_with_api_key` (tests/unit/test_cli.py ~line 248):** Asserts `node_data["api_key"] == "secret-token-xyz"` from the JSON file content. This still passes — we don't encrypt, just chmod.
- **New test needed:** `test_registry_file_permissions` — create a node via registry, verify the file mode is exactly 0o600 using `os.stat(file).st_mode & 0o777`.

---

### Finding #5 — MEDIUM: RemoteNode.to_dict() Leaks Raw API Key

| Attribute | Detail |
|-----------|--------|
| **File** | `llauncher/remote/node.py` |
| **Line(s)** | `to_dict()` method (lines ~276–284) |
| **Root Cause** | `RemoteNode.to_dict()` returns `"api_key": self.api_key` verbatim. Any consumer of this dict — HTTP responses, debug log serialization, state snapshots via `RemoteState.get_snapshot()`, or CLI JSON output — receives the plaintext key. The ADR design explicitly states keys should not appear in display/serialization paths where they are needed for authentication only. |

#### Chosen Fix

Replace `"api_key": self.api_key` with `"has_api_key": self.api_key is not None`. This preserves the structural contract (a boolean presence indicator) without leaking the secret value.

**Code change — `node.py`, `to_dict()` method:**

```python
def to_dict(self) -> dict:
    """Convert node info to dictionary."""
    return {
        "name": self.name,
        "host": self.host,
        "port": self.port,
        "timeout": self.timeout,
        "has_api_key": self.api_key is not None,  # ← CHANGE from: "api_key": self.api_key
        "status": self.status.value,
        "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        "error_message": self._error_message,
    }
```

#### Impact Audit — Callers of `to_dict()` on RemoteNode

| Consumer | File:Line(s) | Breakage Risk | Action Required |
|----------|-------------|---------------|-----------------|
| **`RemoteState.get_snapshot()`** | `remote/state.py:208` | LOW — returns dict to external consumer; `"has_api_key"` replaces `"api_key"`. Verify no downstream JSON schema depends on key name. | Update any code expecting `node_info["api_key"]` to use `node_info["has_api_key"]` instead. |
| **CLI node list (JSON mode)** | `cli.py:290-305` | NO BREAKAGE — CLI already uses manual dict construction with `"has_api_key": bool(node.api_key)`, not `to_dict()`. Confirmed compatible. | No action. |
| **Test `test_to_dict`** | `tests/unit/test_remote.py:56-71` | LOW — test checks `"name"`, `"host"`, `"port"`, `"status"`, `"last_seen"` but does NOT check for `"api_key"` key existence or value. If a separate test creates node WITH api_key and asserts on dict keys, that would break. | Verify all `to_dict()`-asserting tests pass after change. Check if any test has bare `assert "api_key" in data`. |
| **Test `test_registry_extended.py`** | Line 302 | LOW — checks `"name"`, `"host"`, `"port"` only. `"has_api_key"` is a new key, not checked for absence. Passes unchanged. | No action. |

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Mask as `"api_key": "•••••"` string | Still implies the key has content; `"has_api_key": boolean` is semantically clearer for display-only paths. Also avoids leaking information about key length via mask padding. |
| Remove api_key from to_dict entirely (no presence flag) | Consumer code might want to show "✅ authenticated" vs "❌ no credentials configured." Boolean preserves UX signal without the secret value. |

#### Test Impact

- **`test_to_dict` in `tests/unit/test_remote.py:56`:** Node created WITHOUT api_key, so dict will have `"has_api_key": False` instead of `"api_key": None`. Tests check individual keys that are unaffected. If any test does a bare assertion like `assert "api_key" not in data or data["api_key"] is None`, it passes either way. **Likely no breakage.**
- **Integration snapshot tests:** Verify `get_snapshot()` output doesn't contain `"api_key"` key with secret value anywhere in test assertions.

---

### Finding #6 — MEDIUM: ADR Documentation Misrepresents Auth Exemption Scope

| Attribute | Detail |
|-----------|--------|
| **File** | `docs/adrs/003-agent-api-authentication.md` |
| **Lines** | 38–43 (Decision section) vs. `middleware.py:13,49` (implementation) |
| **Root Cause** | ADR-003 states: *"FastAPI middleware checks X-Api-Key header on all `/start`, `/stop`, `/swap`, `/nodes/` endpoints (**read-only endpoints like `/status`, `/health`, `/models` remain unauthenticated**)".* The implementation is stricter than documented: ALL non-exempt paths require auth. Only hardcoded paths (`/health`, `/docs`, `/redoc`, and formerly `/openapi.json`) are exempt — not the read/write distinction described in the ADR. |

#### Chosen Fix

Update the ADR to accurately reflect implementation behavior. This is a documentation-only change that brings specs into alignment with code (a positive security posture: stricter than documented = safe by default).

**Required edits in `docs/adrs/003-agent-api-authentication.md`:**

1. **Decision section, paragraph 3:** Change the bullet from:
   > *"FastAPI middleware checks X-Api-Key header on all `/start`, `/stop`, `/swap`, `/nodes/` endpoints (read-only endpoints like /status, /health, /models remain unauthenticated)"*

   To:
   > *"FastAPI middleware enforces API key authentication on **all request paths** by default. Only the following hard-coded exempt paths are accessible without credentials:"*

2. **Add an exemption table:**
   ```markdown
   | Path | Auth Required? | Rationale |
   |------|---------------|-----------|
   | `/health` | No | Liveness probe for orchestrators (k8s, systemd) must work without auth |
   | `/docs` | Conditional (no auth when `LAUNCHER_AGENT_TOKEN` unset; suppressed when set) | OpenAPI docs — hidden by default in production |
   | `/redoc` | Conditional (same as /docs) | Alternative documentation viewer |
   | `/openapi.json` | Conditional (same as /docs) | Raw schema endpoint, suppressed in production |
   | **All other paths** | **Yes** | Includes `/status`, `/models`, `/node-info`, `/start/*`, `/stop/*`, `/logs/*` |
   ```

3. **Consequences section:** Update the "Negative" bullet about session management to note that the implementation is stricter than originally scoped (all paths auth-gated, not just write endpoints). This should be listed as a positive finding — it aligns with defense-in-depth principles but may surprise users who expected read-only endpoints to be free.

#### Tradeoff Discussion

| Alternative | Rejected Because |
|-------------|------------------|
| Update code to match the ADR (make /status, /models exempt) | Would weaken security posture for no operational benefit — health endpoint exemption is for orchestrators; status/models don't serve that purpose and should require auth. The implementation is correct as-is. |
| Keep ADR as-is since "implementation wins" over docs | Violates the principle that architecture documentation must accurately describe reality. Misleading specs lead to incorrect assumptions during incident response, compliance audits, and onboarding. |

#### Test Impact

- **No code test changes needed.** Documentation-only edit.
- **New ADR verification note:** When reviewing merged PRs, audit agents should verify the documented exemption table matches `_AUTH_EXEMPT_PATHS` in middleware.py.

---

### Finding #7 — LOW: CSRF/Replay Dismissed as Not Material Risk

**Status: No action required.** Bearer token over HTTP without browser context correctly dismissed. The API key authentication model is inherently incompatible with CSRF (which targets session-cookie-based auth) and replay attacks on localhost-bound services are out of scope for the current threat model.

### Finding #8 — LOW: No Hardcoded Defaults, No Localhost Bypass Confirmed Clean

**Status: Verified clean.** No hardcoded keys found in source. No `127.0.0.1` bypass logic present. The middleware exempts paths by exact path match only (no prefix matching that could be exploited via path traversal).

---

## 3. Implementation Phases

Phases are ordered to minimize risk: each phase produces independently verifiable fixes, and phases build on prior changes without creating circular dependencies.

### Phase 1: CRITICAL — Timing Attack Fix + OpenAPI Suppression (Parallel-Safe)

**Files:** `middleware.py`, `server.py`  
**Can proceed in parallel with Phase 2.**

| Step | Action | File | Expected Diff Lines |
|------|--------|------|-------------------|
| 1.1 | Add `import hmac` to middleware.py header | `llauncher/agent/middleware.py` | +1 line |
| 1.2 | Refactor auth check: extract `None` guard → `hmac.compare_digest()` | `llauncher/agent/middleware.py` | ~9 lines → 13 lines |
| 1.3 | Add `openapi_url=None if auth_active else "/openapi.json"` to FastAPI constructor | `llauncher/agent/server.py` | +1 line (around line 102) |
| 1.4 | Remove `/openapi.json` from `_AUTH_EXEMPT_PATHS` in middleware.py | `llauncher/agent/middleware.py` | −1 item from frozenset |
| 1.5 | Update `test_openapi_docs_excluded_from_auth`: assert `/openapi.json` NOT in exempt paths when auth active, or verify no-op exemption set check | `tests/unit/test_agent_middleware.py:68-75` | Rewrite test assertion block (~5 lines) |
| 1.6 | Add new test: `test_hmac_compare_digest_used` verifying middleware dispatch uses `hmac.compare_digest()` | `tests/unit/test_agent_middleware.py` (+new function) | ~10 lines |

**Verification:** Run `pytest tests/unit/test_agent_middleware.py -v`. All 8 existing tests should pass (7 existing + potentially modified test). The test behavior is: when auth active, `/health`, `/docs`, `/redoc` → 200; `/protected` without key → 401; with correct key → 200. With openapi_url=None, the `/openapi.json` route no longer exists (404 from FastAPI itself), but the exemption set test confirms it's removed.

### Phase 2: HIGH — File Permission Hardening + Startup Warning (Depends on None of Above)

**Files:** `registry.py`, `server.py`  
**Can proceed in parallel with Phase 1.**

| Step | Action | File | Expected Diff Lines |
|------|--------|------|-------------------|
| 2.1 | Add `import os` (and add logging import if not present) to registry.py header | `llauncher/remote/registry.py` | +1–2 lines |
| 2.2 | Add `os.chmod(NODES_FILE, 0o600)` after write_text in `_save()` with try/except guard | `llauncher/remote/registry.py:_save()` | +3 lines (inside existing method) |
| 2.3 | Add startup warning in `server.py::run_agent()`: warn about plaintext keys when auth active and registry file has non-0o600 permissions | `llauncher/agent/server.py:run_agent()` | +12–15 lines (new block before uvicorn.run) |
| 2.4 | Add test for file permissions in registry | New or append to `tests/unit/test_registry_extended.py` | ~8 lines |

**Verification:** 
- Create a node via CLI → verify file mode is 0o600: `stat -c '%a' ~/.llauncher/nodes.json`
- Remove the token, start agent on non-0.0.0.0 — verify WARNING still fires in logs.

### Phase 3: MEDIUM-HIGH — Startup Warning Unconditional (Depends on Phase 1 for context)

**File:** `server.py`  
**Should follow Phase 2 since both modify `run_agent()`.**

| Step | Action | File | Expected Diff Lines |
|------|--------|------|-------------------|
| 3.1 | Replace conditional warning block with unconditional auth-disabled warning + CRITICAL for 0.0.0.0 bind | `llauncher/agent/server.py:run_agent()` (end, before uvicorn.run) | ~6 lines → 12 lines |

**Verification:** Manual smoke test — start agent without token on various bind addresses and verify correct log level output. Unit test via patching `logging.warning` and `logging.critical`.

### Phase 4: MEDIUM — Key Masking in to_dict() (Depends on None)

**File:** `node.py`  
**Can proceed in parallel with all above phases.**

| Step | Action | File | Expected Diff Lines |
|------|--------|------|-------------------|
| 4.1 | Change `"api_key": self.api_key` → `"has_api_key": self.api_key is not None` | `llauncher/remote/node.py:to_dict()` | 1 line changed, same length |
| 4.2 | Audit all callers for breakage (manual check against impact matrix in §2) — verified clean above | Multiple files | 0 code changes needed (CLI already compatible; state.py snapshot consumers accept new key name) |
| 4.3 | Add/verify test: `test_to_dict_masks_api_key` — create node with api_key, verify dict contains `"has_api_key": True` but NOT `"api_key"` | New test in `tests/unit/test_remote.py` or new file | ~8 lines |

### Phase 5: MEDIUM — ADR Documentation Correction (No Dependencies)

**File:** `docs/adrs/003-agent-api-authentication.md`  
**Can proceed at any time, preferably merged with the code fix PR.**

| Step | Action | File |
|------|--------|------|
| 5.1 | Replace read-only/write-endpoint language in Decision section with accurate "all paths require auth except listed" model | `docs/adrs/003-agent-api-authentication.md` |
| 5.2 | Add exemption table (health, docs, redoc, openapi.json conditional, all others required) | Same file |
| 5.3 | Update Consequences section to note stricter-than-documented posture as positive finding | Same file |

---

## 4. Test Impact Assessment

### Tests Expected to FAIL Without Modification

| Test File | Test Name | Line(s) | Reason for Failure | Required Fix |
|-----------|-----------|---------|-------------------|-------------|
| `tests/unit/test_agent_middleware.py` | `test_openapi_docs_excluded_from_auth` | 68–75 | Asserts `/openapi.json` returns 200 without auth when token is configured. After Phase 1, the endpoint no longer exists (suppressed by FastAPI constructor). | Rewrite to assert that `/openapi.json` is **not** in `_AUTH_EXEMPT_PATHS` and/or that it returns 404 when auth active. Keep test for `/health`, `/docs`, `/redoc` still returning 200. |
| `tests/unit/test_agent_middleware.py` | `test_no_token_allows_all_requests` | ~39–41 | Should **still pass** — when no token, FastAPI includes `/openapi.json`. Verified compatible. | None. |

### Tests Expected to PASS Unchanged

| Test File | Test Name | Rationale |
|-----------|-----------|----------|
| `tests/unit/test_agent_middleware.py` | All 6 other tests | They exercise status codes (401/403), exempt path access, and empty key handling — all behavior-preserving changes. |
| `tests/integration/test_adr_cross_cutting.py` | All auth + health combined tests | They use the `/health` endpoint and custom routes; neither is affected by our changes. |
| `tests/unit/test_remote_node_auth.py` | All 3 tests | Test `_get_headers()` behavior, not `to_dict()`. Compatible. |
| `tests/unit/test_cli.py` (line 248) | Node persists api_key in nodes.json | Tests plaintext persistence in JSON file — unaffected by `to_dict()` change or chmod fix. |
| `tests/unit/test_registry_extended.py` | `test_to_dict_conversion` | Checks `"name"`, `"host"`, `"port"` only; doesn't assert on api_key presence. Compatible. |

### New Tests Required (Summary)

| # | Test Name | File | Purpose | Priority |
|---|-----------|------|---------|----------|
| N1 | `test_hmac_compare_digest_used` | `tests/unit/test_agent_middleware.py` | Verify middleware uses `hmac.compare_digest()`, not `==` or `!=`. Mock to confirm call arguments. | P0 — Security-critical |
| N2 | `test_auth_disabled_warning_unconditional` | `tests/unit/test_server.py` (new) or append to existing server tests | Patch logging, start agent mock with no token on various bind addresses, verify WARNING fires always and CRITICAL for 0.0.0.0. | P1 — High-visibility fix |
| N3 | `test_registry_file_permissions_0600` | Append to `tests/unit/test_registry_extended.py` or new file | Create node → stat file → assert mode is 0o600. Test both owner-only and permission denial graceful degradation. | P1 — Security-critical |
| N4 | `test_to_dict_masks_api_key` | New: `tests/unit/test_node_serialization.py` or append to `test_remote.py` | Create node with api_key set → assert `"has_api_key": True in dict, `"api_key"` key absent. Also test None case (False). | P1 — Data leak prevention |
| N5 | `test_openapi_suppressed_when_auth_active` | Append to `tests/unit/test_agent_middleware.py` or new server test | Verify that when auth is active and app created via create_app(), the openapi.json route does not exist in registered routes. | P1 — Defense-in-depth verification |

---

## 5. Risk & Rollback Strategy

### Pre-Merge Verification Gate

Before merging, the following must pass:
1. `pytest tests/unit/test_agent_middleware.py -v` — **all tests pass** (modified + new)
2. `pytest tests/integration/test_adr_cross_cutting.py -v` — auth integration suite passes
3. Manual smoke test on a real system: start agent with token → verify 401 on write endpoints, 200 on /health → verify openapi.json returns 404 or is unreachable → stop agent
4. File permission verification: create node via CLI → `stat -c '%a' ~/.llauncher/nodes.json` → confirm `600`

### Rollback Plan

Each phase is independently revertable due to isolated file touches:

| Phase | Rollback Scope | Risk Level | Recovery Command |
|-------|---------------|------------|-----------------|
| 1 (CRITICAL) | Only `middleware.py`, `server.py` — reverts the hmac change and openapi suppression | LOW — original code is trivially restored via git revert. No data loss. | `git checkout HEAD~N -- llauncher/agent/middleware.py llauncher/agent/server.py` |
| 2 (HIGH) | Only `registry.py`, `server.py` — reverts chmod and warning additions | MEDIUM — if file permissions were changed, a prior nodes.json would retain its old permissions until the next save. No data corruption risk. | Same git checkout approach |
| 3 (MEDIUM-HIGH) | Only `server.py` | LOW — logging behavior change only | Same git checkout |
| 4 (MEDIUM) | Only `node.py` | MEDIUM — callers that used `"api_key"` from to_dict() would break. All known consumers audited as safe; regression testing on integration layer needed before rollback decision. | Git revert, plus verify RemoteState.get_snapshot() consumer compatibility |
| 5 (DOC) | Documentation only | N/A — no code impact | Anytime revert is safe |

### Worst-Case Scenario: Middleware Change Breaks Auth Entirely

If Phase 1's hmac change introduces a bug that prevents valid keys from authenticating (e.g., `expected_token` is somehow non-string), the middleware will return 403 for all requests. Recovery:
1. Set `LAUNCHER_AGENT_TOKEN=""` (empty string → None in settings) to revert to no-auth mode (backward compatible path).
2. Kill and restart agent without token to restore access.
3. Apply git revert to Phase 1 files only.

### Database / State Migration Risk

**None.** No schema changes, no file format migrations. The nodes.json format remains `{"name": ..., "api_key": ...}` — we only change its disk permissions (Phase 2) and the in-memory serialization for display (Phase 4). Backward compatibility with existing `nodes.json` files is guaranteed since `_load()` reads `"api_key"` directly from JSON.

---

## 6. Security Verification Checklist

Post-merge validation must confirm every finding is fully remediated:

### Critical Path Tests
- [ ] **Timing oracle eliminated:** Inspect `middleware.py` — verify line uses `hmac.compare_digest(api_key, self.expected_token)` and no `!=` or `==` on `api_key`. Confirm `None` guard precedes comparison.
- [ ] **OpenAPI schema not exposed with auth active:** Start agent with `LAUNCHER_AGENT_TOKEN=xxx`, curl `http://localhost:8765/openapi.json` → expect 404 (route does not exist, not 200). Curl `/docs` and `/redoc` → also 404.
- [ ] **OpenAPI schema visible without auth:** Stop agent with token unset, restart, curl `http://localhost:8765/openapi.json` → expect 200 with full route list.

### High-Priority Security Tests
- [ ] **File permissions hardened:** Add a node via CLI (`llauncher node add test --host 127.0.0.1 --api-key secretkey`) → `stat -c '%a' ~/.llauncher/nodes.json` → output must be `600`.
- [ ] **Plaintext warning on startup:** Run agent without token, redirect stderr: `llauncher-agent 2>&1 | grep -i "disable"` → verify WARNING or CRITICAL message present in output.
- [ ] **to_dict() masks keys:** In Python REPL:
    ```python
    from llauncher.remote.node import RemoteNode
    n = RemoteNode("test", "localhost", api_key="secret")
    d = n.to_dict()
    assert "api_key" not in d, f"KEY LEAK: {d}"
    assert d["has_api_key"] is True
    ```

### Documentation Verification
- [ ] **ADR matches implementation:** Read `docs/adrs/003-agent-api-authentication.md` → confirm the exemption table lists exactly `/health`, `/docs`, `/redoc`, `/openapi.json` (conditional) as exempt, with a clear statement that "all other paths require authentication."

### Regression Smoke Tests
- [ ] **Valid key still authenticates:** `curl -H 'X-Api-Key: xxx' http://localhost:8765/status` → 200.
- [ ] **Missing key returns 401:** `curl http://localhost:8765/status` → 401.
- [ ] **Wrong key returns 403:** `curl -H 'X-Api-Key: wrong' http://localhost:8765/status` → 403.
- [ ] **Health endpoint unauthenticated:** `curl http://localhost:8765/health` → 200 (works with and without token).
- [ ] **Full test suite passes:** `pytest tests/ -v --tb=short` — all pass, no new warnings from deprecation linters.

---

## Appendix A: Diff Summary by File

```
File: llauncher/agent/middleware.py
  + import hmac                          (new import)
  ~ refactored dispatch() auth check     (~10 lines modified: None guard → compare_digest)
  - "/openapi.json" from _AUTH_EXEMPT_PATHS  (frozenset entry removed)

File: llauncher/agent/server.py
  + openapi_url=None if auth_active else "/openapi.json"  (FastAPI constructor, 1 line added)
  ~ run_agent() warning block rewritten    (~6 → 12 lines)
  + startup plaintext-key warning          (~8 lines added before uvicorn.run)

File: llauncher/remote/registry.py
  + import os                              (new import)
  ~ _save() method: chmod(0o600) guard     (+3 lines with try/except)

File: llauncher/remote/node.py
  ~ to_dict(): "api_key" → "has_api_key"   (1 line, same length)

File: docs/adrs/003-agent-api-authentication.md
  ~ Decision section text                  (~5 lines rewritten)
  + exemption table                        (~8 new lines, markdown table)
  ~ Consequences section                   (~2 lines modified for posture note)

Total lines of code changed: ~45 (additions + modifications)
Total new test functions: 4–5
Total documentation changes: 1 ADR file
```

## Appendix B: Dependencies Between Changes

```
Phase 1a: hmac import         ────────────┐
Phase 1b: compare_digest refactoring       │→ Phase 1 (parallel with Phase 2)
Phase 1c: openapi_url in FastAPI           │    (no inter-phase dependencies)
Phase 1d: exempt set cleanup              ─┘

Phase 2a: import os                        ──┐
Phase 2b: chmod(0o600)                     ├→ Phase 2 (parallel with Phase 1)
Phase 2c: startup plaintext warning        │    depends on 2a/2b for context
Phase 3: warn unconditional                ─┘    depends on Phase 2 completion

Phase 4: to_dict() masking                → Standalone, merge whenever ready

Phase 5: ADR documentation correction     → Standalone, merge with code PR or separate docs-only PR
```

---

**END OF PLAN** — Hand off to Implementer subagent for execution. All findings #1–#6 addressed; #7 and #8 confirmed not material risk per audit scope.
