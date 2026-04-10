# CLI Implementation Plan for Scripted Model Management

## Context and Motivation

llauncher currently exposes model management functionality through:
- **MCP tools** - Requires MCP client/host setup
- **HTTP agent API** - Requires running agent daemon on port 8765
- **Streamlit UI** - Interactive only, not scriptable

There is **no direct CLI** for start/stop/model operations. The `llauncher` command only supports `discover`, `mcp`, and `ui` subcommands.

This plan adds CLI subcommands that route through the same `LauncherState` core, enabling scripts to manage models without MCP or daemon dependencies.

### Use Case Example

A shell script that swaps models on a specific port:

```bash
#!/bin/bash
# Swap model on port 8081

llauncher stop 8081    # Eject current model
llauncher start new-model --port 8081  # Pull in new model
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    llauncher CLI                            │
│                 (llauncher/__main__.py)                     │
├─────────────────────────────────────────────────────────────┤
│  Commands: list | status | start | stop | logs | add | rm   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  LauncherState                               │
│                (llauncher/state.py)                          │
│                                                              │
│  - models: dict[str, ModelConfig]                           │
│  - running: dict[int, RunningServer]                        │
│  - start_server(model_name, caller, port)                   │
│  - stop_server(port, caller)                                │
│  - get_model_status(model_name)                             │
│  - can_start() / can_stop() validation                      │
│  - record_action() audit logging                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐  ┌──────────┐  ┌──────────────┐
   │  Config  │  │ Process  │  │   Discovery  │
   │  Store   │  │ Manager  │  │    Scripts   │
   └──────────┘  └──────────┘  └──────────────┘
```

---

## Commands to Implement

### 1. `llauncher list`

List all configured models with current status.

**Arguments:**
- `--json` - Output as JSON instead of table
- `--running` - Filter to only running models
- `--stopped` - Filter to only stopped models

**Output (human-readable):**
```
NAME           STATUS    PORT    PID     MODEL PATH
mistral-7b     running   8081    12345   /models/mistral-7b.gguf
llama-3.1      stopped   8082    -       /models/llama-3.1.gguf
gemma-2b       stopped   auto    -       /models/gemma-2b.gguf
```

**Output (JSON):**
```json
{
  "models": [
    {
      "name": "mistral-7b",
      "status": "running",
      "port": 8081,
      "pid": 12345,
      "model_path": "/models/mistral-7b.gguf"
    },
    {
      "name": "llama-3.1",
      "status": "stopped",
      "port": 8082,
      "model_path": "/models/llama-3.1.gguf"
    }
  ],
  "count": 2
}
```

**Implementation:**
```python
state = LauncherState()  # Auto-refreshes on init
models = []
for name, config in state.models.items():
    status_info = state.get_model_status(name)
    models.append({...})  # Build output object
```

**MCP equivalent:** `list_models`

---

### 2. `llauncher status <model-name>`

Get detailed status of a specific model.

**Arguments:**
- `<model-name>` (required) - Name of the model
- `--json` - Output as JSON

**Output (human-readable):**
```
Model: mistral-7b
Status: running
Port: 8081
PID: 12345
Model Path: /models/mistral-7b.gguf
GPU Layers: 255
Context Size: 131072
```

**Output (JSON):**
```json
{
  "name": "mistral-7b",
  "status": {
    "status": "running",
    "port": 8081,
    "pid": 12345
  },
  "config": {
    "model_path": "/models/mistral-7b.gguf",
    "n_gpu_layers": 255,
    "ctx_size": 131072,
    ...
  }
}
```

**Implementation:**
```python
state = LauncherState()
if model_name not in state.models:
    print(f"Error: Model not found: {model_name}", file=sys.stderr)
    sys.exit(1)

status_info = state.get_model_status(model_name)
config = state.models[model_name]
```

**MCP equivalent:** `get_model_config`

---

### 3. `llauncher start <model-name>`

Start a llama-server instance for the specified model.

**Arguments:**
- `<model-name>` (required) - Name of the model to start
- `--port <int>` - Optional port override (uses config default or auto-allocates if omitted)
- `--json` - Output as JSON
- `--quiet` - Suppress output on success

**Output (human-readable, success):**
```
Started mistral-7b on port 8081 (PID 12345)
```

**Output (JSON, success):**
```json
{
  "success": true,
  "message": "Started mistral-7b on port 8081",
  "pid": 12345,
  "port": 8081
}
```

**Output (error):**
```json
{
  "success": false,
  "message": "Port 8081 is already in use by llama-3.1"
}
```

**Exit Codes:**
- `0` - Success
- `1` - Error (model not found, process failed to start)
- `2` - Validation error (port in use, blacklisted port, model path missing)

**Implementation:**
```python
state = LauncherState()
success, message, process = state.start_server(
    model_name=model_name,
    caller="cli",
    port=args.port
)

if success:
    result = {"success": True, "message": message, "pid": process.pid, "port": state.running[<port>].port}
    sys.exit(0)
else:
    # Determine if validation error vs other error
    if "already in use" in message or "blacklisted" in message or "does not exist" in message:
        sys.exit(2)
    sys.exit(1)
```

**MCP equivalent:** `start_server`

---

### 4. `llauncher stop <port>`

Stop a running llama-server by port number.

**Arguments:**
- `<port>` (required) - Port number of the server to stop
- `--json` - Output as JSON
- `--quiet` - Suppress output on success
- `--force` - Skip validation (use with caution)

**Output (human-readable, success):**
```
Stopped server on port 8081 (mistral-7b)
```

**Output (JSON, success):**
```json
{
  "success": true,
  "message": "Stopped server on port 8081",
  "port": 8081,
  "config_name": "mistral-7b"
}
```

**Exit Codes:**
- `0` - Success
- `1` - Error (no server on port, process not found)

**Implementation:**
```python
state = LauncherState()

# Get config_name before stopping for output
config_name = None
if port in state.running:
    config_name = state.running[port].config_name

success, message = state.stop_server(port=port, caller="cli")

if success:
    result = {"success": True, "message": message, "port": port, "config_name": config_name}
    sys.exit(0)
else:
    sys.exit(1)
```

**MCP equivalent:** `stop_server`

---

### 5. `llauncher logs <port>`

Stream recent log lines from a running server.

**Arguments:**
- `<port>` (required) - Port number of the server
- `--lines <int>` - Number of lines to show (default: 100)
- `--follow` - Follow logs in real-time (like `tail -f`)
- `--json` - Output as JSON array

**Output (human-readable):**
```
[2024-01-15 10:30:00] llama-server started
[2024-01-15 10:30:01] Loading model from /models/mistral-7b.gguf
[2024-01-15 10:30:05] Model loaded successfully
[2024-01-15 10:30:05] Server listening on 0.0.0.0:8081
```

**Output (JSON):**
```json
{
  "port": 8081,
  "config_name": "mistral-7b",
  "lines": ["...", "..."],
  "total_lines": 4
}
```

**Implementation:**
```python
from llauncher.core.process import stream_logs

state = LauncherState()
if port not in state.running:
    print(f"Error: No server running on port {port}", file=sys.stderr)
    sys.exit(1)

server = state.running[port]
log_lines = stream_logs(pid=server.pid, lines=args.lines)

if args.json:
    print(json.dumps({"port": port, "config_name": server.config_name, "lines": log_lines}))
else:
    for line in log_lines:
        print(line)
```

**MCP equivalent:** `get_server_logs`

---

### 6. `llauncher add <name>`

Add a new model configuration from a JSON file.

**Arguments:**
- `<name>` (required) - Name for the model (must match config file)
- `--config <path>` - Path to JSON config file
- `--json` - Output as JSON
- `--quiet` - Suppress output on success

**Config file format:**
```json
{
  "name": "mistral-7b",
  "model_path": "/models/mistral-7b.gguf",
  "default_port": 8081,
  "n_gpu_layers": 255,
  "ctx_size": 131072,
  "flash_attn": "on"
}
```

**Output (JSON, success):**
```json
{
  "success": true,
  "message": "Added model mistral-7b",
  "config": {...}
}
```

**Implementation:**
```python
from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig

config_data = json.loads(Path(args.config).read_text())
config = ModelConfig.model_validate(config_data)

if config.name != args.name:
    print(f"Error: Config name '{config.name}' doesn't match argument '{args.name}'", file=sys.stderr)
    sys.exit(1)

ConfigStore.add_model(config)
state = LauncherState()  # Refresh to include new model
state.models[config.name] = config
state.record_action("add", config.name, "cli", "success", "Model added via CLI")
```

**MCP equivalent:** `add_model`

---

### 7. `llauncher rm <name>`

Remove a model configuration.

**Arguments:**
- `<name>` (required) - Name of the model to remove
- `--json` - Output as JSON
- `--force` - Remove even if server is running (dangerous)

**Output (JSON, success):**
```json
{
  "success": true,
  "message": "Removed model mistral-7b"
}
```

**Implementation:**
```python
from llauncher.core.config import ConfigStore

state = LauncherState()

if name not in state.models:
    print(f"Error: Model not found: {name}", file=sys.stderr)
    sys.exit(1)

# Check if running
for port, server in state.running.items():
    if server.config_name == name:
        if not args.force:
            print(f"Error: Cannot remove model: server is running on port {port}", file=sys.stderr)
            sys.exit(1)

ConfigStore.remove_model(name)
del state.models[name]
state.record_action("remove", name, "cli", "success", "Model removed via CLI")
```

**MCP equivalent:** `remove_model`

---

## Implementation File: `llauncher/__main__.py`

### Structure

```python
"""Entry points for llauncher."""

import argparse
import json
import sys
from pathlib import Path

from llauncher.state import LauncherState
from llauncher.core.config import ConfigStore
from llauncher.core.process import stream_logs
from llauncher.models.config import ModelConfig


def main():
    """Main entry point for llauncher CLI."""
    parser = argparse.ArgumentParser(
        description="llauncher - llama.cpp server launcher and manager"
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- list command ---
    list_parser = subparsers.add_parser("list", help="List all configured models")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--running", action="store_true", help="Show only running models")
    list_parser.add_argument("--stopped", action="store_true", help="Show only stopped models")

    # --- status command ---
    status_parser = subparsers.add_parser("status", help="Get status of a specific model")
    status_parser.add_argument("model", help="Model name")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- start command ---
    start_parser = subparsers.add_parser("start", help="Start a model server")
    start_parser.add_argument("model", help="Model name to start")
    start_parser.add_argument("--port", type=int, help="Port to bind (optional)")
    start_parser.add_argument("--json", action="store_true", help="Output as JSON")
    start_parser.add_argument("--quiet", action="store_true", help="Suppress output on success")

    # --- stop command ---
    stop_parser = subparsers.add_parser("stop", help="Stop a server by port")
    stop_parser.add_argument("port", type=int, help="Port number of the server to stop")
    stop_parser.add_argument("--json", action="store_true", help="Output as JSON")
    stop_parser.add_argument("--quiet", action="store_true", help="Suppress output on success")
    stop_parser.add_argument("--force", action="store_true", help="Skip validation")

    # --- logs command ---
    logs_parser = subparsers.add_parser("logs", help="View server logs")
    logs_parser.add_argument("port", type=int, help="Port number of the server")
    logs_parser.add_argument("--lines", type=int, default=100, help="Number of lines (default: 100)")
    logs_parser.add_argument("--follow", action="store_true", help="Follow logs in real-time")
    logs_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- add command ---
    add_parser = subparsers.add_parser("add", help="Add a new model configuration")
    add_parser.add_argument("name", help="Model name")
    add_parser.add_argument("--config", required=True, help="Path to JSON config file")
    add_parser.add_argument("--json", action="store_true", help="Output as JSON")
    add_parser.add_argument("--quiet", action="store_true", help="Suppress output on success")

    # --- rm command ---
    rm_parser = subparsers.add_parser("rm", help="Remove a model configuration")
    rm_parser.add_argument("name", help="Model name to remove")
    rm_parser.add_argument("--json", action="store_true", help="Output as JSON")
    rm_parser.add_argument("--force", action="store_true", help="Remove even if running")

    # --- discover command (existing) ---
    discover_parser = subparsers.add_parser("discover", help="Discover launch scripts")

    # --- mcp command (existing) ---
    mcp_parser = subparsers.add_parser("mcp", help="Run MCP server")

    # --- ui command (existing) ---
    ui_parser = subparsers.add_parser("ui", help="Run UI")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "rm":
        cmd_rm(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "mcp":
        cmd_mcp(args)
    elif args.command == "ui":
        cmd_ui(args)
    else:
        parser.print_help()
        sys.exit(1)


def cmd_list(args):
    """List all models."""
    state = LauncherState()
    models = []

    for name, config in state.models.items():
        status_info = state.get_model_status(name)

        # Apply filters
        if args.running and status_info.get("status") != "running":
            continue
        if args.stopped and status_info.get("status") != "stopped":
            continue

        port_info = status_info.get("port") if status_info.get("status") == "running" else (config.default_port or "auto")

        models.append({
            "name": name,
            "status": status_info.get("status", "unknown"),
            "port": port_info,
            "pid": status_info.get("pid"),
            "model_path": config.model_path,
        })

    if args.json:
        print(json.dumps({"models": models, "count": len(models)}, indent=2))
    else:
        # Print table
        print(f"{'NAME':<15} {'STATUS':<10} {'PORT':<10} {'PID':<10} MODEL PATH")
        for m in models:
            pid = str(m["pid"]) if m["pid"] else "-"
            print(f"{m['name']:<15} {m['status']:<10} {str(m['port']):<10} {pid:<10} {m['model_path']}")


def cmd_status(args):
    """Get model status."""
    state = LauncherState()

    if args.model not in state.models:
        print(f"Error: Model not found: {args.model}", file=sys.stderr)
        sys.exit(1)

    config = state.models[args.model]
    status_info = state.get_model_status(args.model)

    if args.json:
        result = {
            "name": args.model,
            "status": status_info,
            "config": config.to_dict(),
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Model: {args.model}")
        print(f"Status: {status_info.get('status', 'unknown')}")
        if status_info.get("status") == "running":
            print(f"Port: {status_info.get('port')}")
            print(f"PID: {status_info.get('pid')}")
        else:
            print(f"Default Port: {config.default_port or 'auto-allocate'}")
        print(f"Model Path: {config.model_path}")
        print(f"GPU Layers: {config.n_gpu_layers}")
        print(f"Context Size: {config.ctx_size}")


def cmd_start(args):
    """Start a model server."""
    state = LauncherState()

    if args.model not in state.models:
        print(f"Error: Model not found: {args.model}", file=sys.stderr)
        sys.exit(1)

    success, message, process = state.start_server(
        model_name=args.model,
        caller="cli",
        port=args.port
    )

    if success:
        # Find the port that was allocated
        allocated_port = None
        for port, server in state.running.items():
            if server.config_name == args.model:
                allocated_port = port
                break

        if args.json:
            result = {
                "success": True,
                "message": message,
                "pid": process.pid,
                "port": allocated_port,
            }
            print(json.dumps(result, indent=2))
        elif not args.quiet:
            print(f"Started {args.model} on port {allocated_port} (PID {process.pid})")
        sys.exit(0)
    else:
        # Determine exit code based on error type
        if "already in use" in message or "blacklisted" in message or "does not exist" in message:
            exit_code = 2  # Validation error
        else:
            exit_code = 1  # Other error

        if args.json:
            print(json.dumps({"success": False, "message": message}, indent=2))
        else:
            print(f"Error: {message}", file=sys.stderr)
        sys.exit(exit_code)


def cmd_stop(args):
    """Stop a server."""
    state = LauncherState()

    config_name = None
    if args.port in state.running:
        config_name = state.running[args.port].config_name

    success, message = state.stop_server(port=args.port, caller="cli")

    if success:
        if args.json:
            result = {
                "success": True,
                "message": message,
                "port": args.port,
                "config_name": config_name,
            }
            print(json.dumps(result, indent=2))
        elif not args.quiet:
            print(f"Stopped server on port {args.port} ({config_name or 'unknown'})")
        sys.exit(0)
    else:
        if args.json:
            print(json.dumps({"success": False, "message": message}, indent=2))
        else:
            print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)


def cmd_logs(args):
    """View server logs."""
    state = LauncherState()

    if args.port not in state.running:
        print(f"Error: No server running on port {args.port}", file=sys.stderr)
        sys.exit(1)

    server = state.running[args.port]
    log_lines = stream_logs(pid=server.pid, lines=args.lines)

    if args.json:
        result = {
            "port": args.port,
            "config_name": server.config_name,
            "lines": log_lines,
            "total_lines": len(log_lines),
        }
        print(json.dumps(result, indent=2))
    else:
        for line in log_lines:
            print(line)

    # TODO: Implement --follow with subprocess tail or polling


def cmd_add(args):
    """Add a model configuration."""
    config_data = json.loads(Path(args.config).read_text())
    config = ModelConfig.model_validate(config_data)

    if config.name != args.name:
        print(f"Error: Config name '{config.name}' doesn't match argument '{args.name}'", file=sys.stderr)
        sys.exit(1)

    state = LauncherState()

    if args.name in state.models:
        print(f"Error: Model already exists: {args.name}", file=sys.stderr)
        sys.exit(1)

    ConfigStore.add_model(config)
    state.models[args.name] = config
    state.record_action("add", args.name, "cli", "success", "Model added via CLI")

    if args.json:
        result = {
            "success": True,
            "message": f"Added model {args.name}",
            "config": config.to_dict(),
        }
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        print(f"Added model {args.name}")
    sys.exit(0)


def cmd_rm(args):
    """Remove a model configuration."""
    state = LauncherState()

    if args.name not in state.models:
        print(f"Error: Model not found: {args.name}", file=sys.stderr)
        sys.exit(1)

    # Check if running
    for port, server in state.running.items():
        if server.config_name == args.name:
            if not args.force:
                print(f"Error: Cannot remove model: server is running on port {port}", file=sys.stderr)
                sys.exit(1)

    ConfigStore.remove_model(args.name)
    del state.models[args.name]
    state.record_action("remove", args.name, "cli", "success", "Model removed via CLI")

    if args.json:
        result = {
            "success": True,
            "message": f"Removed model {args.name}",
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Removed model {args.name}")
    sys.exit(0)


def cmd_discover(args):
    """Discover launch scripts (existing functionality)."""
    from llauncher.core.discovery import discover_scripts

    configs = discover_scripts()
    for config in configs:
        print(f"\n{config.name}:")
        print(f"  Model: {config.model_path}")
        print(f"  Default Port: {config.default_port or 'Auto-allocate'}")
        print(f"  GPU Layers: {config.n_gpu_layers}")
        print(f"  Context: {config.ctx_size}")


def cmd_mcp(args):
    """Run MCP server (existing functionality)."""
    from llauncher.mcp.server import main as mcp_main
    mcp_main()


def cmd_ui(args):
    """Run UI (existing functionality)."""
    print("Use 'llauncher-ui' command or 'streamlit run llauncher/ui/app.py'")
    sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Testing Plan

### Unit Tests

File: `tests/unit/test_cli.py`

```python
import pytest
from unittest.mock import Mock, patch
import json
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from llauncher.__main__ import main


class TestCLIList:
    def test_list_json_output(self, mocker):
        mock_state = mocker.patch("llauncher.__main__.LauncherState")
        mock_state.return_value.models = {
            "test-model": Mock(
                name="test-model",
                model_path="/test/model.gguf",
                default_port=8080
            )
        }
        mock_state.return_value.get_model_status.return_value = {
            "status": "running", "port": 8080, "pid": 12345
        }

        main(["list", "--json"])
        # Verify JSON output


class TestCLIStart:
    def test_start_success(self, mocker):
        mock_state = mocker.patch("llauncher.__main__.LauncherState")
        mock_process = Mock(pid=12345)
        mock_state.return_value.start_server.return_value = (True, "Started", mock_process)
        mock_state.return_value.models = {"test": Mock()}
        mock_state.return_value.running = {8080: Mock(config_name="test")}

        main(["start", "test", "--json"])
        # Verify output and exit code


class TestCLIStop:
    def test_stop_success(self, mocker):
        mock_state = mocker.patch("llauncher.__main__.LauncherState")
        mock_state.return_value.stop_server.return_value = (True, "Stopped")
        mock_state.return_value.running = {8080: Mock(config_name="test")}

        main(["stop", "8080", "--json"])
        # Verify output
```

### Integration Tests

File: `tests/integration/test_cli.py`

```python
import pytest
import subprocess
import json


class TestCLIIntegration:
    def test_list_command(self):
        result = subprocess.run(["llauncher", "list", "--json"], capture_output=True, text=True)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "models" in data

    def test_start_stop_cycle(self, test_model_config):
        # Start model
        result = subprocess.run(["llauncher", "start", test_model_config["name"], "--json"],
                               capture_output=True, text=True)
        assert result.returncode == 0
        start_data = json.loads(result.stdout)
        assert start_data["success"]

        # Verify running
        result = subprocess.run(["llauncher", "list", "--json"], capture_output=True, text=True)
        data = json.loads(result.stdout)
        running = [m for m in data["models"] if m["status"] == "running"]
        assert len(running) > 0

        # Stop model
        port = start_data["port"]
        result = subprocess.run(["llauncher", "stop", str(port), "--json"],
                               capture_output=True, text=True)
        assert result.returncode == 0
```

### Manual Testing Checklist

```bash
# 1. List all models
llauncher list
llauncher list --json
llauncher list --running

# 2. Get model status
llauncher status <model-name>
llauncher status <model-name> --json

# 3. Start a model
llauncher start <model-name>
llauncher start <model-name> --port 8081
llauncher start <model-name> --json
llauncher start <model-name> --quiet

# 4. Stop a model
llauncher stop <port>
llauncher stop <port> --json
llauncher stop <port> --quiet

# 5. View logs
llauncher logs <port>
llauncher logs <port> --lines 50
llauncher logs <port> --json

# 6. Add a model
llauncher add my-model --config /path/to/config.json
llauncher add my-model --config /path/to/config.json --json

# 7. Remove a model
llauncher rm <model-name>
llauncher rm <model-name> --force

# 8. Error cases
llauncher start nonexistent-model  # Exit code 1
llauncher start <model> --port 80  # Exit code 2 (blacklisted)
llauncher stop 9999  # Exit code 1 (not running)
```

---

## Documentation Updates

### Update `docs/MCP.md`

Add a new section "CLI Interface" after the MCP configuration section:

```markdown
## CLI Interface

For scripted model management without MCP or daemon dependencies:

### Commands

| Command | Description |
|---------|-------------|
| `llauncher list` | List all configured models with status |
| `llauncher status <name>` | Get detailed status of a model |
| `llauncher start <name>` | Start a model server |
| `llauncher stop <port>` | Stop a server by port |
| `llauncher logs <port>` | View server logs |
| `llauncher add <name>` | Add model from config file |
| `llauncher rm <name>` | Remove model configuration |

### Scripted Model Swap

```bash
#!/bin/bash
PORT=8081

# Stop current model
llauncher stop $PORT --quiet

# Start new model
llauncher start new-model --port $PORT --json
```

### Exit Codes

- `0`: Success
- `1`: Error (model not found, process failed)
- `2`: Validation error (port in use, blacklisted, path missing)
```

### Update `README.md`

Add CLI section after MCP section:

```markdown
### CLI

For scripted automation:

```bash
llauncher list                    # List all models
llauncher start mistral-7b        # Start a model
llauncher stop 8081               # Stop server on port
llauncher logs 8081               # View logs
```
```

---

## Dependencies

No new dependencies required. Uses existing:
- `argparse` (stdlib)
- `json` (stdlib)
- `LauncherState` (llauncher.state)
- `ConfigStore` (llauncher.core.config)
- `stream_logs` (llauncher.core.process)
- `ModelConfig` (llauncher.models.config)

---

## Future Enhancements (Out of Scope)

- `--follow` flag for `logs` command (real-time tail)
- `llauncher update <name>` for inline config updates
- `llauncher validate <name>` for validation-only check
- Interactive mode with prompts
- Batch operations (start/stop multiple models)
- YAML config file support (in addition to JSON)
