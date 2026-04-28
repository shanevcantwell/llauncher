# llauncher Codebase Summary

**Generated:** 2026-04-26  
**Version:** 0.2.0a0  
**Python ≥3.11, Pydantic ≥2.0, MCP ≥1.0**

---

## 1. Architecture Overview

### What is llaunch?
llauncher is an **MCP-first launcher and management tool for llama.cpp `llama-server` instances**. It provides three entry surfaces that all orchestrate the same underlying `llama-server` processes:
- **MCP Server** (`llauncher-mcp`) — programmatic control for LLM agents via stdio transport
- **Agent HTTP API** (`llauncher-agent`) — per-node FastAPI service on port 8765 for remote/headless management
- **Streamlit UI** (`streamlit run llauncher.ui.app`) — human dashboard with live log streaming and multi-node aggregation

Each process (MCP server, agent, Streamlit session) maintains its **own `LauncherState` instance**. There is no cross-process state synchronization by design.

### Major Components & Interaction

```
┌───────────────────────┐     ┌───────────────────────┐     ┌───────────────────────┐
│    MCP Server         │     │  Agent HTTP (FastAPI)  │     │   Streamlit UI        │
│  (stdio/stdin/stdout) │     │  per-node :8765       │     │   human dashboard     │
│                       │     │                        │     │                       │
│ Lazy singleton        │     │ Global instance        │     │ Per-session instance  │
│ get_mcp_state() →     │     │ get_state() →          │     │ st.session_state["state"]
│ LauncherState()       │     │ LauncherState()        │     │                       │
└──────────┬────────────┘     └──────────┬────────────┘     └──────────┬────────────┘
           │                             │                            │
           ▼                             ▼                            ▼
   ════════ LAUNCHER STATE (per-process) ════════
   • models: dict[str, ModelConfig]  (from ~/.llauncher/config.json)
   • running: dict[int, RunningServer]  (discovered from psutil process table)
   • audit: list[AuditEntry]
   • rules: ChangeRules (port blacklists, caller filters)
           │
           ▼
   ════════ CORE INFRASTRUCTURE ════════
   • ConfigStore — JSON file persistence (atomic write via tmp+rename)
   • Process manager — psutil-based start/stop/find/watch of llama-server processes
   • Settings — env vars + .env for binary path, ports, blacklists
           │
           ▼
   ════════ EXTERNAL: llama-server processes ════════
```

**Remote/Multi-Node Architecture:**
Each managed node runs its own `llauncher-agent`. The Streamlit UI's head dashboard connects to these agents over HTTP. This is a **flat peer-to-peer topology** — no central routing service exists.

```
┌─────────────── Head Dashboard ───────────────┐
│  RemoteAggregator ──► pings all nodes         │
│     NodeRegistry ◄── ~/.llauncher/nodes.json   │
└──────┬──────────┬──────────┬──────────────────┘
       ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Agent 1  │ │ Agent 2  │ │ Agent N  │
│ :8765    │ │ :8765    │ │ :8765    │
└──────────┘ └──────────┘ └──────────┘
```

### Layer Boundaries (Current State — See ADR-004 for planned enforcement)

| Layer | May Import | Must NOT Import |
|-------|-----------|-----------------|
| **Endpoint** (agent/routing, mcp_server) | state, models, remote.node/http clients only | core.process directly |
| **State** (`state.py`) | core.config, core.process, models | agent/, mcp_server/, ui/ |
| **Core** (config, process, settings) | models, settings | state, endpoints, ui |
| **Remote** (node, registry, state) | models types only | state, endpoint, ui |
| **UI** | state, remote.state, remote.registry | core.process directly |

### Key Dependencies
- `pydantic` — all data modeling (`ModelConfig`, `RunningServer`, `ChangeRules`)
- `psutil` — process scanning and management
- `httpx` — HTTP client for multi-node communication  
- `fastapi + uvicorn` — agent REST API
- `mcp >= 1.0` — MCP server protocol (stdio transport)
- `streamlit` — web UI (optional dependency `[ui]`)

---

## 2. Directory Structure Breakdown

### `/llauncher/` — Application Package

| File/Dir | Purpose | Key Contents |
|----------|---------|-------------|
| `__init__.py` | Package metadata | Version `0.2.0a0` |
| `state.py` | **Core state management** | `LauncherState` dataclass: models dict, running servers dict, audit log, change rules; methods for start/stop/start_with_eviction/refresh/can_start/can_stop; `EvictionResult` dataclass (5-phase swap semantics) |
| `models/config.py` | **Pydantic data models** | `ModelConfig` — 20+ fields describing a llama-server config with validation; `RunningServer`, `AuditEntry` (`result: Literal["success"|"error"|"validation_error"]`), `ChangeRules` (blacklisted ports/set, caller filters) |
| `core/config.py` | **Persistence** | `ConfigStore` — atomic JSON read/write to `~/.llauncher/config.json`, methods for add/update/remove/list/get model configs |
| `core/process.py` | **Process management** | `start_server()` — builds llama-server command, writes to log file in `~/.llauncher/logs/`; `stop_server_by_port/pid()` — graceful terminate + kill fallback; `find_all_llama_servers()`, `find_server_by_port()`, `is_port_in_use()`, `build_command()` (maps all ModelConfig fields → CLI args), `stream_logs()`, `wait_for_server_ready()` |
| `core/settings.py` | **App settings** | Env vars: `LLAMA_SERVER_PATH`, `DEFAULT_PORT`, `BLACKLISTED_PORTS`, `LOG_LEVEL` — with defaults from home directory |

### `/llauncher/mcp_server/` — MCP Tools Layer

| File | Purpose |
|------|---------|
| `server.py` | MCP stdio server; lazy singleton `get_mcp_state()` with failure recovery (try/except around constructor); `_dispatch_tool()` maps 12 tool names to handler functions |
| `tools/models.py` | `list_models`, `get_model_config` — both call `state.refresh()` before reading, return structured `{identification, status}` dict |
| `tools/servers.py` | `start_server`, `stop_server`, `server_status`, `get_server_logs`, `swap_server` — thin wrappers around state methods; `swap_server` calls `_start_with_eviction_impl(strict_rollback=True)` and maps `EvictionResult` → MCP response |
| `tools/config.py` | `update_model_config`, `validate_config` (stateless, bypasses lazy init), `add_model`, `remove_model` — CRUD for model configs |

### `/llauncher/agent/` — Per-Node HTTP API

| File | Purpose |
|------|---------|
| `config.py` | `AgentConfig` Pydantic model (host, port, node_name) with env var construction via `from_env()` |
| `routing.py` | FastAPI router: GET `/health`, `/node-info`, `/status`, `/models`; POST `/start/{model}`, `/stop/{port}`, `/start-with-eviction/{model}`; GET `/logs/{port}`. All use shared `_state` global via `get_state()`. Eviction endpoint calls `_start_with_eviction_impl(strict_rollback=False)` |
| `server.py` | Entry point — creates FastAPI app, runs uvicorn on configured host:port, supports `--stop` flag to terminate running agent |

### `/llauncher/remote/` — Multi-Node Client Layer

| File | Purpose |
|------|---------|
| `node.py` | `RemoteNode` — HTTP client wrapper around a single agent node (`ping()`, `get_status()`, `get_models()`, `start_server()`, `stop_server()`, `get_logs()`). Uses httpx with configurable timeout. `NodeStatus` enum (ONLINE/OFFLINE/ERROR), `RemoteServerInfo` data class |
| `registry.py` | `NodeRegistry` — CRUD for nodes.json at `~/.llauncher/nodes.json`; auto-starts local agent on first UI load; `refresh_all()` pings all registered nodes |
| `state.py` | `RemoteAggregator` — aggregates state across all nodes via HTTP calls; caches results; provides per-node and global views (get_all_servers, get_all_models, start_on_node, stop_on_node, get_logs_on_node) |

### `/llauncher/ui/` — Streamlit Dashboard

| File | Purpose |
|------|---------|
| `app.py` | Main entry point. Two tabs: **Dashboard** (models/status overview with live refresh) and **Nodes** (registry management). Loading screen during agent startup. Node selector in sidebar for multi-node filtering |

### `/tests/` — Test Suite

| Directory | Contents | Key Tests |
|-----------|----------|-----------|
| `conftest.py` | Fixtures: `mock_config_store`, `sample_model_config` |  |
| `unit/models/test_models.py` | Pydantic model validation | ModelConfig field constraints, path existence check |
| `unit/test_state.py` | LauncherState CRUD and start/stop | Start on free port, stop running server, eviction scenarios |
| `unit/mcp/` (6 files) | MCP tool dispatch, lazy singleton | `_dispatch_tool`, `get_mcp_state()`, read handler refresh calls, `validate_config` bypass, `swap_server` delegation |
| `unit/test_registry_extended.py` + `test_remote.py` | Node registry CRUD, RemoteNode HTTP client | Add/remove nodes, ping detection, error handling |
| `integration/test_swap.py` + `test_state.py` | Integration tests for swap behavior | Swap rollback on invalid model, roundtrip start/stop/start |

### `/docs/` — Planning & Documentation

| Path | Purpose |
|------|---------|
| `adrs/001-ts-extension-for-pi.md` | **Approved.** TS extension design to let Pi coding agents control llauncher nodes via native fetch() against agent REST API. Zero npm deps. Two-port awareness (agent port 8765 vs inference port). Tool surface table with node management. Known bug: `start-with-eviction` endpoint missing `port` query param in FastAPI signature. |
| `adrs/002-swap-eviction-consistency.md` | **Draft.** Five-phase eviction redesign for `state.start_with_eviction()`. Currently broken (UI and Agent API have no rollback). Proposed: single `EvictionResult` dataclass with 4 port_states (`unchanged/restored/serving/unavailable`). MCP uses `strict_rollback=True`, UI/Agent use `False`. Full decision tree + migration plan. |
| `PLAN-architectural-remediation.md` | **Comprehensive 5-phase plan.** Phase 0 (discovery), Phase 1 (MCP lazy singleton — already implemented per phase1-verification.md with partial bug found), Phase 2 (Agent HTTP redundancy elimination), Phase 3 (cleanup/dead code), Phase 4a-h (minor fixes: logs expander, orphaned log cleanup, remote node Pydantic validation, np exposure in MCP output), Phase 5 (ADR governance — ADR-003/004/005/006). Contains all GitHub issue mappings. |
| `plans/phase1-verification.md` | Verification report for Phase 1. Confirms lazy singleton pattern implemented with partial object caching bug discovered in `get_mcp_state()`. Full test plan with pass/fail status per section. 86 tests passing, 94% coverage on modified files. Critical gap: no end-to-end stale-data-elimination test exists. |
| `MCP.md` | MCP integration documentation |
| Architecture docs (1-3-) | Layer diagrams, cross-layer reach patterns, refresh-reconcile patterns |

### `/pi-footer-extension/` — Pi Extension Work

TypeScript extension stub for Pi coding agent to control llauncher nodes natively (no stdio transport). Defined in ADR-001.

---

## 3. Key Classes & Functions

### Entry Points

| Entrypoint | Command | Module |
|-----------|---------|--------|
| MCP Server | `llauncher-mcp` | `llauncher.mcp_server:main()` → stdio transport loop |
| Agent HTTP | `llauncher-agent [--stop]` | `llauncher.agent:main()` → uvicorn on :8765 |
| Streamlit UI | `streamlit run llauncher.ui.app` | `llauncher.ui.app.main()` |

### Central Data Model (`ModelConfig`)

```python
class ModelConfig(BaseModel):
    name: str                     # Required identifier
    model_path: str               # Path to .gguf file (validated at construction, skipped for persisted configs)
    mmproj_path: str | None       # Multimodal projector path
    default_port: int | None      # Preferred port; auto-allocated from 8080-8999 range if null
    n_gpu_layers: int = 255       # GPU layers to offload
    ctx_size: int = 131072        # Context window size
    np: int | None                # KV cache pages
    threads: int | None           # CPU threads (optional)
    flash_attn: Literal["on", "off", "auto"] = "on"
    extra_args: str = ""          # Free-form CLI args appended to command line
    
    _skip_path_validation: bool   # Internal flag for persisted config loading
    
    @classmethod  
    def from_dict_unvalidated(cls, data) → cls  # Migration-aware deserializer (port→default_port, list→str extra_args)
```

### StateManager (`LauncherState`) — The Core State Object

Key methods:
- `refresh()` — reloads models from disk + scans process table for running servers
- `can_start(config, caller, port)` — validates port not in use, not blacklisted, caller allowed, model path exists
- `start_server(model_name, caller, port)` — auto-allocates port if needed → starts process → records PID/port
- `stop_server(port, caller)` — terminates process via psutil (terminate→wait 5s→kill)
- `_start_with_eviction_impl()` — **5-phase swap**: pre-flight validation → stop old model → start new → readiness poll → rollback on failure. Returns `EvictionResult`. Uses `strict_rollback` parameter (True for MCP, False for UI/Agent).
- `record_action(...)` — appends to audit log with timestamp/action/model/caller/result/message

### Swap/Eviction Flow (`_start_with_eviction_impl`)

This is the most complex method. Phase-by-phase:

1. **Pre-flight** (no mutations): Model exists → path exists → not already running elsewhere → if strict, old config+path must exist
2. **Stop old**: `stop_server(port)` on occupied port; if fails, return unchanged
3. **Start new**: `process_start_server(config, port)`; on exception with `strict_rollback=True` and old config available → restart old process
4. **Readiness poll**: `wait_for_server_ready(port, timeout=120)`; on timeout → terminate new + rollback same logic as step 3
5. **Success**: refresh running servers

Return values via `EvictionResult.port_state`: `"unchanged"` | `"restored"` (rolled back) | `"serving"` | `"unavailable"` (both new and rollback failed).

### MCP Dispatch Pattern (`_dispatch_tool`)

```python
def _dispatch_tool(name, arguments):
    # Stateless bypass — no lazy init triggered
    if name == "validate_config":
        return await config_tools.validate_config(None, arguments)
    
    # Lazy singleton (creates + refreshes on first access)
    state = get_mcp_state()
    
    # Read tools call state.refresh() internally
    dispatch_map = {
        "list_models": models.list_models(state),      # reads, calls refresh
        "get_model_config": models.get_model_config(state),  # reads, calls refresh
        "start_server": servers.start_server(state),   # mutation, self-consistent
        "stop_server": servers.stop_server(state),     # mutation, self-consistent  
        "swap_server": servers.swap_server(state),     # uses _start_with_eviction_impl(strict=True)
        "server_status": servers.server_status(state), # reads, calls refresh
        "get_server_logs": servers.get_server_logs(state),  # reads, calls refresh
    }
```

### Remote Aggregation Pattern

`RemoteAggregator` holds a `NodeRegistry`. On each call (`get_all_servers`, `get_all_models`), it pings every registered node via HTTP and caches results. Offline nodes return cached data with `[OFFLINE]` marker in config_name or `_offline=True` flag on models. No cross-node transaction support.

### Configuration Persistence Flow

```
Disk: ~/.llauncher/config.json ←─── ConfigStore.save() ──► state.models
Disk: ~/.llauncher/nodes.json  ←─── NodeRegistry._save() ──► nodes dict
Logs: ~/.llauncher/logs/{name}-{port}.log
```

Both files use atomic write (write to `.tmp` then rename). ConfigStore also provides `add_model`, `update_model`, `remove_model`, `get_model`.

---

## 4. Implemented vs Planned

### ✅ FULLY IMPLEMENTED

| Feature | Location | Notes |
|---------|----------|-------|
| MCP server with stdio transport | `mcp_server/server.py` | 12 tools across models/servers/config |
| Lazy singleton state initialization | `mcp_server/server.py:get_mcp_state()` | With failure recovery try/except (as of current code) |
| Per-call refresh on read handlers | `tools/models.py`, `tools/servers.py` | All read tools call `state.refresh()` before reading |
| Agent HTTP API with FastAPI | `agent/routing.py` | /health, /status, /models, /start, /stop, /eviction, /logs |
| Pydantic data models with validation | `models/config.py` | 20+ fields on ModelConfig, path existence check at construction |
| Process management (psutil) | `core/process.py` | Start, stop, find by port, stream logs, wait_for_ready |
| Atomic config persistence | `core/config.py` | JSON via tmp+rename |
| Port auto-allocation with blacklist | `core/process.py:find_available_port()` | Scans DEFAULT_PORT (8080) → 8999 range, skips BLACKLISTED_PORTS |
| Build command from ModelConfig | `core/process.py:build_command()` | Maps all config fields to CLI arguments |
| Change rules (port blacklist, caller filter) | `models/config.py:ChangeRules` | Port blacklisting default {8080}, configurable via env |
| Audit logging | `state.py:record_action()` + `AuditEntry` | Timestamp/action/model/caller/result/message |
| Streamlit dashboard (Dashboard tab) | `ui/app.py` + `ui/tabs/dashboard.py` | Grid view, status indicators, start/stop buttons |
| Nodes management UI | `ui/tabs/nodes.py` | Add/remove/register nodes with test connection |
| Multi-node aggregation via HTTP | `remote/node.py`, `remote/state.py` | RemoteAggregator, NodeRegistry, ping-based health check |
| Auto-start local agent on UI launch | `registry.py:start_local_agent()` | Cross-platform process detachment (Windows/Unix) |
| Swap with rollback guarantee (MCP tool layer) | `mcp_server/tools/servers.py:swap_server` | Calls `_start_with_eviction_impl(strict_rollback=True)` |
| validate_config stateless bypass | `server.py:_dispatch_tool()` line 1 early return | Bypasses lazy init entirely |

### ⚠️ PARTIALLY IMPLEMENTED / NEEDS ATTENTION

| Issue | Status | Details |
|-------|--------|---------|
| UI/Agent API eviction rollback (ADR-002) | Draft plan, NOT implemented | ADR-002 proposes 5-phase with rollback. Current `start_with_eviction` in state.py appears to be already upgraded (code shows full implementation with `_start_with_eviction_impl`). Need to confirm whether the migration tasks from ADR-002's Phase II have been executed. The `EvictionResult` dataclass and `_compat` wrapper exist, but agent/routing.py and UI still may not use the new pattern fully. |
| Partial object caching bug in get_mcp_state() | Bug found during phase1 verification | Current code has try/except but verify it properly clears `_mcp_state = None`. See `phase1-verification.md` Section F. |
| No end-to-end stale-data test for Phase 1 | Gap confirmed by verification | Phase 1's core value proposition (zero staleness on reads) is untested at integration level |
| Post-mutation refresh redundancy in Agent HTTP | Planned → Phase 2 of remediation plan | POST /start does `refresh()` + mutation + another `refresh_running_servers()`. 3 scans for eviction. Plan says reduce to 1. |
| `_find_model_by_path` symlink mismatch | Known issue (Phase 4h) | Exact string comparison doesn't resolve symlinks or path normalization differences → shows "unknown" config_name |

### 🔲 PLANNED BUT NOT IMPLEMENTED

| Feature | Source | Notes |
|---------|--------|-------|
| ADR-003: State ownership and refresh discipline doc | PLAN Phase 5a | Formal governance document for the one-per-process + refresh-on-read pattern |
| ADR-004: Import layer boundaries enforcement | PLAN Phase 5b | MAY/MUST NOT import table (already documented in this summary) |
| ADR-005: Refresh/reconcile patterns doc | PLAN Phase 5c | Canonical refresh paths per operation type |
| ADR-006: MCP state initialization pattern | PLAN Phase 5d | Lazy-init as canonical for new processes |
| Pydantic validation on remote node config (Issue #27) | PLAN Phase 4e | New `RemoteNodeConfig` model before writing to nodes.json |
| Logs expander in dashboard running servers (Issue #16) | PLAN Phase 4a | Add log viewer UI component to dashboard |
| Orphaned log file cleanup (Issue #10) | PLAN Phase 4b | Document as known limitation + add cleanup button/script |
| Remote model management via dashboard (Issue #15) | Explicitly OUT OF SCOPE in plan | Feature request for full remote CRUD from UI; backlog item |
| Windows `run.bat` fix (Issue #14) | Requires Windows test env | Separate worker needed |
| TS extension for Pi (ADR-001) | Approved, not yet implemented | TypeScript extension at `~/.pi/agent/extensions/llauncher.ts` |

---

## 5. ADR Summary

### ADR-001: TypeScript Extension for Pi to Control llauncher Agents
**Status:** ✅ Approved  
**Problem:** No way for pi coding agents to control llauncher nodes programmatically — MCP requires Python stdio transport, adding overhead and complexity.
**Decision:** Build a single-file TS extension using native `fetch()` with zero npm dependencies. Maps directly to agent REST endpoints. No abstraction layer. Uses shared `nodes.json` registry from Python codebase. Two-port awareness (agent port 8765 vs inference ports).
**Tool Surface:** 12 tools — list_models, get_model_config, server_status, get_server_logs (reads); start_server, stop_server, swap_server (writes); add_node, remove_node (node management). All node parameter is explicit per-call. Parallel all-nodes queries supported.
**Known Bug:** Agent's `/start-with-eviction/{model_name}` FastAPI endpoint references `port` query param but doesn't declare it in function signature — will likely cause 422 validation errors. Fix: add `port: int | None = Query(None)` to routing.py.

### ADR-002: Unified Swap-with-Eviction Semantics
**Status:** Draft  
**Problem:** Three entry surfaces (UI, Agent API, MCP tool) implement swap/eviction differently. MCP tool has ~120 lines of rollback logic; UI and Agent API call a broken `start_with_eviction` that has NO rollback — if new model fails to start, old model is dead with no recovery.
**Decision:** Elevate `state.start_with_eviction()` to be the single source of truth with full 5-phase implementation (pre-flight → stop-old → start-new → readiness-poll → rollback). Return structured `EvictionResult` instead of `(bool, str)`. All three entry points delegate to one method.
**Key Design:** MCP uses `strict_rollback=True` (requires old config persisted); UI/Agent use `strict_rollback=False` (graceful degradation). `_compat` wrapper preserves tuple return for backward compatibility. Audit log enum expanded with `"rolled_back"` and `"unavailable"`.
**Scope:** Only touches start code path — running servers unaffected. No migration script needed.

---

## 🔧 IMPORTANT PATCH: Verification Corrections (Post-Write)

The following was discovered during verification after the initial summary was written:

### ADR-002 — Partially Implemented (Core Done, Downstream Incomplete)

**What IS implemented:**
- `EvictionResult` dataclass exists in `state.py:33` with all fields from ADR-002
- `_start_with_eviction_impl()` implements full 5-phase swap flow with rollback (lines ~280-470 of state.py)
- MCP tool (`mcp_server/tools/servers.py:swap_server`) delegates to `_start_with_eviction_impl(strict_rollback=True)` — thin wrapper, not inline duplicate
- Agent HTTP (`agent/routing.py`) imports `EvictionResult` and calls `_start_with_eviction_impl(strict_rollback=False)`

**What is NOT yet implemented (gap between draft ADR and code):**
1. **Audit log enum NOT updated**: `AuditEntry.result` in `models/config.py` still has old values `Literal["success", "error", "validation_error"]` — missing `"rolled_back"` and `"unavailable"` as required by Phase 3c of the remediation plan. This means audit entries for swap outcomes will lose structured rollback/unavailable information even though state computes it correctly.
2. **Agent API response structure**: The eviction handler in routing.py (line ~260+) calls `_start_with_eviction_impl` and returns a dict with `success`, `port_state`, `previous_model`, `new_model` fields, but doesn't currently include the full structured `EvictionResult` mapping that ADR-002 envisions for the JSON response body on both success AND error.
3. **UI dashboard uses `dashboard.py`, not `model_card.py`**: The main app imports tab1 from `dashboard.py` and tab2 from `nodes.py`. The file `ui/tabs/model_card.py` exists as a component but is NOT imported in the top-level tab navigation — it may be used internally by dashboard.py or may be residual/stub code.
4. **Manager/running tabs exist as files but are not active UI routes**: Neither `manager.py`, `forms.py`, nor `model_card.py` appear to be mounted as Streamlit tabs in app.py. The Dashboard tab appears self-contained within dashboard.py.

### Other Verified Corrections

| Claim in Summary | Verdict | Correction |
|-----------------|---------|------------|
| MCP swap_server uses `_start_with_eviction_impl(strict_rollback=True)` | ✅ Confirmed | Line 225 of servers.py — delegation is thin, ~30 lines not ~120 as described in ADR-002 context |
| Agent HTTP imports EvictionResult | ✅ Confirmed | routing.py line 8 |
| UI has separate Model Card tab | ❌ Correction needed | Dashboard.py handles all model display; model_card.py is a component not mounted as top-level tab |

### Revised "Implemented vs Planned" — ADR-002 Row

Replace the earlier entry with:

| Feature | Status | Details |
|---------|--------|---------|
| Swap/Eviction with rollback (ADR-002) | ⚠️ Core implemented, integration incomplete | `EvictionResult` + `_start_with_eviction_impl()` with full 5-phase in state.py ✅. MCP tool delegates with strict_rollback=True ✅. Agent HTTP uses it with strict_rollback=False ✅. BUT: AuditEntry.result enum NOT updated (still old Literal values) ⚠️. UI tab structure different than documented (dashboard.py instead of model_card.py). Phase 3c audit enrichment and Phase 5b/c ADR docs remain pending |

---
