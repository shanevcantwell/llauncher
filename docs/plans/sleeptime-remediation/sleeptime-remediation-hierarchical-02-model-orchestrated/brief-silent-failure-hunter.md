# Brief: Silent Failure Remediation (Worker)

**Source Review:** plan-sleeptime-remediation-00-review-opus-4.7-complete-review.md, silent-failure-hunter agent output  
**Related Coordinators:** brief-python-reviewer.md (error handling overlaps), brief-pr-test-analyzer.md (untested VRAM/gap behaviors)  
**Scope Limitation:** Only modify files under `/home/node/github/llauncher/`. No filesystem edits outside the codebase.

---

## Objective

Hunt and eliminate all silent failure patterns in GPU health collection, model file health checks, TTL caching, and pre-flight validation. Focus on **error-path observability**: when something goes wrong, the system must either (a) report it clearly via logging/response body or (b) fail safely rather than returning "all good" when it doesn't know.

---

## Coordination Notes with Other Briefs

| Silent-Failure Issue | Coordinated With | Responsibility |
|----------------------|------------------|----------------|
| C1: Bare except in GPU backends (`gpu.py`) | Python Reviewer H3 — **Silent-failure-hunter owns the implementation** for error-specific catches; python-reviewer handles non-error cleanup (dead params, import placement) | This brief defines which exceptions to catch per backend and writes the scoped handlers. python-reviewer removes any conflicting changes to those same lines. |
| C2: ROCm UnboundLocalError cascade (`gpu.py:263-292`) | Python Reviewer H4 (different issue in MPS) — **Silent-failure-hunter owns this fix** | Restructure try/except order and initialize `out` variable. python-reviewer handles dead loop cleanup.
| T2/C5: TTL Cache Lock + sentinel + invalidate() | Python Reviewer H1 — **CONSOLIDATED: Silent-failure-hunter owns the complete cache fix per strategic-planner finding #7** | Add threading.Lock (H1), implement _MISSING sentinel for None/miss distinction, add public invalidate(key) method. python-reviewer brief updated to NOT implement Lock/invalidate — it only verifies Lock exists in acceptance criteria. |
| C3: `/status` silent GPU drop (`routing.py:177-183`) | Independent — **Silent-failure-hunter owns** | Add degraded flag to response. |
| T2: TTL cache error poisoning | Python Reviewer H1 — **CONSOLIDATED per strategic-planner finding #7: Silent-failure-hunter owns all cache changes** | Add Lock, _MISSING sentinel, public invalidate(). python-reviewer brief updated. |

---

## Fixed Issue List

### CRITICAL — GPU Backend Error Swallowing (C1)

**Files:** `core/gpu.py` lines 141-142 (_try_NVIDIA), 151-152 (_try_ROCM), 161-162 (_try_MPS)  
**Current pattern:**
```python
def _query_NVIDIA(self):
    ...
    try:
        out = subprocess.run([...], capture_output=True, text=True)
    except Exception:  # ← swallows PermissionError, CalledProcessError, etc.
        return []
    
    # If exception fired here, downstream code sees [] and reports "no GPU"
```

**Fix:** Replace bare `except Exception:` with scoped catches in each backend query method:
```python
# In _query_NVIDIA (and similarly for _query_ROCM, _query_MPS):
try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        logging.debug("subprocess returned non-zero (%d): %s", out.returncode, out.stderr.strip())
        return []
except PermissionError as e:
    logging.warning("Permission denied running GPU query tool — check file permissions on binary")
    self.backend_available = False
    return []
except subprocess.TimeoutExpired:
    logging.warning("GPU query timed out — backend may be unresponsive")
    self.backend_available = False
    return []
except FileNotFoundError as e:
    # Tool not installed — this is the "no GPU" case, expected behavior
    logging.debug("GPU tool not found (%s): backend unavailable", cmd[0])
    self.backend_available = False
    return []
except json.JSONDecodeError as e:
    logging.warning("GPU query returned malformed JSON — output may have changed: %s", str(e))
    self.backend_available = True  # tool runs, but output format unknown
    return []
except Exception as e:
    logging.debug("Unexpected error in GPU query: %s", str(e))
    self.backend_available = False
    return []
```

**Key semantics change:** `self.backend_available` is set per-backend so that `_check_vram_sufficient()` can distinguish "no GPUs detected" from "all backends failed with errors." When any backend sets `backend_available = False`, the VRAM pre-flight must NOT silently allow the launch.

**Impact on routing.py:** After this fix, if `backends == []` AND at least one had an error flag, `_check_vram_sufficient()` should return `(False, "GPU backends unavailable — cannot verify VRAM")` rather than `(True, None)`. This is a logic correction in `agent/routing.py` lines 73-74.

### HIGH — ROCm UnboundLocalError Cascade (C2)

**File:** `core/gpu.py` lines 263-292  
**Current pattern:**
```python
try:
    out = subprocess.run(...)  # line 265
except Exception:
    pass                       # line 270 — exception swallowed, out unbound

# line 280+: out.stdout is referenced — UnboundLocalError if except path taken
# This error is then caught by outer bare except in _try_ROCM → silent empty return
```

**Fix:** Restructure the method so `out` is always initialized:
```python
def _query_ROCM(self):
    out = None
    try:
        result = subprocess.run(...)  # line ~265
        if result.returncode != 0:
            logging.debug("rocm-smi returned %d", result.returncode)
            return []
        # Now safe to reference result.stdout
    except FileNotFoundError:
        logging.debug("rocm-smi not found")
        self.backend_available = False
        return []
    except Exception as e:
        logging.debug("ROCm query error: %s", str(e))
        self.backend_available = False
        return []
    
    # Parse result.stdout — only reachable after successful execution
```

### HIGH — /status Endpoint Silent GPU Drop (C3)

**File:** `agent/routing.py` lines 177-183  
**Current code:**
```python
try:
    gpu_data = self.collector.collect()
except Exception:
    pass  # ← silently drops entire GPU field from response
# Response returns 200 with no "gpu" key — caller assumes no GPUs, not that collection failed
```

**Fix:** Add degraded flag and diagnostic to the status response:
```python
try:
    gpu_data = self.collector.collect()
except Exception as e:
    logging.debug("GPU health collector failed: %s", str(e))
    gpu_data = None
    status_response["gpu"] = {"degraded": True, "error": str(type(e).__name__)}
else:
    if gpu_data and "backends" in gpu_data:
        for backend_status in gpu_data.get("backends", []):
            if not backend_status.get("available"):
                status_response["gpu"]["degraded"] = True
    else:
        status_response["gpu"] = {"degraded": False, "note": "No GPU hardware detected"}
```

### HIGH — Model Health stat() Failures Misreported (C4)

**File:** `core/model_health.py` lines 90-95  
**Current pattern:**
```python
try:
    stat = os.stat(model_path)
except OSError:
    pass  # size_bytes stays None → at line 113, (None or 0) < 1MB → "too small" 
         # instead of the true cause: metadata unreadable
```

**Fix:** Set `result.size_bytes = -1` on stat failure (negative value is distinct from any valid size). Then update the size check:
```python
if result.size_bytes and result.size_bytes < _MIN_SIZE_BYTES:
    result.reason = "too small"
elif result.size_bytes == -1:
    result.reason = "file metadata unavailable — check permissions"
```

### MEDIUM — TTL Cache Stampede / Error Behavior (C5)

**File:** `util/cache.py`  
**Issue:** Two sub-issues:
1. `_TTLCache.get()` returns `None` for both cache-miss and cached-None-payload cases. Currently indistinguishable.
2. If the underlying expensive call raises, nothing is cached — every concurrent call hits the live backend with no backoff.

**Fix:** This brief handles issue #1; thread-safety (#2: Lock) is owned by `brief-python-reviewer.md` H1.

```python
class _TTLCache:
    _MISSING = object()  # sentinel — never confused with cached None
    
    def get(self, key):
        with self._lock:  # coordinated with python-reviewer's Lock addition
            entry = self._store.get(key)
        if entry is None or entry is self._MISSING:
            return None
        if time.monotonic() > entry["expiry"]:
            with self._lock:
                del self._store[key]  # removed via lock
            return None
        return entry["value"]
    
    def set(self, key, value, ttl):
        sentinel = self._MISSING if value is None else True  # mark for special handling
        with self._lock:
            self._store[key] = {"value": value, "expiry": time.monotonic() + ttl}
```

**Note:** The `set()` signature must be unchanged — callers should not know about the sentinel. The `_MISSING` sentinel is an internal implementation detail. When caching a callable result, callers wrap in:
```python
try:
    cache.set(key, func(), ttl)
except Exception as e:
    # Don't cache failures — but log them so operator can see recurring errors
    logging.debug("Cache set failed for %s: %s", key, str(e))
    raise
```

### MEDIUM — VRAM Pre-flight Diagnostic Drop (C6)

**File:** `agent/routing.py` lines 409-415  
**Current code:**
```python
try:
    health = check_model_health(model_path)
except Exception:
    pass  # If model path is None or health check throws, diagnostic hint dropped silently
```

**Fix:** Always log the exception. Even if we can't produce a good error message for the user, operators need visibility:
```python
try:
    health = check_model_health(model_path)
except Exception as e:
    logging.debug("VRAM pre-flight model health check failed: %s", str(e))
    # Continue without the hint — but at least it's logged
```

---

## Verification Requirements (Issue-Tag Checklist per Strategic-Planner Finding #11)

### Specific checks for each issue tag:
| Tag | Verification Method |
|-----|--------------------|
| C1 | `grep -c 'except Exception:' core/gpu.py` — must be 0. Each except block catches scoped types (PermissionError, TimeoutExpired, FileNotFoundError, JSONDecodeError) AND logs the exception message. Run: `pytest tests/unit/test_gpu_health.py -v` |
| C2 | `grep -A15 'def _query_ROCM' core/gpu.py` — verify `out = None` initialized before any try block; verify no UnboundLocalError path exists |
| C3 | Start server without GPU, make GET /status → response body must contain `{"gpu": {"degraded": True, "error": ...}}` or `{"gpu": {"degraded": False, "note": ...}}`, NOT just empty object |
| C4 | `grep -A5 'except OSError' core/model_health.py` — verify size_bytes set to -1 on stat failure (not left as None) |
| T2/C5 | `grep '_MISSING\|self._lock\|invalidate' util/cache.py` — all three present; `_TTLCache.get()` returns None for miss, NOT cached-None distinction lost. Run: `pytest tests/unit/test_ttl_cache.py -v` |
| C6 | `grep 'check_model_health' agent/routing.py` — verify exception handler has logging.debug, not bare pass |

### Post-change verification:
1. Full grep sweep for remaining bare except blocks: `grep -rn "except:" core/ agent/ | grep -v 'except:' | head -50` (all results must contain specific exception types)
2. Every modified file must have at least one test exercising its error paths after Phase C re-execution
3. No new silent-failure patterns introduced in the process of fixing existing ones
