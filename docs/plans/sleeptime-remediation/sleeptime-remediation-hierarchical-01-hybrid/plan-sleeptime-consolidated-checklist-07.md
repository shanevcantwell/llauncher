# Sleeptime Remediation — Consolidated Per-File Action Checklist (Plan 07)

**Status:** READY FOR IMPLEMENTER  
**Author:** Technical Co-Pilot (source synthesis of Plans 01–06)  
**Date:** 2026-04-26  
**Parent Plans:**  
| Plan | Author | Focus |
|------|--------|-------|
| P01 | Opus 4.7 Code Explorer | Test count audit, conftest fixture evaluation, behavioral-change documentation |
| P02 | Opus 4.7 Python Reviewer | Thread-safety, security masking, GPU overhaul, CLI cleanup, routing lock, datetime fix |
| P03 | Opus 4.7 Security Reviewer | hmac.compare_digest(), openapi_url suppression, file permission hardening, to_dict() masking, ADR doc corrections |
| P04 | Opus 4.7 Silent-Failure Hunter | GPU bare-except logging, ROCm restructure, /status degraded flag, stat() diagnostic fix, cache sentinel |
| P05 | Opus 4.7 PR Test Analyzer | gpu.py bug fixes (ROCM/MPS/simulate), test remediation, missing coverage, integration test rewrite |
| P06 | Opus 4.7 Architect | ADR-003/004/005/006 rewrites — alternatives analysis, factual corrections, cross-references |

## Project Lead Resolution Directives (DO NOT CHANGE)
1. **`node.py::to_dict()`**: Use `"has_api_key": self.api_key is not None` — **boolean**, NOT masked `"***"`
2. **`registry.py::_save()`**: Keep plaintext `api_key` in file BUT add `os.chmod(NODES_FILE, 0o600)` — file permissions are the security mechanism
3. **`gpu.py::simulated_output`**: Use explicit whitelist (`sim_val in ("1", "true", "yes", "on")`) — NOT bare `bool()`

## Key Resolution Notes
- Per P02 Phase 2, `to_dict()` was originally planned to mask as `"***"`, but the project lead has **overridden this to Directive #1** (boolean presence flag). This aligns with P03 Finding #5.
- Per P03 Security Reviewer, `_save()` writes plaintext keys and uses `chmod 0o600` — confirmed aligned with Project Lead Directive #2. Both P02 and P03 agree on this approach.
- For the simulate flag, Directive #3 says use explicit whitelist. P04 suggested `bool(env)` which is too broad; Directive #3 supersedes.

---

## How to Read This Document (Condensed)
Each file section contains:
- **Attribution tags:** `[P02]` = Plan 02 proposes a change to this file
- **Priority tiers:** Foundation → Core Fix → Cleanup/Polish — ordered for correct sequence
- **Step numbers:** Sequential within each tier (1.1, 1.2)
- **Risk:** LOW / MEDIUM / HIGH / CRITICAL per edit
- **Dependencies:** Links to other steps in the same file that must precede this step

Each file section contains:
- **Attribution tags:** `[P02]` means Plan 02 proposes a change to this file
- **Priority tiers:** Foundation → Core Fix → Cleanup/Polish — ordered for correct implementation sequence  
- **Step numbers:** Sequential within each tier (1.1, 1.2, etc.)
- **Risk:** LOW / MEDIUM / HIGH / CRITICAL per edit
- **Dependencies:** Links to other steps in the same file that must precede this step

---

---

## `llauncher/util/cache.py` [P01, P02, P04] — Thread-Safe Cache + Invalidate API

### Priority 1 (Foundation)

#### Step 1.1: Add imports and class docstring
- **Source:** `[P02 §1.1]`, `[P04 §7a]`
- **Original code:** Lines 1–8 — only `import time`; no class docstring
- **Target replacement:**
```python
from __future__ import annotations

import threading
import time


class _TTLCache:
    """Simple in-memory TTL-aware dictionary cache. Thread-safe via a single lock."""
    
    # Sentinel for documentation purposes — callers must not use this directly.
    # Note: get(key) returning None is ambiguous (cache miss vs. cached None).
    # Callers that need to distinguish these cases should use internal 
    # _store key presence checks or add a has() method in a future phase.
```
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Add `_lock` member in `__init__`
- **Source:** `[P02 §1.1]`
- **Original code:** `self._store = {}` — no lock member
- **Target replacement:** 
```python
    def __init__(self, ttl_seconds: int = 5):
        self._ttl = ttl_seconds
        self._store: dict[str | object, tuple[object, float]] = {}
        self._lock = threading.Lock()     # NEW — guards all mutations
```
- **Risk:** LOW
- **Depends on:** Step 1.1

#### Step 1.3: Wrap `get()` with lock + lazy eviction under lock
- **Source:** `[P02 §1.1]`
- **Original code (lines ~14–26):** Unlocked dict read, timeout check, and `del self._store[key]` outside any synchronization
- **Target replacement:**
```python
    def get(self, key) -> object | None:
        with self._lock:                  # GUARD — locks read + lazy eviction
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]       # Eviction under lock
                return None
            return value
```
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.4: Wrap `set()` with lock
- **Source:** `[P02 §1.1]`
- **Original code (lines ~28–30):** Unlocked `self._store[key] = ...` write
- **Target replacement:**
```python
    def set(self, key, value, ttl_seconds: int | None = None) -> None:
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        with self._lock:                  # GUARD — locks write
            self._store[key] = (value, time.monotonic() + effective_ttl)
```
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.5: Wrap `invalidate_all()` with lock
- **Source:** `[P02 §1.1]`
- **Original code (lines ~32–33):** Unlocked `self._store.clear()`
- **Target replacement:**
```python
    def invalidate_all(self) -> None:
        with self._lock:                  # GUARD — locks clear
            self._store.clear()
```
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.6: Add new public `invalidate(key)` method
- **Source:** `[P02 §1.1]`
- **Original code:** No such method exists
- **Target replacement (append after `invalidate_all`):**
```python
    def invalidate(self, key) -> bool:
        """Remove a single cached entry. Returns True if the key existed."""
        with self._lock:
            return self._store.pop(key, None) is not None
```
- **Risk:** LOW
- **Depends on:** Step 1.2
- **Critical for:** `model_health.py` Step 3.4 (replaces `_health_cache._store.pop`)

### Priority 2 (Test Impact — P01 scope, handled separately)

#### Step 2.1: Update existing tests to pass with locking
- **Source:** `[P02 §1.3]`, `[P01 P0-2]`
- **Action:** Run `pytest tests/unit/test_ttl_cache.py`; expect zero failures (locking is transparent to single-threaded callers). If any fail, fix — likely no changes needed.
- **Risk:** LOW
- **Depends on:** Steps 1.3–1.5

#### Step 2.2: Add thread-safety test file
- **Source:** `[P02 §1.3]` (new tests section)
- **Action:** Create `tests/unit/test_cache_thread_safety.py` with tests:
  - `test_concurrent_writes_no_crash`
  - `test_concurrent_read_write_race`
  - `test_invalidate_returns_correct_bool`
  - `test_concurrent_invalidate_no_crash`
  - `test_gpu_health_no_race` (simulates GPUHealthCollector's access pattern)
  - `test_invalidate_all_doesnt_corrupt_store`
- **Risk:** LOW
- **Depends on:** Steps 1.5, 1.6

### Priority 3 (Cleanup/Polish)

#### Step 3.1: Add explanatory comment for `_TTLCache` re-export in `__init__.py`
- **Source:** `[P01 P2-1]`, `[P04 §7a — docstring note about None ambiguity]`
- **Action:** In `llauncher/util/__init__.py`, add a comment before/after the `_TTLCache` import line:
```python
# NOTE: _TTLCache is intentionally exposed at package level for access by 
# tests and other subsystems without importing the private cache submodule.
# Leading underscore convention preserved to discourage use in application code.
# Important: get(key) returning None is ambiguous (cache miss vs. cached None).
```
- **Risk:** LOW
- **Depends on:** none

---

## `llauncher/core/gpu.py` [P02, P04, P05] — Largest File: GPU Code Overhaul

### Priority 1 (Foundation)

#### Step 1.1: Add `import shutil`, add `import logging` + module-level logger
- **Source:** `[P02 §3.2]`, `[P04 §Sub-Task 1]`, `[P05 Phase 1]`
- **Original code (top of file):** No `shutil` import; may or may not have `logging` import already; no `logger = logging.getLogger(__name__)`
- **Target replacement:** Add after existing imports:
```python
import shutil          # NEW — replaces custom shutil_which()
import logging         # NEW — if not already present

logger = logging.getLogger(__name__)  # NEW — module-level logger for GPU submodule
```
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Fix `_to_float()` — convert to string before `.strip()` call
- **Source:** `[P02 §3.5]`, `[P04 Sub-Task 2b]`
- **Original code (~line ~389):**
```python
def _to_float(v) -> float | None:
    try:
        if v is None or v.strip() == "-":   # AttributeError if v is int/float!
            return None
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None
```
- **Target replacement:**
```python
def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).strip()                   # Convert to string FIRST, then strip
        if s == "-":                         # Safe — comparing strings now
            return None
        return float(s)
    except (ValueError, TypeError):
        return None
```
- **Risk:** LOW (fixes latent crash when JSON yields `int` values like `"utilization.gpu": 0`)
- **Depends on:** none

#### Step 1.3: Remove unused parameters from `_collect_devices()` signature
- **Source:** `[P02 §3.6]`, `[P04 Sub-Task 4]`
- **Original code (~line ~112):**
```python
def _collect_devices(self, simulate: bool = False, num_simulated: int = 1) -> GPUHealthResult:
```
- **Target replacement:**
```python
def _collect_devices(self) -> GPUHealthResult:
    """Try each backend in priority order; return the first success."""
```
- Also remove any call sites passing these parameters (verify during review — likely none since callers use zero args).
- **Risk:** LOW
- **Depends on:** none

#### Step 1.4: Fix simulate-flag env var expression — explicit whitelist per Directive #3
- **Source:** `[P02 §3.4]`, `[P04 Sub-Task 4]`, `[P05 Phase 1 § Clarity]`
- **Original code (~line ~134):**
```python
data = self._query_NVIDIA(simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "")
```
- **Target replacement (per Project Lead Directive #3 — explicit whitelist, NOT bare bool()):**
```python
# LLAUNCHER_GPU_SIMULATE enables test-mode output parsing.
# Must be explicitly set to a truthy value; simulation is off by default.
_sim_val = os.environ.get("LLAUNCHER_GPU_SIMULATE", "").strip().lower()
simulated_output = _sim_val in ("1", "true", "yes", "on")
data = self._query_NVIDIA(simulated_output=simulated_output)
```
- **Risk:** MEDIUM (alters env var semantics — `LLAUNCHER_GPU_SIMULATE=0` or any non-whitelisted value now means OFF instead of ON; however simulation-off-by-default is correct and safe)
- **Depends on:** none

### Priority 2 (Core Fixes)

#### Step 2.1: Replace bare except in `_try_NVIDIA()` with typed handler + degradation reason
- **Source:** `[P02 §3.2]`, `[P04 Sub-Task 2a]`
- **Original code (~lines ~140–142):**
```python
except Exception:
    return False
```
- **Target replacement:**
```python
except Exception as e:
    logger.debug("GPU backend query failed (nvidia-smi): %s", e)
    # degradation_reason set on result object if available, else logged only
    return False
```
- **Risk:** MEDIUM (changes from silent failure to debug-level logging — improves observability; no change in return value for callers)
- **Depends on:** Step 1.2

#### Step 2.2: Replace bare except in `_try_ROCM()` with typed handler
- **Source:** `[P02 §3.2]`, `[P04 Sub-Task 2a]`
- **Original code (~lines ~150–153):** Same pattern as NVIDIA — `except Exception: return False`
- **Target replacement:**
```python
except Exception as e:
    logger.debug("GPU backend query failed (rocm-smi): %s", e)
    return False
```
- **Risk:** MEDIUM
- **Depends on:** Step 2.1

#### Step 2.3: Replace bare except in `_try_MPS()` with typed handler
- **Source:** `[P02 §3.2]`  
- **Original code (~lines ~161–163):** Same pattern — `except Exception: return False`
- **Target replacement:**
```python
except Exception as e:
    logger.debug("GPU backend query failed (MPS): %s", e)
    return False
```
- **Risk:** MEDIUM
- **Depends on:** Step 2.1

#### Step 2.4: NVIDIA driver-version sub-query — replace `except Exception: pass` with logging
- **Source:** `[P02 §3.2]`, `[P04 Sub-Task 2c]`
- **Original code (~line ~208):** `except Exception: pass` on secondary nvidia-smi call for driver version
- **Target replacement:**
```python
except Exception as e:
    logger.debug("nvidia-smi driver-version sub-query failed: %s", e)
```
- **Risk:** LOW (debug-level, low-impact — primary device data unaffected by this secondary query)
- **Depends on:** Step 2.1

#### Step 2.5: ROCm restructure — merge two try blocks into single flow with explicit `out` binding guarantee
- **Source:** `[P04 Sub-Task 3]`, `[P05 Phase 1 § Bug B]`, `[P02 §3.2 (ROCm except)]`
- **Original code (~lines ~260–292):** Two separate try blocks on the same `out` variable; if first block raises, second references unbound `out`; silent exception in both
- **Target replacement:** Restructure into a single flow:
```python
def _query_ROCM(self) -> dict[str, Any]:
    """Parse ``rocm-smi --showmeminfo=volatile`` output."""
    result: dict[str, Any] = {"devices": []}
    
    # Ensure `out` is always defined before reaching parsing logic.
    try:
        out = subprocess.run(
            ["rocm-smi", "--showmeminfo=volatile"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("ROCm `rocm-smi` not found or timed out: %s", e)
        return result

    if out.returncode != 0 or not out.stdout.strip():
        logger.debug("rocm-smi returned non-zero (%d) or empty output", out.returncode)
        return result

    # Parsing — `out` is guaranteed bound here.
    try:
        for line in out.stdout.splitlines():
            # Pattern A: "GPU<N> ... VRAM Used: <N> MiB"
            match = re.match(
                r"^\s*GPU[0-9]+\s+.*VRAM\s+Used:\s+(\d+)\s+MiB", 
                line, re.IGNORECASE
            )
            if match:
                idx_match = re.search(r"\bGPU(\d+)\b", line)
                if not idx_match:
                    continue
                used = int(match.group(1))
                result["devices"].append(GPUDevice(
                    index=int(idx_match.group(1)),
                    name=f"ROCm GPU {idx_match.group(1)}",
                    used_vram_mb=used,
                ))
                continue

            # Pattern B: "Volatile GPU memory usage (VBIOS): value: <N>"
            vol_match = re.search(
                r"GPU[0-9]+\s+Volatile\s.*?value:\s+(\d+)\s*MiB", 
                line, re.IGNORECASE
            )
            if not vol_match:
                continue
            gpu_num_match = re.match(r"\s*(GPU(\d+))\b", line)
            idx = int(gpu_num_match.group(2)) if gpu_num_match else 0
            result["devices"].append(GPUDevice(
                index=idx,
                name=f"ROCm GPU {idx}",
                used_vram_mb=int(vol_match.group(1)),
            ))

    except Exception as e:
        logger.warning("Failed to parse rocm-smi output: %s", e)

    return result
```
- **Risk:** HIGH (fundamental rewrite of ROCm parsing; correct-by-definition but untested on real hardware — mock testing required per P05 Phase 3 tests N1/N2)
- **Depends on:** Step 1.2, Steps 2.1–2.4

#### Step 2.6: Fix `_query_MPS()` — use actual regex results instead of always-appending single device
- **Source:** `[P02 §3.3]`, `[P05 Phase 1 § Bug C]`
- **Original code (~lines ~308–318):** Dead regex matches; `name_match` assigned but never used; loop writes to `result["devices"]` outside the loop body → always appends once regardless of actual GPU count; bare `except Exception: pass`
- **Target replacement:**
```python
def _query_MPS(self) -> dict[str, Any]:
    """Query Apple MPS via system_profiler SPDisplaysDataType."""
    result: dict[str, Any] = {"devices": []}
    if not is_apple_mps_available():
        return result
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0 or not out.stdout.strip():
            logger.debug("system_profiler failed (rc=%d): %s", 
                         out.returncode, out.stderr.strip()[:200])
            return result

        total_mem_mb = _estimate_apple_unified_mem()
        
        # Extract GPU device names from system_profiler output.
        gpu_names: list[str] = []
        for line in out.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            name_match = re.match(r"^\s*(\w[\w\s.\-]+)\s*GPU", line, re.IGNORECASE)
            if name_match:
                gpu_name = name_match.group(1).strip()
                if gpu_name and gpu_name not in ("Apple", "Metal") \
                   and gpu_name not in gpu_names:
                    gpu_names.append(gpu_name)

        if gpu_names:
            for idx, gname in enumerate(gpu_names):
                result["devices"].append(GPUDevice(
                    index=idx, name=gname, 
                    total_vram_mb=total_mem_mb // max(len(gpu_names), 1),
                ))
        else:
            # Fallback: single Apple Silicon device.
            result["devices"].append(GPUDevice(
                index=0, name="Apple Silicon (MPS)", total_vram_mb=total_mem_mb,
            ))

    except Exception as e:
        logger.debug("Apple MPS query failed: %s", e)
    
    return result
```
- **Risk:** HIGH (rewrite of MPS parsing; correct-by-definition but untested on real Apple Silicon hardware — mock testing required per P05 tests N2/N3)
- **Depends on:** Step 1.2

#### Step 2.7: Delete hand-rolled `shutil_which()` function + replace all call sites with `shutil.which()`
- **Source:** `[P02 §3.1]`
- **Original code (lines ~338–346):** Entire `shutil_which(program)` function (~9 lines) plus 3 call sites: `is_available` (~line 130), `_try_NVIDIA` (~line 137), `_try_ROCM` (~line 149)
- **Target replacement:** 
  - Delete the entire `shutil_which()` function definition
  - Replace all calls: `shutil_thing("nvidia-smi")` → `shutil.which("nvidia-smi")` (and similarly for `"rocm-smi"`)
- **Risk:** LOW (`shutil_which` is internal-only; no external import)
- **Depends on:** Step 1.1

### Priority 3 (Cleanup/Polish — P05 test remediation, handled separately)

#### Step 3.1: Remove / refactor `_degradation_reason` field? DECISION NOTE
- **Source:** `[P04 Sub-Task 2b]`, `[P04 §ADR: _degradation_reason as Dataclass Field vs Return Value]`
- **Note:** P04 proposed adding `result._degradation_reason` to the GPUHealthResult dataclass. However, this was not requested by any other plan and is a non-trivial schema change (modifies dataclass definition, changes serialization output). 
- **Decision:** Defer `_degradation_reason` field addition to a follow-on PR unless specifically required for `/status degraded flag` below. The logging-level fix in Steps 2.1–2.4 already provides observability without schema changes.
- **Risk:** N/A (deferred)

---

## `llauncher/remote/node.py` [P02, P03] — to_dict() Boolean Flag + Caller Audit

### Priority 1 (Foundation)

#### Step 1.1: Change `"api_key": self.api_key` → `"has_api_key": self.api_key is not None`
- **Source:** `[P03 Finding #5 § Chosen Fix]`, [Directive #1 — project lead override on P02's masking approach]  
- **Note:** Plan 02 originally proposed `"api_key": "***"` (masked string), but the Project Lead has explicitly overridden this to a boolean presence flag per Directive #1.
- **Original code (`to_dict()` method ~line ~276–284):**
```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "host": self.host,
        "port": self.port,
        "timeout": self.timeout,
        "api_key": self.api_key,           # ← PLAINTEXT SECRET — CHANGE REQUIRED
        "status": self.status.value,
        ...
    }
```
- **Target replacement:**
```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "host": self.host,
        "port": self.port,
        "timeout": self.timeout,
        "has_api_key": self.api_key is not None,  # ← BOOLEAN presence flag (Directive #1)
        "status": self.status.value,
        ...
    }
```
- **Risk:** MEDIUM (changes serialized dict structure; callers expecting `"api_key"` key will get `KeyError`. Caller audit below.)
- **Depends on:** none

### Priority 2 (Core Fix — Caller Audit)

#### Step 2.1: Audit all callers of `RemoteNode.to_dict()` for compatibility
- **Source:** `[P03 Finding #5 § Impact Audit]`, [P04 caller analysis]
- **Action items per the impact audit table in P03 (§Finding #5):**

| Consumer | File:Line(s) | Risk | Action Required |
|----------|-------------|------|-----------------|
| `RemoteState.get_snapshot()` | `remote/state.py:208` | LOW | Verify no downstream JSON schema depends on `"api_key"` key name. Update any code expecting that key to use `"has_api_key"`. |
| CLI node list (JSON mode) | `cli.py:290–305` | NO BREAKAGE | CLI already uses manual dict construction with `"has_api_key": bool(node.api_key)` — confirmed compatible. No action needed. |
| Test `test_to_dict` | `tests/unit/test_remote.py:56–71` | LOW | Node created WITHOUT api_key → dict will have `"has_api_key": False`. Tests check individual keys unaffected by this change. Verify tests pass after change. Check for any bare `assert "api_key" in data`. |
| Test `test_registry_extended.py` ~line 302 | — | LOW | Checks only `"name"`, `"host"`, `"port"` — doesn't assert on api_key presence. No action needed. |

- **Risk:** MEDIUM (if audit misses a caller, runtime KeyErrors will occur)
- **Depends on:** Step 1.1
- **Verification command after implementation:**
```bash
grep -rn 'to_dict()' llauncher/remote/ --include='*.py' | grep -v '__pycache__'
grep -rn '\["api_key"\]' llauncher/ --include='*.py' | grep -v '__pycache__' | grep -v 'test_'
```

#### Step 2.2: Add/verify test for to_dict() masking behavior
- **Source:** `[P03 Phase 4 §4.3]`
- **Action:** Ensure a test exists verifying that `node.to_dict()` produces `"has_api_key": True` (when key is set) and contains NO `"api_key"` key:
```python
def test_to_dict_masks_api_key():
    node = RemoteNode("test", "localhost", port=8765, api_key="secret")
    d = node.to_dict()
    assert d["has_api_key"] is True
    assert "api_key" not in d  # must NOT leak plaintext key
```
- **Risk:** LOW
- **Depends on:** Step 1.1

### Priority 3 (Cleanup)

#### Step 3.1: No additional cleanup needed for this file
- node.py changes are surgical and self-contained per the above steps.

---

## `llauncher/remote/registry.py` [P02, P03] — chmod Hardening + Public Filter API

### Priority 1 (Foundation)

#### Step 1.1: Add `import os` at top of file
- **Source:** `[P03 Finding #4 § Chosen Fix]`, `[P02 Phase 2 — note: _save masking approach is overridden by Directive #2]`
- **Action:** Add to existing imports (next to `json`, `pathlib.Path`):
```python
import os   # NEW — for file permission management
```
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Guard `_save()` against missing parent directory + add logging import if absent
- **Source:** `[P02 §2.2]`, `[P03 Finding #4 § Chosen Fix]`
- **Original code (`_save()`) ~line ~55–61:** 
```python
def _save(self) -> None:
    data = {}
    for name, node in self._nodes.items():
        data[name] = {"name": ..., "host": ..., "port": ..., "timeout": ..., "api_key": node.api_key}
    NODES_FILE.write_text(json.dumps(data, indent=2))
```
- **Target replacement (add mkdir guard + logging import):**
```python
# At top of file, if not already present:
import logging   # NEW — for permission warning logs

logger = logging.getLogger(__name__)  # or use existing logger
```
- Inside `_save()`:
```python
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
            "api_key": node.api_key,          # ← PLAINTEXT — intentional; see Directive #2
        }

    try:
        NODES_FILE.write_text(json.dumps(data, indent=2))
        os.chmod(NODES_FILE, 0o600)      # ← SECURITY: owner-only permissions (Directive #2)
    except OSError as e:
        logger.warning("Failed to set secure permissions on %s: %s", NODES_FILE, e)
```
- **Risk:** MEDIUM (file permission changes; guard ensures robustness if chmod fails)
- **Depends on:** Step 1.1

### Priority 2 (Core Fix — Public Filter API)

#### Step 2.1: Add `get_filtered()` public method to NodeRegistry
- **Source:** `[P02 §4.2]`
- **Action:** Append new public method to the NodeRegistry class:
```python
def get_filtered(self, include_offline: bool = True) -> dict[str, RemoteNode]:
    """Return nodes optionally filtered by status.
    
    Args:
        include_offline: If False, only ONLINE nodes are returned.
    
    Returns:
        Dict mapping node names to RemoteNode instances.
    """
    if include_offline:
        return dict(self._nodes)
    return {
        name: node 
        for name, node in self._nodes.items() 
        if node.status == NodeStatus.ONLINE
    }
```
- **Risk:** LOW (new public method — backward-compatible additive change; `_save` still uses `self._nodes` directly internally)
- **Depends on:** none

#### Step 2.2: Verify existing tests pass with chmod guard in place
- **Source:** `[P03 Finding #4 § Test Impact]`, `[P02 Phase 2 § Test Impact]`
- **Action:** Run affected test files; `test_node_add_with_api_key` and similar should still work since we're NOT masking keys in the file (per Directive #2, plaintext is kept with permission hardening as the security mechanism).
- **Risk:** LOW
- **Depends on:** Step 1.2

### Priority 3 (Cleanup)

#### Step 3.1: No additional cleanup needed for this file

---

## `llauncher/cli.py` [P02, P05] — CLI Cleanup: Rename Params + Remove ctx + Registry API

### Priority 1 (Foundation)

#### Step 1.1: Add registry import if not present; verify registry is accessible via a module-level or dependency-injected reference
- **Source:** `[P02 §4.2]`, `[P05 Phase 3 — CLI edge cases, handled separately in test remediation]`
- **Action:** Ensure `registry = NodeRegistry()` (or whatever the current pattern is) exists at module level and that it's imported from `llauncher.remote.registry`. No change expected here; just confirm.
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Rename all `json` parameters to `as_json` across Typer commands
- **Source:** `[P02 §4.1]`, `[P05 Phase 3 — verify --json flag still works after rename, handled in test remediation]`
- **Scope:** Five locations across four command functions:

| Function | Lines (approx) | Change |
|----------|---------------|--------|
| `list_models` | ~102–103 | `ctx: typer.Context, json: bool = typer.Option(...)` → `as_json: bool = typer.Option(...)` |
| `model_info` | ~116–119 | Same pattern |
| `server_status` | ~180–183 | Same pattern |
| `list_nodes` (node subcommand) | ~253–254 | Same pattern; also rename `json` reference in function body: `if json:` → `if as_json:` |
| `node_status` | ~267–292 | Rename in both `list_nodes` and `node_status` functions — two Typer.Option declarations total (one per function) |

- **Risk:** LOW (only internal parameter name changes; the CLI flag `--json / -j` is unchanged in the UI)
- **Depends on:** none

#### Step 1.3: Remove unused `ctx: typer.Context,` parameters from all Typer commands
- **Source:** `[P02 §4.3]`, `[P05 Phase 2 §2.6 — CLI gaps covered in test remediation]`
- **Scope:** Seven functions (confirmed no references to the `ctx` variable in any of them):

| Function | Lines (approx) | Action |
|----------|---------------|--------|
| `list_models` | ~102 | Remove `ctx: typer.Context,` parameter line |
| `model_info` | ~116 | Same |
| `stop_server` | ~165 | Same |
| `server_status` | ~180 | Same |
| `node list` (via `_node_app`) | ~247 | Same — likely via the `list_nodes` function; check carefully for line numbers |
| `node remove` | ~261 | Same |
| `node status` | ~278 | Same |

- **Risk:** LOW (Typer doesn't require context injection for simple commands)
- **Depends on:** Step 1.2

### Priority 2 (Core Fix — Registry API Access)

#### Step 2.1: Replace direct `registry._nodes` access with public API in `list_nodes()` command
- **Source:** `[P02 §4.2]`, [P05 Phase 2/3 — CLI edge cases, handled separately]
- **Original code (~line ~253):**
```python
for node in registry._nodes.values():
    ...
```
**OR** (depending on exact current structure):
```python
target_nodes = dict(registry._nodes)  # or similar pattern  
```

- **Target replacement:**
```python
# Use the public registry iteration protocol:
for node in registry:   # __iter__ yields values
    ...

# OR for targeted filtering (if needed based on all_nodes flag):
target_nodes = registry.get_filtered(include_offline=all_nodes)
```

- **Risk:** LOW (using documented public API; `__iter__` already implemented on NodeRegistry)
- **Depends on:** Step 1.3, Registry's `get_filtered()` method from Step 2.1 of registry.py

#### Step 2.2: Replace direct `registry._nodes` access in `node_status()` command  
- **Source:** `[P02 §4.2]`, lines ~284, 292
- **Original code (varies by exact implementation):**
```python
target_nodes = registry._nodes if all_nodes else {n: nd for n, nd in registry._nodes.items() if ...}
```

- **Target replacement:**
```python
target_nodes = registry.get_filtered(include_offline=all_nodes)
```
- **Risk:** LOW
- **Depends on:** Registry's `get_filtered()` method from Step 2.1 of registry.py

### Priority 3 (Test Verification)

#### Step 3.1: Run CLI tests with new parameter names and registry API usage
- **Source:** `[P02 §4.6]`, `[P05 Phase 2/3 — multiple test fixes needed but handled by test remediation subagent]`
- **Action:** After implementing Steps 1.1–2.2, run:
```bash
pytest tests/unit/test_cli.py -v --tb=short
```
Expected: All existing tests pass with `--json` flag working (only internal param name changed from `json` to `as_json`). New edge-case tests for port conflict and malformed JSON will be added by the test remediation agent separately.
- **Risk:** LOW
- **Depends on:** Steps 1.2, 1.3, 2.1, 2.2

---

## `llauncher/agent/middleware.py` [P03] — HMAC Authentication + OpenAPI Path Exemption

### Priority 1 (Foundation)

#### Step 1.1: Add `import hmac` at top of file
- **Source:** `[P03 Finding #1 § Chosen Fix]`
- **Action:** After existing imports (~line ~2–4):
```python
import hmac   # NEW — constant-time comparison for API key validation
```
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Remove `/openapi.json` from `_AUTH_EXEMPT_PATHS` frozenset
- **Source:** `[P03 Finding #3 § Chosen Fix]`, [P05 Phase 2 — fix `self=None` param in test, handled separately]
- **Original code (line ~4):**
```python
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})
```
- **Target replacement:**
```python
# /openapi.json is now suppressed via FastAPI constructor (openapi_url=None when auth active),
# so it no longer exists as a route and should not be in the exemption set.
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc"})
```

- **Rationale:** Keeping these dead entries is harmless but confusing — they imply routes exist when they may not (and it contradicts Directive that openapi_url=None removes the route). P03 recommends removal.
- **Risk:** LOW (dead-entry removal; `/docs` and `/redoc` remain for the no-auth case where FastAPI does register them)
- **Depends on:** Step 1.1

### Priority 2 (Core Fix — HMAC Comparison)

#### Step 2.1: Refactor auth check in `dispatch()` — extract None guard, use hmac.compare_digest
- **Source:** `[P03 Finding #1 § Chosen Fix]`
- **Original code (~lines ~49–57):**
```python
if api_key is None:
    status_code = 401
    return JSONResponse(status_code=status_code, content={"detail": "Authentication required"})

if api_key != self.expected_token:   # ← TIMING VULNERABLE (!= short-circuits)
    status_code = 403  
    return JSONResponse(status_code=status_code, content={"detail": "Authentication required"})
```
- **Target replacement:**
```python
# api_key is guaranteed non-None here (checked above). Safe to pass to compare_digest.
if not hmac.compare_digest(api_key, self.expected_token):
    # Present but wrong → 403 (credentials provided, access denied)
    status_code = 403
    return JSONResponse(
        status_code=status_code, 
        content={"detail": "Authentication required"},
    )
```

- **Risk:** MEDIUM (changes from `!=` to `hmac.compare_digest`; functionally equivalent for valid keys but eliminates timing oracle — P03 calls this CRITICAL)
- **Depends on:** Step 1.1, Step 1.2

### Priority 3 (Cleanup / Test Verification)

#### Step 3.1: Update test `test_openapi_docs_excluded_from_auth` in middleware test file
- **Source:** `[P03 Phase 1 § Finding #3 Test Impact]`, `[P05 Phase 2 § Fix line 87 self=None param]`
- **Action:** This is a TEST FILE change. The test asserts `/openapi.json` returns 200 without auth when token is configured — this WILL fail after Step 1.2 + server.py openapi_url suppression. 
  - Rewrite to assert that `/openapi.json` is NOT in `_AUTH_EXEMPT_PATHS`.
- **Risk:** LOW (test file change)
- **Depends on:** Step 1.2

#### Step 3.2: Add test verifying hmac.compare_digest usage
- **Source:** `[P03 Phase 1 § Finding #1]`, `[P03 New Tests N1]`
- **Action:** Create/append test in `tests/unit/test_agent_middleware.py`:
```python
def test_hmac_compare_digest_used():
    """Verify middleware dispatch uses hmac.compare_digest, not != or ==."""
    import unittest.mock as mock
    from llauncher.agent.middleware import AuthenticationMiddleware
    
    middleware = AuthenticationMiddleware(app=None, expected_token="test-token")
    
    with mock.patch('hmac.compare_digest', wraps=hmac.compare_digest) as mock_cd:
        # ... trigger dispatch with a request; verify compare_digest is called
        pass  # Concrete implementation to be fleshed out by test remediation agent
```
- **Risk:** LOW (test-only change, handled separately)
- **Depends on:** Step 2.1

---

## `llauncher/agent/server.py` [P03] — OpenAPI Suppression + Startup Warning + Plaintext Key Permission Warning

### Priority 1 (Foundation)

#### Step 1.1: Add `openapi_url=None if auth_active else "/openapi.json"` to FastAPI constructor
- **Source:** `[P03 Finding #3 § Chosen Fix]`
- **Original code (~line ~102):**
```python
app = FastAPI(
    title="llauncher Agent",
    description="Remote management agent for llauncher nodes",
    version=__version__,
    docs_url=None if auth_active else "/docs",
    redoc_url=None if auth_active else "/redoc",
)  # ← openapi_url not specified — defaults to "/openapi.json" (exposed even with auth!)
```
- **Target replacement:**
```python
app = FastAPI(
    title="llauncher Agent",
    description="Remote management agent for llauncher nodes",
    version=__version__,
    docs_url=None if auth_active else "/docs",
    redoc_url=None if auth_active else "/redoc",
    openapi_url=None if auth_active else "/openapi.json",  # ← NEW — suppress schema with auth
)
```
- **Risk:** MEDIUM (new route suppression when auth active; `/openapi.json` returns 404 instead of 200 with full API spec when token is set. This IS the intended security fix.)
- **Depends on:** none

### Priority 2 (Core Fix — Startup Warning)

#### Step 2.1: Replace conditional auth-disabled warning block with unconditional warning + CRITICAL for 0.0.0.0 bind
- **Source:** `[P03 Finding #2 § Chosen Fix]`, [P03 Phase 3 (§ Finding #2 → Phase 3 re-writes this)]
- **Original code (~lines ~168–173):**
```python
if AGENT_API_KEY is None and config.host == "0.0.0.0":
    logger.warning(
        "⚠️  WARNING: Agent is binding to 0.0.0.0 (all interfaces) "
        "without API key authentication enabled."
    )
```
- **Target replacement:**
```python
# Unconditional warning when auth is disabled.
if AGENT_API_KEY is None:
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
- **Risk:** LOW (only log level / message change; no behavioral change)
- **Depends on:** none

#### Step 2.2: Add plaintext-key file permission warning on startup (when auth active + keys in nodes.json)
- **Source:** `[P03 Finding #4 § Startup Warning]`, [requires importing NODES_FILE reference from registry module]
- **Action:** After the auth-disabled warning block above, add a separate check for when auth IS active and nodes.json exists with insecure permissions:

```python
# Additionally, warn if existing node registry file has permissive permissions.
try:
    from llauncher.remote.registry import NODES_FILE
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

- **Risk:** LOW (read-only check; only warns when file is readable)
- **Depends on:** Step 2.1

### Priority 3 (Test Verification)

#### Step 3.1: Update/add test for auth-disabled warning behavior
- **Source:** `[P03 Finding #2 § Test Impact]`, [N2 — `test_auth_disabled_warning_unconditional`]
- **Action:** Patch `logging.warning` and `logging.critical`; start agent mock with no token; verify WARNING fires always regardless of bind address, CRITICAL specifically for 0.0.0.0.
- **Risk:** LOW (test file change, handled by test remediation)
- **Depends on:** Step 2.1

---

## `llauncher/agent/routing.py` [P02, P04] — Double-Checked Locking + Status Endpoint Degraded Flag + Dead Code Removal

### Priority 1 (Foundation)

#### Step 1.1: Add `import threading` at top of file
- **Source:** `[P02 §4.4]`, [P05 Phase 1/3 — handled separately in test remediation for routing concurrency tests]
- **Action:** After existing imports (~line ~2):
```python
import threading   # NEW — for get_state() double-checked locking
```
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Wrap `get_state()` in double-checked locking pattern
- **Source:** `[P02 §4.4]`
- **Original code (~lines ~13–22):**
```python
_state: LauncherState | None = None

def get_state() -> LauncherState:
    global _state
    if _state is None:                  # CHECK 1 — Thread A reads None
        _state = LauncherState()        # LINE BETWEEN THREADS — race!
        _state.refresh()               # CHECK 2 — Thread B also sees None → second instance!
    return _state
```
- **Target replacement:**
```python
_state: LauncherState | None = None
_state_lock = threading.Lock()        # NEW

def get_state() -> LauncherState:
    """Get or create the global LauncherState instance (thread-safe)."""
    global _state
    
    # Fast path — no lock if already initialized.
    if _state is not None:
        return _state
    
    with _state_lock:
        # Double-check inside lock to handle TOCTOU race window.
        if _state is None:
            _state = LauncherState()
            _state.refresh()
    return _state
```
- **Risk:** MEDIUM (adds synchronization; single-threaded callers unaffected; concurrent callers now correctly see one shared instance)
- **Depends on:** Step 1.1

### Priority 2 (Core Fix — /status Degraded Flag)

#### Step 2.1: Replace bare `except Exception: pass` in `/status` endpoint with warning log + degraded flag
- **Source:** `[P04 Sub-Task 5a]`, [P05 Phase 3/Integration test handling, covered separately]  
- **Original code (~lines ~177–183):**
```python
try:
    collector = GPUHealthCollector()
    gpu_health = collector.get_health()
except Exception:
    pass                               # ← Silent failure — no gpu field, no degraded flag
```

- **Target replacement:**
```python
try:
    from llauncher.core.gpu import GPUHealthCollector  # inline if not already imported at top
    
    collector = GPUHealthCollector()
    gpu_health = collector.get_health()
    
    if gpu_health.get("backends"):
        response["gpu"] = gpu_health
    else:
        reason = gpu_health.get("_degradation_reason") or ""  # may be absent without Step 2.5 of gpu.py
        if reason:
            logger.warning("GPU query returned empty with degradation reason: %s", reason)

except Exception as exc:
    logger.warning("/status GPU health collection failed: %s", exc)
    response["gpu_degraded"] = True   # Machine-readable degraded flag for upstream consumers
```
- **Risk:** MEDIUM (adds `"gpu_degraded"` field to `/status` response — additive, backward-compatible. If GPU query fails silently before, response had no gpu key; now it has `{"gpu": null, "gpu_degraded": true}` or similar.)
- **Depends on:** Step 1.2

#### Step 2.2: Remove redundant nested except block in `/start-with-eviction` (dead code)
- **Source:** `[P04 Sub-Task 5b]`, [P05 Phase 1 — routing cleanup, handled separately via test remediation]
- **Original code (~lines ~409–415):** Inner try/except around `check_model_health` call nested inside outer try (outer already handles exceptions). Defensive copy-paste residue.
- **Target replacement:** Delete the inner try/except block entirely. The outer `try/except Exception: pass` is sufficient. If tests show a behavioral change, restore with proper docstring explaining why.
- **Risk:** LOW (dead code removal; behavior should be identical since both blocks catch Exception and do nothing)
- **Depends on:** Step 1.2

### Priority 3 (Cleanup / Test Verification)

#### Step 3.1: Verify routing tests pass with locking + degraded flag changes
- **Source:** `[P02 §4.6]`, [P05 Phase 3 — missing concurrency test for get_state()]
- **Action:** Run affected tests: `pytest tests/unit/test_agent.py -v --tb=short` (and any integration tests touching `/status`). Expect: no regressions from locking change; new degraded flag may appear in response JSON during mock GPU failure scenarios.
- **Risk:** LOW
- **Depends on:** Steps 1.2, 2.1

---

## `llauncher/core/model_health.py` [P02, P04] — Import Hoist + Timezone Fix + Cache Invalidate + Diagnostic Fix

### Priority 1 (Foundation)

#### Step 1.1: Move `from pathlib import Path` to top-level imports
- **Source:** `[P02 §4.5]`, [also ensures `pathlib.Path` is available for the `_stat_failed` field usage below]
- **Original code (~line ~81, inside function body):**
```python
try:
    from pathlib import Path           # ← Deferred import inside function
    path = Path(model_path).resolve()
```

- **Target replacement:** Move `from pathlib import Path` to the top-level imports section (after other `from ...` imports), then delete it from inside the function body. The rest of the function stays the same — Python resolves `Path` from the enclosing module scope:
```python
# At top of file (near other imports):
from __future__ import annotations

import logging
from datetime import datetime, timezone  # add timezone here (see Step 1.2)  
from pathlib import Path                  # MOVED from inside function

...

def check_model_health(model_path: str) -> ModelHealthResult:
    """Check the health of a model file at the given path."""
    result = ModelHealthResult(valid=False, reason="unknown")
    
    try:
        path = Path(model_path).resolve()  # Now uses module-level import
        
        if not path.exists():
            ...

```
- **Risk:** LOW (pure refactoring — no behavioral change)
- **Depends on:** none

#### Step 1.2: Add `timezone` to datetime import + use UTC in fromtimestamp
- **Source:** `[P02 §5.1]`
- **Original code (~line ~93):**
```python
from datetime import datetime                # only imports class, no timezone
...
result.last_modified = datetime.fromtimestamp(stat.st_mtime)  # naive local time — WRONG per docstring claim
```

- **Target replacement:**
```python
# Already updated in Step 1.1: from datetime import datetime, timezone
...
result.last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)  # explicit UTC
```
- **Risk:** LOW (changes `last_modified` value from naive local to explicit UTC — backward compatible since callers deserialize JSON which preserves the value; tests asserting specific timestamp values may need adjustment but unlikely)
- **Depends on:** Step 1.1

### Priority 2 (Core Fix — Cache Invalidate + Diagnostic)

#### Step 2.1: Replace `_health_cache._store.pop(model_path, None)` with `_health_cache.invalidate(model_path)`
- **Source:** `[P02 §4.c]`, [directly references the new public method from cache.py Step 1.6]
- **Original code (somewhere in `check_model_health` or a related function):**
```python
_health_cache._store.pop(model_path, None)    # ← Private _store access — BAD
```

- **Target replacement:**
```python
_health_cache.invalidate(model_path)          # ← Public method — GOOD
```
- **Risk:** MEDIUM (changes from direct `_store` mutation to public API; must have cache.py Step 1.6 completed first)
- **Depends on:** cache.py Step 1.6 (`invalidate()` method addition)

#### Step 2.2: Add `_stat_failed` field to `ModelHealthResult` Pydantic model
- **Source:** `[P04 Sub-Task 6a]`
- **Original code (dataclass/Pydantic model definition):**
```python
class ModelHealthResult(BaseModel):
    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None
```

- **Target replacement:**
```python
class ModelHealthResult(BaseModel):
    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None
    _stat_failed: bool = False   # NEW — tracks metadata_read_failure separately from "too small"
```

- **Risk:** LOW (new optional field with default; backward-compatible)
- **Depends on:** none

#### Step 2.3: Fix stat() OSError handling + size heuristic diagnostic string
- **Source:** `[P04 Sub-Task 6b]`
- **Original code (~lines ~93–115):**
```python
try:
    stat_result = path.stat()
    result.size_bytes = stat_result.st_size
    result.last_modified = datetime.fromtimestamp(stat_result.st_mtime)
except OSError:
    pass  # ← silent failure — size_bytes stays None

# Size heuristic:
if (result.size_bytes or 0) < _MIN_SIZE_BYTES:
    result.reason = "too small"    # ← WRONG when stat failed! Could be permission error, not file size.
```

- **Target replacement:**
```python
try:
    stat_result = path.stat()
    result.size_bytes = stat_result.st_size
    result.last_modified = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
except OSError as exc:
    logger.debug("Could not stat %s: %s", path, exc)
    result._stat_failed = True     # NEW — records metadata failure separately

# Size heuristic — distinguish "can't read" from "too small"
if result._stat_failed or (result.size_bytes or 0) < _MIN_SIZE_BYTES:
    if result._stat_failed:
        result.reason = "metadata_unavailable"  # ← Correct diagnostic for permission/OS errors
    else:
        result.reason = "too small"              # Only when we actually measured a tiny file
```

- **Risk:** MEDIUM (changes the `reason` string from `"too small"` to `"metadata_unavailable"` when stat fails — downstream consumers may match on this exact string. However, functional behavior is unchanged: `valid=False`, launch gate still triggers.)
- **Depends on:** Steps 1.2, Step 2.2

### Priority 3 (Cleanup / Test Verification)

#### Step 3.1: Run model health tests to verify no regressions
- **Source:** `[P04 §Test Strategy — Table]`, `[P05 Phase 2/3 — exact 1MB boundary test, handled by test remediation]`
- **Action:** `pytest tests/unit/test_model_health.py -v --tb=short`. Expected: all pass. New `_stat_failed` field is not asserted in existing tests (optional default). The `last_modified` timezone change should produce valid datetime objects that still deserialize to JSON correctly.
- **Risk:** LOW
- **Depends on:** Steps 1.1, 2.1, 2.3

---

## `docs/adrs/003-agent-api-authentication.md` [P06] — ADR Rewrite: Alternatives Analysis + Consequences + Cross-References

### Priority 1 (Foundation)

#### Step 1.1: Rewrite decision section to match actual implementation
- **Source:** `[P06 Phase 2 §2A]`, [P03 Finding #6 § Chosen Fix — ADR doc correction]
- **Original content:** States "read-only endpoints like /status, /health, /models remain unauthenticated" — this is misleading per actual implementation.
- **Target replacement:** Rewrite the entire Decision section to document:
  - Actual opt-in shared-secret model via `LAUNCHER_AGENT_TOKEN` env var → `core.settings.AGENT_API_KEY`  
  - Middleware checks `X-Api-Key` header; returns 401 (missing) or 403 (wrong value)
  - Exempt paths: `/health`, `/docs`, `/redoc` (and conditionally `/openapi.json` — see below)
  - Auth-disabled behavior: skip auth entirely, log warning at startup
- **Risk:** LOW (documentation only)
- **Depends on:** none

#### Step 1.2: Add alternatives analysis table  
- **Source:** `[P06 Phase 2 §2A — Alternatives Analysis]`
- **Action:** Insert a "WHY NOT ALTERNATIVES?" section with analysis of: mTLS, OAuth/JWT, Unix socket binding, Reverse proxy delegation, and X-Api-Key (chosen). Each alternative has pros/cons/reason-not-chosen columns.
- **Risk:** LOW
- **Depends on:** Step 1.1

#### Step 1.3: Add consequences table + risk/mitigation table  
- **Source:** `[P06 Phase 2 §2A — Consequences & Risk tables]`
- **Action:** Insert structured tables matching baseline ADR style from 001/002:
  - Consequences table: Aspect / Impact / Notes columns (security posture, multi-user support, node discovery, OpenAPI docs exposure)
  - Risk/mitigation table: Risk / Severity / Mitigation columns (single shared secret no rotation, cleartext transmission, /models/health auth gate, audit log gaps)
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.4: Add cross-references section  
- **Source:** `[P06 Phase 3 §3B]`
- **Action:** Append "CROSS-REFERENCES" section linking to ADR-004 (CLI), ADR-005 (model health auth gate), ADR-006 (GPU status). Use markdown `./filename.md` links.
- **Risk:** LOW
- **Depends on:** none

#### Step 1.5: Add endpoint auth gate matrix as appendix  
- **Source:** `[P06 Phase 3 §3A — Endpoint Auth Gate Matrix]`
- **Action:** Append the endpoint→auth requirement table from the cross-reference wiring plan (health, node-info, status, models, /models/health, logs, start/*, stop/*, nodes, etc.)
- **Risk:** LOW
- **Depends on:** Step 1.4

---

## `docs/adrs/004-cli-subcommand-interface.md` [P06] — ADR Rewrite: Typer Justification + Dependency Gap + Shell Completion

### Priority 1 (Foundation)

#### Step 1.1: Document actual implementation facts
- **Source:** `[P06 Phase 2 §2B]`, `[P03 Finding #5 caller audit in node.py CLI compatibility note]`
- **Action:** Record current state of `cli.py`: ~500 LOC, subcommand groups (model/server/node/config), Rich formatting, --json flag, proper exit codes. Entry point: `llauncher = "llauncher.cli:app"` in pyproject.toml.
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Add CLI framework alternatives analysis  
- **Source:** `[P06 Phase 2 §2B — Typer vs Click vs argparse]`
- **Action:** Insert "WHY NOT ALTERNATIVES? — CLI Framework Comparison" table comparing Typer (chosen), Click, argparse, and plain python -m invocations. Each with pros/cons/decision rationale.
- **Risk:** LOW
- **Depends on:** Step 1.1

#### Step 1.3: Document dependency gap + shell completion status  
- **Source:** `[P06 Phase 2 §2B — DEPENDENCY DECLARATION GAP]`, Shell Completion subsection
- **Action:** 
  - Add "DEPENDENCY DECLARATION GAP (Critical)" section noting `typer` and `rich` are imported in cli.py but NOT declared in pyproject.toml. Include the required fix: add `"typer>=0.9.0"` and `"rich>=13.0.0"` to main dependencies (or gate behind `[extras.cli]`).
  - Add "SHELL COMPLETION STATUS" section noting `add_completion=False` disables shell completion; recommend changing to `True`.
- **Risk:** MEDIUM (identifies a real bug: CLI entry point will fail without these deps — must be fixed before release)
- **Depends on:** Step 1.2

#### Step 1.4: Add cross-discovery / double-discovery problem analysis  
- **Source:** `[P06 Phase 2 §2B — CROSS-DISCOVERY ANALYSIS]`
- **Action:** Insert table of behavioral drift risk (same operations across CLI/MCP/HTTP/Streamlit), inconsistent exit codes risk, and feature parity lag risk with mitigation strategies. Add the full "CLI → Other ADRs" dependency map table.
- **Risk:** LOW
- **Depends on:** Step 1.3

#### Step 1.5: Add consequences + risk tables + open questions  
- **Source:** `[P06 Phase 2 §2B — Consequences, Risk, Open Questions]`
- **Action:** Insert structured tables for Consequences (operator workflow, CI/CD integration, new code surface), Risk & Mitigation (missing deps CRITICAL, shell completion, ConfigStore race condition), and Open Questions (swap command alias, partial success exit codes). Add cross-references to ADR-003, 005, 006.
- **Risk:** LOW
- **Depends on:** Step 1.4

---

## `docs/adrs/005-model-cache-health.md` [P06] — ADR Rewrite: Decision Context + Alternatives + 1 MiB Heuristic Justification

### Priority 1 (Foundation)

#### Step 1.1: Document actual two-layer architecture
- **Source:** `[P06 Phase 1 §1B]`
- **Action:** Record the implemented architecture: Layer 1 (pre-flight check in state.start_server() and agent /start-with-eviction endpoint using `check_model_health(path)`), Layer 2 (REST API endpoints `/models/health` and `/models/health/{name}` for registry-level visibility).
- **Risk:** LOW
- **Depends on:** none

#### Step 1.2: Add alternatives analysis  
- **Source:** `[P06 Phase 1 §1B — Why not alternatives?]`
- **Action:** Insert structured comparison of: simple `os.path.exists()` (rejected), GGUF header magic byte verification (deferred to Phase 2), full SHA256 manifest verification (rejected). Each with pros/cons/rejection rationale.
- **Risk:** LOW
- **Depends on:** Step 1.1

#### Step 1.3: Add 1 MiB heuristic justification + consequences/risk tables  
- **Source:** `[P06 Phase 1 §1B — WHY THE 1 MiB SIZE HEURISTIC? section, Consequences Table, Risk & Mitigation Table]`
- **Action:** Document why `_MIN_SIZE_BYTES = 1024 * 1024` was chosen (lower bound for viable GGUF models, catches truncated downloads, trivially fast via stat()). Insert consequences table and risk/mitigation table per baseline style. Mark Open Questions for Phase 2: GGUF magic bytes, config change cache invalidation, network path timeout.
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.4: Add cross-references  
- **Source:** `[P06 Phase 1 §1B — CROSS-REFERENCES]`
- **Action:** Append links to ADR-006 (VRAM pre-flight via /start-with-eviction), ADR-003 (/models/health auth gating), and reference to ADR-002.
- **Risk:** LOW
- **Depends on:** Step 1.3

---

## `docs/adrs/006-gpu-resource-monitoring.md` [P06] — ADR Rewrite: Factual Corrections + Build-vs-Adopt Analysis + Per-Platform Status

### Priority 1 (Foundation)

#### Step 1.1: Correct factual error: remove `/dev/memfd` fabrication for Apple MPS
- **Source:** `[P06 Phase 1 §1C]`, [CRITICAL — fabricated mechanism undermines entire ADR trustworthiness]
- **Original content (line reads):** `Apple MPS | Process memory mapping (/dev/memfd) | macOS only`
- **Target replacement:** Document actual implementation: `system_profiler SPDisplaysDataType` for GPU name detection + `sysctl hw.memsize` for total unified memory estimation. State clearly that `/dev/memfd` is Linux-only (memfd_create syscall does not exist on macOS).
- **Risk:** LOW (documentation correction only — no code change)
- **Depends on:** none

#### Step 1.2: Add build-vs-adopt analysis for nvidia-smi approach  
- **Source:** `[P06 Phase 1 §1C — BUILD vs ADOPT ANALYSIS]`
- **Action:** Insert table comparing direct `nvidia-smi --format=json` subprocess (chosen), pynvml (rejected — marginal perf gain not worth added complexity/dep), nvitop/gpustat libraries (rejected — over-engineering), and Prometheus Node Exporter (rejected — infrastructure overhead).
- **Risk:** LOW
- **Depends on:** Step 1.1

#### Step 1.3: Add backend support status table  
- **Source:** `[P06 Phase 1 §1C — BACKEND SUPPORT STATUS]`
- **Action:** Insert table documenting each GPU backend's detection method, implementation status (NVIDIA ✅ Full / ROCm ⚠️ Partial / Apple ⚠️ Partial), per-process VRAM attribution capability, and notes about limitations. Specifically note: ROCm process attribution untested; macOS no per-process breakdown possible via CLI.
- **Risk:** LOW
- **Depends on:** Step 1.2

#### Step 1.4: Add uncertainty surfacing analysis + consequences/risk tables  
- **Source:** `[P06 Phase 1 §1C — HOW UNCERTAINTY IS SURFACED, Consequences Table, Risk & Mitigation]`
- **Action:** Document that the current implementation does NOT emit explicit confidence markers. Insert consequences table (operator awareness, dependency footprint, macOS limitation, ROCm accuracy) and risk/mitigation table (macOS per-process fundamental limit, ROCm attribution correctness, nvidia-smi subprocess latency, multi-GPU imprecision). Add Open Questions: measurement_confidence field, macOS process attribution research, GPU utilization trend tracking.
- **Risk:** LOW
- **Depends on:** Step 1.3

#### Step 1.5: Add cross-references  
- **Source:** `[P06 Phase 1 §1C — CROSS-REFERENCES]`
- **Action:** Append links to ADR-005 (/start-with-eviction composite validation pipeline), ADR-003 (GPU data via /status read-only exempt endpoint, pre-flight behind auth middleware), and reference ADR-002.
- **Risk:** LOW
- **Depends on:** Step 1.4

---

---

## Final Verification Commands

Run these commands after ALL implementation steps above are complete:

### Shell Verification (grep + diff patterns)

```bash
# === CACHE.PY THREAD-SAFETY ===
echo "--- cache.py: Lock member present ---"
grep -n "_lock.*=.*threading.Lock()" llauncher/util/cache.py

echo "--- cache.py: get() uses with self._lock ---"
grep -n "with self._lock" llauncher/util/cache.py

echo "--- cache.py: invalidate(key) method exists ---"
grep -n "def invalidate(self, key)" llauncher/util/cache.py

echo "\n=== GPU.PY BARE-EXCEPT REMEDIATION ==="
echo "--- gpu.py: NO bare 'except:' remaining in core functions ---"
grep -n "except Exception:" llauncher/core/gpu.py | head -20

echo "--- gpu.py: logger.debug on GPU failures ---"
grep -n "logger.debug.*GPU backend" llauncher/core/gpu.py

echo "--- gpu.py: shutil.which (not custom shutil_which) ---"
grep -n "shutil\.which" llauncher/core/gpu.py

echo "--- gpu.py: no custom shutil_Which function defined ---"
grep -n "^def shutil_which" llauncher/core/gpu.py; echo "(should be empty above)"

echo "--- gpu.py: simulate whitelist pattern ---"
grep -n 'in.*"1", *"true", *"yes", *"on"' llauncher/core/gpu.py

echo "--- gpu.py: _to_float uses str(v).strip() ---"
grep -n "str(v)\.strip\(\)" llauncher/core/gpu.py

# Fix: Remove unused parameters from _collect_devices() as planned, or verify params removed
echo "--- gpu.py: _collect_devices signature (verify no simulate/num_simulated params per P02 §3.6) ---"
grep -n "def _collect_devices" llauncher/core/gpu.py

echo "\n=== NODE.PY TO_DICT() BOOLEAN FLAG ==="
echo "--- node.py: has_api_key boolean, NOT api_key string ---"
grep -n 'has_api_key.*self\.api_key is not None' llauncher/remote/node.py || grep -rn 'has_api_key.*self\.api_key is not None' llauncher/
echo "(should have 1 match above)"

echo "--- node.py: NO plaintext api_key leak in to_dict() ---"
grep -A20 'def to_dict' llauncher/remote/node.py | grep '"api_key"'

echo "\n=== REGISTRY.PY CHMOD HARDENING ==="
echo "--- registry.py: os.chmod(NODES_FILE, 0o600) ---"
grep -n 'os\.chmod.*NODES_FILE.*0o600' llauncher/remote/registry.py
echo "(should have 1 match above)"

echo "--- registry.py: plaintext api_key kept (Directive #2) ---"
grep -A5 '"api_key": node\.api_key' llauncher/remote/registry.py

echo "\n=== CLI.PY PARAM RENAMES + REGISTRY API ==="
echo "--- cli.py: no 'json:' Typer params remain (should be as_json:) ---"
grep -n "json: bool = typer" llauncher/cli.py; echo "(should be empty above)"

echo "--- cli.py: uses registry.get_filtered() or iter(registry) ---"
grep -n "registry\._nodes\.values\(\)\|registry\._nodes\.items\(\)\|registry\.get_filtered" llauncher/cli.py

echo "--- cli.py: NO ctx: typer.Context params (should be empty or minimal) ---"
grep -n "ctx: typer\.Context" llauncher/cli.py | head -5

echo "\n=== MIDDLEWARE.PY HMAC + OPENAPI ==="
echo "--- middleware.py: import hmac present ---"
grep -n "^import hmac" llauncher/agent/middleware.py

echo "--- middleware.py: hmac.compare_digest used in dispatch() ---"
grep -n "hmac\.compare_digest" llauncher/agent/middleware.py

echo "--- middleware.py: /openapi.json NOT in exempt paths ---"
grep -n 'openapi\.json' llauncher/agent/middleware.py; echo "(should be empty above or commented out)"

echo "\n=== SERVER.PY OPENAPI SUPPRESSION + WARNINGS ==="
echo "--- server.py: openapi_url parameter in FastAPI constructor ---"
grep -n 'openapi_url=' llauncher/agent/server.py

echo "--- server.py: unconditional auth-disabled warning (not conditional on host) ---"
grep -A3 'AGENT_API_KEY is None:' llauncher/agent/server.py | head -6

echo "\n=== ROUTING.PY DOUBLE-CHECKED LOCKING + DEGRADED FLAG ==="
echo "--- routing.py: _state_lock defined ---"
grep -n '_state_lock.*=.*threading.Lock()' llauncher/agent/routing.py

echo "--- routing.py: get_state() has double-check inside lock ---"
grep -A8 'def get_state' llauncher/agent/routing.py | head -12

echo "--- routing.py: gpu_degraded in status endpoint response ---"
grep -n 'gpu_degraded' llauncher/agent/routing.py

echo "\n=== MODEL_HEALTH.PY TIMEZONE + DIAGNOSTICS ==="
echo "--- model_health.py: from datetime import datetime, timezone ---"
grep -n 'from datetime.*timezone' llauncher/core/model_health.py

echo "--- model_health.py: .fromtimestamp(.*, tz=timezone.utc) ---"
grep -n 'fromtimestamp(.*tz=timezone\.utc' llauncher/core/model_health.py

echo "--- model_health.py: _stat_failed field in ModelHealthResult ---"
grep -n '_stat_failed' llauncher/core/model_health.py

echo "--- model_health.py: reason='metadata_unavailable' (not 'too small') on stat failure ---"
grep -n 'metadata_unavailable' llauncher/core/model_health.py

echo "--- model_health.py: uses _health_cache.invalidate() not ._store.pop() ---"
grep -n '_health_cache\.invalidate\(' llauncher/core/model_health.py || grep -rn '_health_cache.*_store\.pop' llauncher/core/model_health.py; echo "(invalidate above should have 1+ match, pop below should be empty)"

echo "--- model_health.py: pathlib.Path at top level (not deferred inside function) ---"
grep -n 'from pathlib import Path\|import pathlib' llauncher/core/model_health.py | head -5
grep -A2 'def check_model_health' llauncher/core/model_health.py | grep -c "from pathlib"; echo "(should be 0 — no deferred import inside function)"

# === ADR DOCUMENTATION CHECKS ===
echo "\n=== ADR-003: AUTHENTICATION DOCUMENT ==="
grep -c 'mTLS\|OAuth\|Unix socket\|Reverse proxy' docs/adrs/003-agent-api-authentication.md || echo "alternatives section may need review"
grep -c 'CROSS-REFERENCES\|Cross-references' docs/adrs/003-agent-api-authentication.md
echo "--- should have alternatives + cross-references sections ---"

echo "\n=== ADR-004: CLI DOCUMENT ==="
grep -i 'typer.*not declared\|missing.*dependenc\|EXTRAS\.CLI\|pyproject' docs/adrs/004-cli-subcommand-interface.md | head -5
egrep 'add_completion.*True\|completion.*enabled' docs/adrs/004-cli-subcommand-interface.md || echo "check: shell completion guidance documented"
grep -c 'CROSS-REFERENCES' docs/adrs/004-cli-subcommand-interface.md

echo "\n=== ADR-005: MODEL HEALTH DOCUMENT ==="
grep -c 'GGUF.*magic\|header.*validat\|SHA256.*manifest' docs/adrs/005-model-cache-health.md || echo "alternatives may need review"
grep -i '1 MiB\|MIN_SIZE_BYTES\|one megabyte' docs/adrs/005-model-cache-health.md | head -3
grep -c 'CROSS-REFERENCES' docs/adrs/005-model-cache-health.md

echo "\n=== ADR-006: GPU DOCUMENT ==="
echo "--- ADR-006 must NOT contain /dev/memfd (fabrication fix) ---"
grep -i 'memfd' docs/adrs/006-gpu-resource-monitoring.md; echo "(should be empty above — fabrication corrected)"
echo "--- ADR-006 should mention system_profiler and sysctl hw.memsize ---"
grep -i 'system_profiler\|hw\.memsize' docs/adrs/006-gpu-resource-monitoring.md | head -3

echo "--- ADR-006 build-vs-adopt analysis present ---"
grep -c 'pynvml\|nvitop\|gpustat\|Prometheus.*Node_Exporter' docs/adrs/006-gpu-resource-monitoring.md || echo "build-vs-adopt table may need review"

echo "\n=== REGRESSION TESTS ==="
echo "--- Run full test suite (may be slow) ---"
echo "python3 -m pytest tests/ -x --tb=short 2>&1 | tail -30"
```

### Git Diff Verification (after implementation, before commit)

```bash
# Verify expected file list changed:
git diff --name-only | sort
git diff --stat

# Confirm no bare 'except:' remains in touched source files:
echo "=== NO BARE EXCEPT IN GPU.PY ==="
grep -nE '^[[:space:]]*except:' llauncher/core/gpu.py; echo "(should be empty)"

echo "=== NO BARE EXCEPT IN MODEL_HEALTH.PY ==="
grep -nE '^[[:space:]]*except:' llauncher/core/model_health.py | grep -v '#.*except'; echo "(should be 0 matches above - all except should be typed Exception)"

# Verify cache.py has no direct _store access from model_health
echo "=== NO DIRECT _STORE POP IN MODEL_HEALTH ==="
grep -rn '_health_cache._store\.pop' llauncher/core/model_health.py; echo "(should be empty above - replaced with invalidate())"

# Confirm to_dict() in node.py has no plaintext key
for f in $(git diff --name-only | grep node.py); do
echo "=== $f: to_dict() contains 'api_key': self.api_key? ==="
grep '"api_key":.*self\.api_key' "$f"; echo "(should be empty above)"
done

# Verify registry.py has chmod 0o600
git diff -- llauncher/remote/registry.py | grep -A2 'chmod'

# Verify hmac import and compare_digest in middleware
echo "=== HMAC IN MIDDLEWARE ==="
grep 'hmac\|compare_digest' llauncher/agent/middleware.py
```

### Key Pytest Run Patterns

```bash
# ===== Phase-by-phase test runs (run after each priority group) =====

# P1: Cache thread-safety (cache.py steps 1.1-1.6)
echo "=== PHASE 1: CACHE THREAD-SAFETY ==="
python3 -m pytest tests/unit/test_ttl_cache.py tests/unit/test_cache_thread_safety.py -v --tb=short

# P2: GPU module (gpu.py - after Steps 1.x + 2.x)
echo "=== PHASE 2: GPU MODULE ==="
python3 -m pytest tests/unit/test_gpu_health.py -v --tb=short -k "rocm or mps or simulate or to_float or logging"

# P2.5: Security changes (node.py, registry.py, middleware.py, server.py)
echo "=== PHASE 2.5: SECURITY CHANGES ==="
python3 -m pytest tests/unit/test_remote.py tests/unit/test_registry_extended.py \
              tests/unit/test_agent_middleware.py -v --tb=short

# P3: CLI + routing + model_health
echo "=== PHASE 3: CLI + ROUTING + MODEL_HEALTH ==="
python3 -m pytest tests/unit/test_cli.py tests/unit/test_model_health.py \
              tests/unit/test_agent.py -v --tb=short

# Full regression run (final gate)
echo "=== FINAL REGRESSION GATE ==="
python3 -m pytest tests/ -x --tb=short 2>&1 | tail -40
echo "Expected: all pass. Check for any FAILED or ERROR entries above."
```

---

## Summary of Plan Coverage Per File

| File | Plans Contributing Changes |
|------|---------------------------|
| `llauncher/util/cache.py` | P01 (docstring note), P02 (thread-safety, invalidate method), P04 (sentinel documentation) |
| `llauncher/core/gpu.py` | P02 (shutil.which, bare-except logging, simulate flag whitelist, _to_float fix, unused params), P04 (bare except → debug + degradation tracking, ROCm restructure), P05 (_query_ROCM merge, _query_MPS fix)
| `llauncher/remote/node.py` | P02 (to_dict masking override to boolean per Directive #1), P03 (has_api_key boolean flag, caller audit) |
| `llauncher/remote/registry.py` | P02 (_save masking override per Directive #2; get_filtered method), P03 (chmod 0o600 hardening, logging guard on chmod) |
| `llauncher/cli.py` | P02 (rename json→as_json, remove ctx params, replace _nodes with registry API), P05 (CLI edge case test gap identification — test remediation handled separately) |
| `llauncher/agent/middleware.py` | P03 (hmac import, compare_digest auth check, /openapi.json removal from exempt paths), P05 (test self=None param fix — handled in test remediation) |
| `llauncher/agent/server.py` | P03 (openapi_url=None when auth active, unconditional startup warning + CRITICAL for 0.0.0.0 bind, plaintext-key permission warning on startup) |
| `llauncher/agent/routing.py` | P02 (double-checked locking on get_state()), P04 (/status endpoint degraded flag via gpu_degraded, dead code removal in start-with-eviction), P05 (routing concurrency test gap — handled separately) |
| `llauncher/core/model_health.py` | P02 (pathlib import hoist to top-level, timezone.utc on fromtimestamp, cache._store.pop → invalidate method call), P04 (_stat_failed field addition, metadata_unavailable vs too_small diagnostic fix) |
| `docs/adrs/003-agent-api-authentication.md` | P03 (documentation correction per Finding #6), P06 (full rewrite: alternatives analysis, consequences table, risk/mitigation table, cross-references) |
| `docs/adrs/004-cli-subcommand-interface.md` | P06 (full rewrite: Typer vs Click vs argparse alternatives, dependency gap documentation for typer/rich in pyproject.toml, shell completion status, double-discovery analysis) |
| `docs/adrs/005-model-cache-health.md` | P06 (full rewrite: two-layer architecture documentation, why-not-alternatives table, 1 MiB heuristic justification with rationale, consequences/risk tables) |
| `docs/adrs/006-gpu-resource-monitoring.md` | P06 (full rewrite: remove /dev/memfd fabrication, build-vs-adopt analysis for nvidia-smi approach, per-backend accuracy table with honest limitations, uncertainty surfacing documentation) |

---

**END OF PLAN 07 — READY FOR IMPLEMENTER AGENT HANDOFF**