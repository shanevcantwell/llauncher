"""MCP tools for model listing and configuration."""

from mcp import Tool

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

    Returns structured data separating model identification from status information
    to prevent confusion between model names and port numbers.

    Args:
        state: The launcher state.
        args: Tool arguments (empty for this tool).

    Returns:
        Dictionary with list of models. Each model includes:
        - identification: model name and path
        - status: running status, port, and PID if applicable
    """
    # Per-call refresh via dispatch-provided state (Fix #31/#32 — no circular import, single refresh)
    state.refresh()

    models = []

    for name, config in state.models.items():
        status_info = state.get_model_status(name)
        model_entry = {
            "identification": {
                "name": name,
                "model_path": config.model_path
            },
            "status": {
                "state": status_info.get("status", "unknown"),
                "port": status_info.get("port") if status_info.get("status") == "running" else None,
                "default_port": config.default_port,
                **({"pid": status_info["pid"]} if status_info.get("status") == "running" else {})
            }
        }
        models.append(model_entry)

    return {"models": models, "count": len(models)}


async def get_model_config(state: LauncherState, args: dict) -> dict:
    """Get full configuration for a specific model by name.

    Returns structured data separating model identification from configuration and status.

    Args:
        state: The launcher state.
        args: Tool arguments including 'name'.

    Returns:
        Dictionary with model configuration. Includes:
        - identification: model name and path
        - configuration: full model configuration
        - status: running status, port, and PID if applicable
    """
    # Per-call refresh via dispatch-provided state (Fix #31/#32 — no circular import, single refresh)
    state.refresh()

    name = args.get("name")

    if not name:
        return {"error": "Missing required argument: name"}

    if name not in state.models:
        return {"error": f"Model not found: {name}"}

    config = state.models[name]
    status_info = state.get_model_status(name)

    return {
        "identification": {
            "name": name,
            "model_path": config.model_path
        },
        "configuration": config.to_dict(),
        "status": {
            "state": status_info.get("status", "unknown"),
            "port": status_info.get("port") if status_info.get("status") == "running" else None,
            "default_port": config.default_port,
            **({"pid": status_info["pid"]} if status_info.get("status") == "running" else {})
        }
    }
