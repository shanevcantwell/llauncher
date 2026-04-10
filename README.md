# llauncher

An MCP-first launcher and management tool for llama.cpp `llama-server` instances. Designed for both programmatic control via LLMs and human operators via a web UI.

## Features

### MCP Server
Full programmatic control for LLM agents and automation:
- **List models** with current status (running/stopped)
- **Start/stop servers** with validation and audit logging
- **Manage configurations** - add, update, remove model configs
- **Get server logs** for debugging and monitoring
- **Validate configurations** before applying changes

### Streamlit UI
Web-based dashboard for human operators:
- **Dashboard**: Overview of all models with quick Start/Stop buttons
- **Manager**: Add new models or edit existing configurations
- **Running**: View live logs from active servers with Stop controls

### Discovery & Configuration
- **Script Discovery**: Automatically finds `launch-*.sh` scripts in `~/.local/bin`
- **Config Persistence**: Store configurations in `~/.llauncher/config.json`
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

## Quick Start

Use the runner scripts for easiest setup:

**Linux/macOS:**
```bash
./run.sh install   # Set up virtual environment and install
./run.sh ui        # Start dashboard (auto-starts agent)
./run.sh agent     # Start agent in foreground
./run.sh stop      # Stop running agent
```

**Windows:**
```cmd
run.bat install    # Set up virtual environment and install
run.bat ui         # Start dashboard (auto-starts agent)
run.bat agent      # Start agent in foreground
run.bat stop       # Stop running agent
```

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

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `list_models` | List all configured models with current status (running/stopped) |
| `get_model_config` | Get full configuration details for a specific model |
| `start_server` | Start a llama-server instance for a model (with validation) |
| `stop_server` | Stop a running server by port number |
| `swap_server` | Atomically swap models on a port with rollback guarantee |
| `server_status` | Get status summary of all running servers |
| `get_server_logs` | Fetch recent log lines from a running server |
| `update_model_config` | Update an existing model's configuration |
| `validate_config` | Validate a configuration without applying it |
| `add_model` | Add a new model configuration to the store |
| `remove_model` | Remove a model configuration (blocks if running) |

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

The UI automatically starts a local agent if one isn't running. You can also start the agent separately with `./run.sh agent` or `run.bat agent`.

#### Dashboard Tab
- Grid view of all configured models with status indicators (🟢 Running / ⚫ Stopped)
- Quick **Start** and **Stop** buttons for each model
- **Edit** button redirects to Manager for configuration changes
- Links to API docs when server is running

#### Manager Tab
- **List Models**: View all models with expandable details (port, model path, GPU layers)
- **Add New Model**: Form to create new configurations with validation
- **Edit Model**: Pre-populated form to modify existing configurations
- **Delete Model**: Remove configurations (blocked if server is running)

#### Running Tab
- List of currently running servers with uptime
- Live log streaming for each server
- Stop button for each running instance

### CLI

Discover configured models:

```bash
python -m llauncher discover
```

## Configuration

Models can be configured in two ways:

### Launch Scripts
Create `launch-*.sh` scripts in `~/.local/bin`. The script name becomes the model name (e.g., `launch-mistral.sh` → `mistral`):

```bash
#!/bin/bash
llama-server \
  -m /path/to/model.gguf \
  --mmproj /path/to/mmproj.gguf \
  --n-gpu-layers 255 \
  --port 8081 \
  --host 0.0.0.0 \
  -c 131072 \
  --flash-attn on \
  --no-mmap
```

Supported arguments are parsed automatically:
- `-m` / `--model`: Model path (required)
- `--mmproj`: Multimodal projector path (for vision models)
- `--n-gpu-layers`: GPU offload layers (0-1024)
- `--port`: Server port
- `--host`: Bind address
- `-c` / `--ctx-size`: Context size
- `--threads`: Thread count
- `--flash-attn`: Flash attention (on/off/auto)
- `--no-mmap`: Disable memory mapping
- And more...

### Config File
Persist configurations in `~/.llauncher/config.json`. Persisted configs take precedence over discovered scripts and can be edited via the UI or MCP tools.

Example config entry:
```json
{
  "mistral": {
    "name": "mistral",
    "model_path": "/path/to/model.gguf",
    "mmproj_path": null,
    "port": 8081,
    "host": "0.0.0.0",
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    "flash_attn": "on",
    "no_mmap": false
  }
}
```

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
│   ├── state.py           # StateManager
│   ├── models/
│   │   └── config.py      # Pydantic models
│   ├── core/
│   │   ├── discovery.py   # Script parser
│   │   ├── process.py     # Process management
│   │   └── config.py      # Config persistence
│   ├── mcp/
│   │   ├── server.py      # MCP server
│   │   └── tools/         # Tool implementations
│   └── ui/
│       ├── app.py         # Streamlit app
│       └── tabs/          # UI components
```

## Testing

Run the test suite:

```bash
pytest
# or with coverage
pytest --cov=llauncher --cov-report=term-missing
```

Test files are in `tests/`:
- `tests/unit/`: Unit tests for models, discovery, and config
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
- `LAUNCHER_AGENT_HOST`: Host to bind to (default: `0.0.0.0`)
- `LAUNCHER_AGENT_PORT`: Port to listen on (default: `8765`)
- `LAUNCHER_AGENT_NODE_NAME`: Friendly name for the node

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
2. Start a local agent if one isn't running
3. Register itself as the "local" node

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

- **Trusted LAN Only**: Agents run without authentication by default. Only expose them on trusted networks.
- **Bind to Specific Interface**: Use `LAUNCHER_AGENT_HOST` to bind to a specific IP instead of `0.0.0.0`.
- **Firewall**: Restrict port 8765 to your LAN subnet.

### Usage

#### Dashboard Tab

- **Node Selector** (sidebar): Filter view by specific node or "All Nodes"
- **Running Servers**: Shows all active servers with node badges
- **Models**: Lists all configured models grouped by node
- **Start/Stop**: Control servers on any node

#### Nodes Tab

- **Registered Nodes**: List of all connected nodes with status
- **Test Connection**: Verify agent connectivity
- **Remove Node**: Unregister a node from the dashboard

### Troubleshooting

#### "Connection Failed" when adding node

1. Verify agent is running on the remote node:
   ```bash
   curl http://<node-ip>:8765/health
   ```

2. Check firewall rules on the remote node

3. Verify the agent is binding to the correct interface:
   ```bash
   # Should show 0.0.0.0:8765 or your LAN IP
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

2. Check that the agent is not binding to localhost only:
   - Look for `0.0.0.0:8765` in agent startup logs
   - If it shows `127.0.0.1:8765`, set `LAUNCHER_AGENT_HOST=0.0.0.0`

### API Documentation

When an agent is running, visit `http://<node-ip>:8765/docs` for interactive API documentation.

### License

MIT
