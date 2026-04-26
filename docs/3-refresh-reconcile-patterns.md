# Refresh & Reconcile Patterns — Every Probe, Sync, and Resync Path

## Summary Table

| Caller | Method Called | What It Does | When | Side Effects |
|---|---|---|---|---|
| `LauncherState.__post_init__` | `refresh()` | Full reload: configs + process scan | Instance creation | Loads disk, scans OS |
| `LauncherState.refresh()` | `.load()` + `.refresh_running_servers()` | Two-step reload: configs from disk, processes from table | Public API | Reads config.json, calls psutil |
| `LauncherState.refresh_running_servers()` | `find_all_llama_servers()` + `_find_model_by_path()` | Process scan only, replaces `self.running` dict entirely | Post-mutation reconcile | Calls psutil for every process |
| `NodeRegistry.refresh_all()` | `node.ping()` on each node | HTTP health check only | Node health polling | HTTP GET /health per node |
| `RemoteAggregator.get_summary()` | `registry.refresh_all()` then `get_all_servers()` + `get_all_models()` | Full aggregation with fresh connectivity data | Summary view rendering | HTTP to every node, caches results |

---

## Call Site Deep-Dive

### 1. `state.py:80` — `LauncherState.refresh()`
**Called by:**
- `__post_init__` (bootstrap)
- `agent/routing.py` GET /models (line 115)
- `agent/routing.py` POST /start/{name} (line 158)
- `agent/routing.py` POST /stop/{port} (line 212)
- `agent/routing.py` POST /start-with-eviction/name (line 253)
- `agent/routing.py` GET /logs/{port} (line 308)
- `ui/app.py` sidebar "Refresh All" button (line 158)
- `ui/tabs/dashboard.py` background render (lines 37, 56, 71)
- `ui/tabs/model_card.py` edit form (line 293)

**Why:** Full state reload. Both config disk and process table must be current. Called on every agent HTTP handler as a defensive prelude to any operation (read or write).

**What it loads:**
```python
self.models = ConfigStore.load()          # Disk I/O: ~/.llauncher/config.json → JSON → dict[str, ModelConfig]
self.refresh_running_servers()            # Process scan: psutil → cmdline parsing → config name resolution
```

### 2. `state.py:88` — `LauncherState.refresh_running_servers()`
**Called by:**
- `refresh()` (inside it — dual call is redundant on post-mutation agent paths)
- `agent/routing.py` GET /status (line 85) — **only call, never loads configs**
- `agent/routing.py` POST /start/{name} (line 181) — after start_server, reconcile
- `agent/routing.py` POST /stop/{port} (line 227) — after stop_server, reconcile
- `state.py:_start_with_eviction_impl()` phase 5 (line 556) — on success
- `agent/routing.py` POST /start-with-eviction/name (line 277) — redundant (refresh already called at line 253)

**Why:** Lightweight process reconciliation without config reload. Used when:
- Only running-server info is needed (GET /status)
- After a mutation to verify the OS matches our in-memory changes
- On success path of eviction flow (reconcile after start)

**What it does:**
```python
current_running = {}
for proc in find_all_llama_servers():       # psutil process_iter, filter "llama-server"
    cmdline = proc.cmdline()                # ["llama-server", "-m", "/path/to/model.gguf", "--port", "8081", ...]
    port = extract(cmdline, "--port")       # parse flag
    model_path = extract(cmdline, "-m")     # parse flag
    config_name = _find_model_by_path(model_path)  # exact string match against all self.models paths
    current_running[port] = RunningServer(pid=proc.pid, port=port, config_name=config_name or "unknown", start_time=datetime.now())
self.running = current_running               # REPLACES entire dict — no merge, no diff
```

**Critical: No merge strategy.** The method completely replaces `self.running` with a fresh snapshot. This means any state set by mutation methods (start_server adding to running) is overwritten by the re-scan, which could read a different PID or config_name if processes changed between mutation and scan.

### 3. `state.py:179` — `_find_model_by_path(model_path)`
**Called by:** `refresh_running_servers()` only (inside process loop)

**What it does:** Exact string match between process cmdline `-m` argument and every ModelConfig's stored `model_path`. Returns first match or None.

```python
for name, config in self.models.items():
    if config.model_path == model_path:     # exact string comparison
        return name
return None
```

**Bug surface:** If two configs have the same path (hardlink, symlink resolution difference, or actual duplicate), only one name is returned. If a process was started with a relative path but config stores absolute path (or vice versa), no match occurs → config_name = "unknown".

### 4. `state.py:556` — `refresh_running_servers()` in eviction success path
**Called by:** `_start_with_eviction_impl()` phase 5 only (success case)

**Why:** The mutation path (`start_server`) optimistically sets `self.running[port]` with the PID from `Popen.pid` and a direct `config_name=model_name`. But the actual process may have forked, exec'd, or the OS may report it differently. Phase 5 reconciles: scan OS to verify what's actually running now.

### 5. `agent/routing.py:85` — `refresh_running_servers()` in /status
**Called by:** GET /status agent HTTP endpoint only.

**Why optimization:** Status polling is the most frequent read operation (likely every few seconds from UI or health monitors). Skipping config disk I/O saves ~10-50ms per call on systems with many models configured.

### 6. `remote/registry.py:122` — `NodeRegistry.refresh_all()`
**Called by:**
- `ui/app.py` sidebar "Refresh All" (alongside local `state.refresh()`)
- `remote/state.py:get_summary()` before building summary view
- `RemoteAggregator.refresh_all_nodes()` public wrapper

**What it does:** HTTP GET /health on every registered node, updates NodeStatus enum.

```python
for name, node in self._nodes.items():
    node.ping()                             # GET /health → ONLINE/OFFLINE/ERROR
    results[name] = node.status
```

### 7. `remote/state.py:194` — `RemoteAggregator.get_summary()`
**Called by:** Implicitly from nodes tab UI (creates aggregator, calls summary)

**What it does:** Calls `registry.refresh_all()`, then `get_all_servers()`, `get_all_models()`. Aggregates counts.

---

## The Redundancy Problem

### Path A: Agent POST /start → post-mutation reconcile

```
handler POST /start/{name}:
    state.refresh()           ← full reload (configs + processes) [line 158]
    ... mutate state via start_server() ...
    state.refresh_running_servers()   ← process scan only [line 181]
    ... return result ...
```

**Problem:** `refresh()` at line 158 already calls `refresh_running_servers()`. Then after mutation, another call to `refresh_running_servers()` repeats the same OS scan. The second call is necessary (mutation changed running), but the first call's process scan is wasted — any changes between that scan and the mutation won't be reflected in the scan result, and the second scan overwrites it anyway.

**Net effect:** Two process scans per start. One is wasteful.

### Path B: Agent POST /start-with-eviction → post-mutation reconcile

```
handler POST /start-with-eviction:
    state.refresh()                    ← full reload (configs + processes) [line 253]
    ... mutate via _start_with_eviction_impl()...
        Inside eviction:
            phase 2: stop old server
            phase 3: start new → self.running[port] = ...    (optimistic write)
            phase 5: refresh_running_servers()   ← process scan [line 556]
    state.refresh_running_servers()    ← redundant! [line 277]
    ... return result ...
```

**Problem:** Three process scans. The eviction's own phase 5 scan at line 556 is correct and necessary (verify what actually started). But the handler's call at line 277 after returning is completely redundant — the method already did the reconcile before returning.

**Net effect:** Three process scans for one swap operation. Two are wasteful.

### Path C: GET /status → optimized, no config reload

```
handler GET /status:
    state.refresh_running_servers()    ← only this [line 85]
    ... return running_servers list ...
```

**OK.** No redundancy here — just one process scan, no config reload. This is the correctly optimized path.

---

## Staleness Windows

### Where refresh DOES NOT happen (read-only paths):

| Endpoint | Refresh? | What It Sees |
|---|---|---|
| MCP `list_models` | ❌ None | State at last mutation or creation time. May be stale indefinitely. |
| MCP `get_model_config` | ❌ None | Same — model configs and running status frozen until refresh. |
| MCP `server_status` | ❌ None | `state.running` read directly. Stale if processes changed externally. |
| MCP `start_server` | ✅ (via `state.start_server()`) | Calls start which internally sets optimistic state. No pre-refresh. |
| Agent `GET /models` | ✅ (`refresh()`) | Always current — calls refresh before reading. |
| Agent `GET /status` | ✅ (`refresh_running_servers()`) | Always current — scans process table. |

### The MCP Staleness Problem

The MCP server maintains its own `state = LauncherState()` at module level. No HTTP agent is involved. Every MCP tool call sees whatever that singleton instance holds. The only way to get fresh data is if something calls refresh on it, and **no MCP read tool calls refresh**.

Meanwhile the UI (which is likely the most common consumer of llauncher tooling via MCP) calls `state.refresh()` before reading local state in dashboard.py but relies on stale MCP state for model lists and status.

### The Temp Instance Pattern

UI model_card creates `temp_state = LauncherState()` just to check port availability:
```python
temp_state = LauncherState()        # Creates fresh instance
temp_state.refresh()                # Reloads from disk + scans processes
if target_port in temp_state.running:
    show_eviction_dialog()          # Port occupied
else:
    proceed_to_start()              # Safe to start
```

This is a read-only check but requires a full refresh to get current process state. It works, but it creates a brand-new LauncherState with its own ConfigStore.load() call just for a port collision check. A lightweight `is_port_in_use(port)` or `self.running.get(port)` would be cheaper, but this is the only place it matters.
