# State Ownership — Who Owns What Truth, and How

## TL;DR

There is **one source of truth for configs** (disk) but **four independent instances** of `LauncherState`, each with its own in-memory copy. No synchronization mechanism exists between them. Each layer owns its own instance.

---

## Instance Inventory

```
┌──────────────────────────────┬─────────────────────────────────┬──────────────────────┐
│ Instance Location            │ Lifetime                        │ Created By           │
├──────────────────────────────┼─────────────────────────────────┼──────────────────────┤
│ agent/routing.py: _state     │ Process lifetime (module-level) │ first HTTP request   │
│ mcp_server/server.py: state  │ Process lifetime (module-level) │ module import        │
│ ui/app.py: session_state     │ Browser session lifetime        │ st.session_state     │
│ ui/tabs/model_card.py        │ One-shot (ad-hoc)               │ _handle_start()      │
└──────────────────────────────┴─────────────────────────────────┴──────────────────────┘
```

### Instance 1: Agent HTTP (`agent/routing.py`)

```python
_state: LauncherState | None = None

def get_state() -> LauncherState:
    global _state
    if _state is None:
        _state = LauncherState()           # bootstrap: refresh() called by __post_init__
        _state.refresh()
    return _state
```

**Owner:** The agent HTTP process (llauncher-agent).  
**Scope:** Single HTTP server instance on one node.  
**Truth source for:** Remote UI views of this node. Any `RemoteNode` client talks to this agent via HTTP and gets data from THIS state.  
**Refresh discipline:** Every HTTP handler calls `refresh()` or `refresh_running_servers()` before reading. Reads are always "fresh."

### Instance 2: MCP Server (`mcp_server/server.py`)

```python
state = LauncherState()     # Created at module import time
```

**Owner:** The MCP stdio server process (llauncher-mcp).  
**Scope:** Single stdio-bound MCP process. Consumed by AI tool-use systems (this pi agent, Claude, etc.).  
**Truth source for:** External AI agents invoking llaunch tools.  
**Refresh discipline:** **None for reads.** `list_models`, `get_model_config`, `server_status` all read `state.models` and `state.running` directly — never calling refresh. Only mutations (start, stop, swap) implicitly touch state via internal calls.  
**Critical gap:** If the MCP server process was started before the latest config change or process change, its state is stale until something calls refresh. Nothing in the read path does.

### Instance 3: UI Session State (`ui/app.py`)

```python
def get_state() -> LauncherState:
    if "state" not in st.session_state:
        st.session_state["state"] = LauncherState()    # bootstrap
    return st.session_state["state"]
```

**Owner:** One per browser session (Streamlit creates a new session state per user connection).  
**Scope:** Single Streamlit app instance, one user session.  
**Truth source for:** Local dashboard display.  
**Refresh discipline:** Dashboard tab calls `refresh()` before reading local servers. Remote data comes through the aggregator (which makes HTTP calls to the agent — those are inherently fresh per-request). "Refresh All" button triggers explicit full sync.

### Instance 4: Ad-Hoc Temp (`ui/tabs/model_card.py`)

```python
temp_state = LauncherState()    # fresh, never stored
temp_state.refresh()
if target_port in temp_state.running:
    show_eviction_dialog()
```

**Owner:** The calling function `_handle_start()`. Instantiated and discarded.  
**Scope:** One call to check port collision before start.  
**Truth source for:** Port availability check only. Immediately discarded after the check.  
**Problem:** This is a **read-only intent** (checking if a port is occupied) that requires a full process scan. It could use `is_port_in_use(port)` from core.process instead, which avoids creating a whole state instance and scanning every process on the system.

---

## What Each Instance Owns

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Shared (on disk)                                  │
│                                                                      │
│  ~/.llauncher/config.json     ← Single source of truth for configs  │
│  ~/.llauncher/nodes.json      ← Single source of truth for nodes    │
│  ~/.llauncher/logs/*.log      ← Shared log files                    │
│                                                                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ ConfigStore.load() reads from here
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
    ┌───────────┐        ┌───────────┐        ┌───────────┐
    │ Agent     │        │ MCP       │        │ UI        │
    │ _state    │        │ state     │        │ session   │
    │           │        │           │        │           │
    │ models:   │        │ models:   │        │ models:   │
    │ self.copy │        │ self.copy │        │ self.copy │
    │           │        │           │        │           │
    │ running:  │        │ running:  │        │ running:  │
    │ self.copy │        │ self.copy │        │ self.copy │
    │           │        │           │        │           │
    │ audit:    │        │ audit:    │        │ audit:    │
    │ own list  │        │ own list  │        │ own list  │
    │           │        │           │        │           │
    │ rules:    │        │ rules:    │        │ rules:    │
    │ empty     │        │ empty     │        │ empty     │
    └───────────┘        └───────────┘        └───────────┘

   ┌─────────────────────────────────────────────────────┐
   │ REMOTE LAYER (not LauncherState instances)           │
   │                                                      │
   │  NodeRegistry ── manages ~/.llauncher/nodes.json     │
   │  RemoteAggregator ── caches HTTP responses           │
   │  RemoteNode ── transient HTTP client                 │
   │                                                      │
   │  Owns: _nodes dict, _model_cache, _server_cache      │
   │  Does NOT own: any LauncherState instance            │
   └─────────────────────────────────────────────────────┘
```

---

## Synchronization Mechanisms (Existing)

### 1. Config changes via ConfigStore — DISK-DELAYED

When ConfigStore.add/update/remove is called, it writes to `~/.llauncher/config.json`. The next call to `ConfigStore.load()` (via `refresh()`) on ANY instance will see the new data. But there's no file watcher, so instances with cached configs won't see changes until they call refresh.

**Visible to:** All instances that call `refresh()` (agent HTTP always does; MCP never does for reads).

### 2. Process scanning — EVENT-DELAYED

When a process starts/stops via any instance's mutation methods, other instances won't know until they scan the process table. The agent HTTP endpoints do this on every read. The MCP server does not.

**Visible to:** Agent HTTP (every read). MCP server (never unless mutated). UI dashboard (on refresh before display).

### 3. No pub/sub, no callbacks, no file watchers, no shared memory

There is zero cross-instance communication. Four processes can have four completely different views of the world simultaneously.

---

## Remote Layer State (Separate from LauncherState)

```
┌──────────────────────────────────────────────────────┐
│ NodeRegistry (ui/app.py session state)                │
│   _nodes: dict[str, RemoteNode]                       │
│   owns: ~/.llauncher/nodes.json (read/write)          │
│                                                      │
│  ┌─────────────────────────────────────┐             │
│  │ RemoteNode (transient HTTP client)   │             │
│  │  name, host, port, status            │             │
│  │  ping() → GET /health               │             │
│  │  get_status() → GET /status          │             │
│  │  get_models() → GET /models           │             │
│  └─────────────────────────────────────┘             │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│ RemoteAggregator (ui/app.py session state)            │
│   _model_cache: dict[node, list[model]]               │
│   _server_cache: dict[node, list[RemoteServerInfo]]   │
│                                                      │
│  get_all_servers() → iterates registry, calls        │
│    node.get_status() on each, aggregates + caches     │
│                                                      │
│  get_all_models() → iterates registry, calls         │
│    node.get_models() on each, aggregates + caches     │
│                                                      │
│  get_summary() → refresh_all() + get_all_servers()   │
│    + get_all_models()                                 │
└──────────────────────────────────────────────────────┘
```

**Key insight:** RemoteAggregator and NodeRegistry are **per-session** in the UI. Each browser session has its own aggregator and registry. The `NodeRegistry` manages node connectivity metadata (not model/running state). The `RemoteAggregator` caches HTTP responses from agent endpoints — this is a separate cache from any LauncherState instance, living entirely in the remote layer.

When the UI calls `aggregator.get_all_servers()`, it's making fresh HTTP calls to every registered node's agent → which means those calls hit the Agent HTTP handler → which calls `refresh()` on that node's `_state` → so remote data is always fresh (from that node's perspective).

---

## Conflict Matrix: Which Instances See What, When

```
Event: User starts a model via MCP tool
────────────────────────────────────────
1. MCP state.start_server() → sets self.running[port] = RunningServer(...)
   → MCP sees it immediately ✓
   → Agent HTTP _state does NOT see it (different instance) ✗
   → UI session state does NOT see it (different instance) ✗

Event: User starts a model via Agent HTTP POST /start
──────────────────────────────────────────────────────
1. get_state().refresh() → reloads configs + process scan
2. .start_server() → sets self.running[port] = ...
3. .refresh_running_servers() → re-scans, confirms
   → Agent HTTP _state sees it immediately ✓
   → MCP state does NOT see it (different instance) ✗
   → UI session state does NOT see it (different instance) ✗

Event: User starts a model via UI "Start" button (local node)
─────────────────────────────────────────────────────────────
1. _handle_start() creates temp_state.refresh() for port check
2. state.start_server() → sets self.running[port] = ...
3. Dashboard later calls state.refresh() → picks up the change
   → UI session state sees it on next refresh ✓
   → MCP state does NOT see it (different instance) ✗
   → Agent HTTP _state does NOT see it (different instance) ✗

Event: User starts a model via UI "Start" button (remote node)
───────────────────────────────────────────────────────────────
1. aggregator.start_on_node() → HTTP POST to remote agent
2. Remote agent handles via its own _state.refresh() + .start_server()
3. Next UI refresh or aggregator.get_all_servers() call → HTTP GET to remote agent
   → Remote data shows up on next UI render ✓ (fresh HTTP)
   → MCP state does NOT see it (different instance, no HTTP) ✗
```

---

## Implications

1. **MCP read tools can return stale data indefinitely.** No refresh in the read path means `list_models` and `server_status` reflect whatever the last mutation or process startup saw.

2. **Four instances of truth exist simultaneously.** Any operation done via one channel (MCP, HTTP, UI) is invisible to the others until that other instance happens to refresh.

3. **Config changes are eventually consistent (via disk).** Anyone who calls refresh will see config changes. MCP never calls refresh on reads, so it misses config changes permanently until restarted.

4. **Process changes are only visible to instances that scan.** Agent HTTP always scans before reading → always fresh. MCP never scans before reading → potentially stale for hours.

5. **The temp_instance anti-pattern wastes resources.** Creating a full LauncherState + ConfigStore.load() + psutil scan just to check one port is expensive and unnecessary when `is_port_in_use()` already exists.
