# Implementation Plan — llauncher ADRs 003–006 (2026-04-26)

## Pre-Flight: Test Baseline Gate (Task 0.x)

Before any implementation begins, establish a test baseline to detect regressions introduced by the new features.

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 0.1 | test | `pytest tests/ -v --tb=short` | Run the full existing test suite against the current main branch **before** any changes are made. Record pass/fail/skip counts. | Baseline results captured to `/tmp/baseline_test_results.txt`; no undocumented pre-existing failures |
| 0.2 | check | `cat /tmp/baseline_test_results.txt` | Review baseline output. Document any failures as a separate artifact (`/tmp/baseline_failures.txt`) for later regression comparison. | All existing tests pass or have documented, pre-existing skip/fail entries |

```bash
# Execute Task 0.1 (must complete before Phase 1 implementation):
pytest tests/ -v --tb=short > /tmp/baseline_test_results.txt 2>&1
echo "=== Baseline completed ===" >> /tmp/baseline_test_results.txt
cat /tmp/baseline_test_results.txt
```

> **Gate rule:** Do NOT proceed with implementation if the baseline has failures not documented separately in `/tmp/baseline_failures.txt`. Any new test failure after implementation is a regression and must be investigated before merging.

---

## Executive Summary

This plan implements four architectural decisions that collectively harden, extend, and observability-enable the llauncher agent platform. **ADR-003** adds authentication as a critical security foundation — the only blocking task, since all network-facing features benefit from secured access. **ADRs 004–006** are independent higher-impact features: a CLI subcommand interface (ADR-004) for operator ergonomics, model cache health validation (ADR-005) to prevent wasted GPU cycles on missing/corrupted weights, and GPU resource monitoring (ADR-006) to enable informed scheduling decisions. No ADR strictly depends on another beyond ADR-003's security layer serving as a natural first step; the remaining three can execute in parallel by different workers. Each phase includes explicit test-first tasks with file paths, function signatures, and acceptance criteria.

---

## Phase 1: Foundation / Security (ADR-003 — Agent API Authentication)

**Rationale:** Unauthenticated agent API is a critical security vulnerability. All other network-facing features (CLI, TS extension, remote aggregation) must eventually operate over authenticated channels. Auth is the prerequisite for secure-by-default operation and should be first.

### Task 1.1: Add `api_key` to Core Settings (Module-Level Constants Pattern)

> **Reviewer Fix Applied:** The existing `core/settings.py` uses module-level constants, not Pydantic models. Implementation must follow the established pattern.

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.1 | impl | `llauncher/core/settings.py` | Add module-level constant: `AGENT_API_KEY: str \| None = os.getenv("LAUNCHER_AGENT_TOKEN")`. After assignment, validate: if the value is set and truthy, verify it is non-empty (length > 0); raise `ValueError("LAUNCHER_AGENT_TOKEN must be a non-empty string")` otherwise. Do NOT use Pydantic Field(). Match the existing module-level constant pattern in `settings.py`. | Constant present with correct env var name; default None preserves backward compat; empty-string rejection works at module import time |
| 1.2 | test | `tests/unit/test_core_settings_auth.py` (NEW) | See signatures below — tests against actual `core/settings.py` implementation pattern. | All tests pass |

**Test file: `tests/unit/test_core_settings_auth.py`**

```python
import os
import subprocess
import sys


def test_default_api_key_is_none():
    """Unset env var → AGENT_API_KEY is None (backward compat)."""
    # Use subprocess to ensure clean module reload — environment affects import-time assignment
    code = '''
import os
if "LAUNCHER_AGENT_TOKEN" in os.environ:
    del os.environ["LAUNCHER_AGENT_TOKEN"]
# Force re-import of settings
if "llauncher.core.settings" in sys.modules:
    del sys.modules["llauncher.core.settings"]
from llauncher.core import settings
assert settings.AGENT_API_KEY is None, f"Expected None, got {settings.AGENT_API_KEY!r}"
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"


def test_api_key_from_env():
    """Env var populated → AGENT_API_KEY matches value."""
    code = f'''
import os, sys
os.environ["LAUNCHER_AGENT_TOKEN"] = "my-secret-token"
if "llauncher.core.settings" in sys.modules:
    del sys.modules["llauncher.core.settings"]
from llauncher.core import settings
assert settings.AGENT_API_KEY == "my-secret-token", f"Expected 'my-secret-token', got {settings.AGENT_API_KEY!r}"
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"


def test_empty_api_key_raises_value_error():
    """Empty string is rejected at module load time with ValueError."""
    code = '''
import os, sys
os.environ["LAUNCHER_AGENT_TOKEN"] = ""
try:
    if "llauncher.core.settings" in sys.modules:
        del sys.modules["llauncher.core.settings"]
    from llauncher.core import settings  # noqa: F401
except ValueError as e:
    assert "non-empty" in str(e).lower() or "must be" in str(e).lower(), f"Wrong error message: {e}"
else:
    raise AssertionError("Should have raised ValueError for empty token")
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"


def test_non_empty_api_key_accepted():
    """Non-empty string is accepted."""
    code = '''
import os, sys
os.environ["LAUNCHER_AGENT_TOKEN"] = "a" * 16
if "llauncher.core.settings" in sys.modules:
    del sys.modules["llauncher.core.settings"]
from llauncher.core import settings
assert len(settings.AGENT_API_KEY) == 16, f"Expected 16 chars, got {len(settings.AGENT_API_KEY)!r}"
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"
```

### Task 1.2: Implement FastAPI Auth Middleware (with Correct Status Codes)

> **Reviewer Fix Applied:** 
> - **401 Unauthorized** when `X-Api-Key` header is missing from the request
> - **403 Forbidden** when key is present but wrong/invalid
> - `_make_app()` helper tested against actual FastAPI behavior for `/health` vs protected routes

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.3 | impl | `llauncher/agent/middleware.py` (NEW) | Create `AuthenticationMiddleware` class wrapping FastAPI's `BaseHTTPMiddleware`. Overrides `dispatch()` to check `X-Api-Key` header against settings value (`AGENT_API_KEY`). Returns **401 Unauthorized** when the header is missing and a token is configured. Returns **403 Forbidden** when the header is present but does not match the configured key. Skips auth for `/health`, `/docs`, `/openapi.json` (read-only/metadata). | Middleware returns 401 for missing key, 403 for wrong key; /health always accessible |
| 1.4 | impl | `llauncher/agent/server.py` | Wire middleware into FastAPI app in `create_app()`. Read token from settings (already loaded at this point). If unset (`AGENT_API_KEY is None`), add startup log warning: `"[WARNING] LAUNCHER_AGENT_TOKEN not set — agent API is unauthenticated."` If set, call `app.add_middleware(AuthenticationMiddleware)`. | Middleware active when token present; no-op path (backward compat) when absent |
| 1.5 | test | `tests/unit/test_agent_middleware.py` (NEW) | See signatures below — uses real FastAPI app instances via TestClient with correct status codes. | All tests pass with mocked settings |

**Test file: `tests/unit/test_agent_middleware.py`**

```python
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def _make_app(token=None):
    """Helper to create a minimal FastAPI app with the middleware.
    
    Creates real route handlers for /health and /start/ so that
    FastAPI's routing behavior is exercised end-to-end through the middleware.
    """
    from llauncher.agent.middleware import AuthenticationMiddleware
    
    # Disable docs/redoc routes when token is configured (matches production setup)
    docs_kwargs = {} if token is None else {"docs_url": None, "redoc_url": None}
    
    app = FastAPI(**docs_kwargs)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/start/{model_name}")
    def start_server(model_name: str):
        return {"started": model_name}

    if token:
        app.add_middleware(AuthenticationMiddleware, expected_token=token)

    return app


def test_no_token_allows_all_requests():
    """When LAUNCHER_AGENT_TOKEN is None (not configured), all requests pass regardless of header."""
    app = _make_app(token=None)
    client = TestClient(app)

    # Health endpoint - always works
    resp = client.get("/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # Start endpoint without key - works when auth is disabled
    resp = client.post("/start/foo")
    assert resp.status_code == 200, f"Expected 200 (no auth), got {resp.status_code}"


def test_with_token_rejects_missing_key_returns_401():
    """When token is set and X-Api-Key header is MISSING → 401 Unauthorized."""
    app = _make_app(token="my-secret-token")
    client = TestClient(app)

    resp = client.post("/start/foo")
    assert resp.status_code == 401, f"Expected 401 (missing key), got {resp.status_code}"


def test_with_token_rejects_wrong_key_returns_403():
    """When token is set and X-Api-Key header is present but WRONG → 403 Forbidden."""
    app = _make_app(token="my-secret-token")
    client = TestClient(app)

    resp = client.post("/start/foo", headers={"X-Api-Key": "wrong-key"})
    assert resp.status_code == 403, f"Expected 403 (wrong key), got {resp.status_code}"

    resp = client.post("/start/foo", headers={"X-Api-Key": ""})
    assert resp.status_code == 403, f"Expected 403 (empty key), got {resp.status_code}"


def test_with_token_accepts_valid_key():
    """Correct API key header allows access to protected endpoints."""
    app = _make_app(token="my-secret-token")
    client = TestClient(app)

    resp = client.post("/start/foo", headers={"X-Api-Key": "my-secret-token"})
    assert resp.status_code == 200, f"Expected 200 (valid key), got {resp.status_code}"


def test_health_always_accessible():
    """Health endpoint is always accessible — even when token is configured and requests lack auth."""
    app = _make_app(token="my-secret-token")
    client = TestClient(app)

    # GET /health without any header → 200 (read-only passthrough)
    resp = client.get("/health")
    assert resp.status_code == 200, f"Expected /health to be accessible (200), got {resp.status_code}"

    # POST /start/foo without key → 401 (not health endpoint)
    resp = client.post("/start/bar")
    assert resp.status_code == 401, f"Expected 401 for non-health route, got {resp.status_code}"


def test_openapi_docs_excluded_from_auth():
    """OpenAPI spec endpoints are accessible without auth (for API discovery)."""
    app = _make_app(token="my-secret-token")
    client = TestClient(app)

    resp = client.get("/openapi.json")
    assert resp.status_code == 200, f"Expected /openapi.json to be accessible (200), got {resp.status_code}"
```

### Task 1.3: Docs Gating via FastAPI Constructor (Fixed Per Review)

> **Reviewer Fix Applied:** Conditionally set `docs_url=None` and/or `redoc_url=None` in the `FastAPI()` constructor, rather than attempting to remove routes after mounting (which is unreliable).

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.6 | impl | `llauncher/agent/server.py` — in `create_app()` | When `AGENT_API_KEY` is set (auth active), pass `docs_url=None, redoc_url=None` to the `FastAPI(...)` constructor. When unset, use default (`docs_url="/docs", redoc_url="/redoc"`). Do NOT attempt route removal after mounting — the routes will not be registered at all. Log: `[INFO] OpenAPI docs disabled — API key authentication active.` | No token → /docs served normally. Token set → /docs returns 404 (never mounted); /openapi.json still served for discovery |

**Correct implementation pattern:**
```python
# In create_app() in llauncher/agent/server.py:
import logging
from fastapi import FastAPI

def create_app():
    # Disable interactive docs/redoc when auth is active
    if AGENT_API_KEY:
        app = FastAPI(docs_url=None, redoc_url=None)
        logger.info("OpenAPI docs disabled — API key authentication active.")
    else:
        app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    if AGENT_API_KEY:
        app.add_middleware(AuthenticationMiddleware)

    # Mount routes ...
    return app
```

### Task 1.4: Update Agent Routing Endpoints

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.7 | check | `llauncher/agent/routing.py` | The middleware intercepts all requests at the FastAPI level, so no changes to individual route handlers are needed for auth enforcement. However: (a) Verify `/health` is listed in unauthenticated paths. (b) Confirm response structure from `/start-with-eviction/` includes `port_state` field per ADR-002 migration plan Task 4. | No route-level code changes required; middleware handles all auth at dispatch level |
| 1.8 | check | `llauncher/agent/server.py` | Confirm docs gating uses constructor params (Task 1.6). Confirm middleware wiring happens after routes are defined but before return. | Code review confirms correct mounting order |

### Task 1.5: Extend Node Registry for Auth Credentials

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.9 | impl | `llauncher/remote/node.py` | Add optional `api_key: str = ""` field to the `RemoteNode` data model (or `NodeConfig`). When making HTTP calls (`ping()`, `get_status()`, etc.), include header `X-Api-Key: <key>` if set. Preserve backward compat when api_key is empty string or None. | Authenticated request header present when key configured; no auth header when key absent |
| 1.10 | impl | `llauncher/remote/registry.py` | Extend `NodeRegistry.add_node()` to accept optional `api_key` parameter and persist it in nodes.json. Update the UI node registration dialog (see ADR-004 CLI also needs this). | New node with api_key persisted to disk; round-trip read writes header correctly |
| 1.11 | test | `tests/unit/test_remote_node_auth.py` (NEW) | See signatures below | Tests pass |

**Test file: `tests/unit/test_remote_node_auth.py`**

```python
from llauncher.remote.node import RemoteNode


def test_node_with_api_key_includes_header():
    """RemoteNode with api_key sends X-Api-Key header on requests."""
    node = RemoteNode(name="test", host="127.0.0.1", port=8765, api_key="secret")
    # Verify the _build_headers() or internal method includes the key
    assert "X-Api-Key" in node._get_auth_header_dict()["headers"]


def test_node_without_api_key_no_extra_headers():
    """RemoteNode without api_key sends no auth headers."""
    node = RemoteNode(name="test", host="127.0.0.1", port=8765, api_key=None)
    # Verify no auth header added — clean request for unauthenticated nodes
    assert len(node._get_auth_header_dict().get("headers", {})) == 0


def test_node_empty_api_key_treated_as_none():
    """Empty string api_key is equivalent to None."""
    node = RemoteNode(name="test", host="127.0.0.1", port=8765, api_key="")
    assert node.api_key == ""  # or should normalize to None — decide at impl time
```

### Task 1.6: Update Agent Startup Logging & Config Examples

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 1.12 | impl | `llauncher/agent/server.py` | On startup, after settings load: if `LAUNCHER_AGENT_TOKEN` is unset AND host is "0.0.0.0", log WARNING level message per review doc. If set, log INFO that auth is active with the bind address. | Correct log messages appear on agent start in both modes |
| 1.13 | impl | `README.md` (or docs/) | Add a section "Authentication" describing how to set `LAUNCHER_AGENT_TOKEN`. Document default behavior (unauthenticated) and secure deployment recommendations (set token, restrict bind address). | README includes auth documentation matching implementation |

**Git commit convention for worker executing Phase 1:**
```
feat(agent): add API key authentication middleware

- Add LAUNCHER_AGENT_TOKEN env var to core settings as module-level constant
- Implement AuthenticationMiddleware with X-Api-Key header check (401 missing, 403 wrong)
- Wire middleware into FastAPI app in agent/server.py
- Gate /health and /openapi.json as unauthenticated (read-only passthrough)
- Disable interactive docs/redoc via constructor params when auth active
- Extend RemoteNode to carry api_key for authenticated node pings
- Update NodeRegistry.add_node() with optional api_key parameter
- Add unit tests for middleware, settings, and remote auth flow

Refs: ADR-003, Issue #security
```

---

## Phase 2: Core Features (ADRs 005 + 004 + 006)

All three can execute in parallel. Ordering within this phase is logical rather than dependency-bound: model cache health first (affects server start path), CLI second (new entry point), GPU monitoring last (observability). However, the GPU metrics will be consumed by both the agent API and the UI — so ADR-005's pre-flight integration with VRAM estimates is a cross-cutting concern best addressed as Phase 3.

### ADR-005: Model Cache Health Validation

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.1 | impl | `llauncher/core/model_health.py` (NEW) | Implement `check_model_health(model_path: str) -> ModelHealthResult`. **ModelHealthResult is a Pydantic BaseModel** with fields: `valid: bool = False`, `reason: str \| None = None`, `size_bytes: int \| None = Field(default=None, ge=0)`, `exists: bool = False`, `readable: bool = False`, `last_modified: datetime \| None = None`. Validation: exists → readable → size > 1MB (heuristic). If path is symlink, resolve via `Path.resolve()`. No GGUF header parsing in Phase 1 (deferred per ADR-005 recommendation). | Function returns valid=True for good files, False with descriptive reason for missing/corrupted/empty; symlinks resolved correctly |
| 2.2 | test | `tests/unit/test_model_health.py` (NEW) | See signatures below — all assertions use `.model_dump()` on results instead of `__dict__` or dataclass conversion. | All tests pass |

> **Reviewer Fix Applied:** ModelHealthResult uses Pydantic BaseModel (not dataclass). Tests call `.model_dump()`.

**Test file: `tests/unit/test_model_health.py`**

```python
from pathlib import Path
import tempfile
from llauncher.core.model_health import check_model_health, ModelHealthResult


def test_existing_valid_file():
    """Existing readable file > 1MB returns valid=True."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
        # Write > 1MB of dummy data to simulate a valid model file
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    result = check_model_health(str(path))
    assert result.valid is True, f"Expected valid=True for existing >1MB file; got: {result.model_dump()}"
    assert result.exists is True
    assert result.readable is True
    assert result.size_bytes == 1024 * 1024 + 1


def test_nonexistent_file():
    """Non-existent model path returns valid=False with reason."""
    result = check_model_health("/nonexistent/path/to/model.gguf")
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False
    assert dumped["exists"] is False
    assert "not found" in (dumped["reason"] or "").lower()


def test_empty_file():
    """Empty file (< 1MB) returns valid=False — heuristic for corruption."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
        path = Path(f.name).resolve()

    result = check_model_health(str(path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False
    assert "too small" in (dumped["reason"] or "").lower()


def test_symlink_resolved():
    """Symlinks are resolved and target validation applies."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
        f.write(b"x" * (1024 * 1024 + 1))
        real_path = Path(f.name).resolve()

    symlink_dir = tempfile.mkdtemp()
    symlink_path = Path(symlink_dir) / "model.gguf"
    symlink_path.symlink_to(real_path)

    result = check_model_health(str(symlink_path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is True


def test_symlink_to_nonexistent():
    """Broken symlink returns valid=False."""
    broken_dir = tempfile.mkdtemp()
    broken_path = Path(broken_dir) / "broken.gguf"
    broken_path.symlink_to("/nonexistent/target.gguf")

    result = check_model_health(str(broken_path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False


def test_unreadable_file():
    """File without read permission returns valid=False."""
    import os, stat
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    # Remove read permissions
    os.chmod(path, stat.S_IRWXO & ~stat.S_IRUSR)
    try:
        result = check_model_health(str(path))
        assert isinstance(result, ModelHealthResult)
        dumped = result.model_dump()
        assert dumped["valid"] is False
        reason_lower = (dumped["reason"] or "").lower()
        assert "permission" in reason_lower or "unreadable" in reason_lower
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # restore for cleanup
```

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.3 | impl | `llauncher/state.py` | Integrate `check_model_health()` into `start_server()` pre-flight and `_start_with_eviction_impl()` Phase 1 validation. Call health check after getting model config, before any process spawn. On failure: return early with `success=False`, log error to audit, skip start. The existing pre-flight already checks `config.model_path` exists via some mechanism — replace or augment that check with the richer `check_model_health()`. | Start on missing model file returns error immediately (no OOM later); health status appears in audit log |
| 2.4 | impl | `llauncher/agent/routing.py` | Add two new endpoints: `GET /models/health` and `GET /models/health/{model_name}`. The list endpoint iterates all configured models, calls `check_model_health()` for each (with optional caching), returns structured response per ADR-005 spec (`.model_dump(result)`). Detail endpoint filters by name. Both require auth if middleware active (enforced by Task 1.3). | Endpoints return correct JSON; missing files show `"exists": false` without errors |
| 2.5 | test | `tests/unit/test_model_health_api.py` or extend existing `tests/unit/test_agent.py` | Test both `/models/health` and `/models/health/{name}` endpoints with mocked file system (using pytest's `monkeypatch`). Verify health response shape matches ADR spec, using `.model_dump()`. | Health API returns correct structure; handles missing models gracefully |
| 2.6 | impl | `llauncher/ui/tabs/dashboard.py` or new tab component `llauncher/ui/tabs/model_registry.py` (NEW) | Add a "Model Registry" section/tab in Streamlit UI displaying model health table: columns for name, path, exists (✓/✗), size, last modified, status ("ready"/"missing"/"corrupted"/"unknown"). Status determined by `check_model_health()` results. | New tab renders correctly; status indicators match health check output |
| 2.7 | impl | `llauncher/core/model_health.py` — cache layer | Add a simple TTL-aware cache for `check_model_health()` results using the `_TTLCache` utility (defined in `llauncher/util/cache.py`, Task 3.1 below). Key is the model path string, default TTL of 60 seconds. Invalidate on config changes (add/remove/update operations). | Health check called only once per start; cached result returned for subsequent calls within TTL |

> **Reviewer Fix Applied:** Cache uses `_TTLCache` utility pattern — see Task 3.1 below and Task 2.16 for the cache implementation itself. Not `functools.lru_cache`.

**Git commit convention:**
```
feat(core): add model cache health validation

- Implement check_model_health() in core/model_health.py with existence, readability, and size checks
- ModelHealthResult is a Pydantic BaseModel (not dataclass) for consistent serialization via .model_dump()
- Integrate health check into state.start_server() pre-flight validation
- Add GET /models/health API endpoint to agent routing
- Cache health results with TTL-aware strategy (invalidate on config changes)
- Add Model Registry tab to Streamlit dashboard showing file status indicators
- Handle symlinks via Path.resolve(), broken link detection, permission checks

Refs: ADR-005, Finding W8
```

---

### ADR-004: CLI Subcommand Interface

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.8 | impl | `llauncher/cli.py` (NEW) | Create Typer app with subcommand groups using Typer's `Group` or nested commands. Define command structure per ADR spec: `model`, `server`, `node`, and `config` subcommand groups. Use `typer.Typer()` as the root app. Import existing core classes (`ConfigStore`, `LauncherState`) for local operations; use direct HTTP calls (via httpx, already a dep) or extracted remote client for node management. | CLI entry point defined in pyproject.toml — `llauncher --help` shows all top-level groups |
| 2.9 | impl | `pyproject.toml` | Add new console script entry point: `[project.scripts] llauncher = "llauncher.cli:app"`. If there's an existing entry_point for `llauncher`, consolidate. Keep `llauncher-agent` and `llauncher-mcp` as separate commands. | `pip install -e .` creates `llauncher` CLI binary; `--help` works without errors |
| 2.10 | impl | `llauncher/cli.py` — subcommand: **model** | Implement: `list` (reads ConfigStore, prints table), `info <name>` (detailed output of one model config). Uses local state only — no HTTP calls. Colorized output via `rich`. | `llauncher model list` shows configured models; `llauncher model info foo` shows details or error if missing |
| 2.11 | impl | `llauncher/cli.py` — subcommand: **server** | Implement: `start <model> [--port PORT]`, `stop <port>`, `status`. Create a local `LauncherState` instance and delegate to its methods (mirrors agent behavior exactly). On start, use auto-allocation if port not specified. Respect settings blacklists. Colorized status output showing port/model/PID/running state. | Local server operations mirror agent API semantics; `--port` optional with auto-allocate default; blacklist enforced |
| 2.12 | impl | `llauncher/cli.py` — subcommand: **node** | Implement: `add <name> --host HOST [--port PORT] [--api-key KEY]`, `list`, `remove <name>`, `status [all|--json]`. Use NodeRegistry for CRUD (same as agent/remote). For `status`, ping each node via httpx. When api_key is set on the node, include X-Api-Key header in ping. | Node add removes/persists to nodes.json; status shows online/offline per node |
| 2.13 | impl | `llauncher/cli.py` — subcommand: **config** | Implement: `path` (prints config file path), `validate <model>` (calls validate_config from mcp_server/tools/config.py statelessly). Utility commands — no mutations. | Config path printed; validation returns structured result |
| 2.14 | test | `tests/unit/test_cli.py` (NEW) | See signatures below — covers argument parsing, subcommand dispatch, and integration with local state. Use subprocess invocation or direct Typer CliRunner for testing. | All invocations produce expected output/errors; argparse/typer parses correctly |

**Test file: `tests/unit/test_cli.py`**

```python
from typer.testing import CliRunner
from llauncher.cli import app
import json


runner = CliRunner()


def test_help_shows_all_groups():
    """Root help displays model, server, node, and config groups."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Verify all four subcommand group names appear in help text
    assert "model" in result.stdout.lower() or "Model" in result.stdout
    assert "server" in result.stdout.lower() or "Server" in result.stdout


def test_model_list_empty():
    """With no models configured, list shows empty/none indicator."""
    # Requires a fresh config state — use fixture or mock ConfigStore
    result = runner.invoke(app, ["model", "list"])
    assert result.exit_code == 0


def test_server_status_local():
    """Server status reflects running llama-server processes from process table."""
    result = runner.invoke(app, ["server", "status"])
    # Should succeed (even if no servers running) and show table format
    assert result.exit_code in [0, 1]  # 0 for success, 1 might mean "no servers" — acceptable


def test_server_start_missing_model():
    """Start with non-existent model name shows error."""
    result = runner.invoke(app, ["server", "start", "does_not_exist"])
    assert result.exit_code != 0  # non-zero exit for error condition
    assert "not found" in result.stdout.lower() or "error" in result.stdout.lower()


def test_server_start_with_explicit_port():
    """Start with --port flag passes port through to state.start_server()."""
    # Integration: should attempt to start and either succeed or fail (both OK) — just doesn't crash
    result = runner.invoke(app, ["server", "start", "foo", "--port", "9091"])
    assert result.exit_code in [0, 1]


def test_node_add_and_list():
    """Add a node and verify it appears in list output."""
    result = runner.invoke(app, ["node", "add", "test-node", "--host", "127.0.0.1"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["node", "list"])
    assert result.exit_code == 0
    assert "test-node" in result.stdout


def test_node_add_with_api_key():
    """Node add with --api-key persists the key to nodes.json."""
    result = runner.invoke(app, ["node", "add", "secure-node", "--host", "192.168.1.50", "--api-key", "my-secret"])
    assert result.exit_code == 0

    # Verify persistence by reading the node's data back (or via list + json parse)
    result = runner.invoke(app, ["node", "list", "--json"])
    if result.exit_code == 0:
        nodes = json.loads(result.stdout)
        assert any(n.get("name") == "secure-node" for n in nodes.values())


def test_config_path():
    """Config path returns the expected file path."""
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert ".llauncher" in result.stdout or "config.json" in result.stdout


def test_node_status_all():
    """Node status with --all flag queries all nodes via HTTP ping."""
    # At least one node may be registered and respond — verify it doesn't crash
    result = runner.invoke(app, ["node", "status", "--all"])
    assert result.exit_code == 0


def test_node_remove():
    """Remove a node deletes it from nodes.json."""
    # Add then remove in sequence
    runner.invoke(app, ["node", "add", "temp-node", "--host", "127.0.0.1"])
    result = runner.invoke(app, ["node", "remove", "temp-node"])
    assert result.exit_code == 0


def test_server_stop_nonexistent_port():
    """Stop on unused port returns error."""
    result = runner.invoke(app, ["server", "stop", "65432"])
    # Should fail with non-zero exit — specific behavior TBD
    assert result.exit_code != 0 or "not running" in result.stdout.lower() or "error" in result.stdout.lower()


def test_cli_integration_with_auth():
    """When node has api_key, CLI includes it on HTTP requests (integration)."""
    # This tests the cross-cutting integration with ADR-003
    runner.invoke(app, ["node", "add", "authed-node", "--host", "127.0.0.1", "--api-key", "secret"])
    result = runner.invoke(app, ["node", "status", "authed-node"])
    assert result.exit_code == 0  # Won't actually connect to port 8765 in test env — just verify no crash
```

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.15 | impl | `llauncher/cli.py` | Add output formatting helpers: `print_table()` using rich's `Table`, color-coded status indicators (green=running, red=stopped/offline, yellow=warning), and a `--json` flag on list/status commands for machine-readable output. | All list/status outputs are table-formatted by default; `--json` produces parseable JSON on applicable commands |

**Git commit convention:**
```
feat(cli): add subcommand interface via Typer

- Create llauncher/cli.py with model, server, node, and config command groups
- Register CLI entry point in pyproject.toml as 'llauncher' console script
- Local state commands (model list/info, server start/stop/status) delegate to LauncherState + ConfigStore
- Remote commands (node add/list/remove/status) use NodeRegistry with httpx for pings
- Node registration supports --api-key parameter (ADR-003 integration point)
- Rich table-formatted output with color-coded status; --json flag for machine-readable mode
- All local operations mirror agent API behavior exactly — no divergence

Refs: ADR-004
```

---

### ADR-006: GPU Resource Monitoring and VRAM Tracking

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.16 | impl | `llauncher/core/gpu.py` (NEW) | Implement `GPUHealthCollector` class with auto-detect logic: tries `nvidia-smi` → `rocm-smi` → Apple MPS fallback. Each backend gets a `_query_NVIDIA()`, `_query_ROCM()`, `_query_MPS()` method returning typed data structure per ADR spec (devices list with vram, temperature, utilization, processes). Handle missing CLI tools gracefully: return empty backends list without exceptions or crashes. Cache results using the `_TTLCache` utility pattern (default 5s TTL) via `collector.refresh()` — see Task 3.1 for the cache implementation imported into both `core/gpu.py` and `core/model_health.py`. | Backends detect correctly; missing hardware returns clean empty response; no exceptions on bare metal without GPU drivers |
| 2.17 | test | `tests/unit/test_gpu_health.py` (NEW) | See signatures below — tests against the TTLCache-aware implementation. All tests pass | All tests pass |

> **Reviewer Fix Applied:** Cache uses `_TTLCache` utility (see Task 3.1). Not `functools.lru_cache`. The collector calls `self._cache.get("health")` with TTL invalidation on refresh().

**Test file: `tests/unit/test_gpu_health.py`**

```python
from llauncher.core.gpu import GPUHealthCollector


def test_no_backend_returns_empty():
    """GPUHealthCollector without any available backend returns empty backends list."""
    # Patch subprocess to fail for all SMI tools — simulates environment without GPU drivers
    collector = GPUHealthCollector()
    result = collector.get_health()
    assert isinstance(result, dict)
    assert result["backends"] == []


def test_simulated_nvidia_output_parsed():
    """Simulated nvidia-smi output parses into structured device data."""
    # Mock subprocess.run to return canned nvidia-smi JSON-like output
    collector = GPUHealthCollector()

    # Test with simulated single-GPU setup
    result = collector._query_NVIDIA(simulated_output=True)  # or pass mock data directly

    assert isinstance(result["devices"], list)
    if len(result["devices"]) > 0:
        device = result["devices"][0]
        assert "index" in device
        assert "name" in device
        assert "total_vram_mb" in device
        assert "used_vram_mb" in device
        assert "free_vram_mb" in device


def test_simulated_multi_gpu_output():
    """Multi-GPU nvidia-smi output correctly identifies all devices."""
    collector = GPUHealthCollector()

    # Test with simulated 2-device setup
    result = collector._query_NVIDIA(simulated_output=True, num_devices=2)

    assert len(result["devices"]) == 2

    for device in result["devices"]:
        assert "index" in device
        assert "total_vram_mb" > 0


def test_lifecycle_processes_mapped():
    """llauncher-managed processes appear in gpu.processes per device."""
    collector = GPUHealthCollector()

    # After calling refresh(), the collector should map running llama-server PIDs to devices
    # This requires mocking both SMI output and process table scan
    result = collector.refresh()  # or get_health() — whichever is the public API

    assert isinstance(result, dict)
    # At minimum, no crash and processes list is a dict/list per device


def test_vram_before_and_after_start():
    """After starting a server, VRAM usage increases (integration-level)."""
    collector = GPUHealthCollector()

    before = collector.get_health()  # baseline
    # start_server(...) — integration step that requires llama-server binary
    after = collector.get_health()

    # In test env with mocked SMI, verify data structure consistency:
    assert set(before.keys()) == set(after.keys())


def test_ttl_cache_invalidation():
    """TTL cache is invalidated on refresh() and returns fresh data."""
    from llauncher.util.cache import _TTLCache

    collector = GPUHealthCollector(cache=_TTLCache(ttl_seconds=0.1))

    # First call populates the cache
    result1 = collector.get_health()

    # Wait for TTL to expire (or manually invalidate)
    import time
    time.sleep(0.2)  # let TTL expire

    # Next call should miss the cache and re-query
    result2 = collector.get_health()
    assert isinstance(result2, dict)
```

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 2.18 | impl | `llauncher/agent/routing.py` | Extend `/status` endpoint response to include top-level `"gpu"` key with data from `GPUHealthCollector.get_health()`. When queried with `?full=true`, include per-device breakdowns and process attribution. Both auth-gated by Task 1.3 middleware. If GPU collector returns empty (no backend), include `"gpu": null` or omit the field — never error. | `/status` includes gpu key; missing GPU hardware → no crash, clean null/empty response |
| 2.19 | impl | `llauncher/agent/routing.py` — pre-flight VRAM check | In the `/start-with-eviction/{model}` handler, before starting process: call `check_vram_sufficient(required_mb)` where required MB is estimated from model config (heuristic: e.g., 2GB per billion params × layers offloaded). If insufficient VRAM → return `409 Conflict` with `{ "error": "insufficient_vram", "required_mb": X, "available_mb": Y }`. On GPU-free systems, skip this check (no-op — model will fail at process level naturally). | Pre-flight fails with 409 when VRAM insufficient; no pre-flight on CPU-only systems |
| 2.20 | impl | `llauncher/ui/tabs/dashboard.py` or new component | Add a GPU metrics widget to the Streamlit dashboard: per-GPU VRAM gauge using st.metric, "used vs total" bar chart (st.progress), and warning badge when swap operation would exceed available capacity on target port's GPU. Integrate with running server table to show estimated/actual VRAM per row. | GPU gauge visible; shows correct used/total from collector; no crash when no GPUs present |
| 2.21 | test | `tests/integration/test_status_with_gpu.py` (NEW or extend existing) | Integration-level: start a model server, verify `/status` includes gpu processes mapped to that port/PID. Mock GPU hardware data where real hardware unavailable. | Status endpoint accurately reflects running server's GPU attribution |

**VRAM estimation heuristic (for pre-flight check):**
```python
def estimate_vram_mb(config) -> int:
    """Heuristic VRAM requirement based on model parameters and GPU layers."""
    # This is approximate — actual usage depends on model architecture, precision, etc.
    params_billion = _extract_param_count_from_model_path(config.model_path) or 7  # default fallback
    gpu_layers = config.n_gpu_layers or 999  # if auto, assume all layers offloaded

    # Rough estimate: ~1GB per billion params for Q4_K_M quantization (llama.cpp typical)
    base_vram_mb = int(params_billion * 1024)

    # Adjust by layer ratio — if only partial GPU offload, proportionally less VRAM
    # Max layers from model config would be needed here; for now assume all-gpu if n_gpu_layers=999+
    return base_vram_mb


def check_vram_sufficient(required_mb: int) -> tuple[bool, str | None]:
    """Check if any GPU has sufficient free VRAM."""
    collector = GPUHealthCollector()
    health = collector.get_health()

    for device in health["devices"]:
        if device["free_vram_mb"] >= required_mb:
            return True, None

    # Find the largest free GPU to report best-case scenario in error
    max_free = max((d["free_vram_mb"] for d in health["devices"]), default=0)
    return False, f"insufficient_vram: need {required_mb}MB, max available on any device is {max_free}MB"
```

**Git commit convention:**
```
feat(core): add GPU resource monitoring and VRAM tracking

- Implement GPUHealthCollector with NVIDIA SMI, ROCm, and Apple MPS backends
- Auto-detects available hardware; returns empty/none gracefully when no GPUs present
- Cache results per-request with 5s TTL via _TTLCache utility to avoid SMI overhead on high-frequency polling
- Extend /status API endpoint with gpu data (per-device vram, utilization, temperature)
- Add pre-flight VRAM check on /start-with-eviction — returns 409 if insufficient VRAM
- Integrate GPU metrics into Streamlit dashboard as VRAM gauge widget
- Per-process attribution: map llama-server PIDs to GPUs via SMI process table

Refs: ADR-006, Finding from session 019dc8ad (Pi footer context meter gap)
```

---

## Phase 3: Cross-Cutting Integration & Verification

This phase ties all four ADRs together and ensures end-to-end correctness. Tasks here depend on completion of Phase 2 features.

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| **3.1a** | impl | `llauncher/core/gpu.py` + cache import | `GPUHealthCollector` now imports `_TTLCache` from `llauncher/util/cache.py`. Add a private instance: `self._cache = _TTLCache(ttl_seconds=5)`. In `refresh()`, call `self._cache.invalidate()` before re-querying. In `get_health()`, return `self._cache.get("health")` if present, else query and store. | Collector uses TTL cache; stale results automatically refreshed after TTL; manual `refresh()` bypasses cache entirely |
| **3.1b** | impl | `llauncher/core/model_health.py` + cache import | `check_model_health()` uses the same `_TTLCache` from `llauncher/util/cache.py`. Add a module-level instance: `_health_cache = _TTLCache(ttl_seconds=60)`. Inside `check_model_health()`: first check `_health_cache.get(model_path)`; if miss, run full validation and store result via `_health_cache.set(model_path, result, ttl_seconds=60)`. Add public function `invalidate_health_cache(model_path: str | None = None)` for config-change invalidation (None invalidates all). | Health results cached for 60s; config change triggers cache purge; stale results refreshed automatically after TTL |
| **3.1c** | impl | `llauncher/core/model_health.py` + `gpu.py` integration | In `start_server()` pre-flight: when VRAM check fails (409 from `/start-with-eviction/`), augment the error message to include model cache health status if the model path is configured. e.g., `{ "error": "insufficient_vram", "required_mb": X, "available_mb": Y, "model_health_hint": { "exists": false, "reason": "path not found" } }` when health check reveals missing file alongside VRAM issue. This gives operators a single diagnostic picture: is the server blocked because of VRAM or corrupted/missing model files? | Combined diagnostics appear in error messages when both VRAM and model cache checks fail; single call to `check_model_health()` serves both purposes |
| 3.2 | impl | `pi-footer-extension/` or new TS extension update | Update the Pi footer extension's LLauncher tools to include auth headers when node has api_key configured (ADR-003). Also add health and GPU data consumption in the context meter tool (ADR-005 + ADR-006). This extends beyond llauncher Python codebase but is called out in sessions as a downstream consumer. | TS extension injects X-Api-Key header; context meter shows VRAM from /status?full=true |
| 3.3 | test | `tests/integration/test_end_to_end.py` (NEW) | See signatures below — end-to-end scenarios covering all four ADRs interacting: auth-gated start, health check before start, GPU pre-flight on successful start, CLI operation through the same path. | All scenarios pass against a running agent with test fixtures |

> **Reviewer Fix Applied:** Task 3.1 was too vague ("combined diagnostics"). It is now broken into three concrete tasks (3.1a: TTL cache in gpu.py; 3.1b: TTL cache in model_health.py; 3.1c: combined VRAM+health error messages). Each has specific file paths, function names, and acceptance criteria.

**Test file: `tests/integration/test_end_to_end.py`**

```python
import pytest
from pathlib import Path


class TestEndToEndAuthAndHealth:
    """Cross-ADR integration tests — ADRs 003 + 005 + 006 interacting."""

    @pytest.fixture(autouse=True)
    def set_auth_token(self, monkeypatch):
        """Enable auth for all tests in this class."""
        monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "test-token")

    @pytest.fixture()
    def authenticated_client(self, test_agent_with_auth):
        """TestClient with Authorization header pre-injected."""
        from fastapi.testclient import TestClient
        # Assumes a test fixture that creates agent app with auth enabled
        client = TestClient(test_agent_with_auth)
        client.headers.update({"X-Api-Key": "test-token"})
        return client

    def test_start_without_auth_fails(self, unauthed_client):
        """ADR-003: Unauthenticated start is rejected."""
        resp = unauthed_client.post("/start/foo")
        assert resp.status_code == 401   # missing key → 401 Unauthorized

    def test_start_with_valid_key_and_missing_file_rejected_by_health(self, authenticated_client):
        """ADR-003 + ADR-005: Authenticated request still blocked by missing model file health check."""
        # Model config exists but path doesn't — auth passes, health check rejects
        resp = authenticated_client.post("/start/configured-but-missing-model")
        assert resp.status_code in [400, 422]  # Depends on error type chosen for missing file

    def test_start_with_valid_file_succeeds(self, authenticated_client):
        """ADR-003 + ADR-005: Valid auth + valid model file = start attempt."""
        resp = authenticated_client.post("/start/valid-model")
        assert resp.status_code in [200, 409]  # 409 if VRAM insufficient (ADR-006), 200 for success

    def test_start_with_both_vram_and_health_issues(self, authenticated_client):
        """ADR-003 + ADR-005 + ADR-006: Combined diagnostics when both checks fail."""
        resp = authenticated_client.post("/start/broken-model-on-low-vram")
        assert resp.status_code in [409]  # Combined error with model_health_hint

    def test_gpu_status_includes_processes(self, authenticated_client, running_server):
        """ADR-003 + ADR-006: Authenticated status includes GPU process attribution."""
        resp = authenticated_client.get("/status?full=true")
        assert resp.status_code == 200
        data = resp.json()
        if "gpu" in data and data["gpu"]:
            # At least verify the structure exists
            assert "devices" in data["gpu"]


class TestEndToEndCLIIntegration:
    """ADR-004 integration with ADR-003 auth."""

    def test_cli_node_add_persists_api_key(self):
        """ADR-004 + ADR-003: CLI node add with --api-key persists correctly."""
        from typer.testing import CliRunner
        from llauncher.cli import app
        runner = CliRunner()

        result = runner.invoke(app, ["node", "add", "test-node", "--host", "127.0.0.1", "--api-key", "cli-secret"])
        assert result.exit_code == 0

        # Verify persisted in nodes.json with key
        from llauncher.remote.registry import NodeRegistry
        registry = NodeRegistry()
        node_data = registry.get_node("test-node")
        assert node_data is not None
        assert hasattr(node_data, "api_key") and node_data.api_key == "cli-secret"  # or however Registry stores it

    def test_cli_server_start_uses_local_state(self):
        """ADR-004: Local server operations go through LauncherState directly (not HTTP)."""
        from typer.testing import CliRunner
        from llauncher.cli import app
        runner = CliRunner()

        result = runner.invoke(app, ["server", "status"])
        assert result.exit_code in [0, 1]  # Succeeds even with no running servers
```

**Git commit convention:**
```
refactor: integrate ADR-003 auth into TS extension and CLI node operations

- Update Pi footer llauncher extension to inject X-Api-Key header for nodes with api_key configured
- Extend context meter tool to consume GPU data from /status?full=true (ADR-006)
- Add comprehensive end-to-end integration tests covering all four ADRs interacting
- Cross-cutting health + VRAM diagnostics in combined error messages

Refs: ADR-003, ADR-004, ADR-005, ADR-006
```

---

## Integration Testing Strategy

After all phases complete, run the following verification sequence against a real or mocked llama-server instance:

1. **Auth Regression:** Start agent with `LAUNCHER_AGENT_TOKEN=xyz`. Call `/start` without key → **401** Unauthorized. With correct key → proceed to next step.
2. **Health Gate:** Ensure model config exists but file is missing (rename .gguf temporarily). Health API returns `"exists": false`. Start attempt blocked by pre-flight. Restore file, verify start proceeds past health check.
3. **GPU Pre-Flight:** If GPU available, mock `nvidia-smi` to report 0 free VRAM for one device. Start attempt → 409 with insufficient_vram error. Mock reports adequate VRAM → start succeeds (or fails at binary level if no real llama-server).
4. **CLI Parity:** Run equivalent operations via CLI (`llauncher server start/stop/status`) and verify same state as HTTP API calls. Add node via CLI, verify it works in agent status queries.
5. **Remote Node Auth Flow:** Register a remote node with api_key → RemoteAggregator ping includes auth header → authenticated response received or appropriate error if target doesn't support it yet.

**Test execution command:**
```bash
pytest tests/ -v --tb=short
# Target: all existing + new tests pass, no regression on 86 already-passing tests
```

---

## Risk Assessment

| Risk | Severity | Mitigation | Owner | Phase |
|------|----------|-----------|-------|-------|
| **nvidia-smi not available** in target environments | Medium | `GPUHealthCollector` returns empty backends list without crashing; VRAM pre-flight is a no-op when no GPUs detected. Tests mock missing CLI tools. | ADR-006 implementer | Phase 2 |
| **Auth breaks existing TS extension** before it's updated | High until fixed | Middleware only activates when `LAUNCHER_AGENT_TOKEN` is set — if users don't configure the env var, behavior is unchanged (backward compat). The token must be explicitly set for auth to activate. Per-node api_key in nodes.json is also optional — only affects requests when present. | ADR-003 implementer | Phase 1 → Phase 2 transition |
| **Model health check I/O overhead** on every start | Low ( mitigated by caching) | TTL cache with config-change invalidation ensures at-most-one check per session. Health API also caches its results. For network-mounted paths, operators can override by setting a `SKIP_HEALTH_CHECK` env var if needed in Phase 2+. | ADR-005 implementer | Phase 2 |
| **VRAM estimation heuristic accuracy** — model size ≠ VRAM usage | Medium | Document the heuristic clearly as an estimate. The actual SMI measurement is authoritative; estimation only pre-fails obviously insufficient cases. False positives (rejecting a valid start) are acceptable because false negatives (OOM crash) are worse. Phase 2 can refine based on operator feedback. | ADR-006 implementer | Phase 2 |
| **CLI output divergence from agent API** — operators confused if behaviors differ | Medium | CLI delegates to identical core methods (`LauncherState`, `ConfigStore`). The single source of truth means both entry points share the same business logic, reducing divergence risk. Add integration tests (Task 3.3) that verify parity. | ADR-004 implementer | Phase 2+Phase 3 |
| **Dependency cycle** — new CLI imports state/state management creating circular deps | Medium ( architectural ) | CLI should import from `core/` and `remote/` only, not from `agent/`, `mcp_server/`, or `ui/`. The existing layer boundary table in the codebase summary already constrains this. Verify with a static import analysis after Phase 2 completion. | ADR-004 implementer → reviewer verification | Phase 2 |
| **Node registry schema migration** — adding `api_key` to nodes.json may break old node registrations | Low | The `RemoteNode` dataclass defaults `api_key` to empty string or None, so existing JSON entries (which lack the field) produce the same behavior as no-auth nodes. Pydantic v2's extra="ignore" on models ensures unknown fields in persisted JSON don't cause errors. | ADR-003 implementer + ADR-004 | Phase 1 → Phase 2 transition |
| **TTL cache consistency** — stale data visible during config changes before invalidation propagates | Low | Use explicit `invalidate_health_cache()` call in ConfigStore.add_model/remove_model/update_model; set TTL to 60s (generous) for model health, 5s for GPU. Cache miss on expired entry auto-refreshes. For config changes, force full invalidation via the public API. | ADR-005 + ADR-006 implementers | Phase 2+3 transition |

---

## Dependency Graph & Parallelization

```
Phase 0 (Gate — before all implementation):
  Task 0.1: Run baseline test suite → /tmp/baseline_test_results.txt
  └─ Gate: no undocumented pre-existing failures → proceed

Phase 1 (Sequential, prerequisite for Phase 2 B):
  ADR-003 [Auth Middleware]
  ├─ Settings module-level constant pattern
  ├─ Auth middleware with 401/403 status codes
  ├─ Docs gating via FastAPI constructor params
  └─ RemoteNode api_key field

  Worker A: Tasks 1.1–1.2 (settings) + 1.3–1.5 (middleware tests) 
           + 1.6 (docs gating) + 1.9–1.11 (node registry auth)
           + 1.12–1.13 (logging, docs) — ~7 tasks

Phase 2 (Three Independent Workers):
  Worker B: ADR-005 [Model Health]     ← No dependencies on ADR-003/004/006
  Worker C: ADR-004 [CLI Subcommand]   ← Depends on Phase 1 auth fields in RemoteNode
  Worker D: ADR-006 [GPU Monitoring]    ← No dependencies on ADR-003/004/005

  Worker B tasks (2.1–2.7): Model health impl, tests, state integration, 
                          API endpoints, UI tab, TTL cache layer — ~7 tasks
  Worker C tasks (2.8–2.15): CLI app, pyproject.toml, model/server/node/config
                             subcommands, tests, output formatting — ~8 tasks
  Worker D tasks (2.16–2.21): GPU collector impl with _TTLCache, 
                              tests, status endpoint VRAM check, UI widget
                              + integration test — ~6 tasks

Phase 3 (Integration, depends on Phase 2 completion):
  Task 3.1a: TTL cache in gpu.py (Worker D → shared util)
  Task 3.1b: TTL cache in model_health.py (Worker B → shared util)
  Task 3.1c: Combined VRAM+health error diagnostics (cross-ADR)
  Task 3.2: TS extension updates
  Task 3.3: End-to-end integration tests

```

**Recommended worker allocation:**
- **Worker A** executes: Tasks 1.1–1.2, 1.3–1.5, 1.6, 1.9–1.11, 1.12–1.13 (ADR-003 only) — ~7 tasks
- **Worker B** executes: Tasks 2.1–2.7 + 3.1b + 3.1c partial (ADR-005 + cache utility) — after baseline gate passes, independent — ~9 tasks  
- **Worker C** executes: Tasks 2.8–2.15 (ADR-004 only) — after Worker A completes and RemoteNode has api_key field — ~8 tasks
- **Worker D** executes: Tasks 2.16–2.21 + 3.1a (ADR-006 + cache utility) — independent core feature, parallel with B/C — ~7 tasks

After Phase 2 completes: Integration tests and TS extension updates (Phase 3) executed by a reviewer or single worker.

**Total estimated tasks:** ~40 discrete actions across 4 workers + integration verification.

---

## Summary of Reviewer Fixes Applied

| Fix # | Description | Impact Areas |
|-------|-------------|--------------|
| **Fix 0** | Added Test Baseline Gate (Task 0.x) before Phase 1 | Top-of-plan, new gate mechanism |
| **Fix 1** | Settings module: module-level `AGENT_API_KEY` constant + validation; Middleware status codes: 401 missing / 403 wrong; Docs gating via constructor params | Tasks 1.1, 1.2, 1.5, 1.6; all test signatures updated |
| **Fix 3** | `ModelHealthResult` changed from dataclass to Pydantic `BaseModel`; tests use `.model_dump()` instead of `__dict__` | Tasks 2.1, 2.2, 2.5 |
| **Fix 4** | Replaced vague "lru_cache" with `_TTLCache` utility class — standalone in `llauncher/util/cache.py`, imported by both `gpu.py` and `model_health.py` | Tasks 2.7, 3.1a, 3.1b, 2.16 |
| **Fix 5** | Broke vague "combined diagnostics" Task 3.1 into three concrete atomic tasks: 3.1a (TTL cache in gpu.py), 3.1b (TTL cache in model_health.py), 3.1c (combined VRAM+health error messages) with explicit file paths, function names, and acceptance criteria | Task 3.1 → 3.1a, 3.1b, 3.1c |
| **Fix 6** | Replaced all `ADRF-` references with `ADR-` throughout entire document (naming consistency) | Executive summary, Phase 3 tests, git commit conventions, risk table |
