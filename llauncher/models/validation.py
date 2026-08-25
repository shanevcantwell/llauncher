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


# Status vocabulary (ADR-027 §5). One token per distinguishable outcome —
# collapsing every gating failure to ``MISSING`` sends an operator hunting
# for weights that are on disk but unreadable, truncated, or corrupt.
STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_UNREADABLE = "UNREADABLE"
STATUS_TOO_SMALL = "TOO_SMALL"
STATUS_BAD_MAGIC = "BAD_MAGIC"
STATUS_INVALID = "INVALID"  # gating failure with no more specific token
STATUS_STALE_LOCK = "STALE_LOCK"
STATUS_VRAM = "VRAM?"

# ``ModelHealthResult.reason`` -> status token. The reasons are the literal
# strings ``core.model_health`` sets; anything else falls through to
# ``STATUS_INVALID`` rather than being mislabelled.
_WEIGHTS_REASON_STATUS = {
    "not found": STATUS_MISSING,
    "unreadable": STATUS_UNREADABLE,
    "too small": STATUS_TOO_SMALL,
}


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

    @property
    def status(self) -> str:
        """ASCII status token for this entry (ADR-027 §5).

        Gating failures win over advisories, and the most specific token
        wins: a present-but-corrupt 4 GB ``.gguf`` reports ``BAD_MAGIC``,
        never ``MISSING``. ASCII-only by construction — no glyph on the
        CLI's cp1252 path (#471-class).
        """
        for verdict in self.verdicts:
            if verdict.ok or verdict.advisory:
                continue
            if verdict.check == "weights":
                return _WEIGHTS_REASON_STATUS.get(verdict.reason, STATUS_INVALID)
            if verdict.check == "gguf_magic":
                return STATUS_BAD_MAGIC
            return STATUS_INVALID

        for verdict in self.verdicts:
            if verdict.ok or not verdict.advisory:
                continue
            if verdict.check == "lockfile":
                return STATUS_STALE_LOCK
            if verdict.check == "vram":
                return STATUS_VRAM

        return STATUS_OK


class ValidationReport(BaseModel):
    """Aggregate validation outcome across the checked models."""

    checked_at: datetime
    ok: bool  # all(m.ok for m in models)
    models: list[ModelValidation] = Field(default_factory=list)
