# ADR-001: TypeScript Extension for Pi to Control llauncher Agents

**Status:** Approved  
**Date:** 2026-04-24  
**Approved by:** Code owner review + subagent planner validation  

## Context

llauncher exposes a per-node FastAPI agent REST API. The only way to interact with these agents programmatically from **pi** (the coding agent environment) was via the MCP server, which wraps a local `LauncherState` and requires a Python stdio process per session. Pi has its own extension system that can register custom tools directly — no Python, no stdio, no transport overhead.

We need a way for pi users and LLM agents to control llauncher nodes from within pi sessions.

## Decision

Create a TypeScript extension for pi at `~/.pi/agent/extensions/llauncher.ts` that exposes the llauncher HTTP agent REST API as typed pi tools using native `fetch()`. Single file, zero npm dependencies.

## Architecture

```
┌──────────────────────────────────────────────┐
│              Pi Session                       │
│                                              │
│  LLM Agent ──► Tool Call ──► llauncher.ts    │
│         ◄── Result ───────── fetch() ── HTTP │
└──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│         llauncher Agent Node                 │
│  FastAPI service (port 8765)                 │
│                                              │
│  /health  /status  /models                   │
│  /start/{model}  /stop/{port}                │
│  /start-with-eviction/{model}?port={port}    │
│  /logs/{port}                                │
└──────────────────────────────────────────────┘
```

Each llauncher agent is peer-to-peer — no central head service. The Streamlit UI is purely human-facing. Nodes are defined in `~/.llauncher/nodes.json` and shared between the Python codebase and the TS extension.

### Key Principles

1. **No abstraction over REST.** Tools map directly to agent endpoints.
2. **No process spawning.** Native `fetch()` only — no `subprocess`, no stdio, no Python.
3. **Node is routing.** The `node` parameter determines which agent to call; it is not passed to the REST endpoint itself.
4. **Two-port awareness.** Distinguish agent port (8765, HTTP) from inference port (e.g., 8081, llama-server).

## Tool Surface

### Read Operations

| Tool | Parameters | Endpoint | Notes |
|------|-----------|----------|-------|
| `llaunch_list_models` | `node?` | `GET /models` | No node = all nodes in parallel |
| `llaunch_get_model_config` | `name`, **`node`** | `GET /models` (filter) | `node` required — configs are per-node |
| `llaunch_server_status` | `node?` | `GET /status` | No node = all nodes in parallel |
| `llaunch_get_server_logs` | `port`, **`node`**, `lines?` | `GET /logs/{port}` | Port = inference port, defaults to 100 lines |

### Write Operations

| Tool | Parameters | Endpoint | Notes |
|------|-----------|----------|-------|
| `llaunch_start_server` | `model_name`, **`node`**, `wait?` | `POST /start/{model}` | Uses model's `default_port`. `wait` polls readiness. |
| `llaunch_stop_server` | `port`, **`node`** | `POST /stop/{port}` | Port = inference port. Errors if not running. |
| `llaunch_swap_server` | `model_name`, **`node`**, `port`, `timeout?` | `POST /start-with-eviction/{model}?port={port}` | Atomic stop→start on a specific inference port. |

### Node Management

| Tool | Parameters | Endpoint | Notes |
|------|-----------|----------|-------|
| `llaunch_add_node` | `name`, `host`, `port?`, `timeout?` | Local file write | Atomic temp+rename, pings node after writing |
| `llaunch_remove_node` | `name` | Local file write | Removes from `nodes.json`, no pre-check ping |

## Node Configuration

Format matches existing Python codebase:
```jsonc
{
  "inference-host": { "name": "inference-host", "host": "192.168.1.10", "port": 8765 }
}
```

### Discovery Lifecycle

- **On `session_start`:** Read `nodes.json`, ping each node, cache reachable list.
- **During session:** Maintain in-memory list of known, reachable nodes.
- **`add_node`:** Write to disk → immediately ping → add to cache if reachable.
- **`remove_node`:** Remove from disk. No pre-check — allows removing offline nodes.

### Error Handling

- Connection refused/timeout → **throw** `"Node '{name}' unreachable at {url}"`
- 4xx/5xx from agent → **throw** with agent's `detail` message
- JSON parse errors → **throw** with raw response
- Tools **throw** on failure (sets `isError: true` in pi), not returning error text as success.

## Use Cases

### UC1: Start a model for use as primary LLM
```
llaunch_list_models()                    // → available models per node
llaunch_start_server({ model_name: "mistral", node: "inference-host" })
```

### UC2: Swap model for a subagent transition
```
llaunch_swap_server({ model_name: "deep-research", node: "primary", port: 8081, timeout: 120 })
```

### UC3: Start a test model on a custom port
```
llaunch_swap_server({ model_name: "model-A", node: "dev-node", port: 9091 })
```

## Implementation Tasks

| # | Task | Verification |
|---|------|-------------|
| 1 | Extension file with imports, types, constants | Loads without errors |
| 2 | `readNodesConfig()` + `writeNodesConfig()` (atomic) | Handles missing file, invalid JSON |
| 3 | `llaunch_add_node` / `llaunch_remove_node` | Tools appear in pi tool list |
| 4 | HTTP helper (`callAgent<T>`) with timeout | Pings a known node's `/health` |
| 5 | `llaunch_list_models` (all-nodes parallel) | Returns aggregated model data |
| 6 | `llaunch_get_model_config` | Returns config, errors on unknown |
| 7 | `llaunch_server_status` | Shows running servers per node |
| 8 | `llaunch_start_server` with optional wait | Starts server, verifies readiness |
| 9 | `llaunch_stop_server` | Stops a running server |
| 10 | `llaunch_swap_server` with query params | Swaps models or fails with clear error |
| 11 | `llaunch_get_server_logs` with truncation | Returns log lines, handles large responses |
| 12 | Add `promptSnippet` to all tools | Tools appear in pi's "Available tools" |

## Out of Scope

- **Model configuration management** — Writes to each node's local `config.json`. Extension only controls the agent HTTP layer.
- **Agent lifecycle** — Users start agents via runner scripts on each node.
- **Multi-node atomic operations** — No cross-node coordination.

## Known Issues & Risks

### Agent Bug: `start-with-eviction` port parameter

The agent's `/start-with-eviction/{model_name}` in `routing.py` references `port` but only declares `model_name` in the function signature. FastAPI may or may not parse the query param correctly.

- **Mitigation:** Send `?port=X` as query param. If the agent errors, surface it clearly to the LLM.
- **Fix:** Add `port: int | None = Query(None)` to the function signature in `routing.py`. *(This is a separate fix — see ADR-002.)*

### Port confusion

Two port types exist: agent (8765) and inference (8081). Tool parameters use `port` to mean **inference port** only; agent port is implicit in the `node` parameter.

- **Mitigation:** Parameter names are clear in tool schemas; descriptions note both port types.

## Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| Python MCP server via `RemoteAggregator` | Reuses existing structure | stdio transport overhead; Python deps in pi; stale config state; swap atomicity lost over network |
| Central head/routing service | Single endpoint | Significant new infrastructure |
| **Pi TS extension (chosen)** | Zero extra transport, native fetch, shared node registry | Node specified per tool call; no centralized view without cross-node query |
