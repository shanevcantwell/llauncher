# Cross-Layer Reach — Who Calls What, Where Boundaries Cross

## Legend

→  = calls a method on
⬜ = imports a class/function from
◆ = creates an instance of

---

## Dependency Map (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        ENDPOINT LAYER                                    │
│                                                                          │
│   ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐            │
│   │ AGENT HTTP    │  │   MCP SERVER  │  │    STREAMLIT UI   │           │
│   │ (agent/)      │  │  (mcp_server/)│  │     (ui/)         │           │
│   └──────┬────────┘  └───────┬───────┘  └────────┬───────────┘           │
│          │                   │                    │                       │
│          ▼                   ▼                    ▼                       │
│   ┌────────────────────────────────────────────────────────────────┐      │
│   │                    STATE ORCHESTRATION                          │      │
│   │                    (state.py / LauncherState)                  │      │
│   │                                                                │      │
│   │  .refresh()            ← agent, ui/dashboard                   │      │
│   │  .refresh_running_servers() ← agent endpoints (post-mutation)  │      │
│   │  .start_server()     ← agent POST /start, MCP start_server, UI model_card       │
│   │  .stop_server()      ← agent POST /stop, MCP stop_server, UI model_card         │
│   │  ._start_with_eviction_impl() ← agent POST /start-with-eviction, MCP swap_server│
│   │  .get_model_status() ← MCP list_models, MCP get_model_config                     │
│   │  .can_start()        ← UI model_card (start button validation)                   │
│   │  .can_stop()         ← agent POST /stop validation                               │
│   │  state.running       ← MCP server_status, UI dashboard                           │
│   │  state.models        ← MCP list_models, MCP get_model_config                     │
│   │  state.audit         ← (internal only, never read externally)                    │
│   │  state.rules         ← (internal only, used for validation)                      │
│   └──────────┬─────────────────────────────────────────────┘                       │
│              │                                                                      │
│              ▼                                                                      │
│   ┌────────────────────────────────────────────────────────────────┐              │
│   │                      CORE LAYER                                │              │
│   │                                                                │              │
│   │  ConfigStore.load/save()     ← state.py (refresh),             │              │
│   │                               UI forms.py (CRUD),              │              │
│   │                               MCP config tools                 │              │
│   │                                                                │              │
│   │  find_all/find_server_by_port() ← state.py (refresh_running)  │              │
│   │  start_server(config, port)    ← state.py (start/eviction)    │              │
│   │  stop_server_by_port/pid()     ← state.py (stop/eviction)     │              │
│   │  build_command()               ← process.start_server()        │              │
│   │  wait_for_server_ready()       ← state.py (eviction phase 4)  │              │
│   │  stream_logs(pid)              ← MCP tools/servers,           │              │
│   │                                  UI model_card                 │              │
│   │  is_port_in_use()              ← state.py (can_start)          │              │
│   └───────────────────────────────────────────────────────────────┘              │
│                              ▲                                                    │
│                              │ uses                                               │
│   ┌──────────────────────────┼────────────────────────────────────────┐           │
│   │  core/config.py    core/settings.py       models/config.py        │           │
│   │  ConfigStore       constants (env vars)     Pydantic data models  │           │
│   └───────────────────────────────────────────────────────────────────┘           │
│                                                                                    │
│   ┌──────────────────────────────────────────────────────────────────────────┐    │
│   │                     REMOTE LAYER (multi-node)                             │    │
│   │                                                                            │    │
│   │  NodeRegistry ──▶ RemoteNode ──▶ RemoteAggregator                          │    │
│   │     ▲       ↑            ↑              ↑                                  │    │
│   │     │       │            │              │ HTTP calls to agent endpoints    │    │
│   │     │       │            │              │                                  │    │
│   │  UI app.py──┘     (remote/registry)   (remote/state)                       │    │
│   │  Registry.refresh_all() is called from:                                    │    │
│   │    - UI sidebar "Refresh All" button                                       │    │
│   │    - RemoteAggregator.get_summary()                                        │    │
│   └────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                    │
│   ┌──────────────────────────────────────────────────────────────────────────┐    │
│   │                    AGENT HTTP ENDPOINTS (remote layer's target)           │    │
│   │                                                                            │    │
│   │  GET  /health        — health check                                        │    │
│   │  GET  /node-info     — node metadata                                       │    │
│   │  GET  /status        — running servers (calls refresh_running_servers)     │    │
│   │  GET  /models        — all models (calls refresh)                          │    │
│   │  POST /start/{name}  — start model (calls refresh + start_server)         │    │
│   │  POST /stop/{port}   — stop server (calls refresh + stop_server)          │    │
│   │  POST /start-with-eviction/name — swap (calls refresh + eviction impl)    │    │
│   │  GET  /logs/{port}   — server logs (calls refresh)                         │    │
│   └────────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Import Reach Matrix

Every `from llauncher.X import Y` that crosses a module boundary:

| Importer | Imports From | Classes/Functions | Purpose |
|---|---|---|---|
| `agent/routing.py` | `state` | `LauncherState`, `EvictionResult` | Global `_state` singleton, mutation & status |
| `mcp_server/server.py` | `state` | `LauncherState` | Module-level `state = LauncherState()` |
| `mcp_server/tools/models.py` | `state` | `LauncherState` | Read models/status from state |
| `mcp_server/tools/servers.py` | `state` | `LauncherState` | Start/stop/swap/status/log |
| `mcp_server/tools/servers.py` | `core.process` | `stream_logs` | Log fetching for logs tool |
| `mcp_server/tools/config.py` | `state` | `LauncherState` | Config CRUD via state |
| `ui/app.py` | `state` | `LauncherState` | Session-state singleton |
| `ui/app.py` | `remote.registry` | `NodeRegistry` | Session-state registry |
| `ui/app.py` | `remote.state` | `RemoteAggregator` | Session-state aggregator |
| `ui/tabs/dashboard.py` | `state` | `LauncherState` | Calls `.refresh()`, reads `.running`, `.models` |
| `ui/tabs/dashboard.py` | `remote.registry` | `NodeRegistry` | Node filter logic |
| `ui/tabs/dashboard.py` | `remote.state` | `RemoteAggregator` | Multi-node server/model retrieval |
| `ui/tabs/dashboard.py` | `remote.node` | `RemoteServerInfo` | Constructs local→RemoteServer wrapper |
| `ui/tabs/model_card.py` | `state` | `LauncherState` | Start/stop/can_start + **creates temp instance** |
| `ui/tabs/model_card.py` | `core.process` | `stream_logs` | Local log streaming |
| `ui/tabs/model_card.py` | `remote.state` | `RemoteAggregator` | Remote start/stop/log delegation |
| `ui/tabs/model_card.py` | `remote.node` | `RemoteServerInfo` | Server info type hint |
| `ui/tabs/forms.py` | `state` | `LauncherState` | Config persistence via state/models |
| `ui/tabs/forms.py` | `core.config` | `ConfigStore` | add_model/update_model |
| `ui/tabs/nodes.py` | `remote.registry` | `NodeRegistry` | List/manage registered nodes |
| `ui/tabs/nodes.py` | `remote.node` | `NodeStatus` | Online/offline display |
| `ui/tabs/manager.py` | `state` | `LauncherState` | (imports only — usage not examined) |
| `ui/tabs/running.py` | `state` | `LauncherState` | Deprecated, reads state for info display |
| `core/config.py` | `models.config` | `ModelConfig` | Dict→ModelConfig conversion |
| `core/process.py` | `models.config` | `ModelConfig` | build_command needs config fields |
| `core/process.py` | `core.settings` | Settings constants | Binary path, default port, blacklists |
| `remote/node.py` | `models.config` | `ModelConfig` | Type import only (not used at runtime) |
| `remote/state.py` | `remote.registry` | `NodeRegistry` | Node iteration |
| `remote/state.py` | `remote.node` | `RemoteNode`, `RemoteServerInfo` | Server info construction |
| `remote/state.py` | `models.config` | `ModelConfig` | Type import only (not used at runtime) |
| `state.py` | `core.config` | `ConfigStore` | load() in refresh() |
| `state.py` | `core.process` | Process functions | find_all, start, stop, wait_for_ready, stream_logs |
| `state.py` | `models.config` | `ModelConfig`, `RunningServer`, etc. | All data model types |

---

## Boundary Crossings (Detailed)

### A. Endpoints → LauncherState (THE MAIN BRIDGE)

Four separate LauncherState instances exist in memory simultaneously:

```
Agent HTTP routes  ──▶  state = LauncherState()       (module-level global in routing.py)
MCP server         ──▶  state = LauncherState()       (module-level global in mcp_server/server.py)
Streamlit UI       ──▶  state = LauncherState()       (per-session in st.session_state["state"])
UI model_card      ──▶  temp_state = LauncherState()  (ad-hoc, one-time use for port checks)
```

**Crossing points:**

| Source | Target State Instance | Calls Made | Why |
|---|---|---|---|
| `agent/routing.py` (HTTP GET /models) | Agent's `_state` | `.refresh()` + reads `.models`, iterates `.running` | Full state reload for model list |
| `agent/routing.py` (HTTP GET /status) | Agent's `_state` | `.refresh_running_servers()` only | Process scan, skip config disk I/O |
| `agent/routing.py` (POST /start/{name}) | Agent's `_state` | `.refresh()` + `.start_server()` | Validate against fresh state then mutate |
| `agent/routing.py` (POST /stop/{port}) | Agent's `_state` | `.refresh()` + `.stop_server()` | Validate then mutate |
| `agent/routing.py` (POST /eviction) | Agent's `_state` | `.refresh()` + `.start_with_eviction()` | Full preflight then mutate |
| `mcp_server/tools/models.py` (`list_models`) | MCP's `state` | reads `.models`, calls `.get_model_status()` | **No refresh** — reads stale memory |
| `mcp_server/tools/servers.py` (`server_status`) | MCP's `state` | reads `.running.items()` | **No refresh** — reads stale memory |
| `mcp_server/tools/servers.py` (`swap_server`) | MCP's `state` | calls `_start_with_eviction_impl()` | Mutation, trusts existing state |
| `ui/app.py` (startup) | UI session `state` | creates new instance | Bootstrap per-browser-session |
| `ui/tabs/dashboard.py` (`get_servers_to_display`) | UI session `state` | `.refresh()` + reads `.running` | Full reload before display |
| `ui/tabs/model_card.py` (`_handle_start`) | **temp_instance** | creates new → `.refresh()` | Port collision check only, throwaway |
| `ui/tabs/model_card.py` (`_handle_stop`) | UI session `state` | `.stop_server()` | Mutation on current session state |

### B. Remote Layer → Agent HTTP (outbound calls)

```
RemoteNode.start_server(model_name)  ─HTTP POST─▶  agent POST /start/{name}
RemoteNode.stop_server(port)         ─HTTP GET───▶  agent POST /stop/{port}
RemoteNode.get_status()              ─HTTP GET───▶  agent GET  /status
RemoteNode.get_models()              ─HTTP GET───▶  agent GET  /models
RemoteNode.get_logs(port)            ─HTTP GET───▶  agent GET  /logs/{port}
```

Remote layer has **no** direct access to LauncherState on the target node. It talks HTTP only.

### C. RemoteAggregator → Node (local coordination)

```
RemoteAggregator.get_all_servers() ──▶ iterates registry, calls each node.get_status(),
                                         aggregates results, caches in self._server_cache
RemoteAggregator.get_all_models()  ──▶ iterates registry, calls each node.get_models(),
                                         aggregates, caches in self._model_cache
RemoteAggregator.start_on_node()   ──▶ delegates to node.start_server()
RemoteAggregator.stop_on_node()    ──▶ delegates to node.stop_server()
```

### D. UI → Remote (inbound calls)

```
UI dashboard        ──▶ aggregator.get_all_servers()  — display all remote servers
UI dashboard        ──▶ aggregator.get_all_models()   — display all remote models
UI model_card       ──▶ aggregator.stop_on_node()     — stop button on remote card
UI model_card       ──▶ aggregator.start_on_node()    — start button on remote card
UI model_card       ──▶ aggregator.get_logs_on_node() — log viewer for remote
UI app sidebar      ──▶ registry.refresh_all()        — "Refresh All" button
```

---

## Key Observation: No Inter-State Synchronization

The four LauncherState instances are completely independent:
- **Config changes** made via one instance's ConfigStore operations are visible to others only after they call `refresh()` (which reloads from disk).
- **Process state** is never shared — each instance must re-scan the OS process table.
- There is no pub/sub, file watcher, or signal mechanism between them.
- The temp_instance in model_card.py exists purely for a read-check (port availability) and is immediately discarded.
