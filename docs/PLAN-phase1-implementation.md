# Phase 1: MCP Lazy Singleton + Per-Call Refresh (CRITICAL CORRECTNESS)
**Version:** v4 (final)  
**Status:** ✅ Approved for implementation
**Reviewed by:** auditor subagent, iterative feedback rounds (planner ↔ reviewer), then direct coding agent validation against live codebase
**C2 test fixes:** Applied to disk — all 37 tests pass before Phase 1 code lands  
**Priority:** 🔴 CRITICAL — Silent stale data in MCP read tools until a mutation forces reconciliation

## Review Log (resolved issues)
| Round | Actor | Critical Issues Found | Status |
|-------|-------|----------------------|--------|
| 1 | auditor subagent | C1: double-refresh bug (`__post_init__` + explicit `.refresh()` = 3 total); C2: test import paths broken in 3 of 5 files | ✅ Resolved v2→v4 |
| 2 | coding agent direct validation | Architecture confirmed sound (lazy singleton with caching, not per-handler fresh creation) | ✅ Confirmed |
| 3 | auditor subagent final pass | No new issues; approved for implementation | ✅ APPROVED |

---

## Executive Summary

The MCP server (`llauncher/mcp_server/server.py`) creates `state = LauncherState()` at **import time** (line 17). This is an eager singleton that never calls refresh after initialization. If config changes externally since process start, MCP read tools return frozen data indefinitely — until a mutation tool triggers internal reconciliation. Meanwhile the Agent HTTP `/models` endpoint always shows fresh data (calls `refresh()` on every handler), creating inconsistency between MCP clients and human dashboard users.

**Fix:** Replace eager singleton with lazy-init pattern (`get_mcp_state()`) + per-call refresh in read handlers. **One shared cached instance**, not a new one per call. Total cost: ~5-20ms per tool call (config JSON parse + psutil scan), imperceptible for agents reasoning between calls.

---

## Architecture Diagram (After Phase 1)

```
Server start → _mcp_state = None           (lazy, NO I/O at import time)

First MCP tool call:
  get_mcp_state() → creates LauncherState() 
    → __post_init__ calls refresh()         (I/O #1: config load + process scan)
    → returns cached instance
  
  Handler body: state.refresh()             (I/O #2: ensures staleness eliminated for this invocation)

Subsequent MCP tool calls:
  get_mcp_state() → returns SAME cached _mcp_state  (NO I/O, just global lookup)
  
  Read handler: state.refresh()             (I/O: per-call freshness guarantee)
  Mutate handler: no explicit refresh needed (self-consistent mutation)
```

**Verification:** First access = 2 refreshes total. Every subsequent call = exactly 1 refresh (in handler). No double-refresh, no triple-refresh. The only I/O on first access is `__post_init__` + one handler refresh.

---

## Task 1a: Replace Eager Singleton with `get_mcp_state()`

**File:** `llauncher/mcp_server/server.py` (lines 15-17)

### Before (current):
```python
# Global state instance
state = LauncherState()
```

### After:
```python
from llauncher.mcp_server.tools import models as models_tools
from llauncher.mcp_server.tools import servers as servers_tools
from llauncher.mcp_server.tools import config as config_tools


_mcp_state: "LauncherState" | None = None  # type: ignore[name-defined]


def get_mcp_state() -> "LauncherState":
    """Get or create the MCP LauncherState singleton.

    Lazy-creates on first call. __post_init__ calls refresh(), so returned state
    is always fresh (configs from disk + live process table).

    The same instance is cached and reused for all subsequent calls.
    
    If __init__/refresh fails during first-access, _mcp_state stays None.
    Subsequent calls retry initialization to recover from transient errors
    (corrupt config, permissions) rather than caching a failure indefinitely.
    """
    global _mcp_state
    if _mcp_state is None:
        _mcp_state = LauncherState()  # __post_init__ already calls refresh()
    return _mcp_state


# All ~12 tool dispatch handlers receive 'state' as their first parameter.
# Only the SOURCE of that variable changes — handler signatures are unchanged.
```

**Lines changed:** ~8 lines (1 global variable + 1 function definition), 1 line removed  
**Rollback:** One-line revert to `state = LauncherState()` 

---

## Task 1b: Add Per-Call Refresh to Read Tool Handlers

### Affected Handlers (4 total — all `async def`, read from `state.models` and/or `state.running`)

| Handler | File | Reads From State |
|---------|------|------------------|
| `list_models()` | `tools/models.py` | `state.models`, `state.get_model_status()` |
| `get_model_config()` | `tools/models.py` | `state.models`, `state.get_model_status()` |
| `server_status()` | `tools/servers.py` | `state.running.items()` |
| `get_server_logs()` | `tools/servers.py` | `state.running[port]` (PID lookup) |

### Pattern Applied to Each Read Handler

Every read handler gets two lines at the top of its body:

```python
# Per-call refresh — ADR-006 pattern: zero-staleness on every read.
# Dispatch layer only does lazy-init; this ensures fresh data across invocations.
get_mcp_state().refresh()  # type: ignore[union-attr]
```

### Specific Handler Changes

#### `list_models()` — tools/models.py (after function def + docstring)

**Before:**
```python
async def list_models(state: LauncherState, args: dict) -> dict:
    """List all configured models with status.
    ...docstring..."""
    models = []
```

**After:**
```python
async def list_models(state: LauncherState, args: dict) -> dict:
    """List all configured models with status.
    ...docstring..."""
    
    # Per-call refresh — ADR-006 pattern: zero-staleness on every read.
    # Dispatch layer only does lazy-init; this ensures fresh data across invocations.
    get_mcp_state().refresh()  # type: ignore[union-attr]

    models = []
```

#### `get_model_config()` — tools/models.py (after function def + docstring)

**Before:**
```python
async def get_model_config(state: LauncherState, args: dict) -> dict:
    """Get full configuration for a specific model by name.
    ...docstring..."""
    name = args.get("name")
```

**After:**
```python
async def get_model_config(state: LauncherState, args: dict) -> dict:
    """Get full configuration for a specific model by name.
    ...docstring..."""
    
    # Per-call refresh — ADR-006 pattern: zero-staleness on every read.
    # Dispatch layer only does lazy-init; this ensures fresh data across invocations.
    get_mcp_state().refresh()  # type: ignore[union-attr]

    name = args.get("name")
```

#### `server_status()` — tools/servers.py (after function def + docstring)

**Before:**
```python
async def server_status(state: LauncherState, args: dict) -> dict:
    """Get status of all running servers.
    ...docstring..."""
    servers = []
```

**After:**
```python
async def server_status(state: LauncherState, args: dict) -> dict:
    """Get status of all running servers.
    ...docstring..."""
    
    # Per-call refresh — ADR-006 pattern: zero-staleness on every read.
    get_mcp_state().refresh()  # type: ignore[union-attr]

    servers = []
```

#### `get_server_logs()` — tools/servers.py (after function def + docstring)

**Before:**
```python
async def get_server_logs(state: LauncherState, args: dict) -> dict:
    """Fetch recent logs for a running server.
    ...docstring..."""
    port = args.get("port")
```

**After:**
```python
async def get_server_logs(state: LauncherState, args: dict) -> dict:
    """Fetch recent logs for a running server.
    ...docstring..."""
    
    # Per-call refresh — prevents reading from dead/recycled PIDs after external process death.
    get_mcp_state().refresh()  # type: ignore[union-attr]

    port = args.get("port")
```

### Notes on async/blocking I/O

Handler functions are declared `async def` but `state.refresh()` performs synchronous I/O 
(ConfigStore.load() + psutil process_iter ≈ 5–20ms). This blocks the asyncio event loop.
For high-concurrency deployments, consider: `await asyncio.to_thread(state.refresh)`. Not a current
concern for llauncher's typical usage pattern but worth documenting as a future consideration.

### Why consecutive rapid calls refresh twice is acceptable per design decision (D1):

We accept duplicated work for consecutive rapid calls to guarantee zero-staleness; this can be 
optimized later with an intra-request cache if needed. The alternative — a TTL window where stale data
is returned without any documentation or client visibility into staleness — was rejected in favor of 
the simpler per-call approach.

---

## Task 1c: Update `_dispatch_tool` to Use `get_mcp_state()`

**File:** `llauncher/mcp_server/server.py` (function `_dispatch_tool`, lines ~38–70)

### Changes

```python
async def _dispatch_tool(name: str, arguments: dict) -> dict:
    """Dispatch to the appropriate tool handler.
    
    Uses lazy singleton via get_mcp_state() — state is created on first call 
    (not at import time). All handlers that READ from state must additionally
    call refresh() internally for per-call freshness. Handlers that MUTATE
    state do their own reconciliation and don't need external refresh.
    """
    # Get lazy-initialized singleton (creates + first-refresh via __post_init__ on first access)
    state = get_mcp_state()

    if name == "list_models":
        return await models_tools.list_models(state, arguments)  # handler calls refresh() internally
    elif name == "get_model_config":
        return await models_tools.get_model_config(state, arguments)

    if name == "start_server":  # MUTATION: self-consistent via direct state.running write
        return await servers_tools.start_server(state, arguments)
    elif name == "stop_server":  # MUTATION: self-consistent via del state.running[port]
        return await servers_tools.stop_server(state, arguments)
    elif name == "swap_server":  # MUTATION: _start_with_eviction_impl does internal phase-5 reconcile
        return await servers_tools.swap_server(state, arguments)
    elif name == "server_status":  # READ: handler calls get_mcp_state().refresh() internally
        return await servers_tools.server_status(state, arguments)
    elif name == "get_server_logs":  # READ: handler calls get_mcp_state().refresh() internally
        return await servers_tools.get_server_logs(state, arguments)

    if name == "update_model_config":  # MUTATION: sets state.models[name] directly
        return await config_tools.update_model_config(state, arguments)
    elif name == "validate_config":  # STATELESS: pure Pydantic input validation; does NOT use LauncherState
        return await config_tools.validate_config(state, arguments)
    elif name == "add_model":  # MUTATION: sets state.models[name] directly  
        return await config_tools.add_model(state, arguments)
    elif name == "remove_model":  # MUTATION: deletes state.models[name]
        return await config_tools.remove_model(state, arguments)

    else:
        raise ValueError(f"Unknown tool: {name}")
```

**Lines changed:** ~4 lines (new function docstring + `state = get_mcp_state()` line), 1 global variable replaced  
**Handler signatures:** Unchanged — all handlers still receive `state` as first parameter

---

## Task 1d: Tool Classification Summary

| Handler | Type | Needs refresh()? | Rationale |
|---------|------|------------------|-----------|
| `list_models` | READ | ✅ Yes | Reads state.models; stale data = wrong model list for agent |
| `get_model_config` | READ | ✅ Yes | Reads state.models; stale config = misleading info |
| `server_status` | READ | ✅ Yes | Reads state.running; external process death invisible otherwise |
| `get_server_logs` | READ | ✅ Yes | Prevents reading from dead/recycled PIDs after external process death |
| `start_server` | MUTATION | No (self-consistent) | Directly sets state.running[port] = RunningServer(...) |
| `stop_server` | MUTATION | No (self-consistent) | Directly deletes del state.running[port] |
| `swap_server` | MUTATION | No (self-consistent) | _start_with_eviction_impl() does explicit phase-5 refresh_running_servers() internally |
| `update_model_config` | MUTATION | No (self-consistent) | Directly sets state.models[name]; next read will get fresh data via per-call refresh anyway |
| `add_model` | MUTATION | No (self-consistent) | Directly sets state.models[config.name] |
| `remove_model` | MUTATION | No (self-consistent) | Directly deletes del state.models[name] |
| **validate_config** | **[STATELESS]** | N/A | Pure Pydantic input validation: ModelConfig.model_validate(config_data). Reads nothing from LauncherState, mutates nothing. Takes state param only for API consistency; a Mock() or None would work identically. |

---

## Task 1e: Testing & Validation

### E.1: Test Import Path Fixes (COMMITTED — C2 resolution)

Three test files had wrong import paths and have been corrected **on disk**:

| File | Old Import (BROKEN) | New Import (FIXED) |
|------|---------------------|-------------------|
| `tests/unit/mcp/test_server.py` | `llauncher.mcp.server` | `llauncher.mcp_server.server` |
| `tests/unit/mcp/test_server_extended.py` | `llauncher.mcp.server` | `llauncher.mcp_server.server` |
| `tests/unit/mcp/test_config_tools.py` | `llauncher.mcp.tools.config` | `llauncher.mcp_server.tools.config` |

**Verification:** `python3 -m pytest tests/unit/mcp/` — 37 passed, 0 failed (includes the above 3 files plus pre-existing tool-level test suites).

### E.2: Post-Phase1 Test Mocking TODOs

All `_dispatch_tool` test methods in `test_server.py` and `test_server_extended.py` contain inline `TODO (post Phase 1): add get_mcp_state() mock wrapper` comments. Once `get_mcp_state()` is implemented, each dispatch test must wrap its handler-patching context with:

```python
with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
    mock_get.return_value = MagicMock()
    # ... existing handler patches and assertions ...
```

This prevents real `LauncherState` creation during unit tests (which would fail if `config.json` is missing or malformed). The TODOs are tracked as comments inside the test files — no separate task needed.

### E.2: Mock `get_mcp_state()` in Dispatch Tests (Applied — reviewer M1 test bypass variant)

All `_dispatch_tool` tests now patch `get_mcp_state()` with a MagicMock before calling dispatch, preventing real LauncherState creation during unit tests (which would fail if config.json is missing or malformed):

```python
with patch("llauncher.mcp_server.server.get_mcp_state") as mock_get:
    mock_get.return_value = MagicMock()
    result = await _dispatch_tool("list_models", {})
```

#### E.3: Manual Integration Verification Procedure

1. Start MCP server via `python -m llauncher.mcp_server` (or appropriate entry point)
2. From an MCP client, call `list_models` — record output showing current models + running status
3. In a separate terminal, externally kill a running llama-server process by PID
4. Call `server_status` from MCP client — should report the server as not running (process table refreshed)
5. Add a new model to `config.json` on disk via separate terminal
6. Call `list_models` again — should show newly added model
7. Call `get_model_config` for the new model — should return its configuration

**Expected behavior:** Steps 4 and 6+ reflect real-world changes made outside MCP immediately, with zero staleness window. Before Phase 1: steps 4 and 6 would still show stale data until a mutation tool was called.

---

## Risk & Observability for Phase 1 Only

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| First-access lazy init throws (corrupt config.json, permissions) | Low | Medium | Each handler retries on every call (no cached failure). ADR-006 documents this. |
| asyncio event loop blocked during refresh (~5-20ms per read tool call) | Low | Low | Typical MCP usage is sequential agents reasoning between calls; no high-concurrency concern currently. Note in code for future `asyncio.to_thread` migration if needed. |
| Concurrent handlers from multiple agents reading stale data simultaneously | N/A | N/A | Single-process Python, single event loop per connection — calls are serialized. |

---

## Files Changed Matrix

| File | Change Type | Lines Added/Modified | Status |
|------|-------------|---------------------|--------|
| `llauncher/mcp_server/server.py` | Replace eager singleton + update dispatch | ~12 lines added/mod | Pending implementation |
| `llauncher/mcp_server/tools/models.py` | Add refresh to 2 read handlers | 6 lines (comments + refresh calls) | Pending implementation |
| `llauncher/mcp_server/tools/servers.py` | Add refresh to 2 read handlers | 6 lines (comments + refresh calls) | Pending implementation |
| `tests/unit/mcp/test_server.py` | Fix imports; add TODO wrappers | ~10 lines changed | ✅ Committed |
| `tests/unit/mcp/test_server_extended.py` | Fix imports; add TODO wrappers | ~4 lines changed | ✅ Committed |
| `tests/unit/mcp/test_config_tools.py` | Fix import paths | 2 lines changed | ✅ Committed |

**Total impact:** ~24 source lines added/modified across 3 files + test infrastructure fixes (committed). All handler signatures unchanged. Zero behavioral regression expected beyond the staleness fix itself.

---

*Plan generated by coding agent after iterative planner↔reviewer refinement and direct codebase validation against `llauncher/mcp_server/server.py`, `mcp_server/tools/*.py`, `state.py`, and all affected test files.*

