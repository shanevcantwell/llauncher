"""CLI for managing llama.cpp server instances via llauncher.

Provides a Typer-based command-line interface with subcommand groups:
- model: list, info, remove
- server: start, stop, swap, status
- node: add, list, remove, status
- config: path, validate

Output uses Rich tables with color-coded status indicators and supports --json for machine-readable output.
"""

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    name="llauncher",
    help="CLI for managing llama.cpp server instances",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Root callback: --state-dir (issue #215)
# ---------------------------------------------------------------------------
#
# ``llauncher.core.settings`` resolves ``LAUNCHER_STATE_DIR`` (and every
# config/state path derived from it — ``config.CONFIG_PATH``,
# ``registry.NODES_FILE``, ``LAUNCHER_RUN_DIR``, ...) at *module import
# time*. For ``--state-dir`` to win over the env var, this callback must
# set ``LAUNCHER_STATE_DIR`` in ``os.environ`` before any of those modules
# are first imported in this process. That's why every command below
# imports its config/state/registry dependencies *lazily*, inside the
# function body, rather than at module scope: this callback always runs
# before Typer dispatches to a subcommand, so as long as nothing above
# this line has already imported the settings chain, the subcommand's
# lazy import is the first one — and it sees this override.
@app.callback()
def main(
    state_dir: Optional[Path] = typer.Option(
        None,
        "--state-dir",
        help=(
            "Override the llauncher state/config directory for this "
            "invocation. Precedence: --state-dir > LAUNCHER_STATE_DIR env "
            "> ~/.llauncher default."
        ),
    ),
) -> None:
    """CLI for managing llama.cpp server instances."""
    if state_dir is not None:
        os.environ["LAUNCHER_STATE_DIR"] = str(state_dir)


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


def _delegated_outcome(res: dict | None, verb: str, port: int) -> tuple[bool, str]:
    """Reduce a delegated ``RemoteNode`` verb result to ``(success, message)``.

    Mirrors the MCP server's ``_delegated_or_error`` dict|None guard
    (``mcp_server/tools/servers.py``) but for the CLI's render contract: a
    delegated launch returns the agent's ADR-LLNCH-010 envelope over HTTP (a
    ``dict`` with ``success``/``message``); a transport or HTTP failure
    returns ``{"success": False, "error": ...}``; and a 200-with-JSON-null
    body surfaces as Python ``None``. Collapse all three to the ``(bool,
    str)`` pair the CLI renders, never raising on the ``None`` seam.
    """
    if res is None:
        return (
            False,
            f"Local agent returned an empty response for {verb} on port {port}",
        )
    success = bool(res.get("success"))
    message = res.get("message") or res.get("error") or f"{verb} on port {port}"
    return success, message


# ---------------------------------------------------------------------------
# model subcommands
# ---------------------------------------------------------------------------

model_app = typer.Typer(name="model", help="Manage model configurations")


@model_app.command("list")
def list_models(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """List all configured models."""
    from llauncher.core.config import ConfigStore

    names = ConfigStore.list_models()
    if as_json:
        _json_output(names)
        return

    headers = ["NAME"]
    rows = [[name] for name in names]
    _print_table(headers, rows, title="Configured Models")


@model_app.command("info")
def model_info(
    name: str = typer.Argument(..., help="Name of the model to inspect"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show detailed information for a single model."""
    from llauncher.core.config import ConfigStore

    config = ConfigStore.get_model(name)

    if config is None:
        console.print(f"[red]Model '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    if as_json:
        _json_output(config.to_dict())
        return

    headers = ["KEY", "VALUE"]
    cfg_dict = config.model_dump()
    rows = [[k, str(v)] for k, v in cfg_dict.items()]
    _print_table(headers, rows, title=f"Model: {name}")


@model_app.command("remove")
def remove_model(
    name: str = typer.Argument(..., help="Name of the model config to remove"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Remove a model configuration (mirrors ``node remove``).

    Thin wrapper over :func:`llauncher.operations.delete_model` (#276) —
    config-only; refuses (``rejected_in_use``) while the model is running
    on any port. Prompts for confirmation unless ``--yes`` is passed, since
    the delete has no UI/CLI undo.
    """
    from llauncher import operations as ops

    if not yes and not typer.confirm(
        f"Remove model config {name!r}? This cannot be undone."
    ):
        console.print("Aborted.")
        raise typer.Exit(code=1)

    result = ops.delete_model(name, caller="cli")

    if not result.success:
        console.print(f"[red]✗ {result.message}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(result.message, "stopped"))


app.add_typer(model_app)

# ---------------------------------------------------------------------------
# server subcommands
# ---------------------------------------------------------------------------

server_app = typer.Typer(name="server", help="Manage running server processes")


@server_app.command("start")
def start_server(
    name: str = typer.Argument(..., help="Name of the model to start"),
    port: int = typer.Option(
        ...,
        "--port",
        "-p",
        help="Port to bind the server to (required; ADR-LLNCH-010).",
    ),
    caller: str = typer.Option("cli", hidden=True),
) -> None:
    """Start a server for the given model on the specified port.

    Per ADR-LLNCH-010 the caller supplies the port; there is no auto-allocation
    or env-var fallback (issue #58 / audit C3). The CLI is the call site
    for human invocations — pick a port deliberately.
    """
    from llauncher import operations
    from llauncher.core import delegation
    from llauncher.remote.node import local_agent_node

    # Delegation gate (#200/#203): with a healthy local agent present (or an
    # explicit ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT`` override) POST the launch
    # to the agent over HTTP so the ``llama-server`` is a child of the
    # systemd-managed agent — the sole spawner under system-mode (#194). With
    # no agent reachable, fall back to the in-process op (dev/standalone).
    if delegation.should_delegate():
        success, message = _delegated_outcome(
            local_agent_node().start_server(name, port), "start", port
        )
    else:
        result = operations.start(name, port, caller=caller)
        success, message = result.success, result.message

    if not success:
        console.print(f"[red]✗ {message}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(message, "running"))


@server_app.command("stop")
def stop_server(
    port: int = typer.Argument(..., help="Port of the server to stop"),
    caller: str = typer.Option("cli", hidden=True),
) -> None:
    """Stop a running server on the specified port."""
    from llauncher import operations
    from llauncher.core import delegation
    from llauncher.remote.node import local_agent_node

    # Delegation gate (#200/#203): mirror ``start`` — delegate to the local
    # agent over HTTP when one is reachable (sole-spawner intent, #194),
    # else stop in-process (dev/standalone).
    if delegation.should_delegate():
        success, message = _delegated_outcome(
            local_agent_node().stop_server(port), "stop", port
        )
    else:
        result = operations.stop(port, caller=caller)
        success, message = result.success, result.message

    if not success:
        console.print(f"[red]✗ {message}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(message, "stopped"))


@server_app.command("swap")
def swap_server(
    name: str = typer.Argument(..., help="Name of the model to swap in"),
    port: int = typer.Option(
        ...,
        "--port",
        "-p",
        help="Port whose occupant to replace (required; ADR-LLNCH-010).",
    ),
    caller: str = typer.Option("cli", hidden=True),
) -> None:
    """Replace whatever is running on ``port`` with ``name``.

    Per ADR-LLNCH-010 the caller supplies the port, mirroring ``start``/``stop``.
    Performs the ADR-LLNCH-011 5-phase swap (pre-flight, marker, stop, start,
    readiness) with rollback to the previous model on failure — same
    delegation gate as ``start``/``stop``: a healthy local agent gets the
    swap over HTTP, else it runs in-process.
    """
    from llauncher import operations
    from llauncher.core import delegation
    from llauncher.remote.node import local_agent_node

    # Delegation gate (#200/#203): mirror ``start``/``stop`` — delegate to
    # the local agent over HTTP when one is reachable (sole-spawner intent,
    # #194), else swap in-process (dev/standalone).
    if delegation.should_delegate():
        success, message = _delegated_outcome(
            local_agent_node().swap_server(name, port), "swap", port
        )
    else:
        result = operations.swap(name, port, caller=caller)
        success, message = result.success, result.message
        if result.action == "rolled_back" and result.previous_model:
            message = f"{message} (rolled back to {result.previous_model})"

    if not success:
        console.print(f"[red]✗ {message}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(message, "running"))


@server_app.command("cancel")
def cancel_server(
    port: int = typer.Argument(..., help="Port of the in-flight op to cancel"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Signal cancellation of an in-flight start or swap on the given port.

    Per ADR-LLNCH-014: sets the cancel flag on the in-flight marker; the running
    op picks it up at the next phase boundary. Returns success even when
    there is no in-flight op (the caller's intent of "make sure nothing is
    running" is satisfied either way).
    """
    from llauncher.core import marker as mk

    delivered = mk.request_cancel(port)
    payload = {"cancelled": delivered, "marker_existed": delivered, "port": port}

    if as_json:
        _json_output(payload)
        return

    if delivered:
        console.print(_color(f"Cancel signal sent for port {port}.", "stopped"))
    else:
        console.print(f"[yellow]No in-flight op on port {port}; nothing to cancel.[/yellow]")


@server_app.command("status")
def server_status(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show status of all running servers."""
    from llauncher.state import LauncherState

    state = LauncherState()

    if as_json:
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
# orphan subcommands (ADR-LLNCH-015)
# ---------------------------------------------------------------------------

orphan_app = typer.Typer(
    name="orphan",
    help="Inspect unmanaged llama-server processes (ADR-LLNCH-015)",
)


@orphan_app.command("list")
def list_orphans_cmd(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """List unmanaged llama-server processes on the local node.

    An orphan is a live ``llama-server`` whose ``(port, pid)`` does not
    match a live lockfile in ``LAUNCHER_RUN_DIR``. Per ADR-LLNCH-015 this
    revision is read-only; no ``adopt`` verb is exposed.
    """
    from llauncher import operations as ops

    orphans = ops.list_orphans(caller="cli")

    if as_json:
        _json_output([o.to_dict() for o in orphans])
        return

    if not orphans:
        console.print("[green]No orphan llama-server processes found.[/green]")
        return

    headers = ["PID", "PORT", "CMDLINE"]
    rows: list[list] = []
    for orphan in orphans:
        port_str = str(orphan.port) if orphan.port is not None else "-"
        cmdline_str = "unreadable" if orphan.cmdline_unreadable else "ok"
        rows.append([str(orphan.pid), port_str, cmdline_str])

    _print_table(headers, rows, title="Orphan llama-server Processes")


app.add_typer(orphan_app)

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
    from llauncher.remote.registry import NodeRegistry

    registry = NodeRegistry()
    actual_port = port or 8765
    ok, msg = registry.add_node(name=name, host=host, port=actual_port, api_key=api_key)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "online"))


@node_app.command("list")
def list_nodes(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """List all registered nodes."""
    from llauncher.remote.registry import NodeRegistry

    registry = NodeRegistry()

    if as_json:
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
    name: str = typer.Argument(..., help="Name of the node to remove"),
) -> None:
    """Remove a registered node."""
    from llauncher.remote.registry import NodeRegistry

    registry = NodeRegistry()
    ok, msg = registry.remove_node(name)
    if not ok:
        console.print(f"[red]✗ {msg}[/red]")
        raise typer.Exit(code=1)
    console.print(_color(msg, "stopped"))


@node_app.command("status")
def node_status(
    all_nodes: bool = typer.Option(False, "--all", "-a", help="Include offline/error nodes"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Show status of registered nodes (online only by default)."""
    from llauncher.remote.registry import NodeRegistry

    registry = NodeRegistry()

    # Ping all to refresh statuses
    for node_name in list(registry._nodes.keys()):
        try:
            registry.get_node(node_name).ping()
        except Exception:
            pass  # keep current status if ping fails completely

    if as_json:
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
    from llauncher.core.config import CONFIG_PATH

    # soft_wrap: a filesystem path is a single atom — never soft-wrap it. Rich's
    # default width (80 cols, and no TTY under pytest) would otherwise insert a
    # mid-path newline once the path exceeds the console width, corrupting the
    # emitted value for both a narrow terminal and substring-checking callers (#256).
    console.print(f"[green]{CONFIG_PATH}[/green]", soft_wrap=True)


@config_app.command("validate")
def validate_config(
    name: str = typer.Argument(..., help="Name of the model to validate"),
) -> None:
    """Validate a model configuration without starting a server."""
    from llauncher.core.config import ConfigStore
    from llauncher.models.config import ModelConfig

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

# ---------------------------------------------------------------------------
# audit command (issue #338)
# ---------------------------------------------------------------------------


@app.command("audit")
def audit(
    limit: int = typer.Option(200, "--limit", "-l", help="Bound the tail read (default: 200)."),
    action: Optional[str] = typer.Option(
        None, "--action", help="Filter to entries with this exact action value (e.g. 'started')."
    ),
    result: Optional[str] = typer.Option(
        None, "--result", help="Filter to entries with this exact result value (e.g. 'success')."
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output in JSON format"),
) -> None:
    """Read recent audit-log entries (ADR-LLNCH-008, issue #64).

    Mirrors the agent's ``GET /audit`` contract exactly
    (``agent/routing.py::get_audit``): a bounded tail read via
    :func:`llauncher.core.audit_log.read_entries`, followed by in-memory
    ``action``/``result`` enum-value filtering. The audit log is
    process-global (not port-scoped).
    """
    from llauncher.core import audit_log

    entries = audit_log.read_entries(limit=int(limit))
    if action:
        entries = [e for e in entries if e.action.value == action]
    if result:
        entries = [e for e in entries if e.result.value == result]

    if as_json:
        _json_output([e.to_dict() for e in entries])
        return

    if not entries:
        console.print("[yellow]No audit entries found.[/yellow]")
        return

    headers = ["TIMESTAMP", "ACTION", "RESULT", "CALLER", "PORT", "MODEL", "MESSAGE"]
    rows: list[list] = []
    for e in entries:
        rows.append(
            [
                e.timestamp,
                e.action.value,
                e.result.value,
                e.caller,
                str(e.port) if e.port is not None else "-",
                e.model or "-",
                e.message,
            ]
        )
    _print_table(headers, rows, title="Audit Log")
