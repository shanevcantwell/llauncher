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

from llauncher.core import gpu as gpu_mod
from llauncher.core import model_health as mh
from llauncher.models.config import ModelConfig

logger = logging.getLogger(__name__)


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


def default_model_health_check(config: ModelConfig) -> tuple[bool, str]:
    """Wrap :func:`llauncher.core.model_health.check_model_health` for swap pre-flight.

    Returns ``(True, "")`` when the model file passes existence,
    readability, and minimum-size checks; otherwise ``(False, reason)``
    with the underlying ``ModelHealthResult.reason`` string.
    """
    result = mh.check_model_health(config.model_path)
    if result.valid:
        return True, ""
    reason = result.reason or "model file invalid"
    return False, reason


def default_vram_check(config: ModelConfig) -> tuple[bool, str]:
    """VRAM-headroom check for swap pre-flight.

    Strategy:

    - Query :class:`llauncher.core.gpu.GPUHealthCollector` for current device
      state. If no GPU backend is detected, treat the check as a no-op
      pass — the process will fail naturally if the host can't run the
      model. This matches the agent-routing behavior.
    - Compute :func:`estimate_vram_mb` for ``config``.
    - Pass if **any** device reports ``free_vram_mb >= required``. We pick
      the most-free device rather than enforcing an exact placement; the
      single-user / single-GPU-per-node scope (handoff §3) makes that
      sufficient.
    - Otherwise fail with the required and best-available numbers in the
      reason string.
    """
    collector = gpu_mod.GPUHealthCollector()
    health = collector.get_health()

    backends = health.get("backends") or []
    if not backends:
        # No GPU detected — skip the check rather than block on missing tools.
        return True, ""

    devices = health.get("devices") or []
    if not devices:
        return True, ""

    required_mb = estimate_vram_mb(config)
    best_free = max(int(d.get("free_vram_mb") or 0) for d in devices)

    if best_free >= required_mb:
        return True, ""

    return False, (
        f"insufficient VRAM: need ~{required_mb} MiB, "
        f"best free device has {best_free} MiB"
    )
