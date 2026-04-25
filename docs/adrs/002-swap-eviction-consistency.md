# ADR-002: Unified Swap-with-Eviction Semantics

**Status:** Draft  
**Date:** 2026-04-25  

## Context

llauncher has three entry surfaces for starting servers, potentially evicting an existing one on the same port. They implement the same atomic intent at different reliability tiers:

| Surface | Method | Tier | Has Rollback? | Readiness Poll? |
|---------|--------|------|---------------|-----------------|
| **UI** (Streamlit) | `state.start_with_eviction()` | 2 — No safety net | ❌ | ❌ |
| **Agent API** | `POST /start-with-eviction/` → calls `state.start_with_eviction()` | 2 — No safety net | ❌ | Client-side only (Pi extension) |
| **MCP tool** | `swap_server()` in `mcp_server/tools/servers.py` | 3 — Guaranteed availability | ✅ | ✅ (120s default) |

### The Problem

When the UI or Agent API evicts a server and the new model fails to start (OOM, bad weights, crash), the old model is **gone** with no recovery. The MCP tool has proper rollback logic (~120 lines of inline orchestration), but it's entirely duplicated at the tool layer — unreachable by the UI and Agent API callers.

The `state.start_with_eviction()` method (the shared state-level primitive) evicts then starts, but returns `False` on failure with no attempt to restore the old model. The docstring promises "rolls back if the new one fails" but this doesn't exist in the implementation.

## Decision

**Elevate `state.start_with_eviction()` to be the single source of truth**, incorporating rollback and readiness polling. All three entry surfaces delegate to one method. The MCP tool's ~120 lines of duplicated swap logic becomes a thin delegation (~25 lines).

### Why not extract `swap_server` as a separate state method?
Two methods doing similar things with different names splits caller responsibility. Every caller must choose which one — a maintenance and correctness burden.

### Why not keep `start_with_eviction` unchanged?
The "broken contract" remains for UI and Agent API callers. You'd need to update all three surfaces anyway, with no net reduction in change scope.

### Why upgrade the existing method?
- **Semantic correctness:** "Evict" implies displacement, not destruction. A responsible eviction guarantees the displaced process can be restored if needed.
- **Three known internal callers** — controlled migration is straightforward.
- **Eliminates ~120 lines of duplication** in the MCP tool.
- **Fixes both UI and Agent API** simultaneously with one shared primitive.

## Design

### New Signature

```python
def start_with_eviction(
    self,
    model_name: str,
    port: int,
    caller: str = "unknown",
    server_bin: Path = DEFAULT_SERVER_BINARY,
    readiness_timeout: int = 120,     # NEW — seconds to wait for new model ready
    strict_rollback: bool = False,     # NEW — only rollback if old model has persisted config
) -> EvictionResult:
```

**Backward compatibility:** A `_compat` wrapper preserves `tuple[bool, str]` return for callers still using tuple unpacking.

### Failure Mode Decision Tree

```
start_with_eviction(model, port)
│
├─ Pre-flight fails? → port_state=unchanged  ← old server UNTOUCHED
│
├─ Old server exists:
│   ├─ stop_old FAILS → error returned  ← old server still running
│   └─ stop_old succeeds
│       ├─ start_new FAILS
│       │   ├─ strict + no old config → port_state=unchanged (documented limitation)
│       │   └─ rollback → start_old succeeds + ready → port_state=restored, rolled_back=true
│       │                            └─ fails → port_state=unavailable
│       │
│       └─ start_new succeeds → wait ready
│           ├─ timeout → terminate new → rollback → succeeds+ready → restored
│           │                                      └─ fails → unavailable
│           └─ ready → success, port_state=serving
│
└─ Old server did NOT exist:
    └─ start_new success → port_state=serving
```

### Response Contract

| Scenario | `success` | `port_state` | `rolled_back` |
|----------|-----------|--------------|---------------|
| Pre-flight fails (model not found) | ❌ | unchanged | — |
| Start on free port | ✅ | serving | — |
| Evict + start success | ✅ | serving | — |
| Evict + start fails, rollback succeeds | ❌ | restored | ✅ |
| Readiness timeout, rollback succeeds | ❌ | restored | ✅ |
| Both new and rollback fail | ❌ | unavailable | ❌ |

### Tier Mapping After Change

| Surface | How It Calls | `strict_rollback` |
|---------|-------------|-------------------|
| UI | `state.start_with_eviction(model, port, caller="ui")` | `False` (graceful degradation — no config = no rollback guarantee) |
| Agent API | `state.start_with_eviction(model, port, caller="agent")` | `False` |
| MCP tool | `state.start_with_eviction(model, port, caller="mcp", strict_rollback=True)` | `True` (guarantees only rollback if old config persists) |

## Implementation Plan

| # | Task | Files | Dependencies |
|---|------|-------|-------------|
| 1 | Add `EvictionResult` dataclass + `_parse_eviction_result()` helper | `state.py` | — |
| 2 | Rewrite `start_with_eviction()` with rollback + readiness | `state.py` | Task 1 |
| 3 | Simplify MCP `swap_server()` to thin delegation (~25 lines) | `mcp_server/tools/servers.py` | Task 2 |
| 4 | Update Agent API `/start-with-eviction` endpoint | `agent/routing.py` | Task 2 |
| 5 | Update UI eviction dialog for enhanced messaging | `ui/tabs/model_card.py` | Task 2 |
| 6 | Write unit tests (5 new) + update existing tests | `tests/unit/test_state.py`, MCP tests, integration | Task 2 |
| 7 | Backward-compat grep for tuple-unpacking callers | All files | — (can run in parallel) |

## Migration Safety

- **Running servers unaffected.** Only the start code path changes.
- **Pre-flight failures prevent eviction.** If validation fails, nothing gets killed — old server continues running.
- **`tuple[bool, str]` backward compat** via `_compat` wrapper for any external callers not updated.

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Rollback starts unwanted process on stale port | Low | `strict_rollback=True` enforces persisted config requirement for MCP; UI has confirmation dialog |
| Old model's path no longer exists | Medium (strict mode) | Fails pre-flight: "Cannot swap: old model path no longer exists" — no eviction happens |
| Readiness timeout on large model start | Low | Configurable `readiness_timeout`; default 120s is generous |
| Rollback itself fails (port stolen, process dead) | Low | Returns `port_state=unavailable` with explicit manual intervention signal |
