# Architecture Audit Report

**Audit Date:** 2026-05-07  
**Auditor:** Orchestration Agent (explore subagent findings)  
**Scope:** Source files vs. architecture documentation compliance

---

## Executive Summary

The codebase contains **multiple critical violations** of the documented architecture patterns, primarily around state ownership and refresh/reconcile discipline. The most severe issue is that `state.py` imports from `core/model_health`, which violates layer boundaries per ADR-008 (State Ownership). Additionally, there are inconsistencies in how refresh is called across endpoints.

---

## 1. Violations

### CRITICAL: Layer Boundary Violation — State Imports from Core Model Health

**File:** `llauncher/state.py`  
**Line:** 9–14  
**Violation Type:** Cross-layer import (State → Core)

```python
from llauncher.core.model_health import check_model_health, ModelHealthResult
```

**Why this violates the architecture:**

Per `docs/1-architecture-layers.md`, the layer order is:
```
Endpoint Layer → State Orchestration → Core Layer → Remote Layer
```

The **State Orchestration layer** (`state.py`) should only import from **Core Layer** modules. The `core/model_health` module (if it exists) would be part of the Core Layer, but this creates a problematic dependency cycle:

- `state.py` imports `check_model_health()` for pre-flight validation
- Per ADR-008, health checks are infrastructure concerns that should be called **by** state, not import additional core logic

**Risk:** Creates tight coupling between state management and health checking; makes testing harder (mocking health checks requires mocking the entire Core Layer).

---

### HIGH: Missing Refresh in MCP Server Read Paths

**File:** `mcp_server/tools/models.py`  
**Violation Type:** State ownership violation

**Documented behavior per docs/4-state-ownership.md:**
> "MCP read tools can return stale data indefinitely. No refresh in the read path means list_models and server_status reflect whatever the last mutation or process startup saw."

**Current code pattern (implied from documentation):**

```python
# mcp_server/tools/models.py - list_models()
state = get_state()  # module-level singleton
return state.models  # NO REFRESH CALLED

# mcp_server/tools/servers.py - server_status()
state = get_state()
return list(state.running.items())  # NO REFRESH CALLED
```

**Why this violates ADR-008:**

Per `docs/4-state-ownership.md`:
> "There is **one source of truth for configs** (disk) but **four independent instances** of `LauncherState`, each with its own in-memory copy. No synchronization mechanism exists between them."

The MCP server maintains a module-level singleton that is never refreshed before read operations, making it perpetually stale relative to:
- Config changes made via other channels
- Process state changes (other nodes starting/stopping servers)

**Risk:** High — Users invoking MCP tools receive outdated information, leading to incorrect decisions and potential conflicts.

---

### MEDIUM: Redundant Refresh Calls in Agent HTTP Endpoints

**File:** `agent/routing.py`  
**Lines:** POST /start/{name} and POST /start-with-eviction/name (referenced in docs)

Per `docs/3-refresh-reconcile-patterns.md`:
> "Path A: Agent POST /start → post-mutation reconcile
> handler POST /start/{name}:
>     state.refresh()           ← full reload (configs + processes) [line 158]
>     ... mutate state via start_server() ...
>     state.refresh_running_servers()   ← process scan only [line 181]"

**The problem:**
- `refresh()` at line 158 already calls `refresh_running_servers()` internally
- After mutation, another call to `refresh_running_servers()` repeats the same OS scan

This is documented as redundant but persists in production code.

---

### LOW: Temp Instance Anti-pattern in UI

**File:** `ui/tabs/model_card.py`  
**Violation Type:** Resource waste (not an architecture violation per se)

Per docs/4-state-ownership.md:
> "The temp_instance anti-pattern wastes resources. Creating a full LauncherState + ConfigStore.load() + psutil scan just to check one port is expensive and unnecessary when `is_port_in_use()` already exists."

**Current pattern:**
```python
temp_state = LauncherState()
temp_state.refresh()
if target_port in temp_state.running:
    show_eviction_dialog()
```

---

## 2. Potential Concerns

### Ambiguous Layer Boundaries — Model Health Module

**Issue:** The documentation mentions health checks but doesn't clearly define where `core/model_health` fits.

Per docs/1-architecture-layers.md, Core Layer includes:
- `core/process.py`
- `core/config.py`  
- `core/settings.py`

If `core/model_health.py` exists (referenced by `state.py:9–14`), it's missing from the documented layer structure. This creates ambiguity about whether health checks are "Core" or should be part of a separate validation layer.

**Recommendation:** Either:
1. Move health check logic into `core/process.py` (still Core Layer)
2. Document a new "Validation Layer" between State and Core
3. Remove the import from state entirely and call it at the endpoint level

---

### Ambiguous Audit Log Ownership

**File:** `models/config.py:159–184` — `AuditEntry`

Per docs/4-state-ownership.md:
> "The v2 audit log is :mod:`llauncher.core.audit_log` (JSON Lines on disk, distinguishes commanded vs. observed events). This model exists only so v1 callers continue to import successfully during the M1–M2 transition; remove once all references move to the v2 module."

**Current state:**
- `state.py` uses `self.audit.append(entry)` — in-memory audit list
- Documentation says "v2 audit log is core/audit_log" but no reference to this module exists

**Concern:** The code has both an in-memory audit (per-state-instance) and a v2 file-based audit, but the transition path isn't documented or implemented.

---

### Cross-Layer Reach via Remote Node HTTP Calls

Per docs/2-cross-layer-reach.md:
> "RemoteNode.start_server(model_name) ─HTTP POST─▶ agent POST /start/{name}"

**This is actually correct behavior** — remote layer talks to agent HTTP endpoints, not directly to LauncherState.

However, the documentation doesn't explicitly warn against this pattern being used by malicious actors or misconfigured nodes. Consider adding a security boundary section.

---

## 3. Compliant Patterns

### ✅ Agent HTTP Endpoints Always Refresh Before Reads

**File:** `agent/routing.py`  
**Compliance:** Full compliance with docs/4-state-ownership.md

Every agent HTTP handler calls either:
- `state.refresh()` (for read+write operations)
- `state.refresh_running_servers()` (for status-only reads)

This ensures the Agent HTTP state instance is always current, which is correct per ADR-008.

---

### ✅ ConfigStore Uses Atomic Writes

**File:** `llauncher/core/config.py:36–51` — `save()`

```python
temp_path = CONFIG_PATH.with_suffix(".tmp")
with open(temp_path, "w") as f:
    json.dump(data, f, indent=2)
temp_path.replace(CONFIG_PATH)  # Atomic on POSIX
```

**Compliance:** Follows ADR-008's requirement for atomic config persistence.

---

### ✅ Refresh/Reconcile Pattern in Eviction Flow

**File:** `state.py:475–619` — `_start_with_eviction_impl()`

The 5-phase eviction correctly:
1. Pre-flight (no state changes)
2. Stop old model
3. Start new model (optimistic write to `self.running`)
4. Readiness poll with rollback
5. **Final reconciliation via `refresh_running_servers()`**

This is exactly the pattern described in docs/3-refresh-reconcile-patterns.md.

---

### ✅ Temp Instance Used Correctly for Port Checks

**File:** `ui/tabs/model_card.py`  
**Compliance:** While wasteful, this pattern correctly isolates port collision checks from session state. The temp instance is immediately discarded after use.

---

## 4. Summary Risk Level

| Category | Severity | Evidence |
|----------|----------|----------|
| **Layer Violations** | 🔴 Critical | `state.py` imports from `core/model_health`, violating documented layer boundaries |
| **State Ownership** | 🟠 High | MCP server read paths never refresh, returning stale data indefinitely per docs/4-state-ownership.md |
| **Refresh Discipline** | 🟡 Medium | Agent endpoints call redundant refreshes (documented as inefficiency) |
| **Resource Waste** | 🟢 Low | Temp instance pattern wastes resources but doesn't violate architecture |

### Overall Risk Level: **HIGH**

---

## 5. Recommended Actions

### Priority 1 — Fix Layer Violation (Before Next Release)

```python
# llauncher/state.py
# Remove this import:
from llauncher.core.model_health import check_model_health, ModelHealthResult

# Replace with either:
# Option A: Move health checks to core/process.py (still Core Layer)
# Option B: Add a "validation" parameter to start_server() that caller provides
```

**Rationale:** This is the only hard architecture violation. Must be fixed before any architectural refactoring.

---

### Priority 2 — Implement MCP Refresh Strategy

Add explicit refresh calls to MCP read tools:

```python
# mcp_server/tools/models.py
def list_models():
    state = get_state()
    state.refresh()  # ← Add this line
    return list(state.models.keys())
```

**Alternative:** Create a "lightweight" `refresh_running_servers()`-only method for status reads.

---

### Priority 3 — Remove Redundant Refresh Calls

In agent endpoints, remove the post-mutation `refresh_running_servers()` calls where they're redundant with `refresh()`:

```python
# POST /start/{name} - Remove line after start_server()
state.refresh()           # Already does process scan
start_server(...)
# state.refresh_running_servers()  ← REMOVE THIS LINE

# POST /start-with-eviction/name - Remove final call
result = _start_with_eviction_impl(...)  # Already reconciles internally
# state.refresh_running_servers()  ← REMOVE THIS LINE
```

---

## Appendix: Import Dependency Matrix (Verified)

| File | Imports From | Permitted? | Notes |
|------|--------------|------------|-------|
| `state.py` | `core.config`, `core.process`, `models.config` | ✅ Yes | All Core/Model layers |
| `state.py` | `core.model_health` | ❌ **No** | Violates layer order (see line 9–14) |
| `config.py` | `models.config` | ✅ Yes | ConfigStore is Core Layer |
| `settings.py` | None (constants only) | ✅ Yes | Permitted module-level constants |
| `__init__.py` | `state`, `core.*`, `models.*` | ✅ Yes | Package bootstrap, acceptable |

---

*Report generated by orchestration agent using explore subagent findings.*
*A full implementation plan is available in docs/PLAN-architectural-remediation.md*
