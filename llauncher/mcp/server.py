"""MCP server for llauncher."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from llauncher.state import LauncherState
from llauncher.mcp.tools import models as models_tools
from llauncher.mcp.tools import servers as servers_tools
from llauncher.mcp.tools import config as config_tools


# Global state instance
state = LauncherState()


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
    """Dispatch to the appropriate tool handler."""
    # Models tools
    if name == "list_models":
        return await models_tools.list_models(state, arguments)
    elif name == "get_model_config":
        return await models_tools.get_model_config(state, arguments)

    # Servers tools
    elif name == "start_server":
        return await servers_tools.start_server(state, arguments)
    elif name == "stop_server":
        return await servers_tools.stop_server(state, arguments)
    elif name == "server_status":
        return await servers_tools.server_status(state, arguments)
    elif name == "get_server_logs":
        return await servers_tools.get_server_logs(state, arguments)

    # Config tools
    elif name == "update_model_config":
        return await config_tools.update_model_config(state, arguments)
    elif name == "validate_config":
        return await config_tools.validate_config(state, arguments)
    elif name == "add_model":
        return await config_tools.add_model(state, arguments)
    elif name == "remove_model":
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
