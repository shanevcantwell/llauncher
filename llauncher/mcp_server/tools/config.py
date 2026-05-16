"""MCP tools for configuration management."""

from mcp import Tool

from llauncher import operations as ops
from llauncher.models.config import ModelConfig
from llauncher.state import LauncherState


def get_tools() -> list[Tool]:
    """Return tool definitions for configuration operations."""
    return [
        Tool(
            name="update_model_config",
            description="Update an existing model's configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the model to update",
                    },
                    "config": {
                        "type": "object",
                        "description": "Updated configuration fields",
                        "properties": {
                            "n_gpu_layers": {"type": "integer"},
                            "ctx_size": {"type": "integer"},
                            "threads": {"type": "integer"},
                            "flash_attn": {"type": "string", "enum": ["on", "off", "auto"]},
                            "no_mmap": {"type": "boolean"},
                            "extra_args": {"type": "string", "description": "Additional command-line arguments (space-separated)"},
                        },
                    },
                },
                "required": ["name", "config"],
            },
        ),
        Tool(
            name="validate_config",
            description="Validate a model configuration without applying it",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "Configuration to validate",
                        "properties": {
                            "name": {"type": "string"},
                            "model_path": {"type": "string"},
                            "n_gpu_layers": {"type": "integer"},
                            "ctx_size": {"type": "integer"},
                        },
                    },
                },
                "required": ["config"],
            },
        ),
        Tool(
            name="add_model",
            description="Add a new model configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "Full model configuration",
                        "properties": {
                            "name": {"type": "string"},
                            "model_path": {"type": "string"},
                            "mmproj_path": {"type": "string"},
                            "n_gpu_layers": {"type": "integer"},
                            "ctx_size": {"type": "integer"},
                            "threads": {"type": "integer"},
                            "flash_attn": {"type": "string"},
                            "no_mmap": {"type": "boolean"},
                            "extra_args": {"type": "string", "description": "Additional command-line arguments (space-separated, use quotes for args with spaces)"},
                        },
                        "required": ["name", "model_path"],
                    },
                },
                "required": ["config"],
            },
        ),
        Tool(
            name="delete_model",
            description=(
                "Delete a model configuration. Refuses with "
                "action='rejected_in_use' (and the holding port) if the "
                "model is currently running anywhere; stop or swap first. "
                "Idempotent on a missing name (action='not_found')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the model to delete",
                    },
                },
                "required": ["name"],
            },
        ),
    ]


async def update_model_config(state: LauncherState, args: dict) -> dict:
    """Update an existing model's configuration.

    Args:
        state: The launcher state.
        args: Tool arguments including 'name' and 'config'.

    Returns:
        Dictionary with result of the operation.
    """
    name = args.get("name")
    updates = args.get("config", {})

    if not name:
        return {"success": False, "error": "Missing required argument: name"}

    # Refresh before reading state.models (issue #59 / audit H1). With four
    # independent LauncherState instances (UI, CLI, HTTP, MCP), the MCP
    # snapshot of self.models can be stale relative to config.json on
    # disk. Refreshing here narrows the TOCTOU window between "did this
    # model exist?" and "write the update" — without it, MCP could
    # overwrite a foreign edit it never observed.
    state.refresh()

    if name not in state.models:
        return {"success": False, "error": f"Model not found: {name}"}

    # Get existing config and update fields
    existing = state.models[name]
    updated_config = existing.model_copy()

    # Apply updates (default_port silently dropped per ADR-010)
    updates.pop("default_port", None)
    if "n_gpu_layers" in updates:
        updated_config.n_gpu_layers = updates["n_gpu_layers"]
    if "ctx_size" in updates:
        updated_config.ctx_size = updates["ctx_size"]
    if "threads" in updates:
        updated_config.threads = updates["threads"]
    if "flash_attn" in updates:
        updated_config.flash_attn = updates["flash_attn"]
    if "no_mmap" in updates:
        updated_config.no_mmap = updates["no_mmap"]
    if "extra_args" in updates:
        updated_config.extra_args = updates["extra_args"]

    # Validate the updated config
    try:
        ModelConfig.model_validate(updated_config)
    except Exception as e:
        return {"success": False, "error": f"Validation error: {e}"}

    # Save the updated config
    from llauncher.core.config import ConfigStore

    ConfigStore.update_model(name, updated_config, caller="mcp")
    state.models[name] = updated_config

    state.record_action("update", name, "mcp", "success", "Configuration updated")

    return {
        "success": True,
        "message": f"Updated configuration for {name}",
        "config": updated_config.to_dict(),
    }


async def validate_config(state: LauncherState, args: dict) -> dict:
    """Validate a model configuration without applying it.

    Args:
        state: The launcher state.
        args: Tool arguments including 'config'.

    Returns:
        Dictionary with validation result.
    """
    config_data = args.get("config", {})

    if not config_data:
        return {"valid": False, "error": "Missing required argument: config"}

    try:
        # Try to create a ModelConfig from the data
        config = ModelConfig.model_validate(config_data)
        return {"valid": True, "config": config.to_dict()}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def add_model(state: LauncherState, args: dict) -> dict:
    """Add a new model configuration.

    Args:
        state: The launcher state.
        args: Tool arguments including 'config'.

    Returns:
        Dictionary with result of the operation.
    """
    config_data = args.get("config", {})

    if not config_data:
        return {"success": False, "error": "Missing required argument: config"}

    try:
        config = ModelConfig.model_validate(config_data)
    except Exception as e:
        return {"success": False, "error": f"Validation error: {e}"}

    # Refresh before the existence check (issue #59 / audit H1). See the
    # twin comment in ``update_model_config``: MCP's stale view of
    # ``state.models`` can let a duplicate-name add slip through if
    # another process created the same name between MCP's last refresh
    # and this call.
    state.refresh()

    # Check if model already exists
    if config.name in state.models:
        return {"success": False, "error": f"Model already exists: {config.name}"}

    # Save the new config
    from llauncher.core.config import ConfigStore

    ConfigStore.add_model(config, caller="mcp")
    state.models[config.name] = config

    state.record_action("add", config.name, "mcp", "success", "Model added")

    return {
        "success": True,
        "message": f"Added model {config.name}",
        "config": config.to_dict(),
    }


async def delete_model(args: dict) -> dict:
    """Delete a model configuration per ADR-008 §4.1.

    Thin wrapper over :func:`llauncher.operations.delete_model`. Returns
    the ADR-010 envelope (``success``, ``action``, ``model``, optional
    ``in_use_port``).
    """
    name = args.get("name")

    if not name:
        return {
            "success": False,
            "action": "error",
            "error": "Missing required argument: name",
        }

    result = ops.delete_model(name, caller="mcp")
    return result.to_dict()
