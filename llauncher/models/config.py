"""Pydantic models for llauncher configuration.

Per ADR-010: port is a deployment-time concern handled at the call site,
not an attribute of ``ModelConfig``. Per Issue #42 scaffolding: ``kind``
field discriminates the backend inference engine; only ``llama_server``
is implemented in M1, vLLM follows in M6.
"""

import shlex
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


# Deny-list of llama-server flags llauncher manages at its own boundary
# (Issue #81 / security-hardening-plan §3 C7). These flags must not appear
# in :attr:`ModelConfig.extra_args` because:
#
# * ``--api-key`` / ``--alias`` — security-sensitive identity that
#   llauncher owns (#87/#10 landed; ``--alias`` is emitted by
#   ``build_command`` from :attr:`ModelConfig.name` per issue #120 /
#   EMIT-CANONICAL). A config slipping one of these in would silently
#   override llauncher's minted identity — launcher-owned flags stay
#   launcher-owned.
# * ``-m`` / ``--model`` — set by ``build_command`` from
#   :attr:`ModelConfig.model_path` (``core/process.py``). Duplication
#   bypasses the path validator on ``model_path``.
# * ``--host`` / ``--port`` — supplied at start time as runtime
#   parameters (ADR-010). An override here defeats port allocation
#   and the loopback-default binding (C2, PR #75).
#
# Kept intentionally small; the rest of llauncher's managed flags
# (``--ctx-size``, sampling params, etc.) are merely "would conflict",
# not "must be controlled at the boundary". Operators get a clearer
# error from llama-server's argv parser for those.
DENIED_EXTRA_ARG_FLAGS: frozenset[str] = frozenset({
    "--api-key",
    "--alias",
    "-m",
    "--model",
    "--host",
    "--port",
})


class BackendKind(str, Enum):
    """Inference backend discriminator (Issue #42 scaffolding).

    Only ``LLAMA_SERVER`` is implemented in M1. Additional kinds (vLLM, TGI,
    etc.) are introduced under ADR-012 in M6.
    """

    LLAMA_SERVER = "llama_server"


class ModelConfig(BaseModel):
    """Configuration for a single inference server model.

    Note that this model does **not** carry port information — port is
    supplied at call time per ADR-010.
    """

    # ``validate_assignment``: the ``extra_args`` deny-list (C7) is also
    # enforced on field assignment, not only at construction time. Without
    # this, a caller that mutates ``cfg.extra_args`` after construction
    # would silently bypass the deny-list (review of PR #101 / Issue #81).
    # Production assignment surface today: ``mcp_server/tools/config.py``
    # ``update_model_config``.
    model_config = {"arbitrary_types_allowed": True, "validate_assignment": True}

    name: str
    model_path: str
    kind: BackendKind = BackendKind.LLAMA_SERVER
    mmproj_path: str | None = None
    n_gpu_layers: int = Field(default=255, ge=0)
    ctx_size: int = Field(default=131072, gt=0)
    np: int | None = Field(default=None, ge=1, description="Number of KV cache pages")
    threads: int | None = None
    threads_batch: int = Field(default=8, gt=0)
    ubatch_size: int = Field(default=512, gt=0)
    batch_size: int | None = None
    flash_attn: Literal["on", "off", "auto"] = "on"
    no_mmap: bool = False
    cache_type_k: Literal["f32", "f16", "bf16", "q8_0"] | None = None
    cache_type_v: Literal["f32", "f16", "bf16", "q8_0"] | None = None
    n_cpu_moe: int | None = Field(default=None, ge=0)
    parallel: int = Field(default=1, gt=0)
    temperature: float | None = None
    top_k: int | None = None
    top_p: float | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    reverse_prompt: str | None = None
    mlock: bool = False
    extra_args: str = ""

    @field_validator("extra_args", mode="before")
    @classmethod
    def extra_args_no_managed_flags(cls, v):
        """Reject ``extra_args`` tokens that collide with llauncher-managed flags.

        Mirrors the runtime ``shlex.split`` at
        ``llauncher/core/process.py`` so the boundary check sees argv the
        same way the launcher will. Both bare (``--api-key foo``) and
        equals (``--api-key=foo``) forms are rejected.

        Implements security-hardening-plan §3 C7 (Issue #81). See
        :data:`DENIED_EXTRA_ARG_FLAGS` for the curated deny-list and the
        rationale for each entry.
        """
        if v is None or v == "":
            return v
        # Legacy ``list[str]`` shape is normalized to ``str`` in
        # ``from_dict_unvalidated`` *before* validation runs, but defend
        # in depth so the validator behaves sanely if called directly.
        if isinstance(v, list):
            tokens = [str(t) for t in v]
        else:
            try:
                tokens = shlex.split(str(v))
            except ValueError as e:
                # Malformed shell-quoting (unbalanced quote, etc.) —
                # surface as a validation error rather than letting
                # subprocess construction blow up at start time.
                raise ValueError(f"extra_args is not a valid shell token string: {e}")

        for token in tokens:
            # Match both bare flag and ``--flag=value`` form. We compare
            # the head before ``=`` so ``--api-key=foo`` is rejected
            # identically to ``--api-key foo``.
            head = token.split("=", 1)[0]
            if head in DENIED_EXTRA_ARG_FLAGS:
                raise ValueError(
                    f"extra_args contains llauncher-managed flag "
                    f"{head!r} — set it via the dedicated ModelConfig "
                    f"field or remove it. See security-hardening-plan §3 C7."
                )
        return v

    @field_validator("model_path", mode="before")
    @classmethod
    def model_exists(cls, v: str, info) -> str:
        """Validate that the model path exists (supports shard patterns)."""
        if _skip_path_validation_var.get():
            return v

        path = Path(v)
        if not path.exists():
            if "-of-" in v:
                base = path.parent / (path.stem.rsplit("-of-", 1)[0] + ".gguf")
                if not base.exists():
                    raise ValueError(f"Model path does not exist: {v}")
            else:
                raise ValueError(f"Model path does not exist: {v}")
        return v

    @classmethod
    def from_dict_unvalidated(cls, data: dict) -> "ModelConfig":
        """Create from dictionary without path validation.

        Silent migration of legacy fields (per the v2 migration policy:
        old data is not precious; user re-specifies if needed):

        - Drops ``default_port`` (per ADR-010: port is a call-site concern).
        - Drops ``port`` (legacy synonym, same reason).
        - Drops ``host`` (legacy; defaults handled at start time).
        - Migrates ``extra_args`` from ``list[str]`` to ``str``.
        """
        data = data.copy()
        # Silent drop of port-related legacy fields per ADR-010.
        data.pop("default_port", None)
        data.pop("port", None)
        data.pop("host", None)
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

    Per ADR-010, ``port`` is now a required argument for start/swap
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
