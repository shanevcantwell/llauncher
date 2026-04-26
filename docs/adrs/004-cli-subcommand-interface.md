# ADR-004: CLI Subcommand Interface for llauncher

**Status:** Draft  
**Date:** 2026-04-26  

## Context

llauncher currently has three entry surfaces — MCP stdio, Agent HTTP API (:8765), and Streamlit UI — but **no command-line interface** for operators working in SSH sessions or preferring terminal-based workflows. 

Sessions indicate a strong user preference: *"simple verb scripts"* — the ability to do things like `llauncher server start mistral`, `llauncher status`, `llauncher swap 8081 llama3` directly from the shell without opening a browser, crafting HTTP requests, or writing Python.

The existing `__main__.py` entry points (for MCP and agent server) serve their specific transports but don't expose a general-purpose CLI for local operations.

### Current State
- `python -m llauncher.agent` → starts FastAPI agent server
- `python -m llauncher.mcp_server` → starts MCP stdio server  
- No way to: list models, check port availability, start/stop servers, manage nodes from the terminal

## Decision

### Option Chosen: Typer-based Subcommand CLI with Local-State + Remote Awareness

```bash
# Model operations (operate on LOCAL LauncherState)
llauncher model list                    # list all known models from config
llauncher model info <name>             # show details of a specific model
llauncher server start <model> [port]   # start a model, optional port
llauncher server stop <port>            # stop model on port
llauncher server status                 # show all running servers

# Node operations (operate via agent API on remote nodes)
llauncher node add <name> --host <host> [--port 8765] [--api-key KEY]
llauncher node list                     # show registered nodes
llauncher node remove <name>
llauncher node status [all]             # query all nodes or specific

# Utility
llauncher config path                   # show active config file location
llauncher logs <port> [--lines 50]      # tail server logs locally
```

**Implementation approach:**
1. Use **Typer** (already a dependency via FastAPI) for CLI framework — zero new deps
2. New entry point in `pyproject.toml`: `llauncher = "llauncher.cli:app"`
3. Create `llauncher/cli.py` with subcommand groups (model, server, node, config)
4. Local commands read directly from `LauncherState` + `ConfigStore`; remote commands call the agent API
5. Color output via `rich` (already used in Streamlit UI components)

### Directory Structure
```
llauncher/
├── cli.py              # Typer app, subcommand definitions
├── core/
│   ├── launcher_state.py  # shared state for local operations
│   └── config.py          # shared config loading
└── remote/
    └── client.py        # reusable HTTP client to talk to agent API (extracted from remote/*)
```

### Testing Requirements
- CLI argument parsing: valid invocations produce expected subcommand dispatch
- Error handling: missing model name shows usage hint; invalid port shows error
- Local-state operations: start/stop interact with real LauncherState instance
- Remote commands: mock agent API responds, CLI parses and displays results
- Output formatting: consistent table output for list/status commands

## Consequences

**Positive:**
- Enables SSH-only operator workflows — matches user's "simple verb scripts" mental model
- Makes llauncher usable in CI/CD pipelines (automated start/stop patterns)
- Complements existing UI/MCP without replacing them
- Typer is lightweight and well-integrated with the existing FastAPI/rich dependency chain

**Negative:**
- New code surface: ~200-300 lines for CLI framework + error handling
- Must keep local commands consistent with agent API behavior — if they diverge, operators get confused
- Double-discovery problem: same operation exists as CLI subcommand, MCP tool, HTTP endpoint, and Streamlit action

**Open Questions:**
1. Should `llauncher server start` auto-assign ports (like the agent's current behavior) or require explicit port? (Recommendation: optional, default to auto with --port flag for explicit)
2. How should CLI handle node registration persistence — write directly to nodes.json, or go through an "agent first" path? (Recommendation: direct JSON manipulation for simplicity, matching existing ConfigStore pattern)
