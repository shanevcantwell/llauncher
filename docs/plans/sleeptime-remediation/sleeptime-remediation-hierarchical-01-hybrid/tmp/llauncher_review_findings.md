# Implementation Plan Review — llauncher ADRs 003–006

**Date:** 2026-04-26  
**Reviewer:** Code Review Subagent  
**Plan under review:** `/tmp/llauncher_implementation_plan.md`  

---

## Question 1: Auth Middleware Wiring

### Verdict: CONCERN

#### Issue 1.1 — Settings module doesn't use Pydantic (critical architecture mismatch)

The plan's Task 1.1 assumes an `AppSettings` dataclass with Pydantic validation:
```python
AGENT_API_KEY: str | None = Field(default=None, env="LAUNCHER_AGENT_TOKEN")
```
**Reality:** `/home/node/github/llauncher/llauncher/core/settings.py` is **pure module-level constants** — no classes, no Pydantic, no `Field()`. It reads env vars directly into `LLAMA_SERVER_PATH`, `DEFAULT_PORT`, etc. There is no `AppSettings` class to extend.

**Impact:** The entire Task 1.1 implementation will fail as described. A new settings infrastructure must be created before middleware can read the API key programmatically.

**Suggested fix:** Choose one of:
- (a) Create a proper Pydantic `AppSettings(BaseModel, env_file=".env")` class and migrate all existing module-level constants into it (larger refactor but future-proof).
- (b) Define `AGENT_API_KEY` as a simple module-level constant in `core/settings.py` alongside the existing pattern (`LAUNCHER_AGENT_TOKEN = os.getenv("...")`) — faster, less refactoring risk. Task 1.2's middleware reads from this single source.

#### Issue 1.2 — Middleware must handle `/health` explicitly or via route patterns

**Reality:** `agent/routing.py` mounts a single `APIRouter()` into the FastAPI app at **root path** (no `prefix`). All routes are defined as:
- `@router.get("/health")`
- `@router.get("/status")`  
- `@router.post("/start/{model_name}")`
- `@router.get("/logs/{port}")`

The plan says middleware should skip auth for `/health`, `/docs`, `/openapi.json`. This is technically correct — the FastAPI `BaseHTTPMiddleware.dispatch()` will intercept all requests including `/health`. However, **the test in Task 1.2 (test file) creates a synthetic app where `/health` and `/start/foo` are registered as regular routes** rather than using actual route objects. The middleware must be tested against the real `router.include_router(router)` from `server.py`, not a mock app.

**Additional edge case:** The plan doesn't address what happens when auth is **active but** the client sends an empty string `X-Api-Key: ""`. Per ADR-003, this should be rejected (403), and the plan's test does check for it — good. But the middleware must distinguish between "header absent" → 401/403 vs. "wrong header" → also 403. Current spec doesn't specify status code difference.

**Recommendation:** Use consistent HTTP status codes:
- Missing `X-Api-Key` header when token is configured → **401 Unauthorized** (authentication required)
- Present but wrong key → **403 Forbidden** (credentials provided, access denied)  
- When token not configured → 200 OK for all

#### Issue 1.3 — `/redoc` URL configuration conflicts with disable approach

Task 1.6.3 says to remove `/docs` and `/redoc` routes when a token is set. However, `agent/server.py:72-74` explicitly sets both in FastAPI construction:
```python
FastAPI(
    ...,
    docs_url="/docs",
    redoc_url="/redoc",
)
```
To disable them conditionally at runtime, the middleware must filter path `/docs/*` and `/redoc/*`, OR the plan should make `openapi_url` conditional during `create_app()`. The current approach of "remove routes after mounting" is not how FastAPI works — you'd need to either:
- Conditionally set `openapi_url=None, docs_url=None, redoc_url=None` when token is configured (in `create_app()`), or
- Add path-based exclusion in the middleware (`request.url.path.startswith("/docs")`)

**Suggested fix:** In Task 1.4's `create_app()`, read settings first, then conditionally set:
```python
app = FastAPI(
    ...,
    docs_url="/docs" if not token else None,
    redoc_url="/redoc" if not token else None,
)
```

---

## Question 2: CLI Circular Dependencies

### Verdict: PASS (with minor caveats)

#### Import chain analysis — no cycles detected

The existing module dependency graph is clean and unidirectional:

```
models/config.py      → (stdlib + pydantic only) ← LEAF MODULE
core/settings.py      → (stdlib only)               ← LEAF MODULE
core/config.py        → models/config.py             ← depends on leaf
core/process.py       → core/settings.py, models/config.py  ← depends on leaves
state.py              → core/{config, process}, models/config.py
remote/node.py        → httpx + models/config.py     ← uses ModelConfig type hint only for docstrings / import but doesn't call it in constructor
agent/routing.py      → state.py                     ← top-layer consumer
agent/server.py       → agent/routing.py, uvicorn    ← entry point

UI layer              → state.py (via imports in tabs)
mcp_server/           → state.py (via tools/)
```

The proposed `cli.py` would import from:
- **core/** (ConfigStore, LauncherState) — safe, these are leaf/skinny modules
- **remote/** (NodeRegistry, RemoteNode) — safe, depends on httpx and models/config
- NOT agent/ or mcp_server/ — good, per the risk assessment table

**No circular dependency is possible** because:
1. `core/` → only stdlib + pydantic + internal imports (no back-references to `cli.py`)
2. `remote/` → httpx + models/config + itself (no back-references)
3. `models/config.py` → only Pydantic + stdlib (leaf module, universally importable)

#### Caveat: RemoteNode doesn't actually use ModelConfig in any functional code path

Looking at `remote/node.py`, the line `from llauncher.models.config import ModelConfig` is imported but never used. This is a dead import that could cause confusion but won't create a cycle. The reviewer should note this as cleanup opportunity, not a blocker.

#### Caveat: cli.py must avoid importing from ui/ or mcp_server/ at module level

Typer's CliRunner test approach (used in Task 2.14) runs `llauncher.cli` directly without a running server. If `cli.py` imports anything that triggers UI initialization or MCP tool registration, the CLI will fail to load. The plan correctly delegates local operations through `LauncherState`, but ensure no transitive import pulls in Streamlit.

---

## Question 3: ModelHealthResult Pydantic Compatibility

### Verdict: CONCERN

#### Issue 3.1 — Dataclass vs BaseModel mismatch with existing patterns

The plan defines `ModelHealthResult` as a **dataclass** (Task 2.1):
```python
@dataclass
class ModelHealthResult:
    valid: bool
    reason: str | None
    size_bytes: int | None
    exists: bool
    readable: bool
    last_modified: datetime | None
```

However, the existing codebase's Pydantic layer (`models/config.py`) uses **BaseModel** with rich features: `Field()` constraints, validators, `.to_dict()`, `model_validate()`. The ADR-005 spec says the health check response shape is JSON-oriented. A dataclass is functionally fine but creates an inconsistency — all other data shapes in this project are Pydantic BaseModel subclasses.

**Suggested fix:** Define as:
```python
from pydantic import BaseModel, Field

class ModelHealthResult(BaseModel):
    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = None
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None
    
    def to_dict(self) -> dict:
        return self.model_dump()
```

This maintains consistency with the existing `AuditEntry`, `ModelConfig`, and `RunningServer` patterns, and provides automatic JSON serialization for the API response (Task 2.4).

#### Issue 3.2 — No Pydantic Field constraints on ModelHealthResult fields

If defined as a dataclass, there are no built-in validators. The plan's test file (`test_model_health.py`) has assertions like `assert result.valid is True` which will work but provides zero type-level guarantees. If converted to BaseModel, you could add:
```python
size_bytes: int | None = Field(default=None, ge=0)  # reject negative sizes
valid: bool = Field(default=False)                    # default-safe pattern
```

---

## Question 4: Test Baseline Gap

### Verdict: CONCERN

#### Issue 4.1 — No explicit "run all existing tests BEFORE workers start" task

The plan states in the integration testing strategy section:
> `pytest tests/ -v --tb=short`  
> Target: all existing + new tests pass, no regression on **86 already-passing tests**

However, this appears only as a **post-completion verification step**. There is no discrete task with its own acceptance criterion that says:
- "Run `pytest tests/ -v --tb=short` against the main branch before any changes"
- "Record baseline pass/fail count"
- "If baseline has failures, document them and do NOT fix them in this plan's scope"

**Why this matters:** If 5 of those 86 tests are already flaky or failing, workers may start by fixing unrelated code that happens to trigger these flakes, leading to wasted effort. Conversely, if the count is wrong (actual test functions found: **453**, across 24 files), the regression target is undefined.

**Suggested fix — Add as Task 0:**

| # | Type | File Path | Description | Acceptance Criteria |
|---|------|-----------|-------------|---------------------|
| 0.1 | verify | `tests/` | Run full test suite against the current main branch **before any workers begin**. Capture output to `/tmp/baseline_test_results.txt`. Record pass count, fail count, and skipped count. | Baseline result saved; no regressions introduced during implementation; if baseline has failures, they are documented separately |
| 0.2 | verify | `tests/` | Verify no new test files are missing a proper import of pytest fixtures or conftest dependencies (quick audit). | No "ImportError" or "conftest.py not found" errors in any test file |

#### Issue 4.2 — The plan mentions "86 tests" but actual count differs

Running `grep -r "def test_" tests/ --include="*.py"` returns **453 test functions** across 24 files. Either:
- The plan author used a different counting methodology (e.g., only unit tests, or only standalone class-based tests), or  
- The number is stale from an earlier codebase state

**Suggested fix:** Replace "86 already-passing tests" with "the full existing test suite" and have Task 0.1 determine the actual count dynamically (`pytest --collect-only -q | tail -1`).

---

## Additional Observations

### A. GPU health check: `functools.lru_cache` won't work for TTL-based caching (Task 2.16)

The plan says to cache with "5s TTL using a module-level `functools.lru_cache`-equivalent." However, `functools.lru_cache` has **no built-in TTL** — it's a hard-entry-count LRU with no time-based expiration. For the described use case (time-bound SMI caching), you need either:
- A custom wrapper that tracks call timestamps and invalidates on expiry, or  
- Python 3.9+ `functools.cache` + manual decorator with TTL logic

This is a **defect in Task 2.16's spec** — the implementer will likely build something fragile or incorrect if told to use `lru_cache`. Suggest specifying a concrete implementation approach, e.g.:
```python
from functools import wraps
import time

class _TTLCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._timestamps = {}
        self.ttl = ttl_seconds
    
    def get(self, key):
        if key in self._cache and (time.time() - self._timestamps[key]) < self.ttl:
            return self._cache[key]
        return None
    
    def set(self, key, value):
        self._cache[key] = value
        self._timestamps[key] = time.time()
```

### B. Cross-ADR integration in Phase 3 is vague (Task 3.1)

"When a model start is blocked by VRAM... the error message should also include health status from model cache validation." This requires changes to both `core/model_health.py` and `core/gpu.py`, plus `state.py`'s `_start_with_eviction_impl`. No code, no file path change, no acceptance criterion. This is a **wish statement** that will never be picked up by workers because it lacks atomic task structure.

### C. Plan uses inconsistent ADR reference naming

In the integration section, references switch between `ADR-003`, `ADRF-003` (with "F" suffix). The F-suffix doesn't match any documented ADR numbering convention and will confuse workers. Use consistent reference format throughout.

---

## Summary of Findings

| Question | Verdict | Severity |
|----------|---------|----------|
| 1. Auth middleware wiring | CONCERN | Medium-High (settings module mismatch is a real blocker) |
| 2. CLI circular deps | PASS | Low — minor dead-import cleanup needed in remote/node.py |
| 3. ModelHealthResult compatibility | CONCERN | Medium (dataclass vs BaseModel inconsistency; API response serialization will be manual) |
| 4. Test baseline gap | CONCERN | Medium (no explicit pre-flight test gate = regression risk) |

---

## Overall Recommendation: **APPROVED WITH MINOR FIXES**

The plan is architecturally sound overall — module boundaries are respected, the dependency graph shows genuine parallelization potential, and the sequential ordering of ADR-003 before 004/005/006 is correct. The four CONCERN items above are fixable within the existing planning phase without requiring re-scoping:

1. **Fix Task 1.1** to match the actual `core/settings.py` architecture (simple module-level constant approach recommended for speed)
2. **Add Task 0** (test baseline gate) before any worker is dispatched  
3. **Change ModelHealthResult from dataclass → BaseModel** for API serialization consistency
4. **Replace `lru_cache` reference in Task 2.16** with a TTL-aware caching implementation spec

These fixes are additive and non-disruptive. Once applied, the plan can be released to workers. The Phase 3 cross-ADR integration (Task 3.1) should either be broken into atomic sub-tasks or deferred — it adds complexity without clear benefit beyond individual ADR error messages.
