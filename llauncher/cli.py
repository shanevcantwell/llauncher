"""CLI for managing llama.cpp server instances via llauncher.

Provides a Typer-based command-line interface with subcommand groups:
- model: list, info
- server: start, stop, status
- node: add, list, remove, status
- config: path, validate

Output uses Rich tables with color-coded status indicators and supports --json for machine-readable output.
"""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from llauncher.core.config import CONFIG_PATH, ConfigStore
from llauncher.models.config import ModelConfig
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.node import RemoteNode
from llauncher.state import LauncherState

app = typer.Typer(
    name="llauncher",
    help="CLI for managing llama.cpp server instances",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

console = Console()

STATUS_COLOR = {
    "running": "green bold",
    "online": "green bold",
    "serving": "green bold",
    "stopped": "yellow",
    "offline": "red",
    "error": "red bold",
}


def _color(text: str, status: str = "") -> Text:
    """Return a Rich Text with color based on status keyword."""
    if status:
        colour = STATUS_COLOR.get(status.lower(), "white")
    else:
        # Try to infer from text
        for key, style in STATUS_COLOR.items():
            if key in str(text).lower():
                colour = style
                break
        else:
            colour = "white"
    return Text(str(text), style=colour)


def _print_table(headers: list[str], rows: list[list], title: str | None = None) -> None:
    """Render tabular data as a Rich table and print to console."""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for h in headers:
        table.add_column(h, style="dim")
    for row in rows:
        # Apply colour where we recognise status keywords
        styled = []
        for v in row:
            s = str(v).lower()
            if s == "running" or s == "online" or s == "serving":
                styled.append(_color(v, s))
            elif s in ("stopped",):
                styled.append(_color(v, s))
            elif s == "offline" or s == "error":
                styled.append(_color(v, s))
            else:
                styled.append(Text(str(v)))
        table.add_row(*styled)
    console.print(table)


def _json_output(data) -> None:
    """Pretty-print data as JSON."""
    console.print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# model subcommands
# ---------------------------------------------------------------------------

model_app = typer.Typer(name="model", help="Manage model configurations")


@model_app.command("list")
def list_models(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """List all configured models."""
    names = ConfigStore.list_models()
    if json:
        _json_output(names)
        return

    headers = ["NAME"]
    rows = [[name] for name in names]
    _print_table(headers, rows, title="Configured Models")


@model_app.command("info")
def model_info(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the model to inspect"),
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show detailed information for a single model."""
    config = ConfigStore.get_model(name)

    if config is None:
        console.print(f"[red]Model '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    if json:
        _json_output(config.to_dict())
        return

    headers = ["KEY", "VALUE"]
    cfg_dict = config.model_dump()
    # Omit the internal flag used during deserialization
    cfg_dict.pop("_skip_path_validation", None)
    rows = [[k, str(v)] for k, v in cfg_dict.items()]
    _print_table(headers, rows, title=f"Model: {name}")


app.add_typer(model_app)

# ---------------------------------------------------------------------------
# server subcommands
# ---------------------------------------------------------------------------

server_app = typer.Typer(name="server", help="Manage running server processes")


@server_app.command("start")
def start_server(
    name: str = typer.Argument(..., help="Name of the model to start"),
    port: int | None = typer.Option(None, "--port", "-p", help="Optional port override (default: auto-allocate)"),
    caller: str = typer.Option("cli", hidden=True),
) -> None:
    """Start a server for the given model."""
    state = LauncherState()
    ok, msg, _ = state.start_server(name, caller=caller, port=port)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "running"))


@server_app.command("stop")
def stop_server(
    ctx: typer.Context,
    port: int = typer.Argument(..., help="Port of the server to stop"),
    caller: str = typer.Option("cli", hidden=True),
) -> None:
    """Stop a running server."""
    state = LauncherState()
    ok, msg = state.stop_server(port, caller=caller)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "stopped"))


@server_app.command("status")
def server_status(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show status of all running servers."""
    state = LauncherState()

    if json:
        result = {}
        for port_num, srv in state.running.items():
            result[str(port_num)] = srv.to_dict()
        _json_output(result)
        return

    if not state.running:
        console.print("[yellow]No servers running.[/yellow]")
        return

    headers = ["PORT", "MODEL", "PID", "UPTIME"]
    rows: list[list] = []
    for port_num, srv in sorted(state.running.items()):
        secs = srv.uptime_seconds()
        if secs >= 3600:
            uptime = f"{secs // 3600}h {(secs % 3600) // 60}m"
        elif secs >= 60:
            uptime = f"{secs // 60}m {secs % 60}s"
        else:
            uptime = f"{secs}s"
        rows.append([str(port_num), srv.config_name, str(srv.pid), uptime])

    _print_table(headers, rows, title="Running Servers")


app.add_typer(server_app)

# ---------------------------------------------------------------------------
# node subcommands
# ---------------------------------------------------------------------------

node_app = typer.Typer(name="node", help="Manage remote llauncher agent nodes")


@node_app.command("add")
def add_node(
    name: str = typer.Argument(..., help="Unique name for the node"),
    host: str = typer.Option(..., "--host", "-h", help="Hostname or IP address of the node"),
    port: int | None = typer.Option(None, "--port", "-p", help="Agent port (default: 8765)"),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key for authentication"),
) -> None:
    """Register a new llauncher agent node."""
    registry = NodeRegistry()
    actual_port = port or 8765
    ok, msg = registry.add_node(name=name, host=host, port=actual_port, api_key=api_key)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "online"))


@node_app.command("list")
def list_nodes(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """List all registered nodes."""
    registry = NodeRegistry()

    if json:
        _json_output(registry.to_dict())
        return

    headers = ["NAME", "HOST", "PORT", "STATUS"]
    rows: list[list] = []
    for node in registry._nodes.values():
        status_val = str(node.status.value) if hasattr(node, 'status') else "unknown"
        rows.append([node.name, node.host, str(node.port), status_val])

    _print_table(headers, rows, title="Registered Nodes")


@node_app.command("remove")
def remove_node(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the node to remove"),
) -> None:
    """Remove a registered node."""
    registry = NodeRegistry()
    ok, msg = registry.remove_node(name)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "stopped"))


@node_app.command("status")
def node_status(
    ctx: typer.Context,
    all_nodes: bool = typer.Option(False, "--all", "-a", help="Include offline/error nodes"),
    json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show status of registered nodes (online only by default)."""
    registry = NodeRegistry()

    # Ping all to refresh statuses
    for node_name in list(registry._nodes.keys()):
        try:
            registry.get_node(node_name).ping()
        except Exception:
            pass  # keep current status if ping fails completely

    if json:
        result = {}
        target_nodes = registry._nodes if all_nodes else {n: nd for n, nd in registry._nodes.items() if nd.status.value == "online"}
        for node_name, node in target_nodes.items():
            detail = {
                "host": node.host,
                "port": node.port,
                "has_api_key": bool(node.api_key),
                "status": node.status.value,
                "last_seen": node.last_seen.isoformat() if node.last_seen else None,
                "error_message": node._error_message,
            }
            result[node_name] = detail
        _json_output(result)
        return

    target_nodes = registry._nodes if all_nodes else {n: nd for n, nd in registry._nodes.items() if nd.status.value == "online"}

    headers = ["NAME", "HOST", "PORT", "STATUS"]
    rows: list[list] = []
    for node_name, node in target_nodes.items():
        status_val = str(node.status.value)
        rows.append([node_name, node.host, str(node.port), status_val])

    if not rows:
        console.print("[yellow]No nodes registered.[/yellow]")
        return

    _print_table(headers, rows, title="Node Status")


app.add_typer(node_app)

# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------

config_app = typer.Typer(name="config", help="Configuration management utilities")


@config_app.command("path")
def config_path() -> None:
    """Print the path to the llauncher configuration file."""
    console.print(f"[green]{CONFIG_PATH}[/green]")


@config_app.command("validate")
def validate_config(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the model to validate"),
) -> None:
    """Validate a model configuration without starting a server."""
    config = ConfigStore.get_model(name)

    if config is None:
        console.print(f"[red]Model '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    # Basic field validation (re-instantiate to catch schema errors)
    try:
        validated = ModelConfig.model_validate(config.to_dict())  # type: ignore[arg-type]
        console.print(f"[green]✓[/green] Model '{name}' configuration is valid.")
    except Exception as e:
        console.print(f"[red]✗ Validation failed for '{name}': {e}[/red]")
        raise typer.Exit(code=1)


app.add_typer(config_app)
