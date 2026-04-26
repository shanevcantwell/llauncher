"""MCP server for llauncher."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import Tool
from mcp.types import TextContent

from llauncher.state import LauncherState
from llauncher.mcp_server.tools import models as models_tools
from llauncher.mcp_server.tools import servers as servers_tools
from llauncher.mcp_server.tools import config as config_tools


_mcp_state: "LauncherState | None" = None  # type: ignore[assignment]


def get_mcp_state() -> LauncherState:
    """Get or create the MCP LauncherState singleton.

    Lazy-creates on first call. __post_init__ calls refresh(), so returned state
    is always fresh (configs from disk + live process table).

    The same instance is cached and reused for all subsequent calls.

    If __init__/refresh fails during first-access, _mcp_state stays None.
    Subsequent calls retry initialization to recover from transient errors
    (corrupt config, permissions) rather than caching a failure indefinitely.
    """
    global _mcp_state
    if _mcp_state is None:
        _mcp_state = LauncherState()  # __post_init__ already calls refresh()
    return _mcp_state


async def list_tools_handler() -> list[Tool]:
    """List all available tools."""
    tools = []
    tools.extend(models_tools.get_tools())
    tools.extend(servers_tools.get_tools())
    tools.extend(config_tools.get_tools())
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

    Uses lazy singleton via get_mcp_state() — state is created on first call
    (not at import time). All handlers that READ from state must additionally
    call refresh() internally for per-call freshness. Handlers that MUTATE
    state do their own reconciliation and don't need external refresh.
    """
    # Get lazy-initialized singleton (creates + first-refresh via __post_init__ on first access)
    state = get_mcp_state()

    if name == "list_models":
        return await models_tools.list_models(state, arguments)  # handler calls refresh() internally
    elif name == "get_model_config":
        return await models_tools.get_model_config(state, arguments)

    if name == "start_server":  # MUTATION: self-consistent via direct state.running write
        return await servers_tools.start_server(state, arguments)
    elif name == "stop_server":  # MUTATION: self-consistent via del state.running[port]
        return await servers_tools.stop_server(state, arguments)
    elif name == "swap_server":  # MUTATION: _start_with_eviction_impl does internal phase-5 reconcile
        return await servers_tools.swap_server(state, arguments)
    elif name == "server_status":  # READ: handler calls get_mcp_state().refresh() internally
        return await servers_tools.server_status(state, arguments)
    elif name == "get_server_logs":  # READ: handler calls get_mcp_state().refresh() internally
        return await servers_tools.get_server_logs(state, arguments)

    if name == "update_model_config":  # MUTATION: sets state.models[name] directly
        return await config_tools.update_model_config(state, arguments)
    elif name == "validate_config":  # STATELESS: pure Pydantic input validation; does NOT use LauncherState
        return await config_tools.validate_config(state, arguments)
    elif name == "add_model":  # MUTATION: sets state.models[name] directly
        return await config_tools.add_model(state, arguments)
    elif name == "remove_model":  # MUTATION: deletes state.models[name]
        return await config_tools.remove_model(state, arguments)

    else:
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
