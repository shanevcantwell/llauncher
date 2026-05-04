"""Pydantic models for llauncher configuration.

Per ADR-010: port is a deployment-time concern handled at the call site,
not an attribute of ``ModelConfig``. Per Issue #42 scaffolding: ``kind``
field discriminates the backend inference engine; only ``llama_server``
is implemented in M1, vLLM follows in M6.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from llauncher.core.settings import BLACKLISTED_PORTS as _ENV_BLACKLISTED_PORTS


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

    model_config = {"arbitrary_types_allowed": True}

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
    _skip_path_validation: bool = False

    @field_validator("model_path", mode="before")
    @classmethod
    def model_exists(cls, v: str, info) -> str:
        """Validate that the model path exists (supports shard patterns)."""
        if getattr(cls, "_skip_path_validation", False):
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
        cls._skip_path_validation = True
        try:
            return cls.model_validate(data)
        finally:
            cls._skip_path_validation = False

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
