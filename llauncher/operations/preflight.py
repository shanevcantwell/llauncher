"""Default pre-flight check adapters for the swap mechanic.

Per ADR-005 (model health) and ADR-006 (GPU/VRAM monitoring). These
functions adapt :mod:`llauncher.core.model_health` and
:mod:`llauncher.core.gpu` into the
:data:`llauncher.operations.swap.PreflightCheck` shape — a callable
``(ModelConfig) -> (ok: bool, reason: str)`` — so the swap mechanic
can compose them uniformly.

Callers may override the defaults via ``swap()``'s
``model_health_check`` and ``vram_check`` keyword arguments. Passing
``None`` for either disables that check entirely (useful in unit
tests with synthetic configs).

Note: the VRAM heuristic here duplicates the estimator in
``llauncher/agent/routing.py``. Consolidating both into this module
is a future cleanup — tracked separately rather than rolled into the
slice-2 wiring.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from llauncher.core import gpu as gpu_mod
from llauncher.core import model_health as mh
from llauncher.models.config import ModelConfig

logger = logging.getLogger(__name__)


# Type alias for pre-flight check seams. Returns ``(ok, reason)``;
# ``reason`` is empty when ``ok`` is True. ``None`` (when accepted by a
# verb) means the check is skipped entirely. Lives here rather than in
# ``swap.py`` so both ``start`` and ``swap`` can reuse it.
PreflightCheck = Callable[[ModelConfig], "tuple[bool, str]"]


# Cap on how many startup-log lines a verb attaches to its result on failure,
# preserving ADR-002's prior shape (referenced in ADR-011 open question 2).
# Lives here rather than in ``swap.py`` so both ``start`` and ``swap`` import
# it from a neutral source rather than one verb reaching into the other.
STARTUP_LOG_TAIL_MAX = 100

# Default readiness-poll timeout in seconds (ADR-011 open question 1). Shared
# home for the same reason as ``STARTUP_LOG_TAIL_MAX`` above.
DEFAULT_READINESS_TIMEOUT_S = 120


def _tail_logs(logs: list[str]) -> list[str]:
    """Cap startup logs to the last ``STARTUP_LOG_TAIL_MAX`` lines."""
    if len(logs) <= STARTUP_LOG_TAIL_MAX:
        return list(logs)
    return list(logs[-STARTUP_LOG_TAIL_MAX:])


def run_preflight_check(
    check: PreflightCheck | None,
    config: ModelConfig,
    label: str,
) -> tuple[bool, str]:
    """Invoke an optional pre-flight check, defaulting to pass when ``None``.

    Catches exceptions from the check itself and converts them into a
    structured failure (``ok=False``) so a buggy adapter can never crash the
    surrounding verb. The exception is logged at ERROR via
    :func:`logging.Logger.exception` for diagnostics.
    """
    if check is None:
        return True, ""
    try:
        ok, reason = check(config)
    except Exception as exc:  # noqa: BLE001 — failure here must not crash the verb
        logger.exception("%s pre-flight check raised; treating as failure", label)
        return False, f"{label} check raised: {exc}"
    return ok, reason


# Heuristic constant: VRAM (MiB) per billion parameters at ~Q4_K_M quantization.
# Conservative — overestimates slightly to leave a safety margin for KV cache.
VRAM_MB_PER_B_PARAMS = 1024

# Default fallback parameter count when the model name doesn't expose one.
# Matches the agent-routing fallback (7 B is the most common community size).
DEFAULT_PARAM_BILLIONS = 7.0

# Typical max-layers used to scale partial GPU offloads. A coarse heuristic;
# the n_gpu_layers field in ModelConfig is treated as ``unbounded`` when it
# meets or exceeds this threshold.
TYPICAL_MAX_LAYERS = 32


def estimate_vram_mb(config: ModelConfig) -> int:
    """Estimate the VRAM required to run ``config`` on a single GPU.

    Heuristic chain:

    1. Parse a ``<digits>[.digits]b`` token out of the model file path or
       name (e.g. ``llama-3-7b``, ``mistral-7b-v0.1``,
       ``qwen2.5-14b.Q4_K_M.gguf``). On a hit, that's the parameter count.
    2. On miss, fall back to :data:`DEFAULT_PARAM_BILLIONS`.
    3. Multiply by :data:`VRAM_MB_PER_B_PARAMS` for the base estimate.
    4. If ``n_gpu_layers`` is below :data:`TYPICAL_MAX_LAYERS`, scale the
       estimate by ``n_gpu_layers / TYPICAL_MAX_LAYERS`` to account for
       partial-offload configurations.

    The estimate is intentionally rough; treat it as a guard rail, not a
    precise budget. ADR-006 / Issue #42 may refine this when the backend
    adapter layer lands.
    """
    haystack = f"{config.model_path} {config.name}"
    match = re.search(r"(?<!\d)(\d+\.?\d*)\s*[bB]", haystack)
    params_billion = float(match.group(1)) if match else DEFAULT_PARAM_BILLIONS

    base_mb = int(params_billion * VRAM_MB_PER_B_PARAMS)

    n_layers = config.n_gpu_layers
    if n_layers is not None and n_layers < TYPICAL_MAX_LAYERS:
        ratio = max(0.0, min(n_layers / TYPICAL_MAX_LAYERS, 1.0))
        base_mb = int(base_mb * ratio)

    return base_mb


def default_model_health_check(
    config: ModelConfig, *, force_refresh: bool = False
) -> tuple[bool, str]:
    """Wrap :func:`llauncher.core.model_health.check_model_health` for swap pre-flight.

    Returns ``(True, "")`` when the model file passes existence,
    readability, and minimum-size checks; otherwise ``(False, reason)``
    with the underlying ``ModelHealthResult.reason`` string.

    ``force_refresh`` bypasses the 60 s health cache — see
    :func:`llauncher.core.model_health.check_model_health`.
    """
    result = mh.check_model_health(config.model_path, force_refresh=force_refresh)
    if result.valid:
        return True, ""
    reason = result.reason or "model file invalid"
    return False, reason


def default_vram_check(
    config: ModelConfig, *, collector: "gpu_mod.GPUHealthCollector | None" = None
) -> tuple[bool, str]:
    """VRAM-headroom check for swap pre-flight.

    ``collector`` lets a caller that runs this check over *many* configs in
    one pass share a single :class:`~llauncher.core.gpu.GPUHealthCollector`
    — its TTL cache is per-instance, so a fresh collector per call means a
    fresh ``nvidia-smi`` subprocess per call. Use
    :func:`make_vram_check` rather than passing this by hand.

    Strategy:

    - Query :class:`llauncher.core.gpu.GPUHealthCollector` for current device
      state. If no GPU backend is detected at all (genuine CPU-only host),
      treat the check as a no-op pass — the process will fail naturally if
      the host can't run the model. This matches the agent-routing behavior.
    - **Fail loud when a backend is present but exposes no device data.**
      A detected GPU backend with an empty device list means VRAM headroom
      cannot be verified (malformed device query, transient ``nvidia-smi``
      hiccup, etc.). Rather than silently admitting the launch and risking a
      blind OOM, the check refuses with a ``"cannot verify VRAM headroom"``
      reason (#150). This is fail-closed by design: a backend that claims to
      exist must be able to report its devices.
    - **Fail loud (same reason) when devices are present but every one is
      missing ``free_vram_mb``.** A non-empty device list where no entry
      reports usable telemetry is the same "cannot verify" situation as an
      empty list — collapsing it to ``0 MiB`` would misreport unknown
      headroom as a genuine zero (#241). A device list with at least one
      real number takes the normal best-device path below unchanged.
    - Compute :func:`estimate_vram_mb` for ``config``.
    - Pass if **any** device reports ``free_vram_mb >= required``. We pick
      the most-free device rather than enforcing an exact placement; the
      single-user / single-GPU-per-node scope (handoff §3) makes that
      sufficient.
    - Otherwise fail with the required and best-available numbers in the
      reason string.
    """
    if collector is None:
        collector = gpu_mod.GPUHealthCollector()
    health = collector.get_health()

    backends = health.get("backends") or []
    if not backends:
        # No GPU detected — skip the check rather than block on missing tools.
        return True, ""

    devices = health.get("devices") or []
    if not devices:
        # Backend present but no device numbers: VRAM headroom is unknown.
        # Fail loud rather than admit a blind launch that can OOM (#150).
        logger.warning(
            "VRAM pre-flight: backend(s) %s detected but no device data; "
            "refusing launch (cannot verify VRAM headroom)",
            backends,
        )
        return False, (
            f"cannot verify VRAM headroom: GPU backend(s) {backends} detected "
            "but reported no device data"
        )

    free_vram_values = [d.get("free_vram_mb") for d in devices]
    if all(v is None for v in free_vram_values):
        # Devices present but none report usable telemetry: VRAM headroom
        # is unknown, not genuinely zero. Fail loud with the same reason as
        # the empty-devices branch rather than misreport "0 MiB free" (#241).
        logger.warning(
            "VRAM pre-flight: backend(s) %s reported devices but no "
            "free_vram_mb values; refusing launch (cannot verify VRAM headroom)",
            backends,
        )
        return False, (
            f"cannot verify VRAM headroom: GPU backend(s) {backends} reported "
            "devices with no free_vram_mb data"
        )

    required_mb = estimate_vram_mb(config)
    best_free = max(int(v or 0) for v in free_vram_values)

    if best_free >= required_mb:
        return True, ""

    return False, (
        f"insufficient VRAM: need ~{required_mb} MiB, "
        f"best free device has {best_free} MiB"
    )


def make_model_health_check(*, force_refresh: bool = False) -> PreflightCheck:
    """Return a :data:`PreflightCheck` bound to a ``force_refresh`` choice."""

    def _check(config: ModelConfig) -> tuple[bool, str]:
        return default_model_health_check(config, force_refresh=force_refresh)

    return _check


def make_vram_check(
    collector: "gpu_mod.GPUHealthCollector | None" = None,
) -> PreflightCheck:
    """Return a :data:`PreflightCheck` bound to **one** GPU collector.

    Every check produced by a single call shares one collector, so a batch
    validation shells out to ``nvidia-smi`` once (per 5 s TTL window) rather
    than once per model — the N-shell-outs-per-rerun economics ADR-027 §2
    refused to put on the UI hot path.
    """
    shared = collector if collector is not None else gpu_mod.GPUHealthCollector()

    def _check(config: ModelConfig) -> tuple[bool, str]:
        return default_vram_check(config, collector=shared)

    return _check
