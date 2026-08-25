"""Configuration persistence for llauncher.

CRUD methods (``add_model``, ``update_model``, ``remove_model``) emit
audit-log entries per ADR-LLNCH-008 / issue #60. Each method takes an
optional ``caller`` kwarg identifying the surface that initiated the
mutation (``"cli"`` / ``"mcp"`` / ``"http"`` / ``"ui"``); callers that
don't pass one are recorded as ``"unknown"``.
"""

import json
import logging
import shlex

from llauncher.core import audit_log as al
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.core.settings import LAUNCHER_STATE_DIR
from llauncher.models.config import ModelConfig

logger = logging.getLogger(__name__)


# ADR-026 / issue #477: the 16 llama-server mirror fields dropped from
# ModelConfig, mapped to the flag spelling(s) ``build_command`` used to
# emit for them. Multiple spellings (e.g. cache_type_k's ``-ctk`` short
# alias) are checked for "flag already present in extra_args"; the first
# spelling is the one migration *writes* when it materializes a value.
_DROPPED_FIELD_FLAGS: dict[str, list[str]] = {
    "threads": ["--threads"],
    "threads_batch": ["--threads-batch"],
    "ubatch_size": ["--ubatch-size"],
    "batch_size": ["--batch-size"],
    "flash_attn": ["--flash-attn"],
    "no_mmap": ["--no-mmap"],
    "cache_type_k": ["--cache-type-k", "-ctk"],
    "cache_type_v": ["--cache-type-v", "-ctv"],
    "n_cpu_moe": ["--n-cpu-moe"],
    "temperature": ["--temp"],
    "top_k": ["--top-k"],
    "top_p": ["--top-p"],
    "min_p": ["--min-p"],
    "repeat_penalty": ["--repeat-penalty"],
    "reverse_prompt": ["--reverse-prompt"],
    "mlock": ["--mlock"],
}

# Boolean fields build_command emitted as a bare flag (no value) iff true.
_BOOL_FLAG_FIELDS: frozenset[str] = frozenset({"no_mmap", "mlock"})

# Fields build_command emitted *unconditionally*, from the field's default
# when unset (RATIFIED §3's "sharp edge"): the effective current value —
# default included — must be materialized on migration or every persisted
# config's argv silently changes on upgrade.
_ALWAYS_MATERIALIZE_FIELDS: frozenset[str] = frozenset(
    {"threads_batch", "ubatch_size", "flash_attn"}
)

# Fields build_command emitted conditionally on truthiness (``if value:``),
# not ``is not None`` — mirrors build_command's exact prior conditionals so
# migrated argv is byte-identical to pre-migration argv.
_TRUTHY_FIELDS: frozenset[str] = frozenset(
    {"threads", "cache_type_k", "cache_type_v", "n_cpu_moe", "reverse_prompt"}
)

# All other dropped fields (batch_size, temperature, top_k, top_p, min_p,
# repeat_penalty) were emitted conditionally on ``is not None``.

# Fields ModelConfig legitimately drops silently on load (ADR-010 / #235),
# handled inside ``ModelConfig.from_dict_unvalidated`` itself — not part of
# this migration's dropped-field set, but must not trip the "unrecognized
# key" fail-loud check below.
_LEGACY_SILENT_DROP_FIELDS: frozenset[str] = frozenset(
    {"default_port", "port", "host", "np"}
)

_KNOWN_MODEL_CONFIG_FIELDS: frozenset[str] = frozenset(ModelConfig.model_fields)


def _field_should_emit(field: str, value) -> bool:
    """Would ``build_command`` have emitted this field's flag, pre-drop?"""
    if field in _ALWAYS_MATERIALIZE_FIELDS:
        return True
    if field in _BOOL_FLAG_FIELDS or field in _TRUTHY_FIELDS:
        return bool(value)
    return value is not None


def _format_flag_token(field: str, flag: str, value) -> str:
    """Render one dropped field as the extra_args token(s) it materializes to."""
    if field in _BOOL_FLAG_FIELDS:
        return flag
    return f"{flag} {shlex.quote(str(value))}"


def _migrate_config_dict(name: str, data: dict) -> tuple[dict, bool]:
    """One-shot migration at the door for a single persisted model entry.

    PARSE-AT-THE-DOOR (ADR-026 / issue #477): for each of the 16 dropped
    llama-server mirror fields present in ``data``:

    * field's flag already present in ``extra_args`` (either spelling) →
      drop the field, the ``extra_args`` occurrence wins.
    * field would have caused ``build_command`` to emit its flag (see
      :func:`_field_should_emit`) and the flag is absent from
      ``extra_args`` → append the flag(+value) to ``extra_args``, drop
      the field. Boolean fields append the bare flag.
    * neither → drop the field; nothing is appended (the field carried no
      effective value, so argv is unaffected).

    No dual-parse: every dropped-field key that exists in ``data`` is
    removed. Returns ``(migrated_data, dirty)`` — ``dirty`` is True iff
    ``data`` actually changed (a field was dropped, or the legacy
    ``extra_args`` list shape was normalized), so callers only rewrite
    ``config.json`` when something really migrated.

    Raises:
        ValueError: ``extra_args`` is not valid shell-token text, or
            ``data`` carries a key that is neither a current ``ModelConfig``
            field, a dropped field handled here, nor a legacy field
            ``ModelConfig.from_dict_unvalidated`` silently drops — an
            unrecognized persisted shape is not tolerated (PARSE-AT-THE-DOOR).
    """
    data = dict(data)
    dirty = False

    extra_args = data.get("extra_args", "")
    if isinstance(extra_args, list):
        extra_args = " ".join(str(t) for t in extra_args)
        dirty = True

    try:
        tokens_present = set(shlex.split(extra_args)) if extra_args else set()
    except ValueError as e:
        raise ValueError(
            f"Model {name!r}: extra_args is not a valid shell token "
            f"string: {e}"
        ) from e
    heads_present = {t.split("=", 1)[0] for t in tokens_present}

    appended: list[str] = []
    for field, flags in _DROPPED_FIELD_FLAGS.items():
        if field not in data:
            continue
        value = data.pop(field)
        dirty = True
        if any(f in heads_present for f in flags):
            continue
        if _field_should_emit(field, value):
            appended.append(_format_flag_token(field, flags[0], value))

    if appended:
        extra_args = (
            f"{extra_args} {' '.join(appended)}".strip()
            if extra_args
            else " ".join(appended)
        )
    data["extra_args"] = extra_args

    unknown = (
        set(data)
        - _KNOWN_MODEL_CONFIG_FIELDS
        - _LEGACY_SILENT_DROP_FIELDS
    )
    if unknown:
        raise ValueError(
            f"Model {name!r}: unrecognized config key(s) {sorted(unknown)} "
            f"— refusing to guess a migration. Fix or remove the entry in "
            f"config.json."
        )

    return data, dirty


# Derived from the single LAUNCHER_STATE_DIR base (issue #196). With
# LAUNCHER_STATE_DIR unset, this resolves to ~/.llauncher exactly as
# before.
CONFIG_DIR = LAUNCHER_STATE_DIR
CONFIG_PATH = CONFIG_DIR / "config.json"

# Set once the resolved CONFIG_PATH has been logged at INFO for this
# process (issue #403). Repeat loads (every ``/models`` poll, every CLI
# subcommand, etc.) log at DEBUG instead so a process reading an
# unexpected state dir is still self-diagnosing without spamming the log
# at INFO/WARNING on every refresh cycle.
_path_logged = False


class ConfigStore:
    """Persistent storage for model configurations."""

    @classmethod
    def load(cls) -> dict[str, ModelConfig]:
        """Load configurations from disk.

        PARSE-AT-THE-DOOR (issue #403): a missing config is a legitimate
        first-run state and is tolerated -- but observably, via a logged
        WARNING naming the resolved path. An *existing* config that
        cannot be read or parsed is a configuration error, not an empty
        registry: both ``OSError`` (permissions, I/O) and
        ``json.JSONDecodeError`` (corrupt file) are raised, never
        swallowed into ``{}``. Collapsing "no models," "unreadable
        config," and "corrupt config" into the same HTTP-200-empty-list
        response is exactly the defect this fix removes -- see the
        live-observed incident in issue #403.

        Returns:
            Dictionary mapping model names to ModelConfig.

        Raises:
            OSError: The config file exists but could not be read
                (permissions, I/O error, etc.).
            json.JSONDecodeError: The config file exists but is not
                valid JSON.
        """
        global _path_logged
        if _path_logged:
            logger.debug("Loading config from %s", CONFIG_PATH)
        else:
            logger.info("Loading config from %s", CONFIG_PATH)
            _path_logged = True

        if not CONFIG_PATH.exists():
            logger.warning(
                "No config file found at %s; treating as empty registry "
                "(expected on first run).",
                CONFIG_PATH,
            )
            return {}

        try:
            # utf-8-sig: PARSE-AT-THE-DOOR tolerance for a hand-edited
            # config.json carrying a Windows-editor UTF-8 BOM (#310) --
            # same defect class, and same fix, as the registry.py reads.
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))

            # ADR-026 / issue #477: one-shot migration at the door. Each
            # entry's dropped llama-server mirror fields are folded into
            # extra_args (or dropped outright if extra_args already wins)
            # before ModelConfig ever sees them. A ValueError here (bad
            # extra_args quoting, an unrecognized persisted key) is not
            # caught -- it propagates and fails this load loudly, same as
            # a corrupt config, rather than tolerating an unrecognized
            # shape (PARSE-AT-THE-DOOR).
            migrated: dict[str, dict] = {}
            any_dirty = False
            for name, cfg in data.items():
                cfg, dirty = _migrate_config_dict(name, cfg)
                migrated[name] = cfg
                any_dirty = any_dirty or dirty

            # Use from_dict_unvalidated to skip path validation for persisted configs
            models = {
                name: ModelConfig.from_dict_unvalidated(cfg)
                for name, cfg in migrated.items()
            }

            if any_dirty:
                logger.info(
                    "Migrating %d model config(s) at %s to drop llama-server "
                    "mirror fields (ADR-026 / issue #477); rewriting once.",
                    len(models),
                    CONFIG_PATH,
                )
                cls.save(models)

            return models
        except OSError as e:
            logger.error("Cannot read config at %s: %s", CONFIG_PATH, e)
            raise OSError(f"Cannot read config at {CONFIG_PATH}: {e}") from e
        except json.JSONDecodeError as e:
            logger.error("Corrupt config at %s: %s", CONFIG_PATH, e)
            raise json.JSONDecodeError(
                f"Corrupt config at {CONFIG_PATH}: {e.msg}", e.doc, e.pos
            ) from e

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
                Recorded in the audit log per ADR-LLNCH-008 / issue #60.
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
        # ``AuditEntry`` has no payload field by design (ADR-LLNCH-008); the
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
