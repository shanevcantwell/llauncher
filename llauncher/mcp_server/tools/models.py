"""MCP tools for model listing and configuration."""

from mcp import Tool

from llauncher.state import LauncherState


def get_tools() -> list[Tool]:
    """Return tool definitions for model operations."""
    return [
        Tool(
            name="list_models",
            description=(
                "List all configured models with their current status "
                "(running/stopped). Returns ``{models: [...], count: N}`` "
                "where each entry is nested as "
                "``{identification: {name, model_path}, status: {state, port, pid?}}``. "
                "The value to pass as ``model_name`` to ``start_server`` / "
                "``swap_server`` is ``identification.name`` exactly as "
                "returned — do NOT concatenate it with ``status.port`` or "
                "any other field."
            ),
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
        Tool(
            name="validate_models",
            description=(
                "Read-only validation of configured model weights (issue #475, "
                "ADR-LLNCH-027): file existence/readability/size and GGUF magic bytes "
                "(gating), plus VRAM headroom and lockfile staleness reported as "
                "advisory (never gate the result). Never starts a process, "
                "deletes a config entry, or writes an audit line. Returns "
                "``{checked_at, ok, models: [...]}`` where each entry carries "
                "``verdicts`` (per-check outcomes) and its own ``ok``."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Model names to validate. Omit to validate every "
                            "configured model."
                        ),
                    },
                    "vram": {
                        "type": "boolean",
                        "description": (
                            "When false, skip the VRAM check entirely (no "
                            "nvidia-smi shell-out). Default true."
                        ),
                    },
                },
                "required": [],
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
            **({"pid": status_info["pid"]} if status_info.get("status") == "running" else {})
        }
    }


async def validate_models(args: dict) -> dict:
    """Read-only validation of configured model weights (issue #475, ADR-LLNCH-027).

    Stateless — reuses :func:`llauncher.operations.validate_models` directly,
    the same peer as ``start_server``/``delete_model`` in the dispatch table
    (no ``LauncherState`` singleton needed: the op reads ``ConfigStore`` and
    the lockfile registry fresh on every call).

    Args:
        args: Tool arguments — optional ``names`` (list[str]) and ``vram``
            (bool, default True).

    Returns:
        The ``ValidationReport`` as a plain dict.
    """
    from llauncher import operations as ops

    names = args.get("names")
    vram = args.get("vram", True)

    report = ops.validate_models(names=names, vram=vram)
    return report.model_dump(mode="json")
