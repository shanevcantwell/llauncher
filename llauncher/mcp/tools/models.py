"""MCP tools for model listing and configuration."""

from mcp.types import Tool

from llauncher.state import LauncherState


def get_tools() -> list[Tool]:
    """Return tool definitions for model operations."""
    return [
        Tool(
            name="list_models",
            description="List all configured models with their current status (running/stopped)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_model_config",
            description="Get the full configuration for a specific model by name",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the model to retrieve",
                    },
                },
                "required": ["name"],
            },
        ),
    ]


async def list_models(state: LauncherState, args: dict) -> dict:
    """List all configured models with status.

    Args:
        state: The launcher state.
        args: Tool arguments (empty for this tool).

    Returns:
        Dictionary with list of models and their status.
    """
    models = []

    for name, config in state.models.items():
        status_info = state.get_model_status(name)
        # Use running port if available, otherwise default_port
        if status_info.get("status") == "running":
            port_info = status_info.get("port")
        else:
            port_info = config.default_port or "auto-allocate"

        models.append(
            {
                "name": name,
                "status": status_info.get("status", "unknown"),
                "port": port_info,
                "model_path": config.model_path,
                "n_gpu_layers": config.n_gpu_layers,
                "ctx_size": config.ctx_size,
                **(
                    {"pid": status_info["pid"]}
                    if status_info.get("status") == "running"
                    else {}
                ),
            }
        )

    return {"models": models, "count": len(models)}


async def get_model_config(state: LauncherState, args: dict) -> dict:
    """Get full configuration for a specific model.

    Args:
        state: The launcher state.
        args: Tool arguments including 'name'.

    Returns:
        Dictionary with model configuration.
    """
    name = args.get("name")

    if not name:
        return {"error": "Missing required argument: name"}

    if name not in state.models:
        return {"error": f"Model not found: {name}"}

    config = state.models[name]
    status_info = state.get_model_status(name)

    return {
        "name": name,
        "config": config.to_dict(),
        "status": status_info,
    }
