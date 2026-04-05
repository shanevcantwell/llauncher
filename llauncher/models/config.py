"""Pydantic models for llauncher configuration."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ModelConfig(BaseModel):
    """Configuration for a single llama-server model."""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    model_path: str
    mmproj_path: str | None = None
    port: int = Field(ge=1024, le=65535)
    host: str = "0.0.0.0"
    n_gpu_layers: int = Field(default=255, ge=0)
    ctx_size: int = Field(default=131072, gt=0)
    threads: int | None = None
    threads_batch: int = Field(default=8, gt=0)
    ubatch_size: int = Field(default=512, gt=0)
    flash_attn: Literal["on", "off", "auto"] = "on"
    no_mmap: bool = False
    cache_type_k: Literal["f32", "f16", "bf16", "q8_0"] | None = None
    cache_type_v: Literal["f32", "f16", "bf16", "q8_0"] | None = None
    n_cpu_moe: int | None = Field(default=None, ge=0)
    extra_args: list[str] = []
    _skip_path_validation: bool = False  # Internal flag for discovery

    @field_validator("model_path", mode="before")
    @classmethod
    def model_exists(cls, v: str, info) -> str:
        """Validate that model path exists (supports shard patterns)."""
        # Skip validation during discovery or when explicitly set
        if getattr(cls, "_skip_path_validation", False):
            return v

        path = Path(v)
        if not path.exists():
            # Check if this might be a shard pattern (first shard)
            if "-of-" in v:
                # Extract base path for shard files
                base = path.parent / (path.stem.rsplit("-of-", 1)[0] + ".gguf")
                if not base.exists():
                    raise ValueError(f"Model path does not exist: {v}")
            else:
                raise ValueError(f"Model path does not exist: {v}")
        return v

    @classmethod
    def from_dict_unvalidated(cls, data: dict) -> "ModelConfig":
        """Create from dictionary without path validation."""
        cls._skip_path_validation = True
        try:
            return cls.model_validate(data)
        finally:
            cls._skip_path_validation = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

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
    """Audit log entry for actions taken."""

    timestamp: datetime
    action: str
    model: str
    caller: str
    result: Literal["success", "error", "validation_error"]
    message: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "model": self.model,
            "caller": self.caller,
            "result": self.result,
            "message": self.message,
        }


class ChangeRules(BaseModel):
    """Rules for validating actions before execution."""

    whitelisted_models: set[str] = Field(default_factory=set)
    blacklisted_ports: set[int] = Field(default_factory=lambda: {8080})
    blacklisted_callers: set[str] = Field(default_factory=set)

    def validate_start(self, config: ModelConfig, caller: str) -> tuple[bool, str]:
        """Validate if a model can be started."""
        if config.port in self.blacklisted_ports:
            return False, f"Port {config.port} is blacklisted"
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
