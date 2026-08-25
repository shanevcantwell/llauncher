"""MCP server for llauncher."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import Tool
from mcp.types import TextContent

from llauncher.state import LauncherState
from llauncher.mcp_server.tools import audit as audit_tools
from llauncher.mcp_server.tools import models as models_tools
from llauncher.mcp_server.tools import servers as servers_tools
from llauncher.mcp_server.tools import config as config_tools


_mcp_state: "LauncherState | None" = None  # type: ignore[assignment]


def get_mcp_state() -> LauncherState:
    """Get or create the MCP LauncherState singleton.

    Lazy-creates on first call. __post_init__ calls refresh(), so returned state
    is always fresh (configs from disk + live process table).

    The same instance is cached and reused for all subsequent calls.

    On failure during first-access, _mcp_state is reset to None to allow retry
    on the next call. Without this protection, a partially constructed LauncherState
    would be cached forever since __post_init__ runs during construction.
    """
    global _mcp_state
    if _mcp_state is None:
        try:
            _mcp_state = LauncherState()  # __post_init__ already calls refresh()
        except Exception:
            _mcp_state = None  # Clear partial state so next call retries (Fix #34-F)
            raise
    return _mcp_state


async def list_tools_handler() -> list[Tool]:
    """List all available tools."""
    tools = []
    tools.extend(models_tools.get_tools())
    tools.extend(servers_tools.get_tools())
    tools.extend(config_tools.get_tools())
    tools.extend(audit_tools.get_tools())
    return tools


async def call_tool_handler(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to appropriate handlers."""
    try:
        result = await _dispatch_tool(name, arguments)
        return [TextContent(type="text", text=json.dumps(result))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def _dispatch_tool(name: str, arguments: dict) -> dict:
    """Dispatch to the appropriate tool handler.

    The verb tools (``start_server``, ``stop_server``, ``swap_server``,
    ``delete_model``) are stateless per ADR-LLNCH-008 — they delegate to
    :mod:`llauncher.operations` and do not need the LauncherState
    singleton. Stateless config tools (``validate_config``) likewise
    bypass it.

    Read tools (``server_status``, ``get_server_logs``, ``list_models``,
    ``get_model_config``) and the remaining v1 config-mutation tools
    (``update_model_config``, ``add_model``) still use the lazy
    singleton. Read handlers refresh on their passed-in instance for
    per-call freshness (#31/#32).
    """
    # ── Stateless verb tools (ADR-LLNCH-010, ops-backed) ──────────────────
    if name == "start_server":
        return await servers_tools.start_server(arguments)
    elif name == "stop_server":
        return await servers_tools.stop_server(arguments)
    elif name == "swap_server":
        return await servers_tools.swap_server(arguments)
    elif name == "cancel_server":
        return await servers_tools.cancel_server(arguments)
    elif name == "delete_model":
        return await config_tools.delete_model(arguments)
    elif name == "validate_models":
        return await models_tools.validate_models(arguments)
    elif name == "server_metrics":
        return await servers_tools.server_metrics(arguments)
    elif name == "server_slots":
        return await servers_tools.server_slots(arguments)
    elif name == "read_audit":
        return await audit_tools.read_audit(arguments)

    # ── Stateless config tools ──────────────────────────────────────
    if name == "validate_config":
        return await config_tools.validate_config(None, arguments)

    # ── State-backed tools (read-side or v1 config mutation) ────────
    state = get_mcp_state()

    if name == "list_models":
        return await models_tools.list_models(state, arguments)
    elif name == "get_model_config":
        return await models_tools.get_model_config(state, arguments)

    if name == "server_status":
        return await servers_tools.server_status(state, arguments)
    elif name == "get_server_logs":
        return await servers_tools.get_server_logs(state, arguments)
    elif name == "list_orphans":
        return await servers_tools.list_orphans(state, arguments)

    if name == "update_model_config":
        return await config_tools.update_model_config(state, arguments)
    elif name == "add_model":
        return await config_tools.add_model(state, arguments)

    raise ValueError(f"Unknown tool: {name}")


async def main_async():
    """Async main entry point for the MCP server."""
    server = Server("llauncher")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return await list_tools_handler()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await call_tool_handler(name, arguments)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    """Main entry point for the MCP server."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
