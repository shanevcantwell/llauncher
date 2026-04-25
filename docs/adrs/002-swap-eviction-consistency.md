# ADR-002: Unified Swap-with-Eviction Semantics

**Status:** Draft  
**Date:** 2026-04-25  

## Context

llauncher has three entry surfaces for starting servers, potentially evicting an existing one on the same port. They implement the same atomic intent — *"put model B on a port, displacing A if present"* — at three different reliability tiers:

| Surface | Method | Tier | Has Rollback? | Readiness Poll? |
|---------|--------|------|---------------|-----------------|
| **UI** (Streamlit) | `state.start_with_eviction()` | 2 — No safety net | ❌ | ❌ |
| **Agent API** (`POST /start-with-eviction/`) → calls `state.start_with_eviction()` | 2 — No safety net | ❌ | Client-side only (Pi extension polls `/status`) |
| **MCP tool** `swap_server()` in `mcp_server/tools/servers.py` (~120 lines inline) | 3 — Guaranteed availability | ✅ | ✅ (120s default) |

### The Problem

When the UI or Agent API evicts a server and the new model fails to start (OOM, bad weights, binary missing, process crash), the old model is **gone with no recovery**. The `state.start_with_eviction()` method:

```python
def start_with_eviction(self, model_name, port, ...):
    # 1. Track if eviction will happen
    port_was_occupied = port in self.running
    
    # 2. Stop the old server (evict)
    if port_was_occupied:
        stop_success, _ = self.stop_server(port, caller)
    
    # 3. Start the new process
    try:
        process = process_start_server(config, port, ...)
    except Exception as e:
        return False, f"Failed to start: {e}"
        # ⚠ OLD MODEL IS ALREADY GONE — NO ROLLBACK
```

The MCP tool (`swap_server` in `mcp_server/tools/servers.py`) has proper rollback logic — it validates pre-flight, stops the old model, starts the new one, and if starting fails, it restarts the old model from its persisted config. But this ~120-line method is entirely duplicated at the tool layer, unreachable by the UI or Agent API callers.

The `state.start_with_eviction()` docstring claims *"rolls back to the old model if the new one fails"* but this logic doesn't exist in the implementation — it's a broken contract.

### The MCP Tool's Rollback Logic (for reference)

The MCP `swap_server` method performs five phases:

1. **Pre-flight validation** — model exists, path exists, no duplicates, old config exists (if strict), old path exists
2. **Stop old model** — calls `state.stop_server(port)`
3. **Start new model** — calls `state.start_server(model_name, port=...)`; if failure → rollback
4. **Wait for ready** — polls via `wait_for_server_ready(port, timeout)`; if not ready → terminate new + rollback
5. **Success** — refresh state, return

The MCP tool is correctly designed but its logic is entirely duplicated at the tool layer rather than lifted into state.

## Decision

**Elevate `state.start_with_eviction()` to be the single source of truth**, incorporating full pre-flight validation, rollback, and readiness polling. All three entry surfaces delegate to one method. The MCP tool's ~120 lines of inline swap logic becomes a thin delegation (~25 lines).

### Why not extract `swap_server` as a separate state method?

Two methods doing similar things with different names (`start_with_eviction` vs `swap_server`) splits caller responsibility. Every caller must choose which one to call, creating a maintenance and correctness burden. The MCP tool would also still be ~25 lines of delegation rather than truly eliminated.

### Why not keep `start_with_eviction` unchanged and add a new `swap_server`?

The "broken contract" remains in place for UI and Agent API callers — anyone calling `start_with_eviction` still loses their old model on failure. You'd have to update all three callers anyway (UI uses `start_with_eviction`, agent calls it, MCP wraps it), making the migration risk equal but with no net code reduction.

### Why upgrade the existing method?

- **Semantic correctness:** "Evict" implies displacement, not destruction. A responsible eviction guarantees the displaced process can be restored if needed.
- **Three known internal callers** — all local, all tracked: UI (`model_card.py`), Agent API (`routing.py`), MCP tool (`servers.py`). A controlled migration is straightforward.
- **Eliminates ~120 lines of duplication** in the MCP tool (`mcp_server/tools/servers.py`).
- **Fixes both UI and Agent API** simultaneously with one shared primitive.
- **Single failure mode tree** — no need for callers to reason about when to use which method.

## Design

### Architecture

```
                    ┌──────────────────────────────────────┐
                    │  state.start_with_eviction()          │
                    │  (NEW: rollback + readiness)          │
                    │                                       │
                    │  1. Full pre-flight validation         │
                    │  2. Stop old model if occupied         │
                    │  3. Start new model                    │
                    │  4. Wait for readiness                 │
                    │  5. Rollback on failure                │
                    └──────────┬───────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌───────────┐  ┌──────────────┐  ┌──────────────┐
        │ UI         │  │ Agent API    │  │ MCP Tool     │
        │ model_card │  │ routing.py   │  │ servers.py   │
        │ .py        │  │              │  │ (thin wrap)  │
        └───────────┘  └──────────────┘  └──────────────┘
```

### New Signature

```python
@dataclass
class EvictionResult:
    success: bool                        # overall outcome
    port_state: str                      # "unchanged" | "restored" | "serving" | "unavailable"
    error: str                           # human-readable error detail
    rolled_back: bool = False            # did we restore the old model?
    restored_model: str = ""             # what got restored (empty if no rollback)
    previous_model: str = ""             # what was running before (empty if none)
    new_model_attempted: str = ""        # what was attempted (empty if pre-flight failure)
    startup_logs: list[str] = field(default_factory=list)  # logs from failed new model

def start_with_eviction(
    self,
    model_name: str,
    port: int,
    caller: str = "unknown",
    server_bin: Path = DEFAULT_SERVER_BINARY,
    readiness_timeout: int = 120,        # NEW — seconds to wait for new model ready
    strict_rollback: bool = False,       # NEW — only rollback if old config persisted
) -> EvictionResult:
```

**Backward compatibility:** A `_compat` wrapper preserves `tuple[bool, str]` return for callers still using tuple unpacking:

```python
def start_with_eviction_compat(
    self,
    model_name: str,
    port: int,
    caller: str = "unknown",
    server_bin: Path = DEFAULT_SERVER_BINARY,
) -> tuple[bool, str]:
    """Backward-compatible wrapper returning (success, message)."""
    result = self.start_with_eviction(
        model_name, port, caller, server_bin,
        readiness_timeout=120,
        strict_rollback=False,
    )
    msg = result.error if not result.success else f"Started {result.new_model_attempted} on port {port}"
    return result.success, msg
```

The existing test fixtures that use `success, message = state.start_with_eviction(...)` can be updated to call `_compat` during the transition.

### Failure Mode Decision Tree

This is the *exact* logic flow every caller depends on:

```
start_with_eviction(model, port)
│
├─ PHASE 1: PRE-FLIGHT VALIDATION (no state changes)
│   ├─ model not in self.models → return (success=False, port_state="unchanged")
│   ├─ new model path doesn't exist → return (success=False, port_state="unchanged")
│   ├─ new model already running on *another* port → return (success=False, port_state="unchanged")
│   ├─ port occupied AND strict_rollback=True AND old model not in self.models
│   │   → return (success=False, port_state="unchanged", warning="old config missing")
│   └─ port occupied AND strict_rollback=True AND old model path doesn't exist
│       → return (success=False, port_state="unchanged", warning="old path missing")
│
├─ PHASE 2: STOP OLD MODEL
│   ├─ port NOT occupied → skip to Phase 3 (simple start)
│   └─ port IS occupied:
│       ├─ stop_old FAILS → record audit error, return (success=False, port_state="unchanged")
│       │   ← old server is STILL RUNNING, nothing changed
│       └─ stop_old succeeds → remove from self.running, record evict audit entry
│           (old server now stopped)
│
├─ PHASE 3: START NEW MODEL
│   ├─ start_new FAILS (process_start_server raises)
│   │   ├─ strict_rollback=True AND old config available
│   │   │   → ROLLBACK: start_old(model_name, port=port)
│   │   │       ├─ rollback succeeds + ready → return (success=False, port_state="restored", rolled_back=True)
│   │   │       └─ rollback fails → return (success=False, port_state="unavailable")
│   │   ├─ strict_rollback=False AND old config available
│   │   │   → ROLLBACK: start_old(model_name, port=port) [same as above]
│   │   └─ no old config (non-strict) → port is DEAD
│   │       → return (success=False, port_state="unavailable", warning="manual intervention")
│   │
│   └─ start_new succeeds → add to self.running, get pid
│
├─ PHASE 4: WAIT FOR READINESS
│   ├─ ready within timeout → SUCCESS
│   └─ NOT ready (timeout):
│       ├─ terminate new model via stop_server_by_pid(pid)
│       ├─ strict_rollback=True AND old config available
│       │   → ROLLBACK: start_old(model_name, port=port)
│       │       ├─ rollback succeeds + ready → return (success=False, port_state="restored", rolled_back=True)
│       │       └─ rollback fails → return (success=False, port_state="unavailable")
│       └─ no old config → port is DEAD
│           → return (success=False, port_state="unavailable")
│
└─ PHASE 5: SUCCESS
    ├─ refresh_running_servers()
    └─ return (success=True, port_state="serving", new_model=model_name)
```

### Response Contract

| Scenario | `success` | `port_state` | `rolled_back` | `previous_model` |
|----------|-----------|--------------|---------------|------------------|
| Pre-flight: model not found | ❌ | unchanged | — | — |
| Pre-flight: path missing | ❌ | unchanged | — | — |
| Pre-flight: new already running elsewhere | ❌ | unchanged | — | — |
| Pre-flight (strict): old config missing | ❌ | unchanged | — | old_name |
| Start on free port (no eviction) | ✅ | serving | — | — |
| Evict + start success | ✅ | serving | — | old_name |
| Evict + start fails, rollback succeeds | ❌ | restored | ✅ | old_name |
| Start + readiness timeout, rollback succeeds | ❌ | restored | ✅ | old_name |
| Evict + start fails, rollback also fails | ❌ | unavailable | ❌ | old_name |
| Readiness timeout, rollback also fails | ❌ | unavailable | ❌ | old_name |

### Tier Mapping After Change

| Surface | How It Calls | `strict_rollback` | Behavior |
|---------|-------------|-------------------|----------|
| **UI** (`model_card.py`) | `start_with_eviction_compat(model, port, caller="ui")` | `False` | Graceful degradation: if old config is missing or rollback fails, returns `unavailable`. User sees the eviction dialog result. UI also has a confirmation dialog that lets users back out before anything happens. |
| **Agent API** (`routing.py`) | `start_with_eviction(model, port, caller="agent")` | `False` | Non-strict: same graceful degradation as UI. Returns structured response with `port_state` so the client (Pi extension or other HTTP callers) can distinguish between "restored" and "unavailable". |
| **MCP tool** (`servers.py`) | `start_with_eviction(model, port, caller="mcp", strict_rollback=True)` | `True` | Strict: pre-flight fails with `port_state=unchanged` if old config doesn't exist. If rollback is possible, guarantees port always serves something. If both fail, returns `unavailable` with explicit warning. |

### Boundary: State vs. Tool Layer

| Concern | Lives In | Rationale |
|---------|----------|-----------|
| Stop old process | `state` | Deterministic process kill, no retry needed |
| Start new process | `state` | Existing method, deterministic |
| Rollback: restart old process | `state` | Just a `start_server()` call — it's a process action |
| Rollback decision (when to roll back) | `state.start_with_eviction()` | Part of the atomic contract |
| Timeout for readiness polling | Parameter to `start_with_eviction()` | Configurable by caller |
| `strict_rollback` constraint | Parameter: optional bool | MCP constraint — only rollback if old model has persisted config. Non-strict callers accept the limitation (port may be unavailable). |
| Pre-flight duplicate check | `state` | Shared validation logic |

## Implementation Plan

| # | Task | Files | Dependencies | Detail |
|---|------|-------|-------------|--------|
| **1** | Add `EvictionResult` dataclass + `_parse_eviction_result()` helper | `llauncher/state.py` | — | Dataclass with all fields from response contract. Module-level helper that splits message on `|DELIM|` for callers that can't import `EvictionResult`. |
| **2** | Rewrite `start_with_eviction()` with rollback + readiness | `llauncher/state.py` | Task 1 | Full five-phase implementation per decision tree. Add `_compat` wrapper returning `tuple[bool, str]`. Keep existing audit logging pattern. |
| **3** | Simplify MCP `swap_server()` to thin delegation | `llauncher/mcp_server/tools/servers.py` | Task 2 | Remove ~100 lines of inline swap logic. Method becomes: call `state.start_with_eviction(..., strict_rollback=True)`, map `EvictionResult` fields to response dict (~25 lines total). Update imports (remove `stop_server_by_pid`, `find_server_by_port`). |
| **4** | Update Agent API `/start-with-eviction` endpoint | `llauncher/agent/routing.py` | Task 2 | Call upgraded method. Parse result: `port_state=unavailable` → 503, others → 409 with detail. Response includes `port_state`, `previous_model`, `new_model` in body for both success and error cases. |
| **5** | Update UI eviction dialog | `llauncher/ui/tabs/model_card.py` | Task 2 + `_compat` | Use `EvictionResult` (or `_parse_eviction_result` from compat string) to show: `"Success"` (✅), `"Rolled back to X — ⚠️"`, or `"Port unavailable — manual intervention required"` (❌). |
| **6** | Write new unit tests | `tests/unit/test_state.py` | Task 2 | Add `TestEvictionRollback` class with 6 test methods per test matrix below. |
| **7** | Update existing tests | `tests/unit/test_state.py`, `tests/unit/mcp/test_servers_tools.py`, `tests/integration/test_swap.py` | Task 2 | UI/state tests: update tuple unpacking to use `_compat` or direct `EvictionResult`. MCP tests: mock `state.start_with_eviction()` returning pre-built `EvictionResult` instead of mocking `start_server`/`stop_server` individually. Integration test: verify unchanged (it already expects `port_state`). |
| **8** | Audit log enrichment + backward compat check | All files | Task 2 | Add `"rolled_back"` and `"unavailable"` to audit `result` field values. Grep for all `start_with_eviction` callers — ensure none use tuple unpacking without `_compat`. |

### New Unit Tests (Task 6)

| # | Test name | What it verifies | Key mocks |
|---|-----------|-----------------|-----------|
| 1 | `test_pre_flight_model_not_found_untouched` | Pre-flight fails → port state unchanged, old server untouched | No servers running; model missing from config |
| 2 | `test_evict_start_fail_strict_rollback_succeeds` | Evict succeeds, new start fails, rollback succeeds → port_state=restored, rolled_back=True | `process_stop_server` returns True, `process_start_server` side_effect for new=Exception, `wait_for_server_ready` returns True for rollback |
| 3 | `test_evict_start_success_readiness_timeout_rollback_succeeds` | New model starts but doesn't become ready within timeout, rollback succeeds → port_state=restored | Same as above + `stop_server_by_pid` called on new model's PID |
| 4 | `test_evict_start_success_then_ready_timeout_no_rollback_possible` | Non-strict mode, old config deleted between eviction and timeout → port_state=unavailable | No old config in state.models |
| 5 | `test_both_fail_unavailable` | Evict + start new fails + rollback also fails (start_server raises) → port_state=unavailable | `process_start_server` side_effect raises for both new model and rollback |
| 6 | `test_empty_port_no_eviction` | Start on free port, no eviction needed → clean success, no regression | No servers running; simple start path |

### Updated Existing Tests (Task 7)

| File | What changes |
|------|-------------|
| `tests/unit/test_state.py::TestStartWithEviction` | All 6 existing tests use `success, message = state.start_with_eviction(...)` tuple unpacking. Update to either call `_compat()` or access `.success` / `.error` on `EvictionResult`. Add the 6 new test methods above. |
| `tests/unit/mcp/test_servers_tools.py::TestSwapServer` | Currently mocks `state.start_server`, `state.stop_server`, `Path.exists`, `wait_for_server_ready`. Replace with single mock: `state.start_with_eviction.return_value = EvictionResult(success=True, port_state="serving", new_model_attempted="foo")`. Update all assertions to expect new response keys. |
| `tests/integration/test_swap.py::TestSwapServerLive` | `test_swap_rollback_on_invalid_model` already expects `port_state` field — should pass as-is (MCP tool still returns it via mapping). `test_swap_server_roundtrip`: verify unchanged behavior. |

### Audit Log Enrichment (Task 8)

Expand the `AuditEntry.result` enum to include rollback and failure states:

```python
# Before: "success" | "error" | "validation_error"
# After:  "success" | "error" | "validation_error" | "rolled_back" | "unavailable"
```

Each phase of the swap logs its own audit entry:
- `evict` action → `"success"` or `"error"` (unchanged)
- `start` action → `"success"` or `"error"` (unchanged)
- On rollback: additional `start` action with result `"rolled_back"` and message including `"restored to {model}"`

## Migration Safety

### Why existing servers won't be affected

This change only touches the **start** code path — specifically `start_with_eviction`. Servers already running on ports are tracked in `state.running` and unaffected. No migration script or flag is needed because:

1. The `_compat` wrapper preserves the exact `tuple[bool, str]` return for any caller not yet updated.
2. Pre-flight validation happens **before** any state mutation — if validation fails, nothing gets killed.
3. The new readiness polling only affects servers that are *being started*, not already running ones.

### Caller update order

No particular order required — all three callers can be updated in any sequence:

1. State method (Task 2) — core change
2. MCP tool (Task 3) — thin delegation, no behavior change for MCP clients
3. Agent API (Task 4) — adds `port_state` to response
4. UI (Task 5) — uses `_compat` during transition, can be updated anytime

### Rollback limitations

| Scenario | Behavior | Reason |
|----------|----------|--------|
| Old model's config deleted after eviction starts | Non-strict: port goes `unavailable`. Strict: pre-flight fails, nothing evicted. | `strict_rollback=True` is only set by MCP calls. UI/Agent API accept the limitation. |
| Old model's binary no longer exists at startup time | Rollback attempt will fail → `port_state=unavailable`. | Model path is validated at pre-flight, but could be deleted between stop and rollback. The decision tree handles this as a rollback failure. |
| Port stolen between old stop and new start | `can_start` checks `is_port_in_use` — if another process grabbed the port, start fails → rollback attempt. If no config, port `unavailable`. | This is a race condition on very slow systems. The pre-flight `is_port_in_use` check catches it for non-eviction cases. |

## Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Rollback starts an unwanted process on a stale port | **Medium** | `strict_rollback=True` enforces persisted config requirement for MCP. UI has its own confirmation dialog that validates the old model exists in state before showing eviction prompt. Non-strict callers (UI/Agent API) accept this as a known limitation — if the old config is missing, the port may be unavailable. |
| Readiness timeout on very large model start (30B+ params, slow storage) | **Low** | Configurable `readiness_timeout` parameter. Default 120s is generous for most models. Caller can pass a longer timeout. |
| Rollback itself fails (old binary gone, port stolen between stop/start) | **Low** | Returns `port_state=unavailable` with explicit warning message: `"Swap failed and rollback failed — manual intervention required"`. |
| Breaking existing tuple-unpacking callers | **High** | `_compat` wrapper preserves exact `tuple[bool, str]` return. Task 8 includes exhaustive grep of all callers. Any external callers using tuple unpacking continue to work. |
| `|DELIM|` in error message content collides with delimiter | **Very Low** | Messages are generated by us, not user input. Use `\x00|DELIM|\x00` if ever needed, but plain `|DELIM|` is sufficient since we control the message format. |

## Success Criteria

- [ ] `state.start_with_eviction()` implements full five-phase decision tree
- [ ] MCP `swap_server()` reduced to ~25-line delegation (from ~120 lines)
- [ ] Agent API response includes `port_state` for both success and error
- [ ] All 6 new unit tests pass
- [ ] All existing unit + integration tests pass
- [ ] No callers use broken tuple unpacking on the new return type
- [ ] Audit log captures rollback events with `"rolled_back"` result
