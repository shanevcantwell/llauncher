# llauncher

MCP-first launcher for managing llama.cpp `llama-server` instances.

## Features

- **MCP Tools for LLMs**: List, start, stop, and manage llama-server instances via MCP
- **Script Discovery**: Automatically discovers `launch-*.sh` scripts in `~/.local/bin`
- **Config Management**: Persist model configurations with validation
- **Change Management**: Validate actions before execution (port conflicts, blacklists)
- **Streamlit UI**: Dashboard for human operators with live logs

## Installation

```bash
# Install in development mode
cd llauncher
pip install -e ".[ui]"
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
| `list_models` | List all configured models with status |
| `get_model_config` | Get full configuration for a model |
| `start_server` | Start a llama-server for a model |
| `stop_server` | Stop a running server by port |
| `server_status` | Get status of all running servers |
| `get_server_logs` | Fetch recent logs for a server |
| `update_model_config` | Update a model's configuration |
| `validate_config` | Validate a config without applying |
| `add_model` | Add a new model configuration |
| `remove_model` | Remove a model configuration |

### Streamlit UI

Start the UI:

```bash
llauncher-ui
# or
streamlit run llauncher/ui/app.py
```

### CLI

Discover configured models:

```bash
python -m llauncher discover
```

## Configuration

Models can be configured in two ways:

1. **Launch Scripts**: Create `launch-*.sh` scripts in `~/.local/bin`:

   ```bash
   #!/bin/bash
   llama-server \
     -m /path/to/model.gguf \
     --n-gpu-layers 255 \
     -c 131072 \
     --port 8081
   ```

2. **Config File**: Persist configurations in `~/.llauncher/config.json`

   Persisted configs take precedence over discovered scripts.

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

## License

MIT
