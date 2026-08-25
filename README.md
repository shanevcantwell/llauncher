# llauncher

An MCP-first launcher and management tool for llama.cpp `llama-server` instances. The MCP contract is the product; the HTTP Agent, `llauncher` CLI, and Streamlit UI are co-equal consumers of the same `llauncher/operations/` service layer — three surfaces over one core, designed for both programmatic control (LLM agents, multi-node automation) and human operators.

## Features

### Core (`llauncher/operations/`)
The stateless service layer that every surface delegates to (ADR-LLNCH-008). Adding a verb here surfaces it across all four boundaries automatically.
- **Verbs**: `start`, `stop`, `swap`, `cancel`, `delete_model`, `list_orphans`
- **Pre-flight seams**: model-health probe and VRAM estimation, attachable as optional callables on `swap()`
- **ADR-LLNCH-010 port discipline**: every verb takes `port` as a required argument — no auto-allocation, no env-var fallback

### MCP Server
Canonical surface for LLM agents and automation. Stdio transport; full read + mutate coverage of the core verbs.
- **Discovery**: `list_models`, `get_model_config`
- **Lifecycle**: `start_server`, `stop_server`, `swap_server`, `cancel_server`, `server_status`, `get_server_logs`, `list_orphans`
- **Configuration CRUD**: `add_model`, `update_model_config`, `delete_model`, `validate_config`

### HTTP Agent
Same verbs over REST for multi-node setups (ADR-LLNCH-009 hub-spoke). Port-keyed routes (`/start/{port}`, `/swap/{port}`, `/stop/{port}`, `/cancel/{port}`, `/footer-context/{port}`) plus `/status`, `/models`, `/models/validate`. **Always** token-protected via an `X-Api-Key` header — including on loopback, where the token is auto-generated rather than operator-supplied (ADR-LLNCH-003). A non-loopback bind additionally *refuses to start* without a pre-existing token. Unlike the agent, the MCP server and local CLI are in-process and tokenless. See [`docs/auth.md`](docs/auth.md) for the full token/auth model.

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

# Install in development mode (includes UI)
pip install -e .

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
pip install -e .
```

**Set `LLAMA_SERVER_PATH` before your first model load.** The code default
(`~/.local/bin/llama-server`) does not exist on Windows, so an unset
`LLAMA_SERVER_PATH` fails the very first `/start` with `Server binary not
found: C:\Users\...\.local\bin\llama-server`. Point it at your actual
`llama-server.exe`:

- **Dev / `run.bat` usage:** set `LLAMA_SERVER_PATH` in the project-root
  `.env` (template: `.env.example`), e.g.
  `LLAMA_SERVER_PATH=C:\path\to\llama-server.exe`.
- **Service install (`scripts\windows\install.ps1`):** set it in
  `%USERPROFILE%\.llauncher\agent.env` (template:
  `scripts/windows/agent.env.example`) — required whenever the service runs
  under NSSM's default LocalSystem account, since that account's home does
  not resolve to your own profile. `install.ps1` prints a reminder on every
  run if this is still unset.

## Quick Start

Use the runner scripts for easiest setup:

The dashboard requires the local agent to be running. Start the agent
first (in its own terminal), then the dashboard in a second terminal.
The UI deliberately does not auto-spawn the agent — see ADR-LLNCH-009 and the
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

### Running as a service

For a persistent install that survives reboots and restarts on crash,
the agent ships with installers for systemd (Linux, user-mode) and NSSM
(Windows). See [`docs/operations/run-as-a-service.md`](docs/operations/run-as-a-service.md).

The UI supports two postures — pick whichever fits how you work:

- **On demand:** `llauncher-ui` (or `./run.sh ui`) starts the dashboard in
  the foreground for the session; close the terminal and it's gone.
- **Per-operator `systemd --user` service** ([ADR-LLNCH-022](docs/adrs/accepted/adr-llnch-022-llauncher-ui-user-service.md)):
  install with [`scripts/systemd/install-ui.sh`](scripts/systemd/install-ui.sh)
  for a unit that restarts on crash (`Restart=on-failure`) and logs to
  journald (`journalctl --user -u llauncher-ui -f`). See
  [`docs/operations/run-as-a-service.md`](docs/operations/run-as-a-service.md)
  for the full install steps.

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
| `start_server` | Start a llama-server instance on a given port (`model_name` + `port` required; ADR-LLNCH-010) |
| `stop_server` | Stop a running server by port number |
| `swap_server` | Atomically swap models on a port with rollback guarantee (ADR-LLNCH-011) |
| `cancel_server` | Cancel an in-flight start/swap on a port (ADR-LLNCH-014) |
| `server_status` | Get status summary of all running servers |
| `get_server_logs` | Fetch recent log lines from a running server |
| `list_orphans` | List unmanaged `llama-server` processes on the local node (ADR-LLNCH-015) |
| `update_model_config` | Update an existing model's configuration |
| `validate_config` | Validate a configuration without applying it |
| `add_model` | Add a new model configuration to the store |
| `delete_model` | Delete a model configuration (refuses if running; ADR-LLNCH-008 §4.1) |

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
Config CRUD plus the per-model verb buttons. Add / edit / delete configurations and drive **Start**, **Stop**, **Swap** against the selected target node. Includes the explicit port picker (`ui/components/port_picker.py`) — ADR-LLNCH-010 requires the operator to choose the port at every call site; there is no auto-allocation or remembered default.

#### Nodes Tab
Peer registry for multi-node setups. Add / list / remove remote agent nodes, test connectivity, and observe status. The sidebar `node_selector` (`ui/components/node_selector.py`) chooses which node the Models tab acts against.

#### Audit Tab
Tails the local audit log at `LAUNCHER_AUDIT_PATH` (`~/.llauncher/audit.jsonl` by default). Read-only view of commanded vs. observed events. Remote-node audit access is deferred per #64.

### CLI

The `llauncher` Typer CLI is a co-equal consumer of `llauncher/operations/` alongside the MCP server, HTTP Agent, and Streamlit UI. Every group supports a `--json` / `-j` flag for machine-readable output; the default is a Rich-rendered color table for human use.

A global `--state-dir` option (before the subcommand) points a single invocation at a config/state directory other than the default, with precedence `--state-dir` > `LAUNCHER_STATE_DIR` env > `~/.llauncher`:

```bash
llauncher --state-dir /var/lib/llauncher model list
```

This is the mechanism for a non-login/non-interactive caller (an automation harness, a service account) to read a shared multiuser state dir without exporting `LAUNCHER_STATE_DIR` or symlinking `~/.llauncher`.

**Subcommand groups:**

```bash
# Model configurations (read-only)
llauncher model list
llauncher model info mistral-7b

# Server lifecycle — port is required on start (ADR-LLNCH-010)
llauncher server start mistral-7b --port 8081
llauncher server stop 8081
llauncher server cancel 8081         # ADR-LLNCH-014: signals an in-flight start/swap
llauncher server status --json

# Orphans — unmanaged llama-server processes (ADR-LLNCH-015, read-only)
llauncher orphan list

# Remote nodes (ADR-LLNCH-009)
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

Per ADR-LLNCH-010, port is supplied at every call site (UI port picker, CLI `--port`, MCP `port` arg, HTTP `/start/{port}` route) and is **not** persisted in the config. Legacy `default_port` entries in `config.json` are silently dropped on load.

### State Paths & Volume Mounts (Docker)

Per ADR-LLNCH-008, the lockfile directory and audit log are env-configurable so a container can mount host state as a volume — letting an in-container agent (e.g. `pi-coding-agent`) introspect the state of llauncher running on the host.

| Env var | Default | Holds |
|---------|---------|-------|
| `LAUNCHER_STATE_DIR` | `~/.llauncher` | Base for every derived path below |
| `LAUNCHER_RUN_DIR` | `$LAUNCHER_STATE_DIR/run` | Per-server lockfiles (`{port}.lock`) and swap markers |
| `LAUNCHER_AUDIT_PATH` | `$LAUNCHER_STATE_DIR/audit.jsonl` | Append-only JSON Lines audit log |

Precedence for each path is the explicit per-path var (`LAUNCHER_RUN_DIR` / `LAUNCHER_AUDIT_PATH`) > the `LAUNCHER_STATE_DIR`-derived default. With every var unset the paths are byte-identical to the legacy `~/.llauncher/*` layout, so setting them is opt-in.

To let a container read the host's live llauncher state, mount the host paths in read-only and point the in-container env vars at the mount:

```bash
docker run \
  -v "$HOME/.llauncher/run:/host-llauncher/run:ro" \
  -v "$HOME/.llauncher/audit.jsonl:/host-llauncher/audit.jsonl:ro" \
  -e LAUNCHER_RUN_DIR=/host-llauncher/run \
  -e LAUNCHER_AUDIT_PATH=/host-llauncher/audit.jsonl \
  my-agent-image
```

Mount read-only (`:ro`) when the container only introspects; drop `:ro` if the containerized process is the one commanding llauncher and must write lockfiles/audit entries. The audit log is a single file, so bind-mount the file itself (not its parent dir) to avoid masking sibling state.

## Change Management

llauncher includes validation rules to prevent problematic actions:

- **Port conflicts**: Prevents starting models on ports already in use
- **Blacklisted ports**: Default blacklist includes port 8080 (commonly used by other services)
- **Model whitelists**: Optionally restrict which models can be started
- **Caller blacklists**: Restrict which callers (UI, MCP, etc.) can perform actions

## Versioning

`vN` (`v1`, `v2`, `v3` …) denotes the architecture generation; `0.x`
(`0.4.0a0` / `v0.4.0-alpha`) denotes the semver release. They are independent
axes and do not map to each other — see [`docs/VERSIONING.md`](docs/VERSIONING.md).

## Project Structure

```
llauncher/
├── pyproject.toml
├── llauncher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                  # Typer CLI (model/server/orphan/node/config groups)
│   ├── state.py                # Legacy LauncherState — eviction-compat hook (ADR-LLNCH-008)
│   ├── operations/             # Stateless service layer; MCP/HTTP/CLI/UI all delegate here (ADR-LLNCH-008)
│   │   ├── start.py
│   │   ├── stop.py
│   │   ├── swap.py             # ADR-LLNCH-011 five-phase swap with rollback
│   │   ├── delete.py
│   │   ├── orphan.py           # ADR-LLNCH-015 read-only orphan listing
│   │   └── preflight.py        # Model-health + VRAM seams
│   ├── agent/                  # HTTP agent (FastAPI, port-keyed routes per ADR-LLNCH-010)
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── footer_cache.py     # /footer-context/{port} TTL cache (ADR-LLNCH-012)
│   │   ├── middleware.py
│   │   ├── routing.py
│   │   └── server.py           # Lifespan handler reaps managed children on SIGTERM/SIGINT
│   ├── mcp_server/             # MCP server (stdio transport)
│   │   ├── server.py
│   │   └── tools/              # servers / models / config tool groups
│   ├── core/                   # Primitive substrate (no LauncherState)
│   │   ├── audit_log.py        # JSON Lines audit (ADR-LLNCH-008)
│   │   ├── config.py           # ConfigStore — single source of truth
│   │   ├── gpu.py              # GPU collector (ADR-LLNCH-006)
│   │   ├── lockfile.py         # Atomic O_EXCL per-port lockfiles
│   │   ├── log_rotation.py     # ADR-LLNCH-013 append + rotate
│   │   ├── marker.py           # In-flight swap/start marker (ADR-LLNCH-011/014)
│   │   ├── model_health.py     # Cache probe (ADR-LLNCH-005)
│   │   ├── process.py          # Subprocess management
│   │   └── settings.py         # LAUNCHER_* env-var family
│   ├── models/
│   │   └── config.py           # Pydantic ModelConfig (no default_port; ADR-LLNCH-010)
│   ├── remote/                 # Multi-node hub-spoke (ADR-LLNCH-009)
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

**Running from a git worktree:** the dev `.venv` is a shared editable
install, so its `.pth` entry always resolves `import llauncher` to
whichever checkout it was last `pip install -e`'d from (normally the main
checkout) — a worktree invocation whose collection order lets that `.pth`
win reads coverage against the *main checkout's* files, not the worktree's
(#361). `[tool.coverage.paths]` in `pyproject.toml` reconciles the report,
but the sanctioned invocation is still to pin the import explicitly:

```bash
PYTHONPATH="$(pwd)" pytest --cov=llauncher --cov-report=term-missing
```

Never repoint the shared venv's `.pth` at a worktree to work around
this — that `.venv` also backs the live `llauncher-agent` systemd
service, and a crash mid-window leaves the live service importing
worktree code. Restore-after-use is not a substitute for not mutating it.

Test files are in `tests/`:
- `tests/unit/`: Unit tests for models, config, and process
- `tests/integration/`: Integration tests for state management

For an inventory of which tests exist (file-by-file, with markers and
docstring first lines), see [`docs/generated/TEST_SUITE_SUMMARY.md`](docs/generated/TEST_SUITE_SUMMARY.md).
Regenerate after adding or renaming tests:

```bash
python scripts/summarize_tests.py
```

The coverage floor is pinned at `--cov-fail-under=93` against non-UI
scope in `pytest.ini`; UI coverage is deferred to the AppTest harness
in #69 (v3-alpha).

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
LLAUNCHER_AGENT_PORT=9000 LLAUNCHER_AGENT_NODE_NAME="my-server" ./run.sh agent

# Windows (PowerShell)
$env:LLAUNCHER_AGENT_PORT="9000"
$env:LLAUNCHER_AGENT_NODE_NAME="my-server"
run.bat agent
```

**Environment Variables:**
- `LLAUNCHER_AGENT_HOST`: Host to bind to (default: `127.0.0.1`). Set to `0.0.0.0` or a specific LAN IP to expose the agent to other hosts — see "Security Notes" below.
- `LLAUNCHER_AGENT_PORT`: Port to listen on (default: `8765`)
- `LLAUNCHER_AGENT_NODE_NAME`: Friendly name for the node
- `LLAUNCHER_AGENT_TOKEN`: The agent's `X-Api-Key` token. The agent **always** enforces a token (auth is never off, even on loopback); this var lets you supply it explicitly and always wins over the file below. *Required* when binding to anything other than loopback — the agent refuses to start on a non-loopback host without it. Special value `-` reads the token from stdin (one line). On a loopback start with no value set (and no `LLAUNCHER_AGENT_TOKEN=` line in `agent.env`), a fresh token is auto-generated and appended into `~/.llauncher/agent.env` (mode 0600 if newly created). For the full token/auth model (which consumers need a token, exempt paths, resolution order), see [`docs/auth.md`](docs/auth.md).

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
   - **API Key**: the remote agent's token (see *Adding a remote node* below)
4. Click **🔍 Test Connection** to verify
5. Click **➕ Add Node** to register

##### Adding a remote node (token walkthrough)

A remote agent **always** enforces a token (auth is never off, even on
loopback). To pair the head with a remote node you copy that token by hand —
it currently is **not** issued automatically (session-token issuance is
tracked under #137). The token lives in a single live file per platform —
`agent.env`, parsed directly by the agent and the local UI (issue #284):

| Platform | Live source (`agent.env`) | Seeded (once) by |
| --- | --- | --- |
| Linux | `~/.config/llauncher/agent.env` | `scripts/systemd/install.sh` |
| Windows | `%USERPROFILE%\.llauncher\agent.env` | `scripts/windows/install.ps1` |

Step by step:

1. **Get on the remote box.** SSH to a Linux node, or RDP to a Windows node.
2. **Read the token.** The portable way is the agent's own subcommand, which
   resolves the token from env / stdin / `agent.env` and prints it to
   stdout:
   ```bash
   llauncher-agent print-token
   ```
   If you prefer to read the file directly, look for the
   `LLAUNCHER_AGENT_TOKEN=` line:
   ```bash
   # Linux
   grep LLAUNCHER_AGENT_TOKEN= ~/.config/llauncher/agent.env
   ```
   ```powershell
   # Windows (PowerShell)
   Select-String LLAUNCHER_AGENT_TOKEN= $env:USERPROFILE\.llauncher\agent.env
   ```
   Over SSH you can do both in one shot: `ssh windows-box llauncher-agent print-token`.
3. **Copy the value.** It is a single `secrets.token_urlsafe(32)` string on
   one line.
4. **Paste it into the head's UI.** Back on the head machine, paste the value
   into the **API Key** field of the **Add New Node** form (step 3 above).

The token is stored on the head at `~/.llauncher/node_tokens.json` (mode 0600);
the `local` node is excluded because its token already lives in `agent.env`.

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

- **Loopback by default**: The agent binds to `127.0.0.1` unless `LLAUNCHER_AGENT_HOST` is set explicitly. Set it to a LAN IP (or `0.0.0.0`) to expose the agent to other hosts on the network.
- **Token required for non-loopback binds**: Binding to anything other than `127.0.0.1` / `::1` / `localhost` requires `LLAUNCHER_AGENT_TOKEN` to be set. The agent refuses to start otherwise. On loopback first-run with no token configured, a fresh token is generated and appended into `~/.llauncher/agent.env` (mode 0600 if newly created) and printed once to stderr.
- **Trusted LAN Only**: Even with a token, only expose the agent on networks you trust — the transport is plain HTTP (no TLS). Tailscale is the recommended option for cross-host trust.
- **Firewall**: Restrict port 8765 to your LAN subnet.
- **Full auth model**: For the token/auth reference — the two planes (token-bearing HTTP agent vs. tokenless local MCP/CLI), the `X-Api-Key` header, exempt paths, resolution precedence, and file locations/modes — see [`docs/auth.md`](docs/auth.md).

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
   # have set LLAUNCHER_AGENT_HOST and LLAUNCHER_AGENT_TOKEN.
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
   LLAUNCHER_AGENT_PORT=9000 llauncher-agent
   ```

#### Can't connect from Windows to Linux (or vice versa)

1. Verify network connectivity:
   ```bash
   ping <remote-node-ip>
   ```

2. Check that the agent is not binding to loopback only:
   - The default is `127.0.0.1:8765`. For cross-host access set
     `LLAUNCHER_AGENT_HOST=0.0.0.0` (or a specific LAN IP) **and**
     `LLAUNCHER_AGENT_TOKEN` — the agent refuses to start on a
     non-loopback host without a token.

### API Documentation

When an agent is running, visit `http://<node-ip>:8765/docs` for interactive API documentation.

### License

MIT
