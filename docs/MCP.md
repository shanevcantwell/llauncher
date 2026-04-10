# llauncher MCP Server

The llauncher MCP (Model Context Protocol) server provides programmatic control over llama-server instances, enabling LLM agents and automation scripts to manage model deployments.

## Overview

llauncher exposes 11 MCP tools across three categories:

| Category | Tools |
|----------|-------|
| **Model Discovery** | `list_models`, `get_model_config` |
| **Server Management** | `start_server`, `stop_server`, `swap_server`, `server_status`, `get_server_logs` |
| **Configuration** | `add_model`, `remove_model`, `update_model_config`, `validate_config` |

## Installation

```bash
# Install llauncher with MCP support
pip install -e ".[ui]"  # Includes all dependencies
```

The MCP server is installed as a console script:

```bash
llauncher-mcp --version
```

## Configuration

### Claude Code (Claude Desktop)

Add to your `claude_desktop_config.json` or equivalent MCP configuration:

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

### Custom Installation Path

If llauncher is installed in a virtual environment:

```json
{
  "mcpServers": {
    "llauncher": {
      "command": "/path/to/venv/bin/llauncher-mcp",
      "args": []
    }
  }
}
```

### Zed Editor

In Zed's MCP settings:

```json
{
  "llauncher": {
    "command": "llauncher-mcp"
  }
}
```

### Other MCP Clients

Any MCP-compatible client can connect using stdio transport:

```python
# Python example using mcp client
from mcp.client.stdio import StdioServerParameters, stdio_client

server_params = StdioServerParameters(
    command="llauncher-mcp",
    args=[]
)

async with stdio_client(server_params) as (read, write):
    # Use the client to call tools
    pass
```

## Available Tools

### Model Discovery

#### `list_models`

List all configured models with their current status.

**Input:** None

**Output:**
```json
{
  "models": [
    {
      "name": "mistral-7b",
      "status": "running",
      "port": 8081,
      "model_path": "/models/mistral-7b.gguf",
      "n_gpu_layers": 255,
      "ctx_size": 131072,
      "pid": 12345
    },
    {
      "name": "llama-3.1",
      "status": "stopped",
      "port": 8082,
      "model_path": "/models/llama-3.1.gguf",
      "n_gpu_layers": 255,
      "ctx_size": 131072
    }
  ],
  "count": 2
}
```

**Use Cases:**
- Get an overview of all available models
- Check which models are currently running
- Identify available vs. stopped models

---

#### `get_model_config`

Get the full configuration for a specific model.

**Input:**
```json
{
  "name": "mistral-7b"
}
```

**Output:**
```json
{
  "name": "mistral-7b",
  "config": {
    "name": "mistral-7b",
    "model_path": "/models/mistral-7b.gguf",
    "mmproj_path": null,
    "default_port": 8081,
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    "threads": null,
    "threads_batch": 8,
    "ubatch_size": 512,
    "batch_size": null,
    "flash_attn": "on",
    "no_mmap": false,
    "cache_type_k": null,
    "cache_type_v": null,
    "n_cpu_moe": null,
    "parallel": 1,
    "temperature": null,
    "top_k": null,
    "top_p": null,
    "min_p": null,
    "reverse_prompt": null,
    "mlock": false,
    "extra_args": []
  },
  "status": {
    "status": "running",
    "port": 8081,
    "pid": 12345
  }
}
```

**Use Cases:**
- Inspect detailed configuration before making changes
- Debug model startup issues
- Clone configurations for similar models

---

### Server Management

#### `start_server`

Start a llama-server instance for a specified model.

**Input:**
```json
{
  "model_name": "mistral-7b"
}
```

**Output (Success):**
```json
{
  "success": true,
  "message": "Started mistral-7b on port 8081",
  "pid": 12345
}
```

**Output (Error - Port in Use):**
```json
{
  "success": false,
  "message": "Port 8081 is already in use by llama-3.1"
}
```

**Output (Error - Model Not Found):**
```json
{
  "success": false,
  "message": "Model not found: unknown-model"
}
```

**Validation:**
- Checks if port is available (or auto-allocates if not specified)
- Verifies model path exists
- Respects blacklisted ports
- Checks caller permissions

**Port Allocation:**
- If model has `default_port` set and it's available, uses that port
- Otherwise, auto-allocates from the available port range (8080-8999)
- Respects `BLACKLISTED_PORTS` from environment

**Use Cases:**
- Start a model before making it available to applications
- Restart a model after configuration changes
- Dynamically provision models based on demand

---

#### `stop_server`

Stop a running llama-server by port number.

**Input:**
```json
{
  "port": 8081
}
```

**Output (Success):**
```json
{
  "success": true,
  "message": "Stopped server on port 8081"
}
```

**Output (Error - Not Running):**
```json
{
  "success": false,
  "message": "No server running on port 8081"
}
```

**Use Cases:**
- Free up a port for a different model
- Stop unused models to free resources
- Graceful shutdown before configuration changes

---

#### `swap_server`

**Atomic model swap with rollback guarantee.** Stops any server running on the specified port and starts the new model. If the new model fails to start or become ready, the old model is automatically restored.

**Contract:** When this call returns, a model is serving on the port:
- On success (`success: true`): the new model is serving
- On failure with rollback (`success: false, rolled_back: true`): the old model was restored
- Catastrophic failure (`success: false, rolled_back: false, port_state: "unavailable"`): port is dead, manual intervention required

**Pre-flight Requirements:**
- New model must exist and have a valid path
- New model must not already be running on a different port
- If swapping (port has a server), the old model must have a persisted config (not just a discovered script)
- Old model's path must still exist (for rollback capability)

**Input:**
```json
{
  "port": 8081,
  "model_name": "summarizer-model",
  "timeout": 120
}
```

**Parameters:**
- `port` (required): Port number to swap the model on
- `model_name` (required): Name of the new model to start
- `timeout` (optional, default: 120): Maximum seconds to wait for the new model to become ready

**Output (Success):**
```json
{
  "success": true,
  "port": 8081,
  "previous_model": "coding-model",
  "new_model": "summarizer-model",
  "pid": 12345,
  "rolled_back": false,
  "port_state": "serving"
}
```

**Output (Failure with Rollback):**
```json
{
  "success": false,
  "error": "New model 'summarizer-model' failed to become ready within 120s. Rolled back to 'coding-model'.",
  "rolled_back": true,
  "port_state": "restored",
  "restored_model": "coding-model",
  "port": 8081,
  "startup_logs": ["...", "..."]
}
```

**Output (Pre-flight Validation Error):**
```json
{
  "success": false,
  "error": "Model not found: summarizer-model",
  "port_state": "unchanged"
}
```

**Output (Catastrophic Failure - Both Swap and Rollback Failed):**
```json
{
  "success": false,
  "error": "Swap failed and rollback failed",
  "rolled_back": false,
  "port_state": "unavailable",
  "port": 8081,
  "warning": "PORT IS UNAVAILABLE - manual intervention required",
  "startup_logs": ["...", "..."]
}
```

**Port State Values:**
| Value | Meaning |
|-------|---------|
| `serving` | Success - new model is serving on the port |
| `restored` | Rollback succeeded - old model is serving on the port |
| `unchanged` | Pre-flight validation failed - nothing was touched |
| `unavailable` | **CATASTROPHIC** - both swap and rollback failed, port is dead |

**Timing:**
- This is a **blocking** call that waits for the new model to fully load
- Model weights can take 30-60+ seconds to offload to VRAM
- Set your MCP client timeout accordingly (recommend 180s minimum)
- No polling required - the call doesn't return until the model is ready (or failed)

**Use Cases:**
- Dynamic model switching based on task type (coding → summarizer → coding)
- A/B testing different models on the same endpoint
- Emergency fallback to a smaller/faster model if the primary is too slow
- PreCompact hooks that need to swap brains mid-session

---

#### `server_status`

Get a summary of all running servers.

**Input:** None

**Output:**
```json
{
  "running_servers": [
    {
      "pid": 12345,
      "port": 8081,
      "config_name": "mistral-7b",
      "start_time": "2024-01-15T10:30:00.000000"
    },
    {
      "pid": 12346,
      "port": 8082,
      "config_name": "llama-3.1",
      "start_time": "2024-01-15T11:00:00.000000"
    }
  ],
  "count": 2
}
```

**Use Cases:**
- Quick health check of all running instances
- Monitor resource usage across models
- Identify orphaned processes

---

#### `get_server_logs`

Fetch recent log lines from a running server.

**Input:**
```json
{
  "port": 8081,
  "lines": 50
}
```

**Output:**
```json
{
  "port": 8081,
  "pid": 12345,
  "logs": [
    "[2024-01-15 10:30:00] llama-server started",
    "[2024-01-15 10:30:01] Loading model from /models/mistral-7b.gguf",
    "[2024-01-15 10:30:05] Model loaded successfully",
    "[2024-01-15 10:30:05] Server listening on 0.0.0.0:8081"
  ],
  "line_count": 4
}
```

**Parameters:**
- `port` (required): Port number of the server
- `lines` (optional, default: 100): Number of log lines to retrieve

**Use Cases:**
- Debug startup failures
- Monitor server health
- Check for errors or warnings
- Verify model loading completed successfully

---

### Configuration Management

#### `add_model`

Add a new model configuration to the store.

**Input:**
```json
{
  "config": {
    "name": "gemma-2b",
    "model_path": "/models/gemma-2b.gguf",
    "default_port": 8083,
    "n_gpu_layers": 255,
    "ctx_size": 8192,
    "flash_attn": "on"
  }
}
```

**Output (Success):**
```json
{
  "success": true,
  "message": "Added model gemma-2b",
  "config": {
    "name": "gemma-2b",
    "model_path": "/models/gemma-2b.gguf",
    "default_port": 8083,
    "n_gpu_layers": 255,
    "ctx_size": 8192,
    "flash_attn": "on",
    ...
  }
}
```

**Output (Error - Already Exists):**
```json
{
  "success": false,
  "error": "Model already exists: gemma-2b"
}
```

**Output (Error - Invalid Config):**
```json
{
  "success": false,
  "error": "Validation error: model_path is required"
}
```

**Required Fields:**
- `name`: Unique model identifier
- `model_path`: Absolute path to the .gguf file

**Optional Fields:**
- `mmproj_path`: Path to multimodal projector (for vision models)
- `default_port`: Preferred port (auto-allocates if not specified)
- `n_gpu_layers`: GPU offload layers (default: 255)
- `ctx_size`: Context size (default: 131072)
- `flash_attn`: Flash attention mode ("on", "off", "auto")
- `no_mmap`: Disable memory mapping (default: false)
- And many more...

**Use Cases:**
- Register new models discovered on disk
- Create model presets for common use cases
- Add models that aren't in script form

---

#### `remove_model`

Remove a model configuration from the store.

**Input:**
```json
{
  "name": "gemma-2b"
}
```

**Output (Success):**
```json
{
  "success": true,
  "message": "Removed model gemma-2b"
}
```

**Output (Error - Not Found):**
```json
{
  "success": false,
  "error": "Model not found: gemma-2b"
}
```

**Output (Error - Server Running):**
```json
{
  "success": false,
  "error": "Cannot remove model: server is running on port 8083"
}
```

**Important:** You must stop any running server for the model before removing its configuration.

**Use Cases:**
- Clean up unused model configurations
- Remove models after decommissioning
- Reset configuration for re-adding with different settings

---

#### `update_model_config`

Update an existing model's configuration.

**Input:**
```json
{
  "name": "mistral-7b",
  "config": {
    "default_port": 8090,
    "ctx_size": 65536,
    "flash_attn": "auto"
  }
}
```

**Output (Success):**
```json
{
  "success": true,
  "message": "Updated configuration for mistral-7b",
  "config": {
    "name": "mistral-7b",
    "model_path": "/models/mistral-7b.gguf",
    "default_port": 8090,
    "ctx_size": 65536,
    "flash_attn": "auto",
    ...
  }
}
```

**Output (Error - Not Found):**
```json
{
  "success": false,
  "error": "Model not found: mistral-7b"
}
```

**Updateable Fields:**
- `default_port`: Change the preferred port
- `n_gpu_layers`: Adjust GPU offloading
- `ctx_size`: Modify context window size
- `threads`: Set thread count
- `flash_attn`: Toggle flash attention
- `no_mmap`: Enable/disable memory mapping

**Use Cases:**
- Tune model performance parameters
- Change port assignments
- Update context size requirements
- Adjust GPU memory usage

---

#### `validate_config`

Validate a model configuration without applying it.

**Input:**
```json
{
  "config": {
    "name": "test-model",
    "model_path": "/models/test.gguf",
    "n_gpu_layers": 255,
    "ctx_size": 131072
  }
}
```

**Output (Valid):**
```json
{
  "valid": true,
  "config": {
    "name": "test-model",
    "model_path": "/models/test.gguf",
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    ...
  }
}
```

**Output (Invalid):**
```json
{
  "valid": false,
  "error": "model_path does not exist: /models/test.gguf"
}
```

**Use Cases:**
- Validate configuration before adding
- Check if a model path is accessible
- Verify parameter combinations are valid
- Dry-run configuration changes

---

## Workflow Examples

### Example 1: Start a Model and Verify

```
1. list_models
   → See available models and their status

2. start_server({model_name: "mistral-7b"})
   → Returns success with port and PID

3. server_status
   → Confirm model appears in running list

4. get_server_logs({port: 8081, lines: 20})
   → Verify server started successfully
```

### Example 2: Swap Models on a Port

```
1. server_status
   → Find which model is on port 8081

2. stop_server({port: 8081})
   → Stop the current model

3. start_server({model_name: "llama-3.1"})
   → Start new model (will use 8081 if configured)

4. get_server_logs({port: 8081})
   → Verify new model loaded
```

### Example 3: Add and Configure a New Model

```
1. validate_config({config: {...}})
   → Check configuration is valid

2. add_model({config: {...}})
   → Register the model

3. start_server({model_name: "new-model"})
   → Start the server

4. update_model_config({name: "new-model", config: {ctx_size: 65536}})
   → Tune parameters after testing
```

### Example 4: Debug a Failed Startup

```
1. start_server({model_name: "problematic-model"})
   → Returns error message

2. get_model_config({name: "problematic-model"})
   → Check current configuration

3. get_server_logs({port: <port>, lines: 100})
   → If partially started, check logs for errors

4. validate_config({config: {...}})
   → Verify configuration parameters
```

---

## Integration Patterns

### Automated Model Rotation

Rotate between models based on time of day or load:

```python
import schedule
from mcp_client import MCPClient

client = MCPClient("llauncher")

def rotate_model():
    # Stop current model
    status = client.call_tool("server_status", {})
    for server in status["running_servers"]:
        client.call_tool("stop_server", {"port": server["port"]})

    # Start new model
    client.call_tool("start_server", {"model_name": "night-model"})

schedule.every().day.at("22:00").do(rotate_model)
```

### Health Monitoring

Periodically check model health:

```python
def check_health():
    models = client.call_tool("list_models", {})
    running = [m for m in models["models"] if m["status"] == "running"]

    for model in running:
        logs = client.call_tool("get_server_logs", {"port": model["port"], "lines": 10})
        if "error" in "".join(logs["logs"]).lower():
            alert(f"Errors detected in {model['name']}")
```

### Dynamic Provisioning

Start models on-demand based on requests:

```python
def ensure_model_running(model_name: str):
    models = client.call_tool("list_models", {})
    status = next((m for m in models["models"] if m["name"] == model_name), None)

    if not status or status["status"] != "running":
        result = client.call_tool("start_server", {"model_name": model_name})
        if not result["success"]:
            raise Exception(f"Failed to start {model_name}: {result['message']}")

    return status["port"]
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAMA_SERVER_PATH` | `~/.local/bin/llama-server` | Path to llama-server binary |
| `SCRIPTS_PATH` | `~/.local/bin` | Directory to scan for launch scripts |
| `DEFAULT_PORT` | `8080` | Starting port for auto-allocation |
| `BLACKLISTED_PORTS` | `` | Comma-separated list of reserved ports |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

---

## Configuration Storage

### Persisted Configurations

Model configurations added via `add_model` are stored in `~/.llauncher/config.json`:

```json
{
  "mistral-7b": {
    "name": "mistral-7b",
    "model_path": "/models/mistral-7b.gguf",
    "default_port": 8081,
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    ...
  }
}
```

### Script Discovery

llauncher also discovers `launch-*.sh` scripts in `SCRIPTS_PATH` and parses them as model configurations. Persisted configs take precedence over discovered scripts.

---

## Error Handling

All tools return structured error responses:

```json
{
  "success": false,
  "error": "Detailed error message"
}
```

Common errors:

| Error | Cause | Resolution |
|-------|-------|------------|
| `Model not found` | Model name doesn't exist | Use `list_models` to see available models |
| `Port already in use` | Another server is on that port | Stop the other server or use a different port |
| `Model path does not exist` | .gguf file not found | Verify the path and file existence |
| `Cannot remove model: server is running` | Model has active server | Stop the server first with `stop_server` |
| `Validation error` | Invalid configuration | Use `validate_config` to check before applying |

---

## Security Considerations

### Caller Tracking

All actions are logged with the caller identifier (`mcp`, `ui`, `agent`, etc.). This enables:

- Audit trails for who initiated changes
- Caller-based access control via `ChangeRules`
- Debugging of automated workflows

### Change Rules

llauncher enforces validation rules:

- **Port conflicts**: Prevents multiple models on the same port
- **Blacklisted ports**: Respects configured port blacklists
- **Model whitelists**: Can restrict which models are startable
- **Caller restrictions**: Can block specific callers from performing actions

---

## Troubleshooting

### Server Won't Start

1. Check if the model exists: `list_models`
2. Verify model path exists: `get_model_config`
3. Check for port conflicts: `server_status`
4. Review logs: `get_server_logs`

### Tool Calls Failing

1. Verify MCP server is running: `llauncher-mcp` should be active
2. Check client configuration: Ensure correct command path
3. Review MCP server logs: Check for errors in startup

### Models Not Appearing

1. Run discovery: `python -m llauncher discover`
2. Check SCRIPTS_PATH: Verify scripts exist in configured directory
3. Check config file: `~/.llauncher/config.json` for persisted configs

---

## API Reference

For the HTTP agent API (used in multi-node setups), see the agent documentation at `http://<node>:8765/docs` when an agent is running.

The MCP tools map to these HTTP endpoints:

| MCP Tool | HTTP Endpoint |
|----------|---------------|
| `list_models` | `GET /models` |
| `start_server` | `POST /start/{model_name}` |
| `stop_server` | `POST /stop/{port}` |
| `server_status` | `GET /status` |
| `get_server_logs` | `GET /logs/{port}` |
