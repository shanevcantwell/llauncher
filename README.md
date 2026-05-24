# llauncher

An MCP-first launcher and management tool for llama.cpp `llama-server` instances. The MCP contract is the product; the HTTP Agent, `llauncher` CLI, and Streamlit UI are co-equal consumers of the same `llauncher/operations/` service layer — three surfaces over one core, designed for both programmatic control (LLM agents, multi-node automation) and human operators.

## Features

### Core (`llauncher/operations/`)
The stateless service layer that every surface delegates to (ADR-008). Adding a verb here surfaces it across all four boundaries automatically.
- **Verbs**: `start`, `stop`, `swap`, `cancel`, `delete_model`, `list_orphans`
- **Pre-flight seams**: model-health probe and VRAM estimation, attachable as optional callables on `swap()`
- **ADR-010 port discipline**: every verb takes `port` as a required argument — no auto-allocation, no env-var fallback

### MCP Server
Canonical surface for LLM agents and automation. Stdio transport; full read + mutate coverage of the core verbs.
- **Discovery**: `list_models`, `get_model_config`
- **Lifecycle**: `start_server`, `stop_server`, `swap_server`, `cancel_server`, `server_status`, `get_server_logs`, `list_orphans`
- **Configuration CRUD**: `add_model`, `update_model_config`, `delete_model`, `validate_config`

### HTTP Agent
Same verbs over REST for multi-node setups (ADR-009 hub-spoke). Port-keyed routes (`/start/{port}`, `/swap/{port}`, `/stop/{port}`, `/cancel/{port}`, `/footer-context/{port}`) plus `/status`, `/models`, `/models/health`. Token-protected when bound off-loopback (ADR-003).

### Streamlit UI
Web dashboard for human operators. Four tabs: Dashboard (read-only running view), Models (config CRUD + per-model start/stop/swap with explicit port picker), Nodes (peer registry), Audit (local audit-log tail).

### CLI (`llauncher`)
Typer command-line surface, co-equal with MCP and UI. Subcommand groups: `model` (list, info), `server` (start, stop, cancel, status), `orphan` (list), `node` (add, list, remove, status), `config` (path, validate). Rich tables for human output and `--json` on every group for scripting.

### Configuration
- **Config Persistence**: Store configurations in `~/.llauncher/config.json` (single source of truth)
- **Validation**: Model paths verified, port conflicts detected, blacklists enforced

## Installation

```bash
# Clone the repository
git clone https://github.com/shanevcantwell/llauncher
cd llauncher

# Install in development mode (with UI)
pip install -e ".[ui]"

# Optional: Install test dependencies
pip install -e ".[test]"
```

### Windows Notes

If you see warnings like `WARNING: Ignoring invalid distribution ~` during install:

```bat
# Clean up corrupted site-packages and reinstall
cd github\llauncher
rmdir /s /q .venv
python -m venv .venv
\.venv\Scripts\activate
pip install -e ".[ui]"
```

## Quick Start

Use the runner scripts for easiest setup:

The dashboard requires the local agent to be running. Start the agent
first (in its own terminal), then the dashboard in a second terminal.
The UI deliberately does not auto-spawn the agent — see ADR-009 and the
"Why doesn't the UI start the agent for me?" expander rendered on the
dashboard when the agent is down.

**Linux/macOS:**
```bash
./run.sh install     # Set up virtual environment and install
./run.sh agent       # Terminal 1: start agent in foreground
./run.sh ui          # Terminal 2: start dashboard (requires agent)
./run.sh stop        # Stop running agent
# Optional:
./run.sh agent-bg    # Start agent detached (logs to agent.log)
./run.sh discover    # List discovered launch scripts
```

**Windows:**
```cmd
run.bat install      :: Set up virtual environment and install
run.bat agent        :: Terminal 1: start agent in foreground
run.bat ui           :: Terminal 2: start dashboard (requires agent)
run.bat stop         :: Stop running agent
:: Optional:
run.bat agent-bg     :: Start agent detached (logs to agent.log)
run.bat discover     :: List discovered launch scripts
```

### Running the agent as a service

For a persistent install that survives reboots and restarts on crash,
the agent ships with installers for systemd (Linux, user-mode) and NSSM
(Windows). See [`docs/operations/run-as-a-service.md`](docs/operations/run-as-a-service.md).
The UI is not service-managed by design — it's interactive and you
launch it on demand.

## Usage

### MCP Server

Start the MCP server:

```bash
llauncher-mcp
```

Or configure in your MCP client (e.g., Claude Code):

```json
{
  "mcpServers": {
    "llauncher": {
      "command": "llauncher-mcp",
      "args": []
    }
  }
}
```

> **Trust boundary (stdio only).** The MCP server speaks the MCP stdio
> transport and has no authentication of its own — it implicitly trusts
> whatever process spawned it over the stdio pipe (typically your MCP
> client, e.g. Claude Desktop / Claude Code). There is no network
> listener for MCP. Vetting the MCP client you hand these tools to is
> the operator's responsibility; llauncher cannot distinguish a benign
> caller from a malicious one once the stdio pipe is open. See
> `docs/plans/security-hardening-plan.md` §2.2 (control C5) for the
> threat-model rationale.

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `list_models` | List all configured models with current status (running/stopped) |
| `get_model_config` | Get full configuration details for a specific model |
| `start_server` | Start a llama-server instance on a given port (`model_name` + `port` required; ADR-010) |
| `stop_server` | Stop a running server by port number |
| `swap_server` | Atomically swap models on a port with rollback guarantee (ADR-011) |
| `cancel_server` | Cancel an in-flight start/swap on a port (ADR-014) |
| `server_status` | Get status summary of all running servers |
| `get_server_logs` | Fetch recent log lines from a running server |
| `list_orphans` | List unmanaged `llama-server` processes on the local node (ADR-015) |
| `update_model_config` | Update an existing model's configuration |
| `validate_config` | Validate a configuration without applying it |
| `add_model` | Add a new model configuration to the store |
| `delete_model` | Delete a model configuration (refuses if running; ADR-008 §4.1) |

### Streamlit UI

Start the UI using the runner script (recommended):

**Linux/macOS:**
```bash
./run.sh ui
```

**Windows:**
```cmd
run.bat ui
```

> **Bind to loopback (no built-in auth).** Streamlit binds wherever the
> operator launches it; the default is loopback. The runner scripts
> launch with `--server.address 127.0.0.1`, and that is the recommended
> invocation for typical single-operator use. The dashboard itself has
> **no built-in authentication** — anything that can reach the port can
> drive every mutate path (start/stop servers, edit configs, manage
> nodes). Do not expose it beyond loopback without an operator-supplied
> gateway in front: Tailscale, an SSH tunnel, or a reverse proxy that
> enforces auth. Passing `--server.address 0.0.0.0` (or a LAN IP)
> without one of those is equivalent to publishing an unauthenticated
> admin console on your network. See
> `docs/plans/security-hardening-plan.md` §2.8 (control C12) for the
> threat-model rationale.

#### Dashboard Tab
Read-only running view (no mutate verbs live here per M4 Slice 13 / #50). Status indicators (🟢 Running / ⚫ Stopped), uptime, and live log tail for each active server. Use the Models tab to start/stop/swap.

#### Models Tab
Config CRUD plus the per-model verb buttons. Add / edit / delete configurations and drive **Start**, **Stop**, **Swap** against the selected target node. Includes the explicit port picker (`ui/components/port_picker.py`) — ADR-010 requires the operator to choose the port at every call site; there is no auto-allocation or remembered default.

#### Nodes Tab
Peer registry for multi-node setups. Add / list / remove remote agent nodes, test connectivity, and observe status. The sidebar `node_selector` (`ui/components/node_selector.py`) chooses which node the Models tab acts against.

#### Audit Tab
Tails the local audit log at `LAUNCHER_AUDIT_PATH` (`~/.llauncher/audit.jsonl` by default). Read-only view of commanded vs. observed events. Remote-node audit access is deferred per #64.

### CLI

The `llauncher` Typer CLI is a co-equal consumer of `llauncher/operations/` alongside the MCP server, HTTP Agent, and Streamlit UI. Every group supports a `--json` / `-j` flag for machine-readable output; the default is a Rich-rendered color table for human use.

**Subcommand groups:**

```bash
# Model configurations (read-only)
llauncher model list
llauncher model info mistral-7b

# Server lifecycle — port is required on start (ADR-010)
llauncher server start mistral-7b --port 8081
llauncher server stop 8081
llauncher server cancel 8081         # ADR-014: signals an in-flight start/swap
llauncher server status --json

# Orphans — unmanaged llama-server processes (ADR-015, read-only)
llauncher orphan list

# Remote nodes (ADR-009)
llauncher node add my-server --host 192.168.1.100 --port 8765
llauncher node list
llauncher node status --all
llauncher node remove my-server

# Configuration store
llauncher config path                # print path to config.json
llauncher config validate mistral-7b
```

Each group also accepts `--help`. The runner scripts (`./run.sh agent`, `./run.sh ui`) remain the easiest way to launch the agent and dashboard; the CLI subcommands above act against an already-running stack.

## Configuration

Create model configurations directly in `~/.llauncher/config.json`. Configs can be managed via the UI or MCP tools.

Example config entry:


```json
{
  "mistral": {
    "name": "mistral",
    "model_path": "/path/to/model.gguf",
    "mmproj_path": null,
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    "threads": 8,
    "threads_batch": 8,
    "ubatch_size": 512,
    "batch_size": null,
    "flash_attn": "on",
    "no_mmap": false,
    "cache_type_k": "f32",
    "cache_type_v": "f32",
    "n_cpu_moe": null,
    "parallel": 1,
    "temperature": null,
    "top_k": null,
    "top_p": null,
    "min_p": null,
    "repeat_penalty": null,
    "reverse_prompt": null,
    "mlock": false,
    "extra_args": ""
  }
}
```

Per ADR-010, port is supplied at every call site (UI port picker, CLI `--port`, MCP `port` arg, HTTP `/start/{port}` route) and is **not** persisted in the config. Legacy `default_port` entries in `config.json` are silently dropped on load.

## Change Management

llauncher includes validation rules to prevent problematic actions:

- **Port conflicts**: Prevents starting models on ports already in use
- **Blacklisted ports**: Default blacklist includes port 8080 (commonly used by other services)
- **Model whitelists**: Optionally restrict which models can be started
- **Caller blacklists**: Restrict which callers (UI, MCP, etc.) can perform actions

## Project Structure

```
llauncher/
├── pyproject.toml
├── llauncher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                  # Typer CLI (model/server/orphan/node/config groups)
│   ├── state.py                # Legacy LauncherState — eviction-compat hook (ADR-008)
│   ├── operations/             # Stateless service layer; MCP/HTTP/CLI/UI all delegate here (ADR-008)
│   │   ├── start.py
│   │   ├── stop.py
│   │   ├── swap.py             # ADR-011 five-phase swap with rollback
│   │   ├── delete.py
│   │   ├── orphan.py           # ADR-015 read-only orphan listing
│   │   └── preflight.py        # Model-health + VRAM seams
│   ├── agent/                  # HTTP agent (FastAPI, port-keyed routes per ADR-010)
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── footer_cache.py     # /footer-context/{port} TTL cache (ADR-012)
│   │   ├── middleware.py
│   │   ├── routing.py
│   │   └── server.py           # Lifespan handler reaps managed children on SIGTERM/SIGINT
│   ├── mcp_server/             # MCP server (stdio transport)
│   │   ├── server.py
│   │   └── tools/              # servers / models / config tool groups
│   ├── core/                   # Primitive substrate (no LauncherState)
│   │   ├── audit_log.py        # JSON Lines audit (ADR-008)
│   │   ├── config.py           # ConfigStore — single source of truth
│   │   ├── gpu.py              # GPU collector (ADR-006)
│   │   ├── lockfile.py         # Atomic O_EXCL per-port lockfiles
│   │   ├── log_rotation.py     # ADR-013 append + rotate
│   │   ├── marker.py           # In-flight swap/start marker (ADR-011/014)
│   │   ├── model_health.py     # Cache probe (ADR-005)
│   │   ├── process.py          # Subprocess management
│   │   └── settings.py         # LAUNCHER_* env-var family
│   ├── models/
│   │   └── config.py           # Pydantic ModelConfig (no default_port; ADR-010)
│   ├── remote/                 # Multi-node hub-spoke (ADR-009)
│   │   ├── node.py             # RemoteNode (port-keyed ops)
│   │   ├── registry.py         # NodeRegistry
│   │   └── state.py            # RemoteAggregator (swap_on_node parity)
│   └── ui/                     # Streamlit dashboard
│       ├── app.py
│       ├── utils.py            # render_op_result, OpResultSeverity ladder
│       ├── components/
│       │   ├── node_selector.py
│       │   └── port_picker.py  # Explicit port input — no auto-allocation
│       └── tabs/
│           ├── audit.py
│           ├── dashboard.py    # Read-only running view
│           ├── models.py       # Config CRUD + start/stop/swap verbs
│           └── nodes.py
```

## Testing

Run the test suite:

```bash
pytest
# or with coverage
pytest --cov=llauncher --cov-report=term-missing
```

Test files are in `tests/`:
- `tests/unit/`: Unit tests for models, config, and process
- `tests/integration/`: Integration tests for state management

## Multi-Node Management (Remote)

llauncher supports managing llama-server instances across multiple machines (Windows and Linux) on a local network from a single dashboard.

### Architecture

Each managed node runs a lightweight **agent** that exposes an HTTP API. The "head" dashboard connects to these agents over the LAN:

```
┌─────────────────────────────────────┐
│         HEAD DASHBOARD              │
│  - Streamlit UI with node selector  │
│  - Connects to all agents via HTTP  │
└─────────────┬───────────────────────┘
              │ LAN (port 8765)
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌────────┐ ┌────────┐ ┌────────┐
│ Agent  │ │ Agent  │ │ Agent  │
│ Linux  │ │Windows │ │ Linux  │
│ :8765  │ │ :8765  │ │ :8765  │
└────────┘ └────────┘ └────────┘
```

### Deployment

#### 1. Install on Each Node

On every machine you want to manage (including the head):

**Linux/macOS:**
```bash
git clone https://github.com/shanevcantwell/llauncher
cd llauncher
./run.sh install
```

**Windows:**
```cmd
git clone https://github.com/shanevcantwell/llauncher
cd llauncher
run.bat install
```

#### 2. Start the Agent on Each Node

**Using runner scripts (recommended):**

**Linux/macOS:**
```bash
./run.sh agent     # Foreground
./run.sh agent-bg  # Background
./run.sh stop      # Stop agent
```

**Windows:**
```cmd
run.bat agent      # Foreground
run.bat agent-bg   # Background
run.bat stop       # Stop agent
```

**With custom configuration:**
```bash
# Linux/macOS
LAUNCHER_AGENT_PORT=9000 LAUNCHER_AGENT_NODE_NAME="my-server" ./run.sh agent

# Windows (PowerShell)
$env:LAUNCHER_AGENT_PORT="9000"
$env:LAUNCHER_AGENT_NODE_NAME="my-server"
run.bat agent
```

**Environment Variables:**
- `LAUNCHER_AGENT_HOST`: Host to bind to (default: `127.0.0.1`). Set to `0.0.0.0` or a specific LAN IP to expose the agent to other hosts — see "Security Notes" below.
- `LAUNCHER_AGENT_PORT`: Port to listen on (default: `8765`)
- `LAUNCHER_AGENT_NODE_NAME`: Friendly name for the node
- `LAUNCHER_AGENT_TOKEN`: Required when binding to anything other than loopback. The agent refuses to start on a non-loopback host without it. Special value `-` reads the token from stdin (one line). On a loopback start with no value set, a fresh token is auto-generated and written to `~/.llauncher/agent.token` (mode 0600).

#### 3. Start the Dashboard on the Head Machine

**Linux/macOS:**
```bash
./run.sh ui
```

**Windows:**
```cmd
run.bat ui
```

The dashboard will automatically:
1. Show a loading screen while initializing
2. Register itself as the "local" node

#### 4. Add Remote Nodes

In the dashboard:
1. Go to the **Nodes** tab
2. Click **➕ Add New Node**
3. Enter:
   - **Node Name**: Friendly name (e.g., `linux-box`, `windows-server`)
   - **Host**: IP address or hostname (e.g., `192.168.1.100`)
   - **Port**: Agent port (default: `8765`)
4. Click **🔍 Test Connection** to verify
5. Click **➕ Add Node** to register

### Network Configuration

#### Firewall Rules

Ensure port 8765 is open on managed nodes:

**Linux (ufw):**
```bash
sudo ufw allow 8765/tcp
```

**Linux (firewalld):**
```bash
sudo firewall-cmd --permanent --add-port=8765/tcp
sudo firewall-cmd --reload
```

**Windows (PowerShell):**
```powershell
New-NetFirewallRule -DisplayName "llauncher Agent" -Direction Inbound -LocalPort 8765 -Protocol TCP -Action Allow
```

#### Security Notes

- **Loopback by default**: The agent binds to `127.0.0.1` unless `LAUNCHER_AGENT_HOST` is set explicitly. Set it to a LAN IP (or `0.0.0.0`) to expose the agent to other hosts on the network.
- **Token required for non-loopback binds**: Binding to anything other than `127.0.0.1` / `::1` / `localhost` requires `LAUNCHER_AGENT_TOKEN` to be set. The agent refuses to start otherwise. On loopback first-run with no token configured, a fresh token is generated at `~/.llauncher/agent.token` (mode 0600) and printed once to stderr.
- **Trusted LAN Only**: Even with a token, only expose the agent on networks you trust — the transport is plain HTTP (no TLS). Tailscale is the recommended option for cross-host trust.
- **Firewall**: Restrict port 8765 to your LAN subnet.

### Usage

The sidebar **Node Selector** (`ui/components/node_selector.py`) picks the target node — `local` plus any registered remotes. A single target is always selected; the "All Nodes" cross-node aggregate view was dropped in M4 Slice 13 (#50).

- **Dashboard Tab**: read-only running view across the selected node.
- **Models Tab**: config CRUD + per-model Start / Stop / Swap, acting on the selected node.
- **Nodes Tab**: registered-nodes list with Test Connection and Remove controls.
- **Audit Tab**: tails the local `LAUNCHER_AUDIT_PATH`. Remote-node audit access is deferred per #64.

### Troubleshooting

#### "Connection Failed" when adding node

1. Verify agent is running on the remote node:
   ```bash
   curl http://<node-ip>:8765/health
   ```

2. Check firewall rules on the remote node

3. Verify the agent is binding to the correct interface:
   ```bash
   # Default is 127.0.0.1:8765 (loopback). For LAN access you must
   # have set LAUNCHER_AGENT_HOST and LAUNCHER_AGENT_TOKEN.
   netstat -tlnp | grep 8765
   ```

#### Agent won't start

1. Check if port 8765 is already in use:
   ```bash
   lsof -i :8765
   # or
   netstat -tlnp | grep 8765
   ```

2. Use a different port:
   ```bash
   LAUNCHER_AGENT_PORT=9000 llauncher-agent
   ```

#### Can't connect from Windows to Linux (or vice versa)

1. Verify network connectivity:
   ```bash
   ping <remote-node-ip>
   ```

2. Check that the agent is not binding to loopback only:
   - The default is `127.0.0.1:8765`. For cross-host access set
     `LAUNCHER_AGENT_HOST=0.0.0.0` (or a specific LAN IP) **and**
     `LAUNCHER_AGENT_TOKEN` — the agent refuses to start on a
     non-loopback host without a token.

### API Documentation

When an agent is running, visit `http://<node-ip>:8765/docs` for interactive API documentation.

### License

MIT
