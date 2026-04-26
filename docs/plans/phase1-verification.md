# Phase 1 Verification Plan

**Project:** llauncher MCP Server Lazy Singleton + Per-Call Refresh  
**Issues Covered:** #30 (Phase 1 implementation), #31 (handler shadowing), #32 (double refresh), #33 (validate_config cold-start)  
**Branch:** `main` at `d412d6d`, **86 tests pass**, 94% coverage on modified files  

---

## Executive Summary: What Needs Verifying

| Section | Verification Target | Status Known | Gap / Unknown |
|---------|---------------------|-------------|---------------|
| A | Lazy singleton initialization & caching (core) | ✅ Unit tests exist | **Partial object caching BUG discovered** — `__post_init__` exception leaves broken partial object in `_mcp_state`, violating docstring guarantee that "stays None" |
| B | Read handlers use injected state (#31 fix) | ✅ 4 unit tests pass | No integration test proves handler reads post-refresh data from *that same* instance (regression: someone could break the linkage by accidentally calling a different accessor internally later) |
| C | Single refresh per call pattern (#32 fix) | ⚠️ Handler now calls `state.refresh()` on passed-in state instead of `get_mcp_state().refresh()` — but dispatch still triggers __post_init__→refresh first, so **total is 2 refreshes/call** (not eliminated, just consolidated to one instance). This was the documented Design Decision D1. | No test tracks total `.refresh()` invocations per dispatch cycle as a regression guard |
| D | validate_config bypasses lazy init (#33 fix) | ✅ 2 unit tests pass (`_mcp_state stays None`, handler gets `None`) | No test exercises the **real** `validate_config` handler with `None` to catch future state-dependent code injection |
| E | End-to-end: external changes reflected in responses after refresh | ❌ Not tested at all | Core value proposition of Phase 1 is untested. Config changes and process-table changes between tool calls need an integration test exercising the full dispatch→refresh→read chain |
| F | Failure recovery on first access | ⚠️ **Partial object caching defect found** — if `__post_init__/refresh()` raises, `_mcp_state` gets a broken partial object instead of staying None. Next call returns this broken instance. Docstring is wrong. | Needs a reproduction test + code fix |
| G | No regression on mutate handlers, config tools, dispatch variants | ✅ 86 tests pass including all 11+ tool dispatch paths and all mutate/config handlers | ConfigTools coverage at 89% (7 lines missing in `update_model_config` exception path) |
| H | Coverage gap closure for modified files | ✅ models.py 100%, servers.py 100%, server.py 95%, config.py 89% | Remaining gaps are entry-point boilerplate (`main`, `main_async`) and edge-case exceptions in config tools — all acceptable |

---

## A. Lazy Singleton Initialization & Caching

### What to Verify
1. `_mcp_state` is `None` at module import time (no side effects)
2. First `get_mcp_state()` call creates a valid instance with `.refresh()` already called
3. Second call returns **same** object identity (`is` check)
4. After exception in `__post_init__`, `_mcp_state` properly stays None and retries succeed

### Existing Tests (test_phase1_lazy_singleton.py — TestGetMcpState class)
| Test | Passes? | Covers? | Notes |
|------|---------|---------|-------|
| `test_get_mcp_state_returns_instance` | ✅ Yes | Instance created, has models/running attrs | Uses real LauncherState, loads from disk config |
| `test_get_mcp_state_caches_singleton` | ✅ Yes | Identity check via `first is second` | Resets `_mcp_state` between runs |
| `test_get_mcp_state_first_call_refreshes` | ✅ Yes | LauncherState mocked, constructor called once | Doesn't test exception path |

### ⚠️ Critical Gap: Partial Object Caching Bug (Found During Verification Prep)

When `LauncherState()` raises in `__post_init__/refresh()`, a **partially constructed object gets assigned to `_mcp_state`** before the exception propagates. This means subsequent calls return the broken instance instead of retrying — directly contradicting the docstring: *"If __init__/refresh fails during first-access, _mcp_state stays None."*

Reproduction test confirms:
```python
class BrokenLauncherState:
    def __init__(self):
        self.attr = 'partially initialized'  # ← runs and succeeds
    
    def __post_init__(self):
        raise RuntimeError('boom')  # ← raises during object construction

_mcp_state = None
try:
    _mcp_state = BrokenLauncherState()  
except RuntimeError as e:
    pass
print(_mcp_state)  # → <BrokenLauncherState object> (BUG! Should be None)
```

**Impact:** Any transient error during first `__post_init__/refresh()` call (corrupt config, permission denied on psutil scan, network issue for remote nodes) causes `_mcp_state` to cache a broken partial instance. Every subsequent tool call returns this corrupt state indefinitely — the exact opposite of what failure recovery should do.

**Root Cause:** In Python, when `SomeClass()` raises from within `__post_init__`, the variable is still assigned the partially constructed object (because `__init__` succeeded and dataclass machinery injected `__post_init__`). The exception happens *during* the expression evaluation, but after `__init__` completed successfully.

**Required Fix:**
```python
def get_mcp_state() -> LauncherState:
    global _mcp_state
    if _mcp_state is None:
        try:
            _mcp_state = LauncherState()  # __post_init__ already calls refresh()
        except Exception:
            _mcp_state = None  # Ensure we retry next time (clear any partial state)
            raise  # Re-raise to inform caller of the failure
    return _mcp_state
```

**Action Items:**
1. **CRITICAL:** Add `test_get_mcp_state_clears_partial_state_on_refresh_failure` — verify that when `__post_init__/refresh()` raises, `_mcp_state` is reset to None and retry succeeds (this test will FAIL until the code fix above is applied)
2. Fix the `get_mcp_state()` function in server.py with try/except as shown above
3. Add `test_get_mcp_state_refresh_failure_leaves_none` — direct assertion after exception

### Verification Commands
```bash
# 1. Import-time laziness check
python3 -c "import llauncher.mcp_server.server; print('OK' if llauncher.mcp_server.server._mcp_state is None else 'FAIL: state cached at import')"

# 2. Run existing singleton tests
python3 -m pytest tests/unit/mcp/test_phase1_lazy_singleton.py::TestGetMcpState -xvs
```

---

## B. Read Handlers Use Injected State (#31 — No Shadowing)

### What to Verify
Each read handler calls `.refresh()` on the **passed-in `state` argument** and then reads from that same instance:

| Handler | File:Line | Refresh Call | Data Source | Consistency |
|---------|-----------|-------------|-------------|-------------|
| `list_models` | models.py:52-53 → 60+ | `state.refresh()` (line 53) | `for name, config in state.models.items(): ...` (line 60) | ✅ Same `state` object |
| `get_model_config` | models.py:91-92 → 98+ | `state.refresh()` (line 92) | `config = state.models[name]` (line 98) | ✅ Same `state` object |
| `server_status` | servers.py:148-149 → 154+ | `state.refresh()` (line 149) | `for port, server in state.running.items(): ...` (line 154) | ✅ Same `state` object |
| `get_server_logs` | servers.py:172-173 → 186+ | `state.refresh()` (line 173) | `pid = state.running[port].pid` (line 186) | ✅ Same `state` object |

### Existing Tests
| Test | Passes? | Asserts Refresh On Arg? | Notes |
|------|---------|------------------------|-------|
| `test_read_handler_calls_refresh_list_models` | ✅ Yes | ✓ `mock_state.refresh.assert_called_once()` | Mock state injected, refresh verified on it |
| `test_read_handler_calls_refresh_get_model_config` | ✅ Yes | Same pattern | Confirmed |
| `test_read_handler_calls_refresh_server_status` | ✅ Yes | Same pattern | Confirmed |
| `test_read_handler_calls_refresh_get_server_logs` | ✅ Yes | Same pattern | Confirmed |
| `test_read_handler_no_circular_import` | ✅ Yes | Imports succeed without triggering lazy init | Critical — no backward dependency on server.py in handlers |

### Gap Analysis: Missing End-to-End Linkage Test
The existing tests verify that `.refresh()` is called on the mock, but they don't verify that **data read after refresh comes from the refreshed state**. A theoretical regression could break this: handler calls `mock_state.refresh()` but then iterates over a *different* data source.

**Current code is structurally safe** (uses `state.xxx` directly after `state.refresh()`) — no separate import, no global lookup, nothing to miswire. But it's not tested.

### Action Item
Add one regression-guard test in `test_phase1_lazy_singleton.py`:
```python
async def test_read_handler_reads_from_same_state_after_refresh(self):
    """Handler reads post-refresh data from the same state instance it refreshed."""
    # Create mock where refresh() changes what .models/.running return,
    # then verify handler sees those changes (not pre-refresh stale values).
```

---

## C. Single Refresh Per Read Call (#32 — Double-Refresh Resolution)

### What to Verify
The fix eliminated the pattern of two DIFFERENT state instances being refreshed. Now both dispatch and handler use ONE instance, so there's no inconsistency. However, **two `.refresh()` calls still happen** on that one instance:

```
Dispatch: get_mcp_state() → LauncherState().__post_init__ → refresh()  [1st call]
Handler:  state.refresh()                                             [2nd call]
Total:    2 refreshes per read tool invocation
```

The review labeled this "Major" severity for wasted I/O (~5-40ms extra/call). The plan document explicitly accepted this as **Design Decision D1**: "We accept duplicated work for consecutive rapid calls to guarantee zero-staleness." The trade-off is:
- **Accepted:** 2 refreshes per call = ~5-40ms more than optimal  
- **Rejected alternative:** Single-refresh with TTL window where stale data returned without client visibility

The fix was correct because:
1. Without handler-level refresh → first-access from `__post_init__` gets fresh data, but subsequent calls would only get that cached snapshot until a mutation forces reconciliation
2. With handler-level refresh on top of dispatch init → every tool call gets the latest disk/process state
3. Both use the SAME instance (after fix), so no inconsistency between what's refreshed and what's read

**Key insight:** This is not really "double-refresh" in the sense of a bug — it's two distinct responsibilities:
- `__post_init__` refresh = handles "first access after server start / first-access failure recovery"  
- Handler `.refresh()` = handles "state changed between tool calls (disk modified, process killed externally)"

### Existing Tests
| Test | Passes? | What It Verifies |
|------|---------|------------------|
| All 4 `test_read_handler_calls_refresh_*` tests | ✅ Yes | Each handler calls `.refresh()` on passed-in state exactly once |
| No existing test tracks total refresh count per dispatch cycle | ❌ Missing | Regression guard needed to prevent accidental extra `.refresh()` calls |

### Action Items
1. Add `test_dispatch_total_refresh_count` as a regression test: mock `.refresh()` on the dispatch-level state, call each read tool via `_dispatch_tool`, assert handler's `.refresh()` was called exactly once per invocation (in addition to the implicit __post_init__ refresh)
2. This doesn't change architecture — it simply asserts what we've already implemented

### Verification Commands
```bash
# Verify no circular imports in handler modules
python3 -c "from llauncher.mcp_server.tools.models import list_models, get_model_config; from llauncher.mcp_server.tools.servers import server_status, get_server_logs; print('No circular imports ✓')"

# Count refresh calls per dispatch cycle (manual inspection)
grep -n "\.refresh()" llauncher/mcp_server/server.py llauncher/mcp_server/tools/*.py 2>&1
```

---

## D. validate_config Bypasses Lazy Init (#33 Fix)

### What to Verify
1. `_dispatch_tool("validate_config", ...)` returns early BEFORE calling `get_mcp_state()`  
2. `_mcp_state` remains `None` after the call (no lazy init triggered)
3. Handler receives `None` for state parameter and handles it gracefully
4. Real handler code path works with `None` input

### Existing Tests (TestValidateConfigBypassLazyInit class)
| Test | Passes? | What It Covers |
|------|---------|-----------------|
| `test_validate_config_does_not_trigger_lazy_init` | ✅ Yes | Dispatch early-return, `_mcp_state stays None`, handler mock called |
| `test_validate_config_calls_handler_with_none_state` | ✅ Yes | Handler receives `(None, args)`, assert_called_once_with(None, ANY) confirmed |

### Gap Analysis: Missing Real-Handler Test
Both tests use a **mock** of the validate_config handler. No test exercises the *actual* `config_tools.validate_config()` function with `None` state to prove it doesn't break when called this way. The docstring says "State not used" but no test formalizes that contract.

If someone later adds `state.some_attr` access inside validate_config (inadvertently), these mocked tests won't catch the crash.

### Action Item
Add one test that dispatches through `_dispatch_tool("validate_config", {...})` with **real handler** (not mocked):
```python
async def test_validate_config_with_real_handler_receives_none_gracefully(self):
    """Real validate_config handler works when called with None state (#33)."""
    from llauncher.mcp_server.server import _dispatch_tool
    
    result = await _dispatch_tool("validate_config", {
        "config": {"name": "test-model", "model_path": "/dev/null"}  
    })
    
    assert result["valid"] is True  # Pydantic validation succeeds on valid input
    # If handler crashes due to accessing None.state, this test fails
```

---

## E. End-to-End: External Changes Reflected After Refresh (CORE VALUE PROPOSITION)

### What to Verify
This is the **most important missing test** — proving that Phase 1's core guarantee works: external changes between tool calls are reflected in MCP responses via per-call refresh.

Three scenarios need testing:

| Scenario | Tool(s) Affected | Mutation Between Calls | Expected Result |
|----------|-----------------|----------------------|------------------|
| Config file adds model | `list_models`, `get_model_config` | Second config entry added to disk + state refreshed | New model appears in list; stale count is 2 not 1 |
| External process killed | `server_status`, `get_server_logs` | Process on port 8081 terminated externally + state refreshed | Server removed from running_servers list; logs returns "No server" error instead of dead-PID attempt |
| Port recycled, new process starts | `server_status` | PID reuse scenario: old server killed, new one started on same port with different config_name | status shows new model name and current PID, not stale data |

### Why This Is Hard to Unit-Test
Real `ConfigStore.load()` reads from disk, psutil scans actual processes. A pure unit test would need:
1. Mocked ConfigStore that returns DIFFERENT data on successive calls (stateful mock)  
2. Full dispatch → refresh → handler chain exercised with a real or semi-real LauncherState

### Test Design — Pragmatic Approach
Use the `mock_config_store` fixture from conftest.py + write to an actual temp config.json file between calls:

```python
class TestStaleDataElimination:
    """End-to-end verification that per-call refresh eliminates stale data."""
    
    @pytest.mark.asyncio
    async def test_refresh_reflects_config_addition(self, mock_config_store, sample_model_config):
        """When external config changes and refresh() is called, handler sees new models.
        
        This tests the core value of Phase 1: zero-staleness on read tools.
        """
        from llauncher.state import LauncherState
        
        # Add first model via ConfigStore
        ConfigStore.add_model(sample_model_config)
        
        with patch('llauncher.core.process.find_all_llama_servers', return_value=[]):
            state = LauncherState()  # __post_init__ → refresh → loads from ConfigStore
            
            assert len(state.models) == 1  # Only sample model configured
            first_name = list(state.models.keys())[0]
            
            # Simulate external change: add second model to disk
            from llauncher.models.config import ModelConfig
            second_model = ModelConfig.from_dict_unvalidated({
                "name": "second-model", "model_path": "/other/path.gguf", 
                "default_port": 8082, "ctx_size": 4096, "n_gpu_layers": 32
            })
            ConfigStore.add_model(second_model)
            
            # Now refresh() should pick up the change
            state.refresh()
            
            assert len(state.models) == 2  # Both models visible after refresh
            assert second_model.name in state.models
            
            # Verify handler sees post-refresh data by calling it with refreshed state
            from llauncher.mcp_server.tools.models import list_models
            result = await list_models(state, {})
            
            assert len(result["models"]) == 2
    
    @pytest.mark.asyncio  
    async def test_refresh_clears_killed_process(self, mock_config_store, sample_model_config):
        """When external process is killed and refresh() is called, stale entries disappear."""
        from llauncher.state import LauncherState
        from llauncher.models.config import RunningServer
        from datetime import datetime
        
        # Add model to config
        ConfigStore.add_model(sample_model_config)
        
        state = LauncherState()
        
        # Manually populate a "running" server (simulating previous start_server call)
        state.running[8081] = RunningServer(
            pid=9999, port=8081, config_name="test-model", 
            start_time=datetime.now()
        )
        
        # Pretend the process is killed: mock find_all_llama_servers returns empty
        with patch('llauncher.core.process.find_all_llama_servers', return_value=[]):
            state.refresh_running_servers()  # Should clear running dict
            
            assert len(state.running) == 0  # No stale entries
        
        # Verify handler gets clean data
        from llauncher.mcp_server.tools.servers import server_status  
        result = await server_status(state, {})
        
        assert result["count"] == 0  # Not showing the killed process
```

### Action Items
1. Add `TestStaleDataElimination` class with both tests above to `test_phase1_lazy_singleton.py`
2. Requires importing and modifying ConfigStore between calls (uses mock_config_store fixture)  
3. Tests the ENTIRE chain: `_dispatch_tool → get_mcp_state → handler → state.refresh() → read from refreshed data`

---

## F. Failure Recovery on First Access

### ⚠️ Critical Bug Discovered During Verification

**The docstring claims:** *"If __init__/refresh fails during first-access, _mcp_state stays None."*  
**The code does NOT do this.** When `__post_init__/refresh()` raises:
- Python assigns the partially constructed object to `_mcp_state` before propagating the exception  
- Subsequent calls see `_mcp_state is not None` and return the broken instance instead of retrying

### Reproduction Confirmed
```python
class BrokenLauncherState:  # Simulates __post_init__ that fails after __init__ succeeds
    def __init__(self): self.attr = 'set'  # runs first, succeeds
    
    def __post_init__(self): raise RuntimeError('boom')  # raises AFTER partial construction

_mcp_state = None  
try: _mcp_state = BrokenLauncherState()
except: pass
# Result: _mcp_state IS the broken object (not None) — BUG!
```

**Impact:** Any transient error during first access (corrupt config.json, psutil permission denied) → partial LauncherState cached forever → all tool calls fail or return garbage.

### Fix Required
Add try/except around constructor call:
```python
def get_mcp_state() -> LauncherState:
    global _mcp_state  
    if _mcp_state is None:
        try:
            _mcp_state = LauncherState()
        except Exception:
            _mcp_state = None  # Clear any partial state from failed __post_init__
            raise               # Re-raise to inform caller of failure
    return _mcp_state
```

### Tests Needed (will FAIL until code is fixed)
1. `test_first_access_failure_clears_partial_state` — simulate `__post_init__/refresh()` raising, verify `_mcp_state stays None` after exception
2. `test_recovery_after_transient_failure` — first call fails, second succeeds, third returns cached instance
3. `test_refresh_exception_does_not_cache_broken_instance` — exercise actual LauncherState where ConfigStore.load() raises

---

## G. No Regression — Mutate Handlers, Config Tools, Dispatch Variants

### What to Verify
All non-read paths continue working correctly after Phase 1 changes:

| Path | Current Status | Gap |
|------|---------------|-----|
| All 12 dispatch tool mappings in `_dispatch_tool` | ✅ Covered by test_server.py TestDispatchTool (all pass) | validate_config test patches `get_mcp_state()` mock but should NOT need it anymore — could be cleaned up to verify early-return bypass |  
| Mutate handlers (start/stop/swap/add/remove/update) | ✅ No `.refresh()` call in any of them, self-consistent mutations verified | Mutation handler tests all use MagicMock injected by dispatch wrapper; no test verifies mutation doesn't trigger external refresh when called via full `_dispatch_tool` chain |
| `validate_config` stateless path | ⚠️ Tests mock the handler — need real-handler test (see Section D) | Also: test that validate_config early-return bypass means get_mcp_state is NEVER called for this tool |
| Unknown tool → ValueError | ✅ Passes via wrapper mock | Test validates exception propagation through dispatch layer correctly |

### ConfigTools Coverage Gap (89%)
Missing lines 129, 133, 135, 137, 139, 141, 146-147 in config.py — these are `update_model_config` validation exception paths:

```python
# Lines ~129-147 are the ModelConfig.model_validate() try/except block
try:
    ModelConfig.model_validate(updated_config)  
except Exception as e:  # Line 135 catches; lines 137+ handle
    return {"success": False, "error": f"Validation error: {e}"}
```

No test exercises this path (updating config with invalid values that fail Pydantic validation). Worth adding but low severity.

### Action Items  
1. Update `test_dispatch_tool_validate_config` in test_server.py to NOT use get_mcp_state mock — verify early-return bypass instead:
   ```python
   async def test_dispatch_tool_validate_config_bypasses_get_mcp_state(self):
       with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
           result = await _dispatch_tool("validate_config", {...})  
           assert not mock_get.called  # Should return early before calling get_mcp_state!
   ```
2. Add validation error test for update_model_config to close 89% → 100% coverage gap

---

## H. Coverage Gap Closure — Modified Files

### Current Coverage
| File | Stmts | Miss | Cover | Missing Lines | Notes |
|------|-------|------|-------|---------------|-------|
| `server.py` | 66 | 3 | **95%** | 105, 109, 125 | These are `main_async()`, `main()` boilerplate entry points. Testing requires stdio_server harness — acceptable exclusion. |
| `models.py` | 22 | 0 | **100%** | — | ✅ All paths covered including handler data construction |
| `servers.py` | 44 | 0 | **100%** | — | ✅ All handlers, error paths, tool definitions covered |
| `config.py` | 76 | 8 | **89%** | 129-147 (exception path) in update_model_config | Low-value gap: requires creating configs with invalid Pydantic values. Add one test if time permits; otherwise acceptable. |

### Lines Not to Test (Acceptable Exclusions)
- `main()`, `main_async()`, `if __name__ == "__main__"` block — server harness complexity (stdio_server needs pseudo-terminal or subprocess plumbing)  
- ConfigStore.file-writing I/O — filesystem operations require tmpfs setup; integration tests would cover this if needed

### Action Items
1. **Optional:** Add 1 test for update_model_config validation error path to close the remaining config.py gap
2. Document coverage exclusions in pytest.ini or conftest.py if not already done (`omit = ...tests, main`)

---

## Verification Execution Order (Prioritized by Risk)

| Priority | Section | Why First? | Effort |
|----------|---------|-----------|--------|
| 🔴 1 | **F: Failure Recovery** | Bug found during prep — partial object caching breaks docstring guarantee, could cache broken state forever in production | Medium (code fix + tests) |
| 🟠 2 | **A: Lazy Singleton Core** | Requires verifying import-time laziness; retry-after-failure test depends on F fix being applied first | Low-Medium |
| 🔴 3 | **E: End-to-End Stale Data** | CORE VALUE PROPOSITION of Phase 1 is completely untested — no test proves external changes are reflected in MCP responses | Medium-High (needs fixture setup) |
| 🟡 4 | **C: Refresh Count Regression Guard** | Ensures nobody accidentally adds more `.refresh()` calls later; simple assertion-based test | Low |
| 🟢 5 | **D, G: validate_config Real Handler + Dispatch Bypass** | Tests real handler behavior with None state; verify early-return bypass doesn't use get_mcp_state mock | Low |
| 🔵 6 | **B: Read Handler Linkage** | Structural safety test — confirms handler reads post-refresh data from same instance it refreshed. Code is structurally safe but untested. | Low |
| ⚪ 7 | **H: Coverage Gap Closure** | Update_model_config exception path; documentation of acceptable exclusions | Very Low |

---

## Deliverables After Verification

1. **`tests/unit/mcp/test_phase1_lazy_singleton.py`** — enhanced with:
   - Failure recovery tests (F)  
   - Import-time laziness test (A supplement)  
   - End-to-end stale data elimination tests (E)  
   - Refresh count regression guard (C)  
   - Real validate_config handler test (D)

2. **`llauncher/mcp_server/server.py`** — fixed `get_mcp_state()` with try/except (F bug fix)

3. **`tests/unit/test_config_tools.py`** — one additional validation error path test (H gap closure, optional)

4. **Updated dispatch tests** in test_server.py and test_server_extended.py to verify validate_config early-return bypass without get_mcp_state mock (G improvement)

5. **Verification report** summarizing pass/fail for each section, with any newly discovered issues

---

## Quick Reference: All Commands for Verification

```bash
# 1. Import-time laziness check
python3 -c "import llauncher.mcp_server.server as s; print('PASS' if s._mcp_state is None else 'FAIL')"

# 2. No circular import check (handler modules independent of server.py)
python3 -c "from llauncher.mcp_server.tools.models import list_models, get_model_config; from llauncher.mcp_server.tools.servers import server_status, get_server_logs; print('PASS: no circular imports')"

# 3. Existing Phase 1 tests  
python3 -m pytest tests/unit/mcp/test_phase1_lazy_singleton.py -xvs 2>&1 | tail -30

# 4. Full MCP test suite
python3 -m pytest tests/unit/mcp/ -x --tb=short 2>&1 | tail -20

# 5. Coverage on modified files  
python3 -m pytest tests/unit/mcp/ --cov=llauncher.mcp_server --cov-report=term-missing 2>&1 | grep -E "^\s+llauncher|TOTAL"

# 6. All unit tests (full suite)
python3 -m pytest tests/unit/ -x --tb=line 2>&1 | tail -15

# 7. Verify refresh count per handler
grep -n "\.refresh()" llauncher/mcp_server/server.py llauncher/mcp_server/tools/*.py

# 8. Verify no get_mcp_state() call inside any handler function
grep -rn "get_mcp_state" llauncher/mcp_server/tools/ --include="*.py" && echo "FAIL: handler still calls get_mcp_state()" || echo "PASS: no handlers reference get_mcp_state"

# 9. Check for any stale type:ignore comments in handler files
grep -rn "# type: ignore" llauncher/mcp_server/tools/*.py

# 10. Verify dispatch early-return bypass for validate_config  
python3 -c "
import ast, inspect
from llauncher.mcp_server.server import _dispatch_tool

source = inspect.getsource(_dispatch_tool)
lines = source.split('\n')
# Find position of 'validate_config' check and 'get_mcp_state()' call
validate_idx = next(i for i, line in enumerate(lines) if 'validate_config' in line and '==\"' in line)  
mcp_idx = next((i for i, line in enumerate(lines) if 'get_mcp_state' in line), -1)
print(f'PASS: validate_config (line {validate_idx}) before get_mcp_state (line {mcp_idx})') if mcp_idx > validate_idx else print('FAIL')
"
```
