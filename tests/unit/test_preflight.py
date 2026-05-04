"""Unit tests for ``llauncher.operations.preflight``.

Per ADR-005 (model health) + ADR-006 (GPU/VRAM). These adapters bridge
the core health/GPU modules into the swap mechanic's
:data:`PreflightCheck` shape.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from llauncher.core.model_health import ModelHealthResult
from llauncher.models.config import ModelConfig
from llauncher.operations import preflight as pf


def _config(name: str = "mistral-7b", path: str = "/models/mistral-7b.gguf",
            n_gpu_layers: int = 999) -> ModelConfig:
    return ModelConfig.from_dict_unvalidated(
        {
            "name": name,
            "model_path": path,
            "n_gpu_layers": n_gpu_layers,
            "ctx_size": 4096,
        }
    )


# ---------------------------------------------------------------------------
# estimate_vram_mb
# ---------------------------------------------------------------------------


def test_estimate_vram_mb_parses_seven_b() -> None:
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf")
    assert pf.estimate_vram_mb(cfg) == 7 * pf.VRAM_MB_PER_B_PARAMS


def test_estimate_vram_mb_parses_decimal_size() -> None:
    cfg = _config("qwen-2.5-14b", "/models/qwen2.5-14b.Q4_K_M.gguf")
    # 14 B exactly — the 2.5 in "2.5" doesn't match the param regex pattern.
    assert pf.estimate_vram_mb(cfg) == 14 * pf.VRAM_MB_PER_B_PARAMS


def test_estimate_vram_mb_parses_seventy_b() -> None:
    cfg = _config("llama-70b", "/models/llama-3-70b-instruct.Q5_0.gguf")
    assert pf.estimate_vram_mb(cfg) == 70 * pf.VRAM_MB_PER_B_PARAMS


def test_estimate_vram_mb_falls_back_to_default_when_no_param_token() -> None:
    cfg = _config("anonymous", "/models/anonymous-model.gguf")
    expected = int(pf.DEFAULT_PARAM_BILLIONS * pf.VRAM_MB_PER_B_PARAMS)
    assert pf.estimate_vram_mb(cfg) == expected


def test_estimate_vram_mb_scales_with_partial_offload() -> None:
    # Half of TYPICAL_MAX_LAYERS → roughly half of the base estimate.
    half = pf.TYPICAL_MAX_LAYERS // 2
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf", n_gpu_layers=half)
    full = 7 * pf.VRAM_MB_PER_B_PARAMS
    expected = int(full * (half / pf.TYPICAL_MAX_LAYERS))
    assert pf.estimate_vram_mb(cfg) == expected


def test_estimate_vram_mb_unbounded_offload_unchanged() -> None:
    # n_gpu_layers >= TYPICAL_MAX_LAYERS → no scaling applied.
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf",
                  n_gpu_layers=pf.TYPICAL_MAX_LAYERS)
    assert pf.estimate_vram_mb(cfg) == 7 * pf.VRAM_MB_PER_B_PARAMS


# ---------------------------------------------------------------------------
# default_model_health_check
# ---------------------------------------------------------------------------


def test_default_model_health_check_pass() -> None:
    cfg = _config()
    healthy = ModelHealthResult(valid=True, exists=True, readable=True)
    with patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=healthy,
    ):
        ok, reason = pf.default_model_health_check(cfg)

    assert ok is True
    assert reason == ""


def test_default_model_health_check_fail_missing() -> None:
    cfg = _config()
    bad = ModelHealthResult(valid=False, exists=False, reason="not found")
    with patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=bad,
    ):
        ok, reason = pf.default_model_health_check(cfg)

    assert ok is False
    assert reason == "not found"


def test_default_model_health_check_fail_with_no_reason_string() -> None:
    """Defensive: ensure we surface a fallback reason when the underlying
    check returns an invalid result with no reason populated."""
    cfg = _config()
    bad = ModelHealthResult(valid=False, reason=None)
    with patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=bad,
    ):
        ok, reason = pf.default_model_health_check(cfg)

    assert ok is False
    assert reason  # non-empty fallback


# ---------------------------------------------------------------------------
# default_vram_check
# ---------------------------------------------------------------------------


def _patch_gpu(health: dict):
    """Helper to patch ``GPUHealthCollector.get_health`` with a fixed payload."""
    return patch(
        "llauncher.operations.preflight.gpu_mod.GPUHealthCollector.get_health",
        return_value=health,
    )


def test_default_vram_check_no_backend_passes() -> None:
    cfg = _config()
    with _patch_gpu({"backends": [], "devices": []}):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is True
    assert reason == ""


def test_default_vram_check_backend_with_no_devices_passes() -> None:
    cfg = _config()
    with _patch_gpu({"backends": ["nvidia"], "devices": []}):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is True


def test_default_vram_check_sufficient_passes() -> None:
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf")  # ~7168 MiB
    with _patch_gpu({
        "backends": ["nvidia"],
        "devices": [{"index": 0, "name": "RTX 8000", "free_vram_mb": 16000}],
    }):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is True
    assert reason == ""


def test_default_vram_check_insufficient_fails() -> None:
    cfg = _config("llama-70b", "/models/llama-70b.gguf")  # ~70 GiB needed
    with _patch_gpu({
        "backends": ["nvidia"],
        "devices": [{"index": 0, "name": "RTX 4090", "free_vram_mb": 24000}],
    }):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is False
    assert "insufficient vram" in reason.lower()
    assert "24000" in reason


def test_default_vram_check_picks_best_device() -> None:
    """Pass when at least one device has enough VRAM, even if others don't."""
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf")
    with _patch_gpu({
        "backends": ["nvidia"],
        "devices": [
            {"index": 0, "name": "small", "free_vram_mb": 2000},
            {"index": 1, "name": "big", "free_vram_mb": 16000},
        ],
    }):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is True


def test_default_vram_check_handles_missing_free_vram_field() -> None:
    """Resilience: a device without ``free_vram_mb`` is treated as 0 MiB."""
    cfg = _config("mistral-7b", "/models/mistral-7b.gguf")
    with _patch_gpu({
        "backends": ["nvidia"],
        "devices": [{"index": 0, "name": "RTX", "free_vram_mb": None}],
    }):
        ok, reason = pf.default_vram_check(cfg)
    assert ok is False
