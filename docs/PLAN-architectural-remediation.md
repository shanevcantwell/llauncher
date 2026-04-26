# Architectural Remediation Plan — llauncher (Final)

**Date:** 2026-04-26  
**Status:** ✅ Reviewed by auditor subagent, corrections applied  
**Scope:** Fix deep architectural issues in llauncher to achieve clean layered boundaries, proper refresh discipline, eliminate dead code/redundancy, and correct correctness bugs. Target: approach the architectural cleanliness of prompt-prix (strict layer imports, stateless tool primitives, single source of truth).

---

## Executive Architecture Summary

### Current State (Problem)
- **Four independent `LauncherState` instances** in memory simultaneously with zero cross-talk or synchronization mechanism
- **MCP read tools return stale data indefinitely** — no refresh in the read path (`list_models`, `get_model_config`, `server_status`)
- **Redundant process scans**: Agent POST /start does 2 scans per call; eviction does 3. Config I/O on mutation paths where only process state matters.
- **No ADR governance** for current architecture — design direction undocumented

### Target State (Goal)
- **Single authoritative `LauncherState` instance per-process**, with clear refresh discipline: every read path either calls refresh or documents why it doesn't need to
- **Clean layer boundaries**: Import rules documented and enforced via PR review; no endpoint directly mutating core infrastructure
- **Eliminate all redundancy**: One scan per operation. Config I/O only where config data is actually needed for the response.
- **ADR governance established** — architectural decisions captured as formal ADRs

### Design Paradigm: "Refresh-On-Read" with Lazy Initialization

Every public read path (HTTP GET, MCP tool, UI display) must either:
1. Call `refresh()` / `refresh_running_servers()` before reading, OR  
2. Document a provable reason why staleness is acceptable for that specific endpoint

---

## Phase 0 — Discovery & Baseline

**Objective:** Lock in what exists before any changes. Map every GitHub issue to actual code status. Several issues may have already been fixed since the analysis docs were written.

### Subtasks

| # | Task | Files to Inspect | Expected Output |
|---|------|-----------------|-----------------|
| 0a | Confirm `resolve_model_shards` is not in codebase | `state.py`, `core/process.py` (confirmed: **not found** via grep — issue #23 likely already resolved) | Close issue or add to backlog as "already fixed" |
| 0b | Locate greedy/prefix model name matching bug (#20) | `mcp_server/tools/models.py`, `_find_model_by_path()`, config tools | Map to exact line; confirm if still present |
| 0c | Check repeat_penalty in `build_command()` (issue #26) | `core/process.py:142` — **already present** (`--repeat-penalty`) | Close as already implemented, or note form field gap |
| 0d | Check `np` exposure in MCP output (#29) | `mcp_server/tools/models.py:list_models()` response; compare to `agent/routing.py` list_models which **already returns** `"np": config.np` | Confirm if MCP response omits it — if so, single-line fix in `models.py` |
| 0e | Verify script discovery residual status (#21) | `state.py:65` docstring mentions "discovered scripts"; check `_skip_path_validation` flag usage | Document what's residual vs. migration artifact |
| 0f | Check Windows run.bat (issue #14) | `scripts/run.bat`, `__main__.py` entry points | If bug exists, mark for Windows-specific worker agent |

### GitHub Issue Mapping After Phase 0

| Issue | Confirmed Status | Notes |
|-------|-----------------|-------|
| **#23** resolve_model_shards dead code | **Already fixed** — not in current codebase | Close as resolved |
| **#26** repeat_penalty support | **Already implemented** — `build_command()` at line 142 uses it | May need form-field check, otherwise close |
| **#29** expose np in API | Agent HTTP `/models` already returns it (line 108 of routing.py) — verify MCP response. If missing: one-line fix in `mcp_server/tools/models.py`. |
| **#27** Pydantic validation for remote node config | Confirmed needed — no Pydantic model exists yet | Add to Phase 5 |

---

## Phase 1 🔴 — MCP Lazy Singleton + Staleness Fix (CRITICAL CORRECTNESS)

> **NOTE:** This phase subsumes the original "Phase 1: per-tool refresh" and "Phase 3a: singleton migration." The two were merged after review found that doing per-tool-refresh then replacing it with lazy-init was wasted effort. Go straight to lazy-init — one shot, no rollback needed.

**Priority:** 🔴 CRITICAL — This is the only correctness bug that can silently return wrong data indefinitely. AI agents get incorrect model lists or stale server status and cannot detect the problem.

### Problem
MCP server creates `state = LauncherState()` at module import time (eager singleton). No MCP read tool calls refresh(). If config changed externally since process start, tools like `list_models`, `get_model_config`, `server_status` return frozen data until a mutation internally touches state. Meanwhile, the Agent HTTP `/models` endpoint always shows fresh data (it calls `refresh()` on every handler), creating an inconsistency: MCP clients may see different data than human dashboard users.

### Solution: Lazy Singleton with Refresh-on-First-Access + Post-Mutation Reconcile

Replace the eager module-level singleton with a lazy pattern identical to what Agent HTTP already uses.

#### Subtask 1a: Implement `get_mcp_state()` in MCP server

**File:** `llauncher/mcp_server/server.py`

**Before (line ~3, current code):**
```python
from llauncher.state import LauncherState

state = LauncherState()  # Eager at import time — may load stale data if config changed between process start and first request
```

**After:**
```python
from llauncher.state import LauncherState

_state: LauncherState | None = None

def get_mcp_state() -> LauncherState:
    """Get or create the MCP server's LauncherState instance.
    
    Lazy initialization matching Agent HTTP's pattern. Subtask 1b commits to
    PER-CALL REFRESH on every read tool — this function just ensures a fresh
    singleton exists before each handler calls state.refresh().
    """
    global _state
    if _state is None:
        _state = LauncherState()  # __post_init__ calls refresh() on creation
        _state.refresh()
    return _state
```

#### Subtask 1b: COMMIT TO REFRESH POLICY — Per-Call Refresh (Not TTL, Not Lazy-Init-Once)

**Decision:** Every MCP read tool handler calls `get_mcp_state()` AND then calls `refresh()` on the returned instance before reading.

```python
# In mcp_server/tools/models.py list_models():
state = get_mcp_state()  # Get singleton (creates + first-refreshes if needed)
state.refresh()          # Always refresh again — configs may have changed since last tool call
```

**Why per-call refresh (not lazy-init-once, not TTL):**

| Policy | Staleness Window | Performance Cost | Verdict |
|--------|-----------------|-----------------|---------|
| Lazy-init once | Until process restart or next mutation — config changes invisible indefinitely | ~0ms | ❌ Rejected (the bug we're fixing) |
| TTL refresh (N=60s window) | Up to 60 seconds stale after external edit | ~5ms every 60s | ❌ Halfway house; still has a staleness window that's undocumented and easy to forget |
| **Per-call refresh** | Zero — always fresh on read | ~5-20ms per tool call (config JSON parse + process scan) | ✅ **Selected** |

MCP tools are not called in tight loops (agents reason between calls). 5-20ms overhead is imperceptible. The alternative — a TTL window where an agent reads stale model list, then tries to start that model and gets a confusing error because it was deleted — is far worse UX.

**Implementation:** In each MCP read tool handler (`list_models`, `get_model_config`, `server_status`), add:
```python
state = get_mcp_state()  # Get/create singleton
state.refresh()          # Always refresh before reading
```
Mutation tools (`start_server`, `stop_server`, `swap_server`) call state methods that internally do their own post-mutation reconciliations — no explicit refresh needed at the handler layer.

#### Subtask 1c: Update `_dispatch_tool` to use `get_mcp_state()`

**File:** `llauncher/mcp_server/server.py` — the `_dispatch_tool` function (approximately line 38-67)

Change the single line that creates/pass the state variable:

```python
# Before:
state = state   # or however it currently gets passed — need to verify exact dispatch pattern

# After (if state comes from module-level global):
state = get_mcp_state()  # lazy-init with guaranteed freshness on first access + per-call refresh in tools

# IMPORTANT: All ~12 tool handler functions continue to receive `state` as their 
# first parameter. Only the SOURCE of that variable changes — no handler signatures
# need updating, just this one dispatch line.
```

**Rollback:** If something breaks, revert is a single-file change: `git checkout HEAD -- llauncher/mcp_server/server.py`, then restart the MCP server process.

### Expected Outcome
- All MCP read tools return fresh data on first access after any external change
- No per-tool refresh overhead (refresh only happens once at first access or when new instance is needed)
- Behavior is identical to Agent HTTP's freshness pattern within a single process

### GitHub Issues Addressed
| Issue | How Addressed | Notes |
|-------|---------------|-------|
| **#24** BUG-ARCH-001: No single source of truth | Partly — resolves "within-process" staleness. Cross-process remains (different processes still have independent state, which is the correct design constraint). | See W1 in review for clarification |
| **#20** BUG-MCP-001: Greedy/prefix model matching | May or may not be addressed by this fix depending on whether it's about data staleness vs. actual matching logic — Phase 0 discovery will clarify. If it's a separate algorithm bug, add to Phase 5. |
| **#22** BUG-MCP-002: list_models output format confuses LLM agent | **Potentially NOT addressed.** This issue title suggests JSON schema/shape problem (nested `{identification: {}, status: {}}` structure), not data freshness. If confirmed as a format issue rather than staleness, add separate Phase 5 subtask for response restructuring. Investigate in Phase 0. |

---

## Phase 2 🟡 — Agent HTTP Redundancy Elimination (Performance/Correctness)

> **⚠️ IMPORTANT:** After review, three corrections were made to the original plan:
> - POST /start MUST keep `refresh()` for config data lookup (was incorrectly proposed as replace with just process scan)
> - GET /status already does the right thing — REMOVE from change list entirely  
> - model_card.py replacement must retain `can_start()` call for external-process detection

**Priority:** 🟡 HIGH — Reduces process scans by ~50% on common paths, eliminates one race-condition-prone temp instance.

### Problem (Recap)
- POST /start does `refresh()` → `start_server()` → `refresh_running_servers()`. First scan's result is wasted and overwritten by the third call. Two scans where two is still too many — only one refresh + mutation should suffice.
- Eviction: handler calls `refresh()`, eviction impl internally does phase-5 reconcile, handler then redundantly calls `refresh_running_servers()` again after returning. Three scans total for one swap.
- UI model_card.py creates a full `LauncherState` + ConfigStore.load() + psutil scan just to check one port collision.

### Subtask 2a: Fix POST /start/{name} Handler

**File:** `llauncher/agent/routing.py` — POST handler for `/start/{model_name}` (approximately lines 158-190)

**Before:**
```python
state = get_state()
state.refresh()              # FULL RELOAD: configs + process scan ← NEEDED for model lookup & rule validation
... call start_server via mutation ...
state.refresh_running_servers()   # Line ~181 — redundant! first refresh already scanned; this overwrites the result with a second scan of same snapshot window. Only necessary IF there were external changes between refresh and mutation, which is unlikely.
```

**After:**
```python
state = get_state()
state.refresh()              # Keep this — NEED configs for model name lookup (line 153) AND rule validation (can_start checks self.rules.validate_start) AND path existence check
... call start_server via mutation ...
# REMOVE the post-mutation refresh_running_servers() at line ~181.
# start_server already optimistically inserts into self.running with PID + config_name.
# If you want reconciliation, add it explicitly to start_server() internally AFTER Popen completes (but this is a separate optimization, not Phase 2 scope).
```

**Why we KEEP refresh():** `can_start()` calls `self.rules.validate_start(config, ...)` which iterates over `self.models`. Also checks `Path(config.model_path).exists()`. Both depend on `self.models` being current. Without full refresh, if config.json was externally edited and a new model path overlaps with a deleted model's path, validation could use stale rules or outdated paths.

**Process scan reduction:** 2 → 1 (50% improvement)

### Subtask 2b: Fix POST /stop/{port} Handler

**File:** `llauncher/agent/routing.py` — POST handler for `/stop/{port}` (approximately lines 212-230)

**Before:**
```python
state = get_state()
state.refresh()              # Full reload ← unnecessary: can_stop only checks port-in-running and captures config_name
... call stop_server ...
state.refresh_running_servers()   # After mutation — redundant again
```

**After:**
```python
state = get_state()
state.refresh_running_servers()  # Only need process state for validation (can_stop checks `port in self.running`) AND response needs server.config_name captured before removal
... call stop_server via mutation ...
# Remove post-mutation refresh_running_servers(). The config_name needed for the response is captured from `server` dict BEFORE stop removes it. Mutation already modifies self.running. No second scan needed.
```

**Process scan reduction:** 2 → 1 (50% improvement)

### Subtask 2c: Fix POST /start-with-eviction/{name} Handler

**File:** `llauncher/agent/routing.py` — handler for `/start-with-eviction/{name}` (approximately lines 253-280)

**Before:**
```python
state = get_state()
state.refresh()                     # Full reload ← needed for model lookup and pre-flight config validation
result = state._start_with_eviction_impl(...)   # Internally does phase-2 stop, phase-3 start (optimistic), phase-5 refresh_running_servers() on success
state.refresh_running_servers()     # Line ~277 — REDUNDANT: eviction impl already reconciled in its own phase 5 before returning
```

**After:**
```python
state = get_state()
if model_name not in state.models:
    raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")  # Use cached models; if stale, user gets fresh list on next GET /models
result = state._start_with_eviction_impl(...)   # Does its own phase-5 reconcile internally — no second refresh needed by caller
# REMOVE the post-eviction refresh_running_servers() at line ~277. Return result directly.
```

**Process scan reduction:** 3 → 1 (67% improvement)  
Note: pre-flight `refresh()` is still needed because eviction needs config data for model validation and new-model port assignment, but we trust the method's internal phase-5 to be authoritative post-reconciliation.

### Subtask 2d: Fix UI temp_instance Anti-Pattern in model_card.py

**File:** `llauncher/ui/tabs/model_card.py` — `_handle_start()` function (approximately lines 292-310)

**Before:**
```python
temp_state = LauncherState()          # Creates full instance with own ConfigStore.load() + psutil process_iter scan
temp_state.refresh()                   # Reloads ALL configs from disk + scans EVERY running process
if target_port in temp_state.running:  # Just checking ONE port!
    show_eviction_dialog()
else:
    proceed_to_start(config)           # NO external-process detection — starts directly without can_start() validation
```

**After:**
```python
from llauncher.core.process import is_port_in_use  # lightweight utility, already exists

# Check if OUR servers already occupy this port with a different model (fast in-memory lookup)
if target_port in state.running and state.running[target_port].config_name != model_name:
    show_eviction_dialog()  # llauncher-owned conflict → UI eviction flow
else:
    valid, msg = can_start(config, caller="ui")  # Still need external-process check (is_port_in_use scans OS for port binding)
    if valid:
        state.start_server(model_name)
        show_success_message(...)
    else:
        st.toast(msg, icon="❌")  # Fail gracefully for external conflicts (e.g., another app using the port)
```

**Why `can_start()` is retained:** The current code replaces a full refresh with a simple dict lookup. But `is_port_in_use(port)` scans all OS processes to check if ANY process owns that port — not just llauncher-managed ones. If we drop it, and a non-llauncher process (e.g., a manually-started llama-server) is using the target port, start_server will fail at subprocess creation time with an obscure OSError rather than showing a clean error toasts or eviction dialog. `is_port_in_use` does a targeted psutil scan of just that one port — it's ~1ms vs ~50-200ms for full process_iter.

**Cost reduction:** Full state instance + config load + complete process scan → two lightweight checks (~1-3ms total).

### Expected Outcome
- POST /start: 1 process scan instead of 2 (50% reduction)
- POST /stop: 1 process scan instead of 2 (50% reduction)  
- Eviction: 1 internal scan instead of 3 (67% reduction)
- UI start button: no full state instance creation (~3ms vs ~50-200ms)

### GitHub Issues Addressed
These are performance/correctness improvements not directly mapped to existing issues. They address the problems identified in the analysis docs (`3-refresh-reconcile-patterns.md` and `4-state-ownership.md`).

---

## Phase 3 🟢 — Cleanup & Residual Artifact Removal (Low Risk)

**Priority:** 🟢 LOW — Pure cleanup. Doesn't fix runtime behavior but reduces maintenance surface. Independent of all other phases.

### Subtask 3a: Remove Script Discovery Docstring References

| File | Location | Change |
|------|----------|--------|
| `state.py` docstring (~line 65) | "loaded from config + discovered scripts" → `"loaded from config.json"` | One-line edit |
| Check `_skip_path_validation` flag in `models/config.py` and `core/process.py` | If used ONLY for historical migration (one-time), add deprecation comment with planned removal timeline. If still needed, document purpose clearly as legacy artifact. |

### Subtask 3b: Dead Code Audit & Cleanup

| Item | Status | Action |
|------|--------|--------|
| `resolve_model_shards` function | **Not in codebase** (confirmed via grep in Phase 0) | Close issue #23 as resolved |
| `start_with_eviction_compat()` wrapper | `state.py:480-495` — if no callers still use tuple unpacking, remove compat alias, keep only `_compat` internal naming | Check all call sites; remove dead aliases |
| `manager.py` UI tab import | `ui/tabs/manager.py` — imported in app.py but check if actually rendered or dead code | If dead: remove from app.py imports and delete file |
| `running.py` UI tab | Already a stub redirecting to dashboard — can be removed entirely once all external references are updated | Safe removal after verifying no other tabs import it |

### Subtask 3c: Expand Audit Log Result Enum

**File:** `llauncher/models/config.py` — `AuditEntry.result` field

```python
# Before:
result: Literal["success", "error", "validation_error"] = Field(...)

# After:
result: Literal["success", "error", "validation_error", "rolled_back", "unavailable"] = Field(...)
```

**File:** `llauncher/state.py` — `_start_with_eviction_impl()` audit logging calls (~3 locations)

Update to use new values where applicable: `"rolled_back"` when a rollback from phase 4 readiness failure succeeds; `"unavailable"` when the port ends up dead after swap.

### GitHub Issues Addressed
| Issue | How Addressed |
|-------|---------------|
| **#21** BUG-DESIGN-004: Script discovery residual | Subtask 3a + 3b docstring and flag cleanup |
| **#23** BUG-CORE-002: resolve_model_shards dead code | Confirmed absent in Phase 0; close issue as resolved |

---

## Phase 4 🟢 — Minor Fixes & Enhancements (All Parallelizable)

**Priority:** 🟢 LOW — Each is independent. Can be done by separate worker agents simultaneously.  
**Blocking:** None — these do not depend on any previous phase.

### Subtask 4a: Issue #16 — Logs Expander Missing from Dashboard Running Servers

Check whether the dashboard renders running servers differently from model cards. If so, add an `st.expander` block with a log viewer using `stream_logs(pid)` or MCP's `get_server_logs`. Reference existing implementation in `ui/tabs/model_card.py:209-212` as the pattern to follow.

### Subtask 4b: Issue #10 — Orphaned Log Files on Port Changes

When port changes, old `{model}-{old_port}.log` files persist forever in `~/.llauncher/logs/`.

**Recommended fix:** Document as a known limitation in the UI with a "Cleanup Orphaned Logs" button that lists `.log` files in the logs directory and offers to delete them. This is low-impact, no correctness bug — just disk usage cleanup.

Alternative if adding UI complexity is unwelcome: document in `~/.llauncher/logs/` README that orphaned logs are normal and add a cleanup script at `scripts/cleanup_logs.py`.

### Subtask 4c: Issue #15 — Remote Model Management via Dashboard

**Out of scope for this remediation plan.** This is a feature request to manage remote nodes (start/stop models, view status) from the Streamlit dashboard. It requires adding UI controls + `RemoteAggregator` delegation logic in multiple tabs. Separate implementation effort; acknowledge and move to backlog or create as a follow-on task.

### Subtask 4d: Issue #14 — Windows run.bat Errors

**Requires Windows test environment.** Out of scope for general worker agents. Mark for Windows-specific worker with appropriate test infrastructure.

### Subtask 4e: Issue #27 — Pydantic Validation on Remote Node Configuration

**Files:** `llauncher/remote/node.py` (or wherever nodes.json writing happens)

Add a new Pydantic model and apply it before persisting to nodes.json:
```python
class RemoteNodeConfig(BaseModel):
    name: str
    host: str
    port: int = Field(default=8765, ge=1024, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        # Accept hosts, IPs, and localhost-style names; reject URLs or paths
        if not re.match(r'^[\w\.\-]+$', v):
            raise ValueError(f"Invalid host format: {v}")
        return v

# In node registry write path, before writing to nodes.json:
try:
    validated = RemoteNodeConfig(**node_data)
    # proceed to save
except ValidationError as e:
    raise ValueError(f"Invalid node configuration: {e}")
```

### Subtask 4f: Issue #29 — Expose np in MCP list_models Response (if applicable)

After Phase 0 confirms whether `/models` already returns `np` in Agent HTTP but not in MCP. If MCP response omits it, add `"np": config.np` to the model dict in `mcp_server/tools/models.py`. Note: agent/routing.py line 108 already includes it — this is purely a potential MCP gap.

### Subtask 4g: Investigate Issue #22 — "list_models output format confuses LLM agent"

Phase 0 must explicitly determine whether the problem is:
- **Data freshness** (answered by Phase 1 per-call refresh) → if yes, #22 resolves itself. Do NOT close issue by association without verifying.
- **JSON schema shape** (`{identification: {...}, status: {...}}` nested structure vs. flat list) → needs a separate response restructuring task in `models.py`. If confirmed structural, scope as a small follow-on: flatten to `[{"name": ..., "status": "...", ...}]`

### Subtask 4h: Fix `_find_model_by_path` Symlink/Path Mismatch Bug

**File:** `state.py` — `_find_model_by_path()` method (called from within `refresh_running_servers()`, approximately line ~179 based on analysis doc numbering)

**Problem:** The method does exact string comparison between the process cmdline `-m` arg and every config's `model_path`. If:
- A config stores an absolute path but process was started with a relative one (or vice versa) — no match → `config_name = "unknown"`
- Two configs share the same file via hardlink/symlink with different stored paths — first match wins, second is unidentifiable
- Path has trailing slash differences or normalizes differently (`/home/user/model.gguf` vs `/home/user/./model.gguf`)

This causes `running` entries to show `config_name = "unknown"`, making it impossible for the dashboard or Agent HTTP endpoints to identify which model is running.

**Fix:** Normalize both paths before comparison:
```python
def _find_model_by_path(self, process_model_path: str) -> str | None:
    # Normalize both paths for consistent matching
    normalized = os.path.realpath(process_model_path)  # resolves symlinks, .., .
    for name, config in self.models.items():
        if os.path.realpath(config.model_path) == normalized:
            return name
    return None
```
Uses `os.path.realpath()` which resolves symlinks to canonical path. If two configs truly point to the same physical file, the first match still wins (acceptable — this is an unusual misconfiguration edge case).

**Cost:** ~3 lines changed in `_find_model_by_path()`, zero behavior change on normal paths (absolute paths that are already canonical). Only fixes the mismatch cases.

### Subtask 4i: Issue #26 — Verify repeat_penalty in UI forms (if applicable)
Already implemented in `build_command()` at line 142 of `core/process.py`. Confirm the Streamlit "Edit Model" form exposes the field to users. If not, add an input control for `repeat_penalty` in `ui/tabs/forms.py`.

Already implemented in `build_command()` at line 142 of `core/process.py`. Confirm the Streamlit "Edit Model" form exposes the field to users. If not, add an input control for `repeat_penalty` in `ui/tabs/forms.py`.

### GitHub Issue Mapping

| # | Action | Phase |
|---|--------|-------|
| **#16** Logs expander missing from Dashboard Running Servers | Implement log viewer in dashboard | 4a |
| **#29** Expose np in API responses | Already in Agent HTTP — check MCP output only (if missing, one-line fix) | 4f |
| **#27** Pydantic validation for remote node config | New RemoteNodeConfig model + validation gate on write path | 4e |
| **#15** Remote model management via dashboard | **Out of scope** — backlog item | Acknowledge only |
| **#14** Windows run.bat errors | **Requires Windows test env** — separate worker needed | Acknowledge only |
| **#26** repeat_penalty support | Already implemented — verify UI form field | 4h |
| **#22** list_models output format confuses LLM agent | Investigate in Phase 0; if structural: separate restructure task. If staleness-only: resolved by Phase 1 | 4g |
| **#10** Orphaned log files on port changes | Document as known limitation + add cleanup button/script | 4b |

---

## Phase 5 🟡 — Architectural Governance Foundation (ADRs)

**Priority:** 🟡 MEDIUM — Enables future safety. Once established, subsequent refactors are guided by ADRs rather than ad-hoc decisions.

> **ADR TIMING NOTE:** Structural ADRs (#003, #004) that govern cross-cutting patterns should be written as the corresponding code changes happen (during Phases 1 and 2), not retrofitted later. Writing documentation after decisions lock in risks becoming "write down what we already did" — lower value than establishing the pattern going forward.
>
> **ADR-006 (lazy-init pattern)** is written as part of Phase 1 (alongside the code change) because it establishes a pattern that will be reused by any future MCP entry point. The other ADRs follow after Phases 2-3 are settled.

### Subtask 5a: Create ADR-003 — State Ownership and Refresh Discipline

Document:
- The four-instance problem (one per process, each with independent `LauncherState`)
- The one-per-process solution established by Phase 1
- Refresh-on-read contract: every public read path MUST call `refresh()` or `refresh_running_servers()`, OR document why staleness is acceptable
- Why UI keeps per-session state in Streamlit (inherent to SessionState model; merging into global singleton would break concurrent users)
- Remote layer stays independent — it talks HTTP only, never touches another node's `LauncherState`

### Subtask 5b: Create ADR-004 — Import Layer Boundaries and Cross-Layer Reach

Model this after prompt-prix's ADR-006. Define allowed imports between layers:

```
┌──────────────┬─────────────────────────────────────────────┐
│ LAYER        │ MAY IMPORT                               │ MUST NOT IMPORT                    │
├──────────────┼─────────────────────────────────────────────┤
│ Endpoint     │ state, models, remote (HTTP clients only)   │ core.process directly              │
│ State        │ core.config, core.process, models           │ agent/, mcp_server/, ui/           │
│ Core         │ models, settings                            │ state, endpoints, ui               │
│ Remote       │ models (types only), remote.node            │ state, endpoint, ui                │
│ UI           │ state, remote.state, remote.registry        │ core.process directly              │
└──────────────┴─────────────────────────────────────────────┘
```

**Enforcement:** Document that enforcement happens via PR review. Consider adding `import-linter` to CI in the future for automated checks (not phase 5 scope — track as follow-on).

### Subtask 5c: Create ADR-005 — Refresh and Reconcile Pattern

Document the canonical refresh path for each operation type to prevent regression of Phase 2 redundancies:

| Operation | Before Read | After Write | Reasoning |
|-----------|-------------|-------------|-----------|
| GET /models (Agent HTTP) | `refresh()` | — | Need both configs + running status |
| GET /status (Agent HTTP) | `refresh_running_servers()` only | — | Only need process table; configs irrelevant (already optimal in current code) |
| POST /start (Agent HTTP) | `refresh()` | None needed | Full refresh needed for config lookup AND rule validation. Mutation sets optimistic state into self.running. Post-mutation scan redundant. |
| POST /stop (Agent HTTP) | `refresh_running_servers()` only | None needed | Need process state to verify port + capture config_name before removal. No post-scan needed. |
| POST /eviction (Agent HTTP) | `refresh()` | Method-internal phase 5 handles it | Pre-flight needs configs; eviction impl reconciles internally in its own phase 5. |

### Subtask 5d: Create ADR-006 — MCP State Initialization Pattern

Document the lazy-init pattern established by Phase 1 as the canonical approach for any new endpoint process (if additional entry points are added in the future):
- Never use `state = LauncherState()` at import time in a new process
- Always use `get_XXX_state()` with lazy initialization and first-access refresh

---

## Revised Priority Ordering Summary

```
Phase 0 ────► Phase 1 ────► Phase 2 ────┬──► Phase 3 (cleanup)
(Discovery │ MCP lazy-init + staleness│     (low-risk docstring & enum
 fix, no rollback needed) │ Agent HTTP redundancy elimination│ cleanup)
                           │           │                       │
                           ▼           ├──► Phase 4a-h (minor fixes — all parallelizable)
                        ┌─────────────┐ │   
                        │ Phase 5     │◘──► ADR-003, -004, -005, -006 
                        │ Governance  │       governance layer
                        └─────────────┘       
```

**Execution rationale:**
1. **Phase 0 first** — lock in baseline before any changes; clarify which GitHub issues are already resolved vs. active
2. **Phase 1 second** (critical correctness) — fix MCP staleness permanently with lazy singleton, no rollback needed from prior approach
3. **Phase 2 third** (performance/correctness) — Agent HTTP handler refactoring is independent of MCP changes; only needs to establish that Phase 1's architectural pattern (lazy-init + refresh discipline) is in place as a reference
4. **Phases 3, 4a-h** run parallel with each other and after Phases 1-2 (they don't block on or depend on any code change from earlier phases, except Subtask 4e requires knowing where nodes.json write happens and 4f may need Phase 0 clarification about MCP response format)
5. **Phase 5 last** — structural ADRs (#003, #004) written alongside their corresponding phase (as noted above); governance scaffolding in Phase 5 ties everything together

> **⚡ Alternative ordering for momentum:** If you want quick wins early to build confidence before touching MCP infrastructure, Phase 2 can run *before* Phase 1. Both touch completely disjoint file sets (`agent/routing.py` + `ui/tabs/model_card.py` vs. `mcp_server/server.py`). The only dependency is that Phase 1 establishes the refresh discipline as a reference pattern — but you could note in Phase 2's documentation "follow same pattern as Phase 1" and implement them in either order. If choosing this: do Phase 2 first for momentum, then Phase 1 for correctness.

---

## Decision Log (Resolved by Reviewer Corrections)

| # | Decision | Resolution | Rationale |
|---|----------|------------|-----------|
| D1| MCP refresh policy (per-call vs. TTL vs. lazy-init-once) | **Per-call refresh** — every read tool handler calls `state.refresh()` before reading, in addition to getting the singleton via `get_mcp_state()`. Lazy-init once has a staleness window after external config changes. TTL is a halfway house with undocumented windows. Per-call adds ~5-20ms per MCP tool call (imperceptible to agents reasoning between calls) and guarantees zero-staleness. |
| D2| Phase 2 before Phase 1 for momentum? | **Allowed but not default** — disjoint file sets mean either order works; Phase 1 first is default (correctness before performance), Phase 2 first gives quick wins if building confidence matters more than the theoretical ordering. Document decision in Phase ordering callout. |
| D3| Write ADRs before or after code decisions? | **Write structural ADRs alongside code** — ADR-006 during Phase 1 (lazy-init pattern), ADR-003/004 drafted in parallel with their phases, finalized in Phase 5. Prevents documentation from becoming "write down what we already did" vs establishing patterns going forward. |
| D4| Replace `refresh()` with just process scan in POST /start? | **No — keep full refresh** for config data lookup and rule validation | can_start() needs self.models for model name resolution and Path existence checks. Post-mutation re-scan only is incorrect. Removing post-mutation scan IS correct (optimistic write covers it). |
| D5| Replace temp_instance with just `is_port_in_use(port)`? | **No — retain can_start() call** after state.running check | The current code drops full validation, including external-process detection. can_start(config, caller="ui") calls is_port_in_use() which does a targeted one-port psutil scan (~1ms) vs. the full process_iter in temp_instance (~50-200ms). Both are valid; retaining can_start preserves correctness for non-llauncher processes on target port. |
| D6| UI keep per-session state or merge into global singleton? | **Keep separate** (per-session st.session_state) | Streamlit's SessionState model makes merging across browser sessions technically difficult and risky (one user's actions would affect another). Each session has its own LauncherState instance — this is the correct design constraint for the UI layer. |
| D7| Log orphan management strategy? | **Document as known limitation** + add cleanup button/script | Truncate-on-start or archive-by-timestamp adds complexity to the start path where latency matters most. Orphaned logs are harmless except disk usage; a cleanup button is low-effort and gives users control. |
| D8| Issue #22 (list_models confuses LLM) — format vs. freshness? | **Investigate in Phase 0**; may need separate task if structural | If it's data staleness, resolved by Phase 1. If it's nested JSON structure confusing agents, requires response restructuring in models.py as a separate scoped task. |
| D9| Issue #15 (remote model management via dashboard) — implement or defer? | **Defer** — out of scope for this remediation plan | Feature-level UI changes; not an architectural correctness/performance fix. Add to backlog with clear spec. |

---

## Risk & Observability Strategy

### Risks

| Risk | Phase(s) Affected | Likelihood | Impact | Mitigation |
|------|-------------------|------------|--------|------------|
| MCP lazy-init changes initialization timing subtly (state created on first call vs import time) | 1 | Low | Low — behavior is functionally identical; clients see same results regardless of when state was initialized. Add assertion in debug mode if needed. |
| Changing Agent HTTP refresh pattern could break edge cases with external config edits between refresh and mutation | 2 | Medium | Medium — the POST /start fix (keeping full refresh) mitigates most risk. The POST /stop optimization (process-only scan before stop) assumes configs haven't changed since last refresh, which is fine since post-stop only returns already-captured data. |
| Streamlit session isolation broken by shared state leaks | 3+ | Low | High — documented in ADR-003 as a design constraint; keep UI state explicitly in st.session_state |

### Observability

Add across phases:
1. **Refresh counter metric** (Phase 1): Add `_total_refreshes` counter to `LauncherState`, incremented on every `refresh()` call. Expose via `/status` endpoint as `"refresh_count"`. Lets operators verify refresh frequency and detect anomalies.
2. **Audit log enrichment** (Phase 3): Expand `AuditEntry.result` enum to capture `"rolled_back"` and `"unavailable"` states for better visibility into eviction outcomes.

### Regression Prevention

- Every Phase 2 optimization should be accompanied by a unit test that verifies the exact number of process scans performed: mock `psutil.process_iter()` and count invocations per handler call
- Golden-path integration test after Phase 1+3: start → stop → list_models (MCP) should show server stopped with no staleness window
- After Phase 3 singleton migration, add a simple assertion that `_total_refreshes` counter increments predictably across operations

---

## File Change Matrix

### Phase 0 — Discovery & Baseline
| Task | Files to Inspect | Expected Output |
|------|-----------------|-----------------|
| 0a | `state.py`, `core/process.py` | Confirm resolve_model_shards absent → close issue #23 |
| 0b | `mcp_server/tools/models.py`, `_find_model_by_path()`, config tools | Map exact line for prefix matching bug (issue #20) |
| 0c | `core/process.py:142` | Confirm repeat_penalty in build_command → close issue #26 or note form gap |
| 0d | `mcp_server/tools/models.py` + compare to `agent/routing.py:108` | Check if MCP output has np; one-line fix if not (issue #29) |
| 0e | `state.py:~65`, `_skip_path_validation` flag usage locations | Document residual vs. active code |
| 0f | `scripts/run.bat`, `__main__.py` | Windows bug status → separate worker or close if fixed |

### Phase 1 — MCP Lazy Singleton + Staleness Fix (CRITICAL)
| File | Changes | Lines Affected |
|------|---------|----------------|
| `mcp_server/server.py` | Replace eager `state = LauncherState()` with lazy `get_mcp_state()`. Update single-line dispatch to use it. All handler signatures unchanged. | ~8 lines (1 new function + 1 dispatch line change) |

### Phase 2 — Agent HTTP Redundancy Elimination
| File | Changes | Lines Affected |
|------|---------|----------------|
| `agent/routing.py` — POST /start | Keep refresh() for config, REMOVE post-mutation refresh_running_servers() (~line 181) | ~5 lines removed |
| `agent/routing.py` — POST /stop | Replace refresh() with refresh_running_servers(), remove post-mutation scan | ~3 lines changed, ~5 removed |
| `agent/routing.py` — POST /eviction | Keep pre-flight refresh, REMOVE handler's post-eviction refresh (~line 277) | ~5 lines removed |
| `ui/tabs/model_card.py` | Replace temp_instance pattern with state.running check + can_start() call | ~8-10 lines changed in _handle_start() |

### Phase 3 — Cleanup & Residual Artifact Removal
| File | Changes | Lines Affected |
|------|---------|----------------|
| `state.py` docstring (~line 65) | Remove "discovered scripts" reference | ~1 line |
| `models/config.py` AuditEntry.result type | Add `"rolled_back"`, `"unavailable"` to Literal union | ~1 line in type def |
| `state.py` _start_with_eviction_impl() audit calls | Update 3-8 call sites with new result values where applicable | ~6-8 lines |

### Phase 4 — Minor Fixes (All Parallelizable)
| File | Issue | Changes | Lines Affected |
|------|-------|---------|----------------|
| `mcp_server/tools/models.py` | #29 | Add `"np": config.np` if missing from MCP output | ~1 line |
| `ui/tabs/dashboard.py` or model_card patterns | #16 | Add log expander to running servers display section | ~8 lines (new st.expander block) |
| Logs cleanup mechanism | #10 | Document as known limitation + add cleanup button/script | ~15-20 lines |
| `remote/node.py` or registry file | #27 | Add RemoteNodeConfig Pydantic model + validation gate on write path | ~15-20 lines |

### Phase 5 — ADR Governance
| File | Content | Lines Approx. |
|------|---------|---------------|
| `docs/adrs/003-state-ownership-and-refresh-discipline.md` | Four-instance problem → one-per-process solution, refresh-on-read contract | ~60 lines (new file) |
| `docs/adrs/004-import-layer-boundaries-and-cross-layer-reach.md` | MAY/MUST NOT import table per layer; enforcement via PR review | ~50 lines (new file) |
| `docs/adrs/005-refresh-and-reconcile-pattern.md` | Canonical refresh paths per operation type, preventing Phase 2 regression | ~40 lines (new file) |
| `docs/adrs/006-mcp-state-initialization-pattern.md` | Lazy-init pattern as canonical for new processes | ~30 lines (new file) |

---

*This plan incorporates corrections from auditor review: merged Phase 1 into lazy-singleton approach, fixed POST /start correctness bug, added can_start() preservation to model_card.py replacement, removed already-optimal GET /status from changes, resolved GitHub issue mappings, and clarified out-of-scope items. Additional post-review patches: committed per-call refresh policy for MCP (not TTL), added _find_model_by_path symlink resolution fix as Phase 4h, restructured ADR timing so ADR-006 is written alongside Phase 1 code changes, documented alternative ordering for momentum (Phase 2 before Phase 1). Ready for worker execution after Phase 0 discovery confirmation.*
