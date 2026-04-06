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
| `server_status` | Get status summary of all running servers |
| `get_server_logs` | Fetch recent log lines from a running server |
| `update_model_config` | Update an existing model's configuration |
| `validate_config` | Validate a configuration without applying it |
| `add_model` | Add a new model configuration to the store |
| `remove_model` | Remove a model configuration (blocks if running) |

### Streamlit UI

Start the UI:

```bash
llauncher-ui
# or
streamlit run llauncher/ui/app.py
```

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

## License

MIT
