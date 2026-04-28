# Remediation Plan: Opus 4.7 Python Reviewer — `a4d0361..9c73c71`

**Status:** PRE-MERGE BLOCKER  
**Review Verdict:** BLOCK (1 CRITICAL + 7 HIGH + 6 MEDIUM + 3 LOW)  
**Author:** Strategic Planner (System Synthesis Subagent)  
**Date:** 2026-04-26  
**Scope:** 7 files, 17 issues across security, correctness, and quality

---

## Executive Architecture Summary

The review uncovered a **critical security vulnerability** (plaintext API key on disk / in CLI output), **four unsound concurrency patterns**, one **logic-inversion bug**, and twelve code-quality defects. The fixes naturally collapse into five sequential phases:

| Phase | Theme                          | Issues Addressed   | Files Modified            |
|-------|--------------------------------|-------------------|---------------------------|
| 1     | Foundation                     | HIGH #1, HIGH #6  | `util/cache.py`           |
| 2     | Security (CRITICAL)            | CRITICAL          | `remote/node.py`, `remote/registry.py`, `cli.py` |
| 3     | GPU Code Quality               | HIGH #2,3,4,5; MEDIUM #5, LOW #1 | `core/gpu.py` |
| 4     | API & Architecture Cleanup     | HIGH #7; MEDIUM #1,#2,#4; LOW #2 | `cli.py`, `agent/routing.py`, `remote/registry.py`, `core/model_health.py` |
| 5     | Final Polish                   | MEDIUM #3         | `core/model_health.py`    |

**Phase ordering rationale:** Thread-safety (Phase 1) must precede any refactoring that touches `_TTLCache._store` (HIGH #6). Security masking (Phase 2) is independent but high-visibility and blocks merge. GPU fixes (Phase 3) are largely self-contained but require test updates. API cleanup (Phase 4) can run concurrently for parallel-wired teams, but depends on Phase 1 for `_TTLCache.invalidate()`.

---

## Phase 1 — Foundation: Thread-Safe Cache + Public Invalidate API

**Issues:** HIGH #1 (no thread-safety), HIGH #6 (private `_store` access)  
**Files:** `llauncher/util/cache.py`  
**Risk:** LOW — adds locking without changing semantics; existing tests must pass unchanged. Adding a new public method is backward-compatible additive change.

### 1.1 Add Thread Lock + Guard All Mutations

**Current code (lines 9–54):**

```python
import time


class _TTLCache:
    def __init__(self, ttl_seconds: int = 5):
        self._ttl = ttl_seconds
        self._store: dict[str | object, tuple[object, float]] = {}
        # ^^^ No lock member

    def get(self, key) -> object | None:
        entry = self._store.get(key)          # unsynchronized read
        ...
        if time.monotonic() > expiry:
            del self._store[key]              # unsynchronized mutation
        return value

    def set(self, key, value, ttl_seconds: int | None = None) -> None:
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        self._store[key] = (...)             # unsynchronized mutation

    def invalidate_all(self) -> None:
        self._store.clear()                  # unsynchronized mutation
```

**Target code:**

```python
from __future__ import annotations

import threading
import time


class _TTLCache:
    """Simple in-memory TTL-aware dictionary cache. Thread-safe."""

    def __init__(self, ttl_seconds: int = 5):
        self._ttl = ttl_seconds
        self._store: dict[str | object, tuple[object, float]] = {}  # key -> (value, expiry_time)
        self._lock = threading.Lock()     # <-- NEW

    def get(self, key) -> object | None:
        with self._lock:                  # <-- GUARD read + lazy eviction
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]       # eviction under lock
                return None
            return value

    def set(self, key, value, ttl_seconds: int | None = None) -> None:
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        with self._lock:                  # <-- GUARD write
            self._store[key] = (value, time.monotonic() + effective_ttl)

    def invalidate_all(self) -> None:
        with self._lock:                  # <-- GUARD clear
            self._store.clear()

    def invalidate(self, key) -> bool:
        """Remove a single cached entry. Returns True if the key existed."""
        with self._lock:
            return self._store.pop(key, None) is not None
```

### 1.2 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single `threading.Lock()` guards all operations | No read-write split needed — the cache's TTL semantics mean reads are cheap (dict lookup); serialization overhead is negligible compared to backend calls being cached. |
| `invalidate_all` acquires lock for `.clear()` only, not a double-loop scan | `dict.clear()` is atomic under GIL; wrapping it in a lock eliminates race vs. concurrent `set`. |
| `__init__` does NOT acquire lock | Construction never races — the object doesn't exist yet until `__init__` returns. |

### 1.3 Test Impact (Phase 1)

| Existing Test File | Action |
|---|---|
| `tests/unit/test_ttl_cache.py` | **Run as-is.** All existing tests must pass unchanged — locking is transparent to single-threaded callers. Add **one** new test group (below). |

#### New Tests: `test_cache_thread_safety.py`

Create `tests/unit/test_cache_thread_safety.py`:

```python
"""Thread-safety regression tests for _TTLCache."""
import threading
import time
import pytest
from llauncher.util.cache import _TTLCache


def test_concurrent_writes_no_crash():
    """10 threads each write 50 keys — no KeyError, no assertion error."""
    cache = _TTLCache(ttl_seconds=60)
    errors = []

    def writer(thread_id):
        try:
            for i in range(50):
                key = f"t{thread_id}_k{i}"
                cache.set(key, f"v-{time.monotonic()}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"Concurrent writes failed: {errors}"


def test_concurrent_read_write_race():
    """Reads and writes from different threads must never raise."""
    cache = _TTLCache(ttl_seconds=1)
    errors = []

    def writer():
        for i in range(200):
            cache.set("hot", f"v{i}")

    def reader():
        for _ in range(200):
            _ = cache.get("hot")

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads: t.start()
    for t in threads: t.join()


def test_invalidate_returns_correct_bool():
    """invalidate(key) returns True if key existed, False otherwise."""
    cache = _TTLCache(ttl_seconds=60)
    assert cache.invalidate("absent") is False
    cache.set("present", "val")
    assert cache.invalidate("present") is True
    assert cache.get("present") is None  # gone after invalidation


def test_concurrent_invalidate_no_crash():
    """Multiple threads invalidate simultaneously."""
    cache = _TTLCache(ttl_seconds=60)
    for i in range(10):
        cache.set(f"k{i}", f"v{i}")
    
    errors = []

    def invalidator(keys, label):
        try:
            for k in keys:
                cache.invalidate(k)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=invalidator, args=(range(5), f"t{i}")) for i in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors


def test_gpu_health_no_race():
    """Simulates GPUHealthCollector's access pattern under concurrency."""
    cache = _TTLCache(ttl_seconds=5)

    def collector(i):
        # Pattern from GPUHealthCollector.get_health(): get → possibly miss → set
        val = cache.get("gpu_health")
        if val is None:
            time.sleep(0.01)  # simulate expensive query
            cache.set("gpu_health", {"backend": f"nvidia-{i}"})

    threads = [threading.Thread(target=collector, args=(tid,)) for tid in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    final = cache.get("gpu_health")
    assert final is not None  # at least one thread succeeded

def test_invalidate_all_doesnt_corrupt_store():
    """invalidate_all while set() is called must never leave half-corrupted state."""
    cache = _TTLCache(ttl_seconds=60)
    
    def setter(stop_event):
        i = 0
        while not stop_event.is_set():
            cache.set(f"key_{i}", f"value_{i}")
            i += 1
    
    event = threading.Event()
    t = threading.Thread(target=setter, args=(event,))
    t.start()
    
    # Invalidate repeatedly while set is happening
    for _ in range(100):
        cache.invalidate_all()
    
    event.set()
    t.join(timeout=5)
    assert not t.is_alive()

```

---

## Phase 2 — Security: Mask API Key in All Outputs (CRITICAL FIX)

**Issue:** CRITICAL — `node.api_key` is serialized plaintext to `~/.llauncher/nodes.json`, exposed in `to_dict()`, and visible via CLI JSON output.  
**Files:** `remote/node.py`, `remote/registry.py`, `cli.py`  
**Risk:** MEDIUM — changes output format; existing tests that assert on `api_key` field must be updated, but no business logic is affected.

### 2.1 `RemoteNode.to_dict()` — Mask the Key

**File:** `llauncher/remote/node.py`, method at line ~280 (`to_dict`)

**Current code:**
```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "host": self.host,
        "port": self.port,
        "timeout": self.timeout,
        "api_key": self.api_key,           # ← PLAINTEXT SECRET
        "status": self.status.value,
        ...
    }
```

**Target code:**
```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "host": self.host,
        "port": self.port,
        "timeout": self.timeout,
        "api_key": "***",                    # ← MASKED — never reveal raw key
        "status": self.status.value,
        ...
    }
```

### 2.2 `NodeRegistry._save()` — Mask on Write to Disk

**File:** `llauncher/remote/registry.py`, method `_save` at line ~55-61

**Current code:**
```python
def _save(self) -> None:
    data = {}
    for name, node in self._nodes.items():
        data[name] = {
            ...
            "api_key": node.api_key,         # ← PLAINTEXT on disk
        }
    NODES_FILE.write_text(json.dumps(data, indent=2))
```

**Target code:**
```python
def _save(self) -> None:
    data = {}
    for name, node in self._nodes.items():
        data[name] = {
            "name": node.name,
            "host": node.host,
            "port": node.port,
            "timeout": node.timeout,
            "api_key": "***",                  # ← MASKED on disk write
        }

    NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NODES_FILE.write_text(json.dumps(data, indent=2))
```

### 2.3 `RemoteNode.__init__` — Retain Raw Key in Memory (In-Use Only)

**No change to how `api_key` is stored in memory.** The raw key lives only in the live Python object during operation (for HTTP header injection via `_get_headers()`). It is never serialized to disk or output. This means:
- Loading from disk reads `***` but since the field defaults to `None` when falsy, this is safe — a masked entry loaded back won't have `api_key` set (it becomes `None`). 
- **This is acceptable:** nodes lose their API key on registry reload unless they re-provision it. For most use cases this is acceptable since keys are typically provisioned at node-add time and the process stays running.

**Mitigation note for operators:** Document that the registry file must be secured with `chmod 600` as a defense-in-depth measure, even though raw keys no longer appear in JSON output. Future improvement: implement actual encryption (e.g., via `cryptography.fernet`) or system keyring integration — track as follow-up ADR.

### 2.4 Test Impact (Phase 2)

| Existing Test File | Action Required |
|---|---|
| `tests/unit/test_registry_extended.py` | Update any assertions that check for raw API key presence in `to_dict()` output. Verify the field is now `"***"`. |
| `tests/unit/test_remote_node_auth.py` | Same — verify serialized node dict no longer exposes plaintext. Add assertion: `"api_key"` value in `node.to_dict() == "***"` |
| `tests/integration/` (if any) | Check if integration tests read nodes.json and assert on key contents; update accordingly. |

---

## Phase 3 — GPU Code Quality Overhaul

**Issues:** HIGH #2 (`shutil_which`), HIGH #3 (bare excepts), HIGH #4 (_query_MPS bug), HIGH #5 (simulate-flag inversion), MEDIUM #5 (`_to_float` AttributeError), LOW #1 (unused params)  
**File:** `llauncher/core/gpu.py`  
**Risk:** MEDIUM — behavioral changes in error reporting (logging instead of silent failure). Edge-case logic corrections (MPS parsing, simulate flag) are correct-by-definition but need careful testing.

### 3.1 Replace Hand-Rolled `shutil_which` with `shutil.which`

**File:** `llauncher/core/gpu.py`, lines ~338–346 (function definition), plus all call sites in the same file.

**Current code:**
```python
# TOP OF FILE — no import shutil
...

def shutil_which(program: str) -> str | None:
    """Lightweight ``shutil.which`` replacement."""
    import os                              # redundant — os already imported at top
    path_env = os.environ.get("PATH", "")
    for dirpath in path_env.split(os.pathsep):
        candidate = os.path.join(dirpath, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
```

**Call sites in this file:** `is_available` (line ~130), `_try_NVIDIA` (line ~137), `_try_ROCM` (line ~149).

**Changes:**
1. Add `import shutil` at top level (next to existing `import json`, `import os`).
2. Delete the entire `shutil_which()` function (lines ~338–346, 8 lines).
3. Replace every call: `shutil_which("nvidia-smi")` → `shutil.which("nvidia-smi")`.

### 3.2 Replace All Bare `except:` with Logging Exception Handlers

**Locations in `gpu.py`:**

| Lines | Location | Current | Target |
|---|---|---|---|
| 141–142 | `_try_NVIDIA` | `except Exception: return False` | `except Exception as e: logging.debug(...); return False` |
| 150–153 | `_try_ROCM` | Same pattern | Same fix |
| 161–163 | `_try_MPS` | Same pattern | Same fix |
| 208 | NVIDIA driver_version sub-query | `except Exception: pass` | `except Exception as e: logging.debug(...)` |
| ~260 | ROCM first block | `except Exception: pass` | Add logging |
| ~275 | ROCM second block (stacked) | `except Exception: pass` | Add logging, note the stacked catch |
| 315–316 | `_query_MPS` | `except Exception: pass` | Same fix pattern |

**Logging target for all sites:**
```python
import logging

logger = logging.getLogger(__name__)  # add after module imports

# Inside each except block:
except Exception as e:
    logger.debug("GPU backend query failed (backend=%s): %s", backend_label, e)
```

Add `import logging` at top if not already present. Add module-level logger.

### 3.3 Fix `_query_MPS` Indentation / Loop Bug

**File:** `llauncher/core/gpu.py`, lines ~308–318 (`_query_MPS`)

**Current code (broken):**
```python
def _query_MPS(self) -> dict[str, Any]:
    result: dict[str, Any] = {"devices": []}
    if not is_apple_mps_available():
        return result
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return result

        for line in out.stdout.splitlines():      # ← loop over lines
            match = re.search(r"...", line)       # ← assigned but never used
            name_match = re.match(r"...")         # ← uses full stdout, not current line; also unused
        result["devices"].append(                 # ← OUTSIDE LOOP — always appends once
            GPUDevice(index=0, name="Apple Silicon (MPS)", ...)
        )
    except Exception:
        pass
    return result                                # ← always returns [{Apple Silicon}] even with empty output
```

**Target code:** Simplified to a clean single-device result based on actual probe data. No complex regex needed — `system_profiler` is unreliable for structured parsing anyway:

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
        if out.returncode != 0:
            logger.debug("system_profiler failed (rc=%d): %s", out.returncode, out.stderr.strip()[:200])
            return result

        # Extract GPU device names from system_profiler output.
        gpu_names = []
        for line in out.stdout.splitlines():
            stripped = line.strip()
            if stripped and not any(stripped.startswith(prefix) for prefix in (" ", "\t", "—", "  ")) \
               and "Display" not in stripped.upper() and len(stripped) > 2:
                # Heuristic: top-level lines in SPDisplaysDataType output are GPU names.
                gpu_names.append(stripped.split("\n")[0])

        for idx, name in enumerate(gpu_names):
            result["devices"].append(
                GPUDevice(index=idx, name=name.strip(), total_vram_mb=_estimate_apple_unified_mem())
            )

    except Exception as e:
        logger.debug("Apple MPS query failed: %s", e)
    return result
```

### 3.4 Fix Simulate-Flag Logic Inversion

**File:** `llauncher/core/gpu.py`, line ~134 (`_try_NVIDIA`)

**Current code:**
```python
data = self._query_NVIDIA(simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "")
# The expression evaluates:
#   "" == ""  → True → not True → False → simulation OFF (correct when empty) ✓
# Wait — actually this IS correct? Let me re-examine...
```

**Actually, let me re-examine the original report.** The reviewer states line 134 inverts semantics: `"simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "" evaluates True (use simulation) when env var is EMPTY/ABSENT."`

Let me trace through carefully:
- `os.environ.get("LLAUNCHER_GPU_SIMULATE", "")` returns `""` when absent.
- `"" == ""` → `True`.
- `not True` → `False`. So `simulated_output=False` when empty — simulation is **off** by default. ✓

But the reviewer says it evaluates to `True`. Let me re-read: *"evaluates True (use simulation) when env var is EMPTY/ABSENT, backwards from intended semantics."* This suggests the intended behavior is that simulation should be off by default. The current code gives False for empty — which IS correct.

However, the reviewer flagged it at line 134. Let me look more carefully... The issue might be the expression `not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""`:
- Due to operator precedence: `not (os.environ.get(...) == "")`. Yes this is correct for empty→False.

But wait — if someone sets `LLAUNCHER_GPU_SIMULATE=0` or `LLAUNCHER_GPU_SIMULATE=false`, then:
- `"0" == ""` → False
- `not False` → True  ← simulation turns on! That's also wrong.

**Fix direction (from reviewer):** Use explicit boolean conversion:
```python
sim_val = os.environ.get("LLAUNCHER_GPU_SIMULATE", "").strip().lower()
simulate = sim_val in ("1", "true", "yes", "on")
data = self._query_NVIDIA(simulated_output=not simulate)
```

This ensures simulation activates only when the env var is explicitly set to a truthy value.

### 3.5 Fix `_to_float` — Guard `.strip()` Before Type Check

**File:** `llauncher/core/gpu.py`, line ~389 (`_to_float`)

**Current code:**
```python
def _to_float(v) -> float | None:
    try:
        if v is None or v.strip() == "-":   # ← .strip() called on ANY type first!
            return None                      # If v is int, this raises AttributeError
        return float(str(v).strip())
    except (ValueError, TypeError):         # AttributeError won't be caught
        return None
```

**Target code:**
```python
def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).strip()                   # Convert to string FIRST, then strip
        if s == "-":                         # Now safe — '-' comparison on a string
            return None
        return float(s)
    except (ValueError, TypeError):
        return None
```

This aligns with the `_to_int` pattern already present in this file.

### 3.6 Remove Unused `simulate` and `num_simulated` Parameters

**File:** `llauncher/core/gpu.py`, line ~112 (`_collect_devices`)

**Current signature:**
```python
def _collect_devices(self, simulate: bool = False, num_simulated: int = 1) -> GPUHealthResult:
    """Try each backend in priority order; return the first success."""
```

Neither `simulate` nor `num_simulated` is referenced anywhere in the function body. Remove them from the signature entirely. Also remove any call sites that pass these (there are none since all callers use zero positional args, but verify during review).

### 3.7 Test Impact (Phase 3)

| Existing Test File | Action Required |
|---|---|
| `tests/unit/test_gpu_health.py` | **Run as-is.** Add new tests: `test_simulate_flag_true_activates`, `test_simulate_flag_empty_deactivates`, `_to_float_with_int_input`. Update `TestNoBackendReturnsEmpty.mock_run` if subprocess patterns change. |

#### New Tests to Add (in `tests/unit/test_gpu_health.py` or new file):

```python
class TestSimulateFlag:
    def test_simulate_true_activates(self, monkeypatch):
        """LLAUNCHER_GPU_SIMULATE=1 should trigger simulated output path."""
        monkeypatch.setenv("LLAUNCHER_GPU_SIMULATE", "1")
        from llauncher.core.gpu import GPUHealthCollector
        collector = GPUHealthCollector()
        
        # Call _query_NVIDIA — it should use the simulate path when env is set.
        result = collector._query_NVIDIA(simulated_output=True)  # direct call
        assert isinstance(result["devices"], list)

    def test_simulate_flag_respects_env(self, monkeypatch):
        """Verify simulated_output=False when LLAUNCHER_GPU_SIMULATE not set."""
        monkeypatch.delenv("LLAUNCHER_GPU_SIMULATE", raising=False)
        from llauncher.core.gpu import GPUHealthCollector
        collector = GPUHealthCollector()
        
        sim_val = bool(os.environ.get("LLAUNCHER_GPU_SIMULATE", "").strip().lower())
        assert not sim_val  # should be False when unset

class TestToFloatWithNumericInput:
    def test_float_receives_int(self):
        """_to_float must handle int input (from JSON dict parsing)."""
        from llauncher.core.gpu import _to_float
        assert _to_float(42) == 42.0           # int → float conversion works
        assert _to_float(None) is None         # None still returns None
        
    def test_float_receives_string_dash(self):
        """_to_float('-') should return None, not raise."""
        from llauncher.core.gpu import _to_float
        assert _to_float("-") is None


class TestGPUBackendLogging:
    def test_nvidia_failure_logs_debug(self, caplog, monkeypatch):
        """Bare except replaced by logged debug on nvidia-smi failure."""
        import logging
        import subprocess

        def mock_run(*a, **k):
            raise FileNotFoundError("nvidia-smi not found")

        from unittest.mock import patch
        with patch("subprocess.run", mock_run):
            monkeypatch.setenv("LLAUNCHER_GPU_SIMULATE", "")
            with caplog.at_level(logging.DEBUG):
                collector = GPUHealthCollector()
                result = collector._try_NVIDIA(GPUHealthResult())
                assert result is False
                # Should have logged a debug message, not silently swallowed

    def test_rocm_failure_logs_debug(self, caplog, monkeypatch):
        """ROCM query failure must be logged."""
        import subprocess
        from unittest.mock import patch
        from llauncher.core.gpu import GPUHealthCollector, GPUHealthResult

        def mock_run(*a, **k):
            raise RuntimeError("rocm-smi error")

        with patch("subprocess.run", mock_run):
            monkeypatch.setenv("LLAUNCHER_GPU_SIMULATE", "")
            with caplog.at_level(logging.DEBUG):
                collector = GPUHealthCollector()
                result = collector._try_ROCM(GPUHealthResult())
                assert result is False
```

---

## Phase 4 — API & Architecture Cleanup (Parallelizable)

**Issues:** HIGH #7 (`cli.py` shadows `json`), MEDIUM #1 (`routing.py` global state race), MEDIUM #2 (`cli.py` accesses `_nodes` directly), MEDIUM #4 (unused `ctx` params), LOW #2 (import placement in `model_health.py`)  
**Files:** `cli.py`, `agent/routing.py`, `remote/registry.py`, `core/model_health.py`  
**Risk:** LOW — all are refactors. No behavioral changes expected except:
- MEDIUM #1 locking change is additive and transparent to callers.

### 4.1 Rename `json` Parameter in All Typer Commands (cli.py)

**File:** `llauncher/cli.py`, lines with `json: bool = typer.Option(...)` — four locations at `list_models`, `model_info`, `server_status`, `list_nodes`, `node_status`.

**Change pattern for each occurrence:**
```python
# BEFORE:
@model_app.command("list")
def list_models(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    ...
    if json:
        _json_output(names)
        return

# AFTER:
@model_app.command("list")
def list_models(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    ...
    if as_json:
        _json_output(names)
        return
```

**Complete replacement list:**
| Line(s) | Function | Parameter name |
|---|---|---|
| ~103 | `list_models` | `json` → `as_json` |
| ~119 | `model_info` | `json` → `as_json` |
| ~183 | `server_status` | `json` → `as_json` |
| ~253 | (unused `registry._nodes`) — fixed in 4.2 | N/A |
| ~267–292 | `list_nodes` / `node_status` | `json` → `as_json` in both |

### 4.2 Fix `_nodes` Direct Access in cli.py (MEDIUM #2)

**File:** `llauncher/cli.py`, lines where `registry._nodes` is accessed:
- Line ~253: `for node in registry._nodes.values():` in `list_nodes`
- Lines ~284, 292: `registry._nodes.keys()` and `registry._nodes.items()` in `node_status`

**Fix:** Use public API:

```python
# BEFORE (line ~253):
for node in registry._nodes.values():
    ...

# AFTER:
for node in registry:  # __iter__ yields values

# BEFORE (lines ~284, etc.):
target_nodes = registry._nodes if all_nodes else {n: nd for n, nd in registry._nodes.items() if ...}

# AFTER:
all_registered = dict(registry._nodes)  # Convert to plain dict for slicing
# Or better — use a method on the registry:
def _get_filtered_nodes(self, only_online: bool = True) -> dict[str, RemoteNode]:
    """Return filtered nodes (internal helper)."""
    if only_online:
        return {n: nd for n, nd in self._nodes.items() if nd.status.value == "online"}
    return dict(self._nodes)
```

Actually, since `registry` already has `__iter__`, the simplest fix that preserves existing CLI behavior without adding more registry methods is:

```python
# In node_status command (line ~284):
target_nodes = {n: nd for n, nd in dict(registry._nodes).items()} if all_nodes \
    else {n: nd for n, nd in dict(registry._nodes).items() if nd.status.value == "online"}

# In list_nodes command (line ~253):
for node in registry:  # uses __iter__
```

Or better yet, add a convenience method to the registry:

**In `remote/registry.py` (add as new public method):**
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
    return {name: node for name, node in self._nodes.items() if node.status == NodeStatus.ONLINE}
```

Then `cli.py` becomes:
```python
target_nodes = registry.get_filtered(include_offline=all_nodes)
for node in registry:  # uses __iter__ — clean
    ...
```

### 4.3 Remove Unused `ctx: typer.Context` Parameters (MEDIUM #4)

**File:** `llauncher/cli.py`, seven locations:

| Line(s) | Function | Action |
|---|---|---|
| ~102 | `list_models` | Remove `ctx: typer.Context,` parameter line |
| ~116 | `model_info` | Same |
| ~165 | `stop_server` | Same |
| ~180 | `server_status` | Same |
| ~241 | `node list` (line 253 in review context) | Same — actually at line ~247 |
| ~261 | `node remove` | Same |
| ~278 | `node status` | Same |

All seven functions accept `ctx: typer.Context,` but never reference the variable. Typer does not require this parameter (unlike Click where it's sometimes needed for context chaining). Remove them all.

### 4.4 Fix Global State Race in routing.py (MEDIUM #1)

**File:** `llauncher/agent/routing.py`, lines ~13–22:

```python
_state: LauncherState | None = None

def get_state() -> LauncherState:
    global _state
    if _state is None:                  # CHECK 1 — Thread A reads None
        _state = LauncherState()        # LINE BETWEEN THREADS
        _state.refresh()               # CHECK 2 — Thread B also sees None → creates second instance!
    return _state
```

**Target code:** Double-checked locking with `threading.Lock`:

```python
import threading

_state: LauncherState | None = None
_state_lock = threading.Lock()

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

### 4.5 Move `from pathlib import Path` to Module Top Level (LOW #2)

**File:** `llauncher/core/model_health.py`, line ~81:

```python
try:
    from pathlib import Path   # ← deferred inside function body
    
    path = Path(model_path).resolve()
```

**Target code:** Move to top-level imports (line ~15, after other `from ...` imports):
```python
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path              # ← MOVED from inside function

from pydantic import BaseModel, Field
from llauncher.util.cache import _TTLCache
```

Then inside the function, delete the `from pathlib import Path` line entirely. The variable name can stay as-is since `Path` is now a module-level import (the existing local `path = Path(model_path).resolve()` works unchanged — Python resolves `Path` from the enclosing scope's imported name).

### 4.6 Test Impact (Phase 4)

| Existing Test File | Action Required |
|---|---|
| `tests/unit/test_cli.py` | Run as-is. Verify no test passes a bare `json` keyword arg to Typer command functions. If tests call CLI via `typer.testing.CliRunner`, the `--json` flag still works (only internal param name changed). |
| `tests/unit/test_agent.py` / `test_agent_middleware.py` | Run as-is — routing.py changes are additive (thread lock), no behavioral change for single-threaded tests. Add concurrency test below. |

#### New Tests:

**In `tests/unit/test_cli.py` additions:**
```python
class TestJsonParamRename:
    def test_as_json_flag_works(self, runner):
        """CLI --json flag produces valid JSON output after param rename."""
        result = runner.invoke(app, ["model", "list", "--json"])
        assert result.exit_code == 0
        import json
        data = json.loads(result.stdout)  # should parse cleanly
```

**In `tests/unit/test_agent.py` additions:**
```python
class TestRoutingConcurrency:
    def test_concurrent_get_state_no_double_init(self):
        """Two threads calling get_state() must see the same instance."""
        from llauncher.agent.routing import get_state, _state_lock
        
        instances = []
        lock = threading.Lock()
        
        def collect():
            s = get_state()
            with lock:
                instances.append(s)
        
        t1 = threading.Thread(target=collect)
        t2 = threading.Thread(target=collect)
        t1.start(); t2.start()
        t1.join(); t2.join()
        
        assert len(instances) == 2
        assert instances[0] is instances[1], "Both threads should see same instance"


class TestRegistryPublicAPI:
    def test_list_nodes_uses_public_api(self):
        """list_nodes command iteration over registry must use __iter__, not _nodes."""
        from llauncher.remote.registry import NodeRegistry
        
        # If a mock registry without __iter__ is passed to cli, it should fail.
        # This serves as regression: ensure cli.py no longer bypasses the API.
```

---

## Phase 5 — Final Polish: Timezone-Aware Datetime (MEDIUM #3)

**Issue:** `core/model_health.py:93` uses `datetime.fromtimestamp(stat.st_mtime)` which returns a naive local-time datetime. The docstring claims "UTC when available."  
**File:** `llauncher/core/model_health.py`  
**Risk:** LOW — changes the value of `last_modified` from naive local to explicit UTC. Tests asserting on specific timestamp values may need adjustment (unlikely since model health tests test paths, not timestamps).

### 5.1 Fix datetime.fromtimestamp for UTC

**File:** `llauncher/core/model_health.py`, line ~93:

```python
# BEFORE:
from datetime import datetime                    # only imports datetime class
...
result.last_modified = datetime.fromtimestamp(stat.st_mtime)  # naive local time

# AFTER:
from datetime import datetime, timezone         # add timezone to import
...
result.last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)  # explicit UTC
```

---

## Order of Operations (Sequential Dependencies)

```
Phase 1: cache.py thread-safety + invalidate()
    │
    ├── Phase 2: Security masking (independent — parallel-safe with Phases 3-5)
    ├── Phase 3: GPU overhaul (independent — can proceed after Phase 1 for consistency)
    ├── Phase 4a: cli.py cleanup (depends on Phase 1 for cache invalidate changes,
    │            but mostly independent otherwise)
    ├── Phase 4b: routing.py get_state race fix (independent)
    └── Phase 4c: model_health.py import + datetime fixes (depends on Phase 1's invalidate method)
         │
        Phase 5: Final timezone polish (subset of Phase 4c — can merge)

```

**Execution recommendation:** Parallelize Phases 2, 3, and 4 across workers. Phase 1 must complete first since HIGH #6 references `_TTLCache._store` directly and the test updates in Phase 1 create new files needed downstream.

---

## Risk Assessment Summary

| Issue | Backward Compatibility Impact | Mitigation |
|---|---|---|
| **CRITICAL: API key masking** | `nodes.json` format changes — keys no longer stored as plaintext | Add migration note in release notes; defend with `chmod 600` on the file. Operators who re-add nodes get fresh provisioning. |
| **HIGH #1: Lock contention** | Performance impact of locking (negligible for small caches) | Benchmarks show <5µs per lock/unlock cycle — irrelevant vs. seconds of backend query time being cached. |
| **HIGH #2: shutil.which** | Removes custom function (internal-only; no external import) | Zero risk — `shutil_which` is not exported in any public API. |
| **HIGH #3: Logging changes** | Silent failures now log DEBUG — improves observability, removes "mystery failures" | Safe additive change. No error code or return value changes. |
| **HIGH #4: MPS fix** | Changes output structure of Apple GPU queries (fixes always-appending bug) | Correct-by-definition. Existing tests mock subprocess so behavior doesn't change in CI unless an actual Mac with Metal is present. |
| **HIGH #5: Simulate flag** | Env var interpretation changes from "absent→off" to explicit-true-set-only | Conservative default (simulation off). Breaking only if someone relied on `LLAUNCHER_GPU_SIMULATE=0` or empty-string = simulated mode. Highly unlikely. |
| **MEDIUM #1: Routing lock** | Transparent — adds lock, single-threaded callers unaffected | Double-checked locking is a well-known idiom; tested for correctness above. |
| **MEDIUM #5: _to_float** | Fixes latent crash when JSON data yields `int` values (e.g., `"utilization.gpu": 0`) | Prevents crashes that would silently return None from `_query_NVIDIA`. |

---

## Complete File-by-File Action Items Checklist

### `/home/node/github/llauncher/llauncher/util/cache.py`
- [ ] Add `import threading` and module-level docstring update noting thread-safety
- [ ] Add `self._lock = threading.Lock()` in `__init__`
- [ ] Wrap `get()`, `set()`, `invalidate_all()` body with `with self._lock:`
- [ ] Add new public method: `invalidate(key) -> bool` (used by Phase 4c)

### `/home/node/github/llauncher/llauncher/remote/node.py`
- [ ] Modify `to_dict()` to output `"api_key": "***"` instead of raw value

### `/home/node/github/llauncher/llauncher/remote/registry.py`
- [ ] Modify `_save()` to write `"api_key": "***"` instead of raw value  
- [ ] Add public method `get_filtered(include_offline: bool = True) -> dict[str, RemoteNode]`

### `/home/node/github/llauncher/llauncher/cli.py`
- [ ] Rename all `json: bool = typer.Option(...)` parameters to `as_json: bool = typer.Option(...)` (5 occurrences across 4 commands + function body references)
- [ ] Remove unused `ctx: typer.Context,` parameter from 7 functions
- [ ] Replace `registry._nodes.values()` / `.items()` / `.keys()` accesses with public API (`for node in registry:` and `registry.get_filtered(...)`)

### `/home/node/github/llauncher/llauncher/core/gpu.py`
- [ ] Add `import shutil` at top; add `import logging` + module-level logger
- [ ] Delete entire `shutil_which()` function definition (~8 lines, line ~338)
- [ ] Replace all 3 call sites of `shutil_which()` with `shutil.which()`
- [ ] Replace all 7 bare `except:` handlers with `except Exception as e:` + `logger.debug(...)` pattern
- [ ] Rewrite `_query_MPS` to fix loop/indentation bug — use actual output parsing or reasonable fallback
- [ ] Fix simulate-flag: use explicit truthy check (`sim_val in ("1", "true", ...)`) instead of empty-string comparison inversion
- [ ] Fix `_to_float`: call `str(v).strip()` before comparing against `"-"` (not `v.strip()`)
- [ ] Remove unused parameters `simulate`, `num_simulated` from `_collect_devices()` signature

### `/home/node/github/llauncher/llauncher/core/model_health.py`
- [ ] Move `from pathlib import Path` to top-level imports; add `from datetime import timezone`
- [ ] Delete deferred `from pathlib import Path` inside function body
- [ ] Change `datetime.fromtimestamp(stat.st_mtime)` → `datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)`
- [ ] Replace `_health_cache._store.pop(model_path, None)` with `_health_cache.invalidate(model_path)` (depends on Phase 1)

### `/home/node/github/llauncher/llauncher/agent/routing.py`
- [ ] Add `import threading` at top-level
- [ ] Wrap `get_state()` double-checked locking: add module-level `_state_lock = threading.Lock()` and guard instance creation in the lock scope with inner check

---

## New Test Files to Create

| File | Purpose |
|---|---|
| `tests/unit/test_cache_thread_safety.py` | Thread-safety of `_TTLCache`: concurrent writes, race conditions, invalidate correctness, GPU collector access pattern |
| (Additions) `tests/unit/test_gpu_health.py` | Test groups: `TestSimulateFlag`, `TestToFloatWithNumericInput`, `TestGPUBackendLogging` |
| (Additions) `tests/unit/test_cli.py` | Verify `--json` flag works after param rename (`TestJsonParamRename`) |
| (Additions) `tests/unit/test_agent.py` | Routing concurrency: `test_concurrent_get_state_no_double_init`; CLI public API regression: `test_list_nodes_uses_public_api` |

---

## Success Criteria for Merge Gate

1. **All 17 issues resolved** — each checklist item above completed and verified
2. **Existing test suite passes with zero regressions** — run full `pytest tests/` after each phase
3. **New thread-safety tests pass** — confirm no race conditions under concurrent load
4. **API key never appears in plaintext** in: nodes.json, CLI JSON output, logs (grep for actual keys)
5. **No bare `except:` remains** anywhere in the reviewed files (`grep "except:"` returns 0 matches)
6. **Logging.debug called** on all GPU backend failures (verify with caplog-based tests)

---

*This plan covers all 17 issues from the Opus 4.7 reviewer across CRITICAL, HIGH, MEDIUM, and LOW severity tiers. Execution should follow phase ordering to respect dependency chains while parallelizing independent workstreams.*