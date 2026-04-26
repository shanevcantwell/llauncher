# Layer Architecture — llauncher

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    ENDPOINT LAYER                            │
│  HTTP Agent (FastAPI :8765)  │  MCP Server (stdio)    │  UI  │
│  "remote management API"     │  "tool-calling API"    │ "gui" │
└──────┬───────────────────────┴──────────┬───────────────┴────┘
       │                                 │
       ▼                                 ▼
┌─────────────────────────────────────────────────────────────┐
│                    STATE ORCHESTRATION                       │
│                   LauncherState (state.py)                   │
│                                                              │
│  • Model configs  • Running servers  • Audit log            │
│  • Change rules   • Start/stop       • Eviction (swap)      │
└──────┬───────────────────────────────────────────────────────┘
       │    ▲        ▲          ▲         ▲       ▲
       │    │        │          │         │       │
       ▼    │        │          │         │       │
┌──────────┐│  ┌────┴────┐ ┌───┴───┐  ┌──┴────┐  │
│   CORE   ││  │ Config  │ │ Proc  │  │ Models│  │
│ Layer    ││  │Store    │ │Mgr    │  │/Types │  │
└──────────┘│  └─────────┘ └───────┘  └───────┘  │
            │                                    │
            ▼                                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    REMOTE LAYER                              │
│              RemoteAggregator + NodeRegistry                 │
│                                                              │
│  • Multi-node discovery       • Per-node HTTP clients       │
│  • Local model caching        • Node online/offline status  │
│  • Remote start/stop/logs     • nodes.json persistence      │
└─────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Endpoint Layer (External Interfaces)

Three independent entry points, each with its own state instance. No cross-talk between them.

### 1a. Agent HTTP Server (`agent/`) — REST API

| File | Responsibility |
|---|---|
| `server.py` | FastAPI app factory, CLI entry point (`llauncher-agent`), start/stop CLI commands. Binds HTTP on configurable port (default 8765). |
| `routing.py` | Route definitions. Each GET/POST handler calls the **global `_state`** instance for all state reads and mutations. |
| `config.py` | `AgentConfig` — env vars for host, port, node name. Used to start the agent HTTP server. |

**Surface:** `GET /health`, `/status`, `/models`, `/node-info`, `/logs/{port}` · `POST /start/{name}`, `/stop/{port}`, `/start-with-eviction/{name}`, `/swap`

### 1b. MCP Server (`mcp_server/`) — Tool-Calling API

| File | Responsibility |
|---|---|
| `server.py` | MCP stdio server. Module-level `state = LauncherState()` — its own singleton instance. Routes calls to tool implementations. |
| `tools/models.py` | `list_models`, `get_model_config` — read-only tools. Call `state.models` and `state.get_model_status()` directly. **No refresh.** |
| `tools/servers.py` | `start_server`, `stop_server`, `swap_server`, `server_status`, `get_server_logs`. Calls corresponding `state.*` methods. `swap_server` calls `_start_with_eviction_impl()`. `server_status` reads `state.running` directly. **No refresh.** |
| `tools/config.py` | `update_model_config`, `validate_config`, `add_model`, `remove_model` — config CRUD. |

**Surface:** 8 MCP tools (4 models + 4 servers). Stdio-based, no HTTP.

### 1c. Streamlit UI (`ui/`) — Graphical Dashboard

| File | Responsibility |
|---|---|
| `app.py` | Main app entry. Session-state instances: `state`, `registry`, `aggregator`. Startup screen checks agent readiness. Sidebar has "Refresh All" button and node selector. |
| `tabs/dashboard.py` | Primary view. Renders models per-node with status. Calls `state.refresh()` before reading local servers. Selects which nodes to display based on dropdown. |
| `tabs/model_card.py` | Per-model card with start/stop toggle buttons, detail expander, log viewer, edit button. Calls `state.start_server()`, `state.stop_server()`, `state.can_start()`, and remote `aggregator.*`. **Creates its own temporary `LauncherState()` for port collision checks.** |
| `tabs/forms.py` | "Add Model" and "Edit Model" forms — calls `ConfigStore.add_model()`, `ConfigStore.update_model()`. |
| `tabs/manager.py` | (content not examined) |
| `tabs/nodes.py` | Node management tab — creates `RemoteAggregator(registry)` to show server counts, online status. |
| `tabs/running.py` | Deprecated → redirects to Dashboard. |
| `utils.py` | UI helper: `format_uptime()` for display formatting. |

---

## Layer 2: State Orchestration (Core Engine)

### `state.py` — `LauncherState`

The single most important class. Manages two views of truth:

```
self.models  ← dict[str, ModelConfig]    (loaded from disk)
self.running ← dict[int, RunningServer]  (derived from OS process table)
self.audit   ← list[AuditEntry]          (action log)
self.rules   ← ChangeRules               (validation rules)
```

| Method | Purpose |
|---|---|
| `refresh()` | Full reload: `ConfigStore.load()` + `refresh_running_servers()`. Gets both configs and process state. |
| `refresh_running_servers()` | Process table scan: `find_all_llama_servers()` → parse cmdline for port & `-m` path → `_find_model_by_path()` to map path → config name → populate `self.running`. **Replaces the entire dict each time.** |
| `_find_model_by_path(path)` | Exact string match between process `-m` arg and every config's `model_path`. Returns first match. |
| `start_server(name)` | Resolve port → validate with `can_start()` → `process_start_server()` → optimistic insert into `self.running` + audit. |
| `stop_server(port)` | Validate `can_stop()` → `process_stop_server(port)` → remove from `self.running` + audit. |
| `_start_with_eviction_impl(name, port)` | 5-phase swap: preflight (no state change) → stop-old → start-new (with rollback on failure) → readiness poll (with rollback on timeout) → success with `refresh_running_servers()`. |
| `get_model_status(name)` | Read-only: scans `self.running` for matching config_name. Returns running/stopped + port/PID. |
| `can_start()` / `can_stop()` | Validation helpers checking port conflicts, blacklists, caller permissions. |

---

## Layer 3: Core Infrastructure

### `core/process.py` — Process Management

| Function | Purpose |
|---|---|
| `find_all_llama_servers()` | `psutil.process_iter()` filtering on `"llama-server"` in cmdline/name. Returns live `psutil.Process` objects. |
| `find_server_by_port(port)` | Same scan but stops at first matching port. |
| `start_server(config, port)` | Builds command via `build_command()`, spawns `subprocess.Popen`, writes to log file named `{sanitized_name}-{port}.log`. |
| `stop_server_by_port/port()` | Finds process then calls `stop_server_by_pid()` → terminates children + main. |
| `build_command(config, port)` | Constructs cmdline from config fields. Port is a runtime parameter (not stored in config). |
| `wait_for_server_ready(port, timeout)` | Polls TCP port + checks logs for "listening"/"ready". Returns `(bool, log_lines)`. |
| `is_port_in_use(port)` | Scans all processes for port binding. |
| `stream_logs(pid)` | Finds matching `{name}-{port}.log` and tails it. |

### `core/config.py` — ConfigStore

| Method | Purpose |
|---|---|
| `load()` | Reads `~/.llauncher/config.json`, parses JSON, creates `ModelConfig` instances (unvalidated paths). |
| `save(models)` | Atomic write: writes to `.tmp` then renames. |
| `add_model()`, `update_model()`, `remove_model()` | CRUD wrappers that load → mutate → save. |

### `core/settings.py` — Global Config

Environment-variable-backed constants: `LLAMA_SERVER_PATH`, `DEFAULT_PORT`, `BLACKLISTED_PORTS`, `LOG_LEVEL`. No class, just module-level variables.

### `models/config.py` — Data Models (Pydantic)

| Class | Purpose |
|---|---|
| `ModelConfig` | All model parameters: paths, ports, GPU layers, context, sampling params, etc. Has `from_dict_unvalidated()` for loading persisted configs without path checks. |
| `RunningServer` | Runtime representation: pid, port, config_name, start_time, uptime(). |
| `AuditEntry` | Log of every action with timestamp, actor, result, message. |
| `ChangeRules` | Whitelist/blacklist rules for models, ports, callers. |

---

## Layer 4: Remote Layer (Multi-Node)

### `remote/registry.py` — NodeRegistry

Persistent store of registered nodes (`~/.llauncher/nodes.json`). CRUD for adding/removing nodes. Each node becomes a `RemoteNode` instance with health tracking.

| Method | Purpose |
|---|---|
| `refresh_all()` | Calls `node.ping()` on each registered node → updates `NodeStatus` (ONLINE/OFFLINE/ERROR). |
| `is_local_agent_ready()` | Checks for local agent HTTP process on port 8765, auto-adds if found. |
| `start_local_agent()` | Spawns `llauncher-agent` as detached background process. |

### `remote/node.py` — RemoteNode

HTTP client wrapper over a single remote agent. Each method maps 1:1 to an agent endpoint.

| Method | Agent Endpoint | Purpose |
|---|---|---|
| `ping()` | `GET /health` | Health check, updates status + last_seen |
| `get_status()` | `GET /status` | Returns running server list from remote node |
| `get_models()` | `GET /models` | Returns model list from remote node |
| `start_server(name)` | `POST /start/{name}` | Start a model on remote node |
| `stop_server(port)` | `POST /stop/{port}` | Stop a model on remote node |
| `get_logs(port, lines)` | `GET /logs/{port}` | Get logs from remote node |

### `remote/state.py` — RemoteAggregator

Coordinates all nodes. Caches results from HTTP calls with offline fallback.

| Method | Purpose |
|---|---|
| `get_all_servers()` | Concatenates running servers from all reachable nodes (caching with offline indicators). |
| `get_all_models()` | Calls `node.get_models()` on each node, returns dict[node_name → models]. |
| `get_models_by_name()` | Cross-node grouping: maps model_name → [(node, model_data), ...]. |
| `start_on_node()` / `stop_on_node()` | Delegates to `node.start_server()` / `node.stop_server()`. |
| `get_logs_on_node()` | Delegates to `node.get_logs()`. |
| `refresh_all_nodes()` | Delegates to `registry.refresh_all()`, formats as string values. |
| `get_summary()` | Aggregates counts, node info, server list into a single dict. |
