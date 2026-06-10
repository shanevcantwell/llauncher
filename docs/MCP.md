# llauncher MCP Server

The llauncher MCP (Model Context Protocol) server provides programmatic control over llama-server instances, enabling LLM agents and automation scripts to manage model deployments.

MCP is llauncher's canonical surface. The HTTP Agent (port 8765 by default) exposes the same verbs over REST for multi-node setups (ADR-009 hub-spoke). The `llauncher` Typer CLI and Streamlit UI are human-facing consumers of the same `operations/` service layer (ADR-008). Adding a verb to `operations/` surfaces it across all four boundaries.

## Overview

llauncher exposes 13 MCP tools across three categories:

| Category | Tools |
|----------|-------|
| **Model Discovery** | `list_models`, `get_model_config` |
| **Server Management** | `start_server`, `stop_server`, `swap_server`, `cancel_server`, `server_status`, `get_server_logs`, `list_orphans` |
| **Configuration** | `add_model`, `delete_model`, `update_model_config`, `validate_config` |

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
      "identification": {
        "name": "mistral-7b",
        "model_path": "/models/mistral-7b.gguf"
      },
      "status": {
        "state": "running",
        "port": 8081,
        "pid": 12345
      }
    },
    {
      "identification": {
        "name": "llama-3.1",
        "model_path": "/models/llama-3.1.gguf"
      },
      "status": {
        "state": "stopped",
        "port": null
      }
    }
  ],
  "count": 2
}
```

The value to pass as `model_name` to `start_server` / `swap_server` is `identification.name` exactly as returned — do not concatenate it with `status.port` or any other field.

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
    "extra_args": ""
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

Start a llama-server instance for a specified model on a specified port. Per ADR-010, both `model_name` and `port` are required; there is no auto-allocation, no env-var fallback, and no per-config preferred port. Use `swap_server` if a different model is already running on that port.

**Input:**
```json
{
  "model_name": "mistral-7b",
  "port": 8081
}
```

**Required parameters:**
- `model_name` (string): exact name from `list_models` (`identification.name`).
- `port` (integer): port to bind. Required at every API boundary (ADR-010).

**Output (Success):**
```json
{
  "success": true,
  "action": "started",
  "port": 8081,
  "model": "mistral-7b",
  "pid": 12345
}
```

**Output (Error - Port Occupied by Different Model):**
```json
{
  "success": false,
  "action": "rejected_occupied",
  "port": 8081,
  "current_model": "llama-3.1"
}
```

**Output (Error - Model Not Found):**
```json
{
  "success": false,
  "action": "error",
  "error": "Model not found: unknown-model"
}
```

**Validation:**
- Verifies the port is not occupied by a different model (use `swap_server` for that case)
- Verifies the model exists in `ConfigStore` and its `model_path` resolves
- Respects blacklisted ports (`BLACKLISTED_PORTS` env)
- Checks caller permissions via `ChangeRules`

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
- New model must exist in `ConfigStore` and have a valid `model_path`
- Old model's path must still exist (for rollback capability)
- Port must not be empty (use `start_server` for that case — `action='rejected_empty'`)

**Input:**
```json
{
  "port": 8081,
  "model_name": "summarizer-model"
}
```

**Parameters:**
- `port` (required, integer): port number to swap the model on
- `model_name` (required, string): name of the new model to start

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

#### `delete_model`

Delete a model configuration from the store (ADR-008 §4.1). Idempotent on a missing name and refuses to delete a model that is currently running.

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
  "action": "deleted",
  "model": "gemma-2b"
}
```

**Output (Not Found - Idempotent Success):**
```json
{
  "success": true,
  "action": "not_found",
  "model": "gemma-2b"
}
```

**Output (Error - Server Running):**
```json
{
  "success": false,
  "action": "rejected_in_use",
  "model": "gemma-2b",
  "in_use_port": 8083
}
```

**Important:** You must stop or swap the running server for the model before deleting its configuration.

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
- `n_gpu_layers`: Adjust GPU offloading
- `ctx_size`: Modify context window size
- `threads`: Set thread count
- `flash_attn`: Toggle flash attention
- `no_mmap`: Enable/disable memory mapping
- `extra_args`: Additional command-line arguments (subject to the managed-flag deny-list)

Per ADR-010, port is a call-site argument and is not persisted in `ModelConfig` — `default_port` is silently dropped if supplied here.

**Use Cases:**
- Tune model performance parameters
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

2. start_server({model_name: "mistral-7b", port: 8081})
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

2. swap_server({port: 8081, model_name: "llama-3.1"})
   → Atomic five-phase swap with rollback (ADR-011);
     no need to stop first

3. get_server_logs({port: 8081})
   → Verify new model loaded
```

### Example 3: Add and Configure a New Model

```
1. validate_config({config: {...}})
   → Check configuration is valid

2. add_model({config: {...}})
   → Register the model

3. start_server({model_name: "new-model", port: 8084})
   → Start the server on the chosen port

4. update_model_config({name: "new-model", config: {ctx_size: 65536}})
   → Tune parameters after testing
```

### Example 4: Debug a Failed Startup

```
1. start_server({model_name: "problematic-model", port: 8085})
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

    # Start new model on the freed port
    client.call_tool("start_server", {"model_name": "night-model", "port": 8081})

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
def ensure_model_running(model_name: str, port: int):
    """Caller picks the port (ADR-010); no auto-allocation."""
    models = client.call_tool("list_models", {})
    status = next((m for m in models["models"] if m["identification"]["name"] == model_name), None)

    if not status or status["status"]["state"] != "running":
        result = client.call_tool("start_server", {"model_name": model_name, "port": port})
        if not result["success"]:
            raise Exception(f"Failed to start {model_name}: {result.get('error') or result.get('action')}")

    return port
```

---

## Environment Variables

The v2 `LLAUNCHER_*` env-var family (per ADR-008 / ADR-013):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAUNCHER_RUN_DIR` | `~/.llauncher/run` | Per-port lockfile and in-flight marker directory |
| `LLAUNCHER_AUDIT_PATH` | `~/.llauncher/audit.jsonl` | JSON Lines audit log (commanded vs. observed) |
| `LLAUNCHER_LOG_DIR` | `~/.llauncher/logs` | Per-server log directory (append mode, ADR-013) |
| `LLAUNCHER_LOG_MAX_BYTES` | `52428800` (50 MiB) | Per-log rotation threshold |
| `LLAUNCHER_LOG_KEEP` | `3` | Retained rotated log files per server |
| `LLAUNCHER_FOOTER_CACHE_S` | `1.0` | `/footer-context/{port}` TTL (seconds; `<= 0` disables) |
| `LLAUNCHER_AGENT_HOST` | `127.0.0.1` | HTTP Agent bind host. Non-loopback requires a token. |
| `LLAUNCHER_AGENT_PORT` | `8765` | HTTP Agent listen port |
| `LLAUNCHER_AGENT_NODE_NAME` | hostname | Friendly node identifier |
| `LLAUNCHER_AGENT_TOKEN` | — | Required when binding off-loopback (ADR-003); `-` reads stdin |
| `BLACKLISTED_PORTS` | `` | Comma-separated list of reserved ports |

---

## Configuration Storage

### Persisted Configurations

Model configurations added via `add_model` are stored in `~/.llauncher/config.json`:

```json
{
  "mistral-7b": {
    "name": "mistral-7b",
    "model_path": "/models/mistral-7b.gguf",
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    ...
  }
}
```

`ConfigStore` is the single source of truth for model configurations in v2 — there is no script-based discovery fallback. Legacy `default_port` / `port` / `host` keys in the JSON are silently dropped on load (`ModelConfig.from_dict_unvalidated`).

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

1. Check the config file: `~/.llauncher/config.json` is the single source of truth in v2 (no script-based discovery fallback)
2. Add the missing entry via `add_model`, `llauncher`-CLI editing of the JSON, or the Streamlit Models tab
3. If MCP returns stale results, recall that read tools refresh on every call — a stale response means the underlying file has not been updated

---

## API Reference

For the HTTP agent API (used in multi-node setups), see the agent documentation at `http://<node>:8765/docs` when an agent is running.

The MCP tools map to these HTTP endpoints (all port-keyed per ADR-010; `routing.py`):

| MCP Tool | HTTP Endpoint |
|----------|---------------|
| `list_models` | `GET /models` |
| `get_model_config` | `GET /models` (filter client-side) |
| `start_server` | `POST /start/{port}` (body: `{model_name}`) |
| `stop_server` | `POST /stop/{port}` |
| `swap_server` | `POST /swap/{port}` (body: `{model_name}`) |
| `cancel_server` | `POST /cancel/{port}` |
| `server_status` | `GET /status` |
| `get_server_logs` | `GET /logs/{port}` |
| `list_orphans` | `GET /orphans` |
| `delete_model` | `DELETE /models/{model_name}` |
| (footer) | `GET /footer-context/{port}` |
| (health probe) | `GET /models/health`, `GET /models/health/{model_name}` |
