"""Configuration persistence for llauncher.

CRUD methods (``add_model``, ``update_model``, ``remove_model``) emit
audit-log entries per ADR-008 / issue #60. Each method takes an
optional ``caller`` kwarg identifying the surface that initiated the
mutation (``"cli"`` / ``"mcp"`` / ``"http"`` / ``"ui"``); callers that
don't pass one are recorded as ``"unknown"``.
"""

import json

from llauncher.core import audit_log as al
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.settings import LAUNCHER_STATE_DIR
from llauncher.models.config import ModelConfig


# Derived from the single LAUNCHER_STATE_DIR base (issue #196). With
# LAUNCHER_STATE_DIR unset, this resolves to ~/.llauncher exactly as
# before.
CONFIG_DIR = LAUNCHER_STATE_DIR
CONFIG_PATH = CONFIG_DIR / "config.json"


class ConfigStore:
    """Persistent storage for model configurations."""

    @classmethod
    def load(cls) -> dict[str, ModelConfig]:
        """Load configurations from disk.

        Returns:
            Dictionary mapping model names to ModelConfig.
        """
        if not CONFIG_PATH.exists():
            return {}

        try:
            data = json.loads(CONFIG_PATH.read_text())
            # Use from_dict_unvalidated to skip path validation for persisted configs
            return {
                name: ModelConfig.from_dict_unvalidated(cfg)
                for name, cfg in data.items()
            }
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error loading config: {e}")
            return {}

    @classmethod
    def save(cls, models: dict[str, ModelConfig]) -> None:
        """Save configurations to disk atomically.

        Writes to a temporary file first, then renames to prevent
        corruption if the process is interrupted mid-write.

        Args:
            models: Dictionary of model configurations.
        """
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = {name: cfg.to_dict() for name, cfg in models.items()}

        # Write to temp file first, then rename for atomicity
        temp_path = CONFIG_PATH.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
        temp_path.replace(CONFIG_PATH)

 
    @classmethod
    def add_model(cls, config: ModelConfig, *, caller: str = "unknown") -> None:
        """Add a new model configuration.

        Args:
            config: Model configuration to add.
            caller: Identifies the surface that initiated the change
                (``"cli"`` / ``"mcp"`` / ``"http"`` / ``"ui"``).
                Recorded in the audit log per ADR-008 / issue #60.
        """
        models = cls.load()
        models[config.name] = config
        cls.save(models)
        al.record(
            AuditAction.MODEL_ADDED,
            AuditResult.SUCCESS,
            caller=caller,
            model=config.name,
        )

    @classmethod
    def update_model(
        cls, name: str, config: ModelConfig, *, caller: str = "unknown"
    ) -> None:
        """Update an existing model configuration.

        Args:
            name: Name of the model to update (for validation).
            config: New configuration (name should match).
            caller: Identifies the surface that initiated the change
                (audit log; see :meth:`add_model`).
        """
        if name != config.name:
            raise ValueError(f"Name mismatch: {name} != {config.name}")

        models = cls.load()
        if name not in models:
            raise KeyError(f"Model not found: {name}")

        previous = models[name]
        models[name] = config
        cls.save(models)

        # Capture which fields actually changed so the audit message is
        # informative without bloating the entry with a full dump.
        # ``AuditEntry`` has no payload field by design (ADR-008); the
        # ``message`` is the natural carrier.
        prev_d = previous.to_dict()
        new_d = config.to_dict()
        changed = sorted(k for k in new_d if prev_d.get(k) != new_d.get(k))
        message = (
            f"changed: {', '.join(changed)}" if changed else "no field changes"
        )
        al.record(
            AuditAction.MODEL_UPDATED,
            AuditResult.SUCCESS,
            caller=caller,
            model=name,
            message=message,
        )

    @classmethod
    def remove_model(cls, name: str, *, caller: str = "unknown") -> None:
        """Remove a model configuration.

        No audit entry is emitted when ``name`` does not exist — the
        ``remove`` verb is idempotent and a no-op is not a user-visible
        state change worth recording.

        Args:
            name: Name of the model to remove.
            caller: Identifies the surface that initiated the change
                (audit log; see :meth:`add_model`).
        """
        models = cls.load()
        if name in models:
            del models[name]
            cls.save(models)
            al.record(
                AuditAction.MODEL_REMOVED,
                AuditResult.SUCCESS,
                caller=caller,
                model=name,
            )

    @classmethod
    def get_model(cls, name: str) -> ModelConfig | None:
        """Get a single model configuration.

        Args:
            name: Name of the model.

        Returns:
            ModelConfig if found, None otherwise.
        """
        models = cls.load()
        return models.get(name)

    @classmethod
    def list_models(cls) -> list[str]:
        """List all configured model names.

        Returns:
            List of model names.
        """
        return list(cls.load().keys())
