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
                            "parallel": {"type": "integer"},
                            "metrics": {"type": "boolean", "description": "Enable llama-server's Prometheus /metrics endpoint (--metrics). Defaults to true."},
                            "extra_args": {"type": "string", "description": "Verbatim llama-server command-line flags (space-separated), in the spelling from `llama-server --help`. No content validation. Flags llauncher owns (--alias, -m/--model, --host/--port, --api-key, --metrics, --slots/--no-slots) are rejected at launch time, not here (ADR-026 / issue #477)."},
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
                            "parallel": {"type": "integer"},
                            "metrics": {"type": "boolean", "description": "Enable llama-server's Prometheus /metrics endpoint (--metrics). Defaults to true."},
                            "extra_args": {"type": "string", "description": "Verbatim llama-server command-line flags (space-separated, use quotes for args with spaces), in the spelling from `llama-server --help`. No content validation (ADR-026 / issue #477)."},
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

    # Apply updates (default_port silently dropped per ADR-LLNCH-010). With
    # ``validate_assignment=True`` on ``ModelConfig`` (review of PR #101),
    # each per-field assignment below validates that field's constraints;
    # wrap the whole block so the resulting ValidationError returns the
    # same clean error dict as the explicit ``model_validate`` below
    # rather than escaping uncaught.
    updates.pop("default_port", None)
    try:
        if "n_gpu_layers" in updates:
            updated_config.n_gpu_layers = updates["n_gpu_layers"]
        if "ctx_size" in updates:
            updated_config.ctx_size = updates["ctx_size"]
        if "parallel" in updates:
            updated_config.parallel = updates["parallel"]
        if "metrics" in updates:
            updated_config.metrics = updates["metrics"]
        # NOTE: ``slots`` is deliberately NOT agent-writable. It is an
        # llauncher-owned field (ADR-026), but /slots leaks per-slot prompt
        # text and llauncher's posture is that the exposure decision is the
        # operator's (ADR-LLNCH-019). ADR-026 §5 shrinks this hand-maintained
        # allow-list; it does not extend it. Edit ``slots`` in the UI.
        if "extra_args" in updates:
            updated_config.extra_args = updates["extra_args"]

        # Re-validate the whole shape after per-field assignments — catches
        # any cross-field invariants the individual validators don't cover.
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
    """Delete a model configuration per ADR-LLNCH-008 §4.1.

    Thin wrapper over :func:`llauncher.operations.delete_model`. Returns
    the ADR-LLNCH-010 envelope (``success``, ``action``, ``model``, optional
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
