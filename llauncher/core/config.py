"""Configuration persistence for llauncher."""

import json
from pathlib import Path

from llauncher.models.config import ModelConfig


CONFIG_DIR = Path.home() / ".llauncher"
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
        """Save configurations to disk.

        Args:
            models: Dictionary of model configurations.
        """
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = {name: cfg.to_dict() for name, cfg in models.items()}

        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def merge_discovered(
        cls, discovered: list[ModelConfig]
    ) -> dict[str, ModelConfig]:
        """Merge discovered scripts with persisted configs.

        Persisted configs take precedence over discovered scripts.

        Args:
            discovered: List of configs discovered from scripts.

        Returns:
            Merged dictionary of all configurations.
        """
        # Start with persisted configs
        merged = cls.load()

        # Add discovered scripts that aren't in persisted configs
        for config in discovered:
            if config.name not in merged:
                merged[config.name] = config

        return merged

    @classmethod
    def add_model(cls, config: ModelConfig) -> None:
        """Add a new model configuration.

        Args:
            config: Model configuration to add.
        """
        models = cls.load()
        models[config.name] = config
        cls.save(models)

    @classmethod
    def update_model(cls, name: str, config: ModelConfig) -> None:
        """Update an existing model configuration.

        Args:
            name: Name of the model to update (for validation).
            config: New configuration (name should match).
        """
        if name != config.name:
            raise ValueError(f"Name mismatch: {name} != {config.name}")

        models = cls.load()
        if name not in models:
            raise KeyError(f"Model not found: {name}")

        models[name] = config
        cls.save(models)

    @classmethod
    def remove_model(cls, name: str) -> None:
        """Remove a model configuration.

        Args:
            name: Name of the model to remove.
        """
        models = cls.load()
        if name in models:
            del models[name]
            cls.save(models)

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
