"""``validate_models`` verb — read-only model validation (issue #475, ADR-027).

One validation path, reused by the CLI, HTTP Agent, MCP tool, and UI tab.
Reuses the existing preflight adapters (:mod:`llauncher.operations.preflight`)
rather than introducing a fourth verdict vocabulary. Writes nothing: no
config, no lockfile, no audit entry, no reconcile (ADR-027 §4).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from llauncher.core import lockfile as lf
from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig, resolve_shard_path
from llauncher.models.validation import ModelValidation, ValidationReport, ValidationVerdict
from llauncher.operations import preflight

logger = logging.getLogger(__name__)

_GGUF_MAGIC = b"GGUF"


def _weights_verdict(config: ModelConfig) -> ValidationVerdict:
    """The ``weights`` check — existence/readability/size (gates ``ok``)."""
    ok, reason = preflight.default_model_health_check(config)
    return ValidationVerdict(check="weights", ok=ok, reason="" if ok else reason)


def _gguf_magic_verdict(resolved_path: Path, exists: bool) -> ValidationVerdict | None:
    """The ``gguf_magic`` check — read inside the same ``open()`` the
    readability check already implies. Skipped for a non-``.gguf`` suffix;
    a ``.gguf`` that isn't GGUF is a real corruption signal (gates ``ok``).
    """
    if resolved_path.suffix.lower() != ".gguf":
        return None
    if not exists:
        # No file to read magic bytes from — weights already failed and
        # gates ok; don't double-report.
        return None
    try:
        with open(resolved_path, "rb") as f:
            head = f.read(4)
    except OSError as exc:
        return ValidationVerdict(check="gguf_magic", ok=False, reason=str(exc)[:200])
    if head == _GGUF_MAGIC:
        return ValidationVerdict(check="gguf_magic", ok=True)
    return ValidationVerdict(
        check="gguf_magic", ok=False, reason=f"bad magic bytes: {head!r}"
    )


def _vram_verdict(config: ModelConfig) -> ValidationVerdict:
    """The ``vram`` check — always advisory (ADR-027 §3)."""
    ok, reason = preflight.default_vram_check(config)
    return ValidationVerdict(check="vram", ok=ok, reason="" if ok else reason, advisory=True)


def _running_port_and_lockfile_verdict(
    name: str,
) -> tuple[int | None, ValidationVerdict | None]:
    """Scan lockfiles for a claim on ``name``.

    Returns ``(running_port, lockfile_verdict)``. A live lockfile yields
    ``running_port`` populated and no verdict (nothing advisory to report —
    it's healthy). A stale lockfile (dead pid) yields an advisory failure
    verdict and no ``running_port``. Never reconciles (ADR-027 §3 — that's
    ``stop``/``delete``'s job).
    """
    for lock in lf.list_lockfiles():
        if lock.model != name:
            continue
        if lf.is_pid_alive(lock.pid):
            return lock.port, None
        return None, ValidationVerdict(
            check="lockfile",
            ok=False,
            reason=f"stale lockfile on port {lock.port} (pid {lock.pid} not alive)",
            advisory=True,
        )
    return None, None


def _validate_one(name: str, config: ModelConfig, *, vram: bool) -> ModelValidation:
    resolved = resolve_shard_path(config.model_path)
    exists = resolved.exists()

    size_bytes = None
    last_modified = None
    if exists:
        try:
            stat = resolved.stat()
            size_bytes = stat.st_size
            last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            pass

    running_port, lockfile_verdict = _running_port_and_lockfile_verdict(name)

    verdicts: list[ValidationVerdict] = [_weights_verdict(config)]

    magic_verdict = _gguf_magic_verdict(resolved, exists)
    if magic_verdict is not None:
        verdicts.append(magic_verdict)

    # VRAM: advisory, skipped entirely for a currently-running model (its
    # own weights already occupy the VRAM the estimate compares against —
    # ADR-027 §3), and suppressible via vram=False.
    if vram and running_port is None:
        verdicts.append(_vram_verdict(config))

    if lockfile_verdict is not None:
        verdicts.append(lockfile_verdict)

    ok = all(v.ok for v in verdicts if not v.advisory)

    return ModelValidation(
        name=name,
        model_path=config.model_path,
        resolved_path=str(resolved),
        exists=exists,
        size_bytes=size_bytes,
        last_modified=last_modified,
        running_port=running_port,
        verdicts=verdicts,
        ok=ok,
    )


def validate_models(
    names: Sequence[str] | None = None,
    *,
    vram: bool = True,
) -> ValidationReport:
    """Validate one, several, or all configured models — read-only.

    Args:
        names: Model names to validate. ``None`` validates every configured
            model. Unknown names are silently skipped (doors that need a
            usage error for an unknown name — e.g. the CLI's exit code 1 —
            check membership themselves before calling this).
        vram: When ``False``, the VRAM check is skipped entirely (no
            ``nvidia-smi`` shell-out at all), not merely omitted from the
            gate — it was already advisory.

    Returns:
        A :class:`ValidationReport` — never raises on a bad model entry;
        failures are captured as verdicts.
    """
    all_models = ConfigStore.load()

    if names is None:
        selected = list(all_models.items())
    else:
        selected = [(n, all_models[n]) for n in names if n in all_models]

    results = [_validate_one(name, config, vram=vram) for name, config in selected]

    return ValidationReport(
        checked_at=datetime.now(timezone.utc),
        ok=all(m.ok for m in results),
        models=results,
    )
