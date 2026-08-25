"""Pydantic models for llauncher configuration.

Per ADR-LLNCH-010: port is a deployment-time concern handled at the call site,
not an attribute of ``ModelConfig``. Per Issue #42 scaffolding: ``kind``
field discriminates the backend inference engine; only ``llama_server``
is implemented in M1, vLLM follows in M6.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, Field, field_validator

from llauncher.core.settings import BLACKLISTED_PORTS as _ENV_BLACKLISTED_PORTS


# Per-context skip flag for ``ModelConfig.model_path`` existence validation
# (issue #88b). A ``ContextVar`` replaces the prior class-attribute toggle,
# which mutated shared class state (``cls._skip_path_validation = True/False``)
# and was racy: concurrent callers could clobber each other's flag — one
# resetting to ``False`` while another was still mid-``model_validate`` with
# the skip expected. A ContextVar is isolated per thread / async task, so each
# caller sees only its own value (default ``False``) and the race cannot occur.
_skip_path_validation_var: ContextVar[bool] = ContextVar(
    "llauncher_skip_path_validation", default=False
)


@contextmanager
def _skip_path_validation() -> Iterator[None]:
    """Suppress ``ModelConfig.model_path`` existence checks within the block.

    Scoped to the calling context only (see :data:`_skip_path_validation_var`);
    nesting is safe because the token-based ``reset`` restores the prior value
    rather than unconditionally clearing to ``False``.
    """
    token = _skip_path_validation_var.set(True)
    try:
        yield
    finally:
        _skip_path_validation_var.reset(token)


# NOTE (ADR-026, issue #477): the llauncher-owned extra_args deny-list
# (``--alias``, ``-m``/``--model``, ``--host``/``--port``, ``--api-key``,
# ``--metrics``, ``--slots``/``--no-slots``) used to be enforced here as a
# pydantic field validator. Per the ADR-026 ratification it is now enforced
# exactly once, at launch time, in ``core/process.py::build_command`` — not
# as schema validation. ``ModelConfig.extra_args`` carries llama-server
# flags verbatim with no pydantic validation of its contents; see
# :data:`llauncher.core.process.DENIED_EXTRA_ARG_FLAGS`.
#
# Issue #399 (``-ctk``/``-ctv`` short-alias registration in the former
# ``MANAGED_NATIVE_FLAG_TO_FIELD`` collision table) is moot as of this
# commit: ``cache_type_k``/``cache_type_v`` are no longer structured
# ``ModelConfig`` fields (dropped with the other 15 llama-server mirror
# fields above), so there is no longer a native/extra_args collision for
# that table to catch — the whole table and its alias-scope caveats are
# gone with it.


def resolve_shard_path(path_str: str) -> Path:
    """Resolve a model path, following the sharded-GGUF fallback pattern.

    A sharded model is configured with the *first-shard* filename (e.g.
    ``model-00001-of-00003.gguf``), but an operator may instead have only
    the merged single-file form (``model.gguf``) on disk, or vice versa.
    Resolution order:

    1. The literal path, if it exists.
    2. For a ``-of-`` sharded name, the base (pre-shard) ``.gguf`` path,
       if *that* exists.
    3. Otherwise, the literal (non-existent) path is returned unchanged so
       callers can report the original configured path in their failure
       reason.

    This is the single source of truth for shard resolution — both
    :meth:`ModelConfig.model_exists` (construction-time validation) and
    :func:`llauncher.core.model_health.check_model_health` (runtime file
    stat) call this, so a sharded entry is never a false negative in one
    surface and a false positive in the other (issue #475 precondition,
    load-bearing for #468's "delete entries with missing weights" rule).
    """
    path = Path(path_str)
    try:
        literal_exists = path.exists()
    except OSError:
        # Defensive: an errno-less/synthetic OSError from a patched or
        # exotic filesystem layer must not propagate out of a resolution
        # helper — treat it as "not found" and fall through to the shard
        # check / literal-path return, exactly as a normal ENOENT would.
        literal_exists = False
    if literal_exists:
        return path
    if "-of-" in path_str:
        base = path.parent / (path.stem.rsplit("-of-", 1)[0] + ".gguf")
        try:
            base_exists = base.exists()
        except OSError:
            base_exists = False
        if base_exists:
            return base
    return path


class BackendKind(str, Enum):
    """Inference backend discriminator (Issue #42 scaffolding).

    Only ``LLAMA_SERVER`` is implemented in M1. Additional kinds (vLLM, TGI,
    etc.) are introduced under ADR-LLNCH-012 in M6.
    """

    LLAMA_SERVER = "llama_server"


class ModelConfig(BaseModel):
    """Configuration for a single inference server model.

    Note that this model does **not** carry port information — port is
    supplied at call time per ADR-LLNCH-010.
    """

    # ``validate_assignment``: field types/constraints are re-checked on
    # assignment, not only at construction time, so a caller that mutates
    # a field after construction cannot install a value the constructor
    # would have rejected. Production assignment surface today:
    # ``mcp_server/tools/config.py`` ``update_model_config``.
    #
    # Note this no longer has anything to do with ``extra_args``: the
    # deny-list validator it originally existed for (PR #101 / issue #81)
    # was deleted by ADR-026 / issue #477, which moved that check to
    # ``core/process.py::build_command``. The setting is kept for the
    # remaining typed fields (``n_gpu_layers``, ``ctx_size``, ``parallel``
    # and friends carry ge/gt constraints worth enforcing on assignment).
    model_config = {"arbitrary_types_allowed": True, "validate_assignment": True}

    name: str
    model_path: str
    kind: BackendKind = BackendKind.LLAMA_SERVER
    mmproj_path: str | None = None
    n_gpu_layers: int = Field(default=255, ge=0)
    ctx_size: int = Field(default=131072, gt=0)
    parallel: int = Field(default=1, gt=0)
    metrics: bool = Field(
        default=True,
        description=(
            "Enable llama-server's Prometheus /metrics endpoint "
            "(--metrics). Default on: negligible overhead, and the clean "
            "structured source for tps/kv-cache/draft-acceptance "
            "telemetry (issue #169)."
        ),
    )
    slots: bool = Field(
        default=False,
        description=(
            "Expose llama-server's /slots monitoring endpoint (--slots). "
            "Default OFF: /slots includes per-slot prompt text, so this "
            "is a sensitive opt-in — note llama-server's own binary "
            "default is the inverse (ENABLED); llauncher always emits "
            "--slots or --no-slots explicitly so the effective policy is "
            "config-driven, not the binary default (ADR-LLNCH-019, "
            "issue #179 PM-2 de-risk)."
        ),
    )
    # ADR-026 / issue #477: ``extra_args`` carries llama-server flags
    # verbatim, in the spelling the operator read out of
    # ``llama-server --help``. There is deliberately no pydantic content
    # validation here — no shell-quoting check, no managed-flag collision
    # guard. Both are enforced exactly once, at launch time, by
    # ``core/process.py::build_command`` (the single enforcement point):
    # a llauncher-owned flag raises ``DeniedExtraArgError`` and
    # unparseable quoting raises ``MalformedExtraArgsError``, both
    # ``ExtraArgsError``.
    #
    # ``ConfigStore.load`` does NOT parse this field's content either: the
    # ADR-026 migration tokenizes ``extra_args`` only for an entry that
    # still carries pre-#477 mirror fields to place, and never again once
    # that entry is migrated. So the read path is exactly as permissive as
    # this write path — a quoting error the UI accepted fails at launch,
    # it never becomes a config llauncher can no longer load.
    extra_args: str = ""

    @field_validator("model_path", mode="before")
    @classmethod
    def model_exists(cls, v: str, info) -> str:
        """Validate that the model path exists (supports shard patterns)."""
        if _skip_path_validation_var.get():
            return v

        if not resolve_shard_path(v).exists():
            raise ValueError(f"Model path does not exist: {v}")
        return v

    @classmethod
    def from_dict_unvalidated(cls, data: dict) -> "ModelConfig":
        """Create from dictionary without path validation.

        Silent migration of legacy fields (per the v2 migration policy:
        old data is not precious; user re-specifies if needed):

        - Drops ``default_port`` (per ADR-LLNCH-010: port is a call-site concern).
        - Drops ``port`` (legacy synonym, same reason).
        - Drops ``host`` (legacy; defaults handled at start time).
        - Drops ``np`` (issue #235: dead, mislabeled duplicate of
          ``parallel`` — never rendered by ``build_command``; live store
          audit confirmed every persisted value was already null).
        - Migrates ``extra_args`` from ``list[str]`` to ``str``.

        The 16 llama-server-mirror fields dropped by ADR-026 / issue #477
        (``cache_type_k``/``v``, ``threads``, ``threads_batch``,
        ``ubatch_size``, ``batch_size``, ``n_cpu_moe``, ``flash_attn``,
        ``no_mmap``, ``mlock``, ``temperature``, ``top_k``, ``top_p``,
        ``min_p``, ``repeat_penalty``, ``reverse_prompt``) are migrated at
        the door by ``ConfigStore.load`` (``core/config.py``) *before* this
        method is called — not here. This method only handles the
        legacy-field drops it always has.
        """
        data = data.copy()
        # Silent drop of port-related legacy fields per ADR-LLNCH-010.
        data.pop("default_port", None)
        data.pop("port", None)
        data.pop("host", None)
        data.pop("np", None)  # #235: dead field, superseded by `parallel`
        # Migrate extra_args from list[str] to str (legacy v1 shape).
        if "extra_args" in data and isinstance(data["extra_args"], list):
            data["extra_args"] = " ".join(data["extra_args"])
        with _skip_path_validation():
            return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> "ModelConfig":
        """Create from dictionary."""
        return cls.model_validate(data)


class RunningServer(BaseModel):
    """Information about a running llama-server process."""

    pid: int
    port: int
    config_name: str
    start_time: datetime
    logs_path: str | None = None

    def uptime_seconds(self) -> int:
        """Get uptime in seconds."""
        return (datetime.now() - self.start_time).seconds

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "pid": self.pid,
            "port": self.port,
            "config_name": self.config_name,
            "start_time": self.start_time.isoformat(),
            "logs_path": self.logs_path,
            "uptime_seconds": self.uptime_seconds(),
        }


class AuditEntry(BaseModel):
    """Legacy v1 audit entry (kept for backward compat during M1).

    The v2 audit log is :mod:`llauncher.core.audit_log` (JSON Lines on
    disk, distinguishes commanded vs. observed events). This model exists
    only so v1 callers continue to import successfully during the M1–M2
    transition; remove once all references move to the v2 module.
    """

    timestamp: datetime
    action: str
    model: str
    caller: str
    result: Literal["success", "error", "validation_error"]
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "model": self.model,
            "caller": self.caller,
            "result": self.result,
            "message": self.message,
        }


class ChangeRules(BaseModel):
    """Rules for validating actions before execution.

    Per ADR-LLNCH-010, ``port`` is now a required argument for start/swap
    validation — there is no fallback to a per-config preferred port.
    """

    whitelisted_models: set[str] = Field(default_factory=set)
    # Sourced from core.settings.BLACKLISTED_PORTS (env-driven) so the
    # validator and the port allocator share a single source of truth.
    # Empty by default; opt in via the BLACKLISTED_PORTS env var or .env.
    blacklisted_ports: set[int] = Field(
        default_factory=lambda: set(_ENV_BLACKLISTED_PORTS)
    )
    blacklisted_callers: set[str] = Field(default_factory=set)

    def validate_start(
        self, config: ModelConfig, caller: str, port: int
    ) -> tuple[bool, str]:
        """Validate if a model can be started on the given port."""
        if port in self.blacklisted_ports:
            return False, f"Port {port} is blacklisted"
        if caller in self.blacklisted_callers:
            return False, f"Caller '{caller}' is blacklisted"
        if self.whitelisted_models and config.name not in self.whitelisted_models:
            return False, f"Model '{config.name}' is not whitelisted"
        return True, "OK"

    def validate_stop(self, port: int, caller: str) -> tuple[bool, str]:
        """Validate if a server can be stopped."""
        if caller in self.blacklisted_callers:
            return False, f"Caller '{caller}' is blacklisted"
        return True, "OK"
