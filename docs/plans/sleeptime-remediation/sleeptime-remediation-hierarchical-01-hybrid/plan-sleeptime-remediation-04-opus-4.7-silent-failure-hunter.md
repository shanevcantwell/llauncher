# Remediation Plan: Opus 4.7 Silent-Failure Hunter — GPU & Model Health Error Handling

**Plan ID:** `PLAN-SILENTFAIL-001`  
**Author:** Strategic Planner (op. 4.7 silent-failure-hunter review synthesis)  
**Date:** 2026-04-26  
**Review Source:** `shanevcantwell` fork — GPU model-health audit across `/llauncher/core/gpu.py`, `/llauncher/agent/routing.py`, `/llauncher/core/model_health.py`, `/llauncher/util/cache.py`  
**Status:** PRE-MERGE BLOCKER (CRITICAL findings in path-to-launch)

---

## Executive Architecture Summary

This review identified **9 silent-failure defects** across **4 files**. The root pattern is uniform: bare `except Exception` blocks swallow exceptions that should be observable, causing downstream logic to make optimistic decisions ("GPU available" when it isn't, "model file OK" when metadata failed).

### Impact Chain (CRITICAL → HIGH → MEDIUM)

```
GPU query silently fails (gpu.py line 140-162)
  → backends == []
  → _check_vram_sufficient() returns (True, None)    # routing.py:78
  → VRAM pre-flight 409 gate never fires               # start-with-eviction:335-352
  → llama-server starts, OOM-crashes with no diagnostic   ← USER IMPACT

ROCm unbound `out` variable (gpu.py line 263-292)
  → UnboundLocalError
  → Caught by outer `except Exception: return False`     # swallowed root cause

NVIDIA driver-version silent failure (gpu.py lines 199-209)
  → Non-zero exit from text-based nvidia-smi
  → Driver version becomes None silently                 # no log, no degradation flag

/status endpoint silent failure (routing.py lines 177-183)
  → GPU collector import/execution exception swallowed
  → /status returns 200 with NO gpu field AND NO degraded flag   # invisible in production

stat() masking as "too small" (model_health.py line ~93-115)
  → OSError on path.stat() leaves size_bytes = None
  → `None or 0` → 0 < 1 MiB → reason="too small"         # WRONG DIAGNOSTIC for permission errors

Double-negation env var (gpu.py line 134)
  → `not os.environ.get("...") == ""` is a maintenance trap  # future refactor hazard

TTL cache None ambiguity + stampede (cache.py)
  → Cached None indistinguishable from cache miss            # repeated live calls under failure
  → Exception not cached → per-call retry storm              # no backoff under load

Redundant nested except in routing.py (lines 409-415)
  → Dead code, defensive copy-paste residue                 # noise only
```

### Severity Ordering & Rationale

| Priority | Issue # | Severity | Root Cause Category |
|----------|---------|----------|---------------------|
| P0 (block merge) | NVIDIA/ROCm/MPS bare `except` → VRAM gate bypassed | CRITICAL | Error swallowing in launch path |
| P0 (block merge) | ROCm unbound variable | HIGH | Unsound control flow |
| P1 | /status endpoint silent failure | HIGH | No observability for GPU subsystem |
| P1 | NVIDIA driver-version `except Exception: pass` | HIGH | Silent degradation of health data |
| P2 | stat() masking as "too small" | HIGH | Wrong diagnostic story to user |
| P3 | Redundant nested except (routing.py) | MEDIUM | Dead code / noise |
| P3 | Double-negation env var (gpu.py) | MEDIUM | Maintenance trap |
| P4 | TTL cache None ambiguity + stampede | MEDIUM | Caching semantics gap |

---

## Recommended Fix Approach Per File

### File 1: `llauncher/core/gpu.py` — The Core

**Architectural decision:** Introduce a structured error taxonomy and propagate it explicitly rather than silently returning `False`. Three parallel tracks:

#### Track A: Structured exception logging in `_try_NVIDIA`, `_try_ROCM`, `_try_MPS`
Replace every `except Exception: return False` with a typed handler that:
1. Logs at `warning` level (with chained original exception via `from exc`).
2. Returns the caught exception type name (string) so the caller can distinguish "backend not found" from "backend present but query failed."

#### Track B: Backend query methods expose degradation signals
Add an optional `_degradation_reason: str | None` field to `GPUHealthResult`. When a backend is absent (e.g., `nvidia-smi` not on PATH), this stays `None`. When a backend CLI exists but its query fails, set it to the exception class name. This enables downstream consumers (`_check_vram_sufficient`, `/status`) to see **both** "no GPUs" AND "GPU query failed."

#### Track C: ROCm two-block restructure
The current `_query_ROCM` has an awkward two-try/except pattern where `out` may be unbound in the second block. Restructure into a single `try/finally` or `with-contextmanager-style` guard so that the parsing logic only executes after we confirm `out` is bound and has `returncode == 0`.

#### Track D: NVIDIA driver-version graceful handling
The secondary nvidia-smi call for driver version should log at `debug` (not suppress silently). A non-zero return on the driver-version query alone is acceptable — it just means that field will be `None`. No need to elevate this to warning level; the primary query already reports device data.

### File 2: `llauncher/agent/routing.py` — The Router

**Architectural decision:** Every previously-swallowed exception in the routing layer must now either (a) log and include a degraded flag, or (b) be refactored into dead code that simply doesn't exist.

#### Track A: /status endpoint
Replace `except Exception: pass` with:
1. Log at `warning` level with the original exception chained.
2. Set `"gpu": None` and add `"gpu_degraded": True` to the response dict when the GPU query fails (distinct from `"backends": []` which means "no GPUs present").

#### Track B: VRAM pre-flight
When `backends == []` AND `_degradation_reason is not None`, pass this context through the 409 error so the user sees *"VRAM check unavailable — GPU query failed"* rather than silently proceeding. Update `_check_vram_sufficient` to accept an optional `degradation_reason` parameter and document its impact on decision-making.

#### Track C: Dead-code removal
Delete the redundant nested try/except block around the model health hint in `/start-with-eviction` (lines 409-415). The outer try already handles this; removing it eliminates dead code without changing behavior.

### File 3: `llauncher/core/model_health.py` — Model Health

**Architectural decision:** Distinguish "file metadata unavailable" from "file too small."

#### Track A: stat() OSError handling
Replace `except OSError: pass` with explicit logging at `debug` level and set a separate `stat_failed: bool` field on `ModelHealthResult`. When `stat_failed is True`, the size heuristic check should use reason `"metadata_unavailable"` instead of `"too small"` (which currently evaluates `None or 0 = 0`).

#### Track B: Backward compatibility
`ModelHealthResult.valid` still becomes `False` in this case, but the user-visible diagnostic changes from the misleading "too small" to the actionable "metadata_unavailable."

### File 4: `llauncher/util/cache.py` — The Cache Utility

**Architectural decision:** Introduce a sentinel pattern and an optional error-caching mode.

#### Track A: Sentinel value for "explicitly cached None"
Use `object()` as a class-level `_MISSING = object()` sentinel. When `get(key)` returns `None`, distinguish between cache miss (key absent or expired) and cached `None` payload via a separate method or return type. However, to avoid API changes: keep the public signature `get(key) -> object | None` but add documentation and implement an internal `_cache_has(key)` helper used internally by callers who need this distinction.

#### Track B: Per-key error caching option
Add a `set_error(self, key, exc, ttl_seconds=None)` method that caches the exception type and message under the same sentinel-key pattern (with a special prefix). The caller can then detect "this key was previously tried and failed" within the TTL window. This requires changes at call sites but not at the `_TTLCache` interface level for existing callers.

---

## Implementation Tasks — Decomposed Sub-Tasks

### Sub-Task 1: Foundation — Logging infrastructure (1 task, ~30 min)
**Owner:** Worker  
**Files:** `gpu.py`, `routing.py`, `model_health.py`  
**Description:** Ensure all three modules import and use the standard `logging` module with consistent logger names (`"llauncher.gpu"`, `"llauncher.routing"`, `"llauncher.model_health"`). Create a module-level `logger = logging.getLogger(__name__)` in each. No behavioral changes — just infra prep for the following tasks.

### Sub-Task 2: GPU.py — Bare except remediation (3 sub-tasks, ~45 min)
**Owner:** Worker  
**Files:** `gpu.py`  

#### 2a. `_try_NVIDIA`, `_try_ROCM`, `_try_MPS` — typed exception logging
Replace each bare `except Exception: return False` with:
```python
except Exception as exc:
    logger.warning("GPU backend query failed for %s: %s", backend_name, exc)
    result._degradation_reason = f"{type(exc).__name__}: {exc}"
    return False  # semantics unchanged — still returns bool
```

#### 2b. Add `_degradation_reason` field to `GPUHealthResult`
Add a new optional field to the dataclass:
```python
@dataclass
class GPUHealthResult:
    backends: list[str] = field(default_factory=list)
    devices: list[GPUDevice] = field(default_factory=list)
    _degradation_reason: str | None = None  # NEW
```

#### 2c. NVIDIA driver-version — replace `except Exception: pass` with logging
Change the secondary nvidia-smi call's except to log at debug level. This is a very low-impact change since it only affects one specific sub-query.

### Sub-Task 3: GPU.py — ROCm unbound variable fix (1 task, ~20 min)
**Owner:** Worker  
**Files:** `gpu.py`  

Restructure `_query_ROCM()` into a single try block that handles both subprocess execution AND parsing:
```python
def _query_ROCM(self) -> dict[str, Any]:
    result = {"devices": []}
    try:
        out = subprocess.run([...], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("rocm-smi executable not found or timed out")
        return result

    if out.returncode != 0:
        logger.debug("rocm-smi returned non-zero exit code %d", out.returncode)
        return result

    # Now `out` is definitely bound and we have valid output — parse safely.
    try:
        for line in out.stdout.splitlines():
            ...
    except Exception as exc:
        logger.warning("Failed to parse rocm-smi output: %s", exc)

    return result
```

### Sub-Task 4: GPU.py — Double-negation env var cleanup (1 task, ~5 min)
**Owner:** Worker  
**Files:** `gpu.py`  

Change line 134 from:
```python
simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""
```
To:
```python
simulated_output = bool(os.environ.get("LLAUNCHER_GPU_SIMULATE"))
```
or with an explicit docstring constant at module level.

### Sub-Task 5: Routing.py — /status degraded flag (2 tasks, ~30 min)
**Owner:** Worker  
**Files:** `routing.py`  

#### 5a. Add logging + degradation flag in /status endpoint
Replace the bare `except Exception: pass`:
```python
try:
    collector = GPUHealthCollector()
    gpu_health = collector.get_health()
    if gpu_health.get("backends"):
        response["gpu"] = gpu_health
    elif gpu_health.get("_degradation_reason"):
        logger.warning("GPU query failed: %s", gpu_health["_degradation_reason"])
        response.update({"gpu": None, "gpu_degraded": True})
except Exception as exc:
    logger.warning("/status GPU query failed: %s", exc)
    response["gpu_degraded"] = True  # visible indicator of degraded state
```

#### 5b. Remove redundant nested except in start-with-eviction
Delete the inner try/except block around `check_model_health` call inside `/start-with-eviction`. The outer `try/except Exception: pass` is sufficient for defensive purposes — but better yet, document why it's there and remove only if tests confirm no behavior change.

### Sub-Task 6: model_health.py — stat() diagnostic fix (2 tasks, ~30 min)
**Owner:** Worker  
**Files:** `model_health.py`  

#### 6a. Add `_stat_failed` field to `ModelHealthResult`
```python
class ModelHealthResult(BaseModel):
    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None
    _stat_failed: bool = False  # NEW — tracks metadata_read_failure separately from "too small"
```

#### 6b. Fix the stat() catch block and size heuristic
```python
try:
    stat_result = path.stat()
    result.size_bytes = stat_result.st_size
    result.last_modified = datetime.fromtimestamp(stat_result.st_mtime)
except OSError as exc:
    logger.warning("Could not stat %s: %s", path, exc)
    result._stat_failed = True  # NEW — records metadata failure

# Size heuristic — distinguish "can't read" from "too small"
if result._stat_failed or (result.size_bytes or 0) < _MIN_SIZE_BYTES:
    if result._stat_failed:
        result.reason = "metadata_unavailable"
    else:
        result.reason = "too small"
```

### Sub-Task 7: cache.py — Sentinel + error-caching support (2 tasks, ~30 min)
**Owner:** Worker  
**Files:** `util/cache.py`  

#### 7a. Add `_MISSING` sentinel class variable and optional public API notes
Add module-level sentinel and a docstring note about the ambiguity:
```python
class _TTLCache:
    """..."""
    _MISSING = object()

    def get(self, key) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        ...
```
Document in the docstring that `None` as a cached value and cache miss both produce `None` from `get()`.

#### 7b. (Future-phase) Add optional `set_error()` method with sentinel prefixing
Not required for this remediation cycle but flagged for follow-on. Document what the API would look like:
```python
_ERROR_PREFIX = "__error__"

def set_error(self, key: str, exc: Exception, ttl_seconds: int | None = None) -> None:
    """Cache a failed result so future calls within TTL avoid re-executing."""
    self.set(f"{self._ERROR_PREFIX}{key}", (type(exc).__name__, str(exc)), ttl_seconds=ttl_seconds)

def has_cached_error(self, key: str) -> tuple[bool, tuple[str | None, str | None]]:
    """Check if this key previously failed within its TTL."""
    entry = self._store.get(f"{self._ERROR_PREFIX}{key}")
    if entry is None:
        return False, (None, None)
    msg_type, msg_text = entry[0]
    return True, (msg_type, msg_text)
```

---

## Test Strategy

### Existing Tests That Need Updates

| Test File | Test(s) | Required Change | Reason |
|-----------|---------|-----------------|--------|
| `tests/unit/test_gpu_health.py` | `TestNoBackendReturnsEmpty.test_no_backend_returns_empty` | Add assertion for `_degradation_reason` field presence in result dict (when subprocess exceptions are mocked). | New dataclass field; must be present in serialized output. |
| `tests/unit/test_model_health.py` | `test_existing_valid_file`, `test_symlink_resolved`, `test_last_modified_populated_for_valid` | Verify `_stat_failed` is `False` for valid files. | New field assertion. |
| `tests/integration/test_adr_cross_cutting.py` | `TestGPUHealthWithStatus.test_gpu_health_collector_no_backend` | After adding `_degradation_reason`, verify the degradation flag propagates correctly when subprocess errors are mocked during `/status`. | New degraded behavior path. |

### New Tests — Required (8 tests)

| # | Test Name | Module / File | Scenario Covered |
|---|-----------|---------------|-----------------|
| N1 | `test_nvidia_bare_except_logs_warning` | `unit/test_gpu_health.py` | Mock `subprocess.run` to raise `CalledProcessError`; verify logger.warning was called (capture logs); verify `_degradation_reason` is set. |
| N2 | `test_rocm_unbound_variable_fixed` | `unit/test_gpu_health.py` | Mock first subprocess call in `_query_ROCM` to raise exception; verify no UnboundLocalError and graceful return of empty result with log. |
| N3 | `test_rocm_parse_error_logs_warning` | `unit/test_gpu_health.py` | Mock subprocess to succeed but produce malformed output (garbage stdout); verify parse error is logged, not silently swallowed. |
| N4 | `test_status_endpoint_with_gpu_query_failure` | `unit/test_agent.py` or new file | Mock GPUHealthCollector to raise exception in `/status`; verify response includes `"gpu_degraded": True` and log.warning fired. |
| N5 | `test_status_endpoint_no_backend_clean_response` | Integration (extend `test_adr_cross_cutting`) | When `backends == []` AND `_degradation_reason is None`, response should NOT include `"gpu_degraded"`. This tests the distinction between "no GPUs present" and "GPU query failed." |
| N6 | `test_stat_oserror_produces_metadata_unavailable_not_too_small` | `unit/test_model_health.py` | Create a file path where `path.is_file()` succeeds but `path.stat()` raises OSError (e.g., symlink to non-existent target that passes is_file due to race, or use os.chmod to deny stat access); verify reason is "metadata_unavailable". |
| N7 | `test_cache_get_none_vs_miss` | `unit/test_ttl_cache.py` | Verify documented ambiguity: both cache miss and cached None return the same value from `get()`. Assert that callers must use additional methods or check. Document as a known limitation in test. |
| N8 | `test_vram_check_propagates_degradation_reason` | Integration (`test_adr_cross_cutting`) | When GPU collector returns degraded reason, `_check_vram_sufficient` should return `(True, None)` but the degradation info should be visible for upstream consumers to log or warn. |

### Test Execution Notes

- All new tests must work in a CI environment without GPUs (mock subprocess calls).
- The `/status` endpoint test (N4) requires mocking both `GPUHealthCollector` and `get_state()`. Use the same pattern as existing `test_agent.py:TestStatusEndpoint`.
- For N6, an OSError on `path.stat()` after `is_file()` is hard to trigger in a controlled temp directory. Alternative approach: monkeypatch `Path.stat` directly with `patch.object(Path, 'stat', side_effect=OSError("nope"))`.

---

## Rollout Order — Recommended Commit Grouping

### Commits (5 atomic commits)

| Commit | Theme | Tasks Included | Files Modified | Risk |
|--------|-------|----------------|---------------|------|
| 1/5 | Logging infrastructure prep | Sub-Task 1 | gpu.py, routing.py, model_health.py | NONE — imports only |
| 2/5 | GPU bare except remediation + ROCm fix | Sub-Tasks 2a, 3 | gpu.py | LOW — backend query behavior identical for happy path; degradation field added (non-breaking) |
| 3/5 | Env var double-negation cleanup | Sub-Task 4 | gpu.py | NONE — pure readability improvement |
| 4/5 | Routing + model health diagnostics | Sub-Tasks 5a, 5b, 6a, 6b | routing.py, model_health.py | LOW — new fields on response dicts are additive; "too small" reason changes to more accurate "metadata_unavailable" but validity remains `False` so downstream gates unchanged |
| 5/5 | Cache sentinel + error-caching scaffolding | Sub-Task 7a (7b deferred) | util/cache.py | NONE — docstring addition only for Phase 1 |

### What to Defer to a Follow-On Cycle

- **Sub-Task 7b** (`set_error()` / `has_cached_error()`) is intentionally deferred. It requires call-site changes in both `gpu.py` and `model_health.py` to adopt the error-caching pattern. This adds meaningful surface area. Implement it when a real stampede incident motivates it, with its own review cycle.
- **The `_degradation_reason` → `/start-with-eviction` integration** (where VRAM pre-flight should actively refuse launch when GPU query failed) is a P0 enhancement but beyond this remediation's scope of "fix silent failures." The current behavior (warn + proceed to let llama-server fail naturally) is acceptable; the next phase can add an active refusal gate.

---

## Risk & Observability Strategy

### Runtime Observability After Fix

1. **Structured logging on GPU query failure** — every previously-swallowed exception now fires `logger.warning()` with `from exc` chaining. These appear in application logs and must be visible in any log aggregation system (promtail, fluentd, etc.). The message format includes the backend name and exception class/type for filtering.

2. **New response field: `"gpu_degraded"`** — when present in `/status` or `get_health()` output, indicates a GPU backend was expected but its query failed. This is machine-readable (a boolean flag) so upstream consumers (web UI, monitoring dashboards, alerting rules) can react to it independently of log parsing.

3. **New diagnostic string: `"metadata_unavailable"`** — in model health results when `stat()` fails. Alerting on this reason pattern allows detecting filesystem permission issues or dangling mounts distinct from missing/corrupt model files.

### Monitoring Recommendations (Post-Launch)

- Alert on `gpu_degraded: true` appearing in `/status` responses (detectable via synthetic health checks).
- Log-level alerting: fire a warning when the log aggregator sees ≥3 occurrences of "GPU backend query failed" within any 5-minute window.
- Model health dashboard filter for `"reason": "metadata_unavailable"` — this is an operational signal that needs filesystem investigation, not model file replacement.

### Failure Mode Analysis (Post-Fix)

| Scenario | Before Fix | After Fix | Remaining Risk |
|----------|-----------|-----------|----------------|
| nvidia-smi returns non-zero during driver reload | `backends=[]`, VRAM gate passes silently | Warning logged, but `_try_NVIDIA` still returns `False` → same empty backends behavior. Degradation reason set on result object but not surfaced to routing layer yet. | **Partial** — the query now logs, but downstream routing doesn't see `_degradation_reason` from this specific path (nvidia-smi failure within `_query_NVIDIA` itself). This is addressed by Sub-Task 2a's `result._degradation_reason = ...` being set on `result`, which then gets propagated through `get_health()` → `/status`. |
| ROCm CLI segfaults with signal | Signal causes subprocess to raise `CalledProcessError` or just return non-zero; either way now caught with logging. | Same handling — warning logged, empty result returned. | **Low** — signal crashes are typically one-off; the warning is visible. |
| stat() permission denied on inode | Wrongly reported as "too small" (0 bytes). | Reported as "metadata_unavailable." File still marked invalid (`valid=False`), so launch gate triggers, but with correct diagnostic. | **None** — functional behavior (refuse to launch) is unchanged; only the message improves. |
| TTL cache under heavy concurrent start requests | Every request re-hits live backend → GPU query storm + network/API pressure. | Not yet addressed (deferred to Sub-Task 7b). | **Medium** — stampede still possible until error-caching is implemented. |

---

## Tradeoffs & Alternatives Considered

### ADR: Exception Logging Level
| Option | Pros | Cons | Chosen? |
|--------|------|------|---------|
| `logger.warning()` for all GPU failures | High visibility, triggers alerts naturally | May be noisy in environments where GPUs are hot-plugged or drivers reload frequently | **Yes** — production-grade observability demands it. Can downgrade to INFO per-operaor-request later. |
| `logger.debug()` | Zero noise by default | Silent failures remain silent unless debug logging is explicitly enabled | No — defeats the purpose of this entire remediation. |

### ADR: `_degradation_reason` as Dataclass Field vs Return Value
| Option | Pros | Cons | Chosen? |
|--------|------|------|---------|
| New field on `GPUHealthResult` dataclass | Transparent propagation to all callers (`get_health()` → `/status`); backward-compatible (optional with default) | Adds one more key to the response dict that consumers need to be aware of | **Yes** — cleanest path. |
| Separate return tuple `(result, degradation)` | Explicit API contract | Breaks existing callers' destructuring patterns; requires docstring migration | No — too disruptive for a remediation-level change. |

### ADR: Cache Sentinel Pattern (None Ambiguity)
| Option | Pros | Cons | Chosen? |
|--------|------|------|---------|
| Return `(value, is_hit)` tuple from `get()` | Unambiguous semantic meaning | **API break** — all callers must change destructuring pattern. Even though the method is `object | None` return, changing it to `tuple[object, bool]` is a breaking signature change. | No for Phase 1. Defer to follow-on ADR cycle where it can be batched with other cache API improvements (e.g., from Plan-02). |
| Add `has(key)` method alongside `get()` | Non-breaking addition; callers opt in | Slightly awkward mental model: two methods to check existence. Call sites must choose which to use. | **Partial** — document the limitation now, implement `has()` only if needed before 7b lands. |

### ADR: stat() Failure Handling
| Option | Pros | Cons | Chosen? |
|--------|------|------|---------|
| Set `_stat_failed=True`, reason="metadata_unavailable" | Actionable diagnostic; user can investigate filesystem permissions | Adds a new field to the Pydantic model (backward-compatible for serialization) | **Yes** — correct diagnosis matters more than field count. |
| Treat stat() failure as "unreadable" and reuse existing code path | Minimal change, reuses `result.reason = "unreadable"` | Semantically incorrect — the file may be readable, we just can't stat it. Different root cause requires different resolution (chmod vs. replace model). | No — conflates distinct error classes. |

---

## Appendix: Diff Summary by File

### gpu.py
- Lines 134: double-negation env var → cleaned up
- Lines ~140-162: three `except Exception` blocks → typed catch with warning logging + `_degradation_reason` assignment
- Lines ~199-209: NVIDIA driver-version secondary query's bare except → debug-level log
- Lines 263-292: ROCm two-block pattern → single try block with explicit `out` binding guarantee

### routing.py
- Lines ~177-183: /status GPU exception handler → warning log + `"gpu_degraded"` flag in response
- Lines ~409-415: redundant nested except block → removed (dead code)
- `_check_vram_sufficient()`: no functional change (still returns `(True, None)` when no backends), but the new `_degradation_reason` on `GPUHealthResult` is now visible in the response dict for downstream consumers

### model_health.py
- Lines ~93-115: stat() OSError → warning log + `_stat_failed=True`; size heuristic uses `"metadata_unavailable"` instead of incorrectly setting `"too small"`

### util/cache.py
- Class-level `_MISSING = object()` sentinel documented
- Docstring note about `None` ambiguity from `get()`
- `set_error()` / `has_cached_error()` method signatures as ADR placeholder (not implemented yet)
