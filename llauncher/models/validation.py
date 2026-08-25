"""Result types for the read-only ``model validate`` verb (issue #475, ADR-027).

The floor for the validation surface — imports ``pydantic`` and stdlib
only, nothing from ``core``/``operations``/anywhere upward (rule 3,
``docs/ARCHITECTURE.md``). One type crosses CLI, HTTP, MCP, and UI so the
verdict vocabulary is never forked across doors again.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ValidationVerdict(BaseModel):
    """A single named check's outcome for one model.

    ``advisory=True`` means the check is reported but never gates
    ``ModelValidation.ok`` (e.g. ``vram``, ``lockfile`` — see ADR-027 §3).
    """

    check: str  # "weights" | "gguf_magic" | "vram" | "lockfile"
    ok: bool
    reason: str = ""  # empty iff ok
    advisory: bool = False


class ModelValidation(BaseModel):
    """Validation outcome for a single configured model."""

    name: str
    model_path: str  # as configured
    resolved_path: str | None = None  # after symlink + shard resolution
    exists: bool = False
    size_bytes: int | None = None
    last_modified: datetime | None = None
    running_port: int | None = None
    verdicts: list[ValidationVerdict] = Field(default_factory=list)
    ok: bool = False  # all non-advisory verdicts ok


class ValidationReport(BaseModel):
    """Aggregate validation outcome across the checked models."""

    checked_at: datetime
    ok: bool  # all(m.ok for m in models)
    models: list[ModelValidation] = Field(default_factory=list)
