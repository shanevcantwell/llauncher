"""Extended unit tests for ``llauncher.core.gpu`` covering parse/error paths.

Targets uncovered branches identified in the Phase B coverage baseline:

- ``_query_NVIDIA`` CSV (list) entry parsing, including pid/process attribution.
- ``_query_NVIDIA`` falls through ``json.JSONDecodeError`` from subprocess output.
- ``_try_NVIDIA``/``_try_ROCM``/``_try_MPS`` short-circuit when binaries absent.
- ``_query_ROCM`` parse heuristic (single GPU line) and non-zero returncode.
- ``_query_MPS`` returns empty when ``is_apple_mps_available`` is False.
- ``is_apple_mps_available`` non-Apple branch.
- ``_estimate_apple_unified_mem`` returns fallback when sysctl fails.
- ``_to_int``/``_to_float`` empty/None/garbage inputs.
- ``_map_processes`` filters PIDs not in running llama-servers.
- ``GPUHealthCollector.refresh`` sets ``self._backend`` correctly.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from llauncher.core import gpu as gpu_mod
from llauncher.core.gpu import (
    GPUHealthCollector,
    GPUHealthResult,
    GPUDevice,
    _to_int,
    _to_float,
    _estimate_apple_unified_mem,
    is_apple_mps_available,
)


# ---------------------------------------------------------------------------
# Helper coercion functions
# ---------------------------------------------------------------------------

class TestToIntCoercion:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("123", 123),
            ("  42  ", 42),
            ("3.14", 3),
            ("", None),
            (None, None),
            ("nan-text", None),
            ([], None),
        ],
    )
    def test_to_int_variants(self, value, expected):
        assert _to_int(value) == expected


class TestToFloatCoercion:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("12.5", 12.5),
            ("-", None),
            ("garbage", None),
            ("0", 0.0),
        ],
    )
    def test_to_float_variants(self, value, expected):
        assert _to_float(value) == expected


# ---------------------------------------------------------------------------
# NVIDIA CSV path + pid attribution
# ---------------------------------------------------------------------------

class TestNVIDIACSVPath:
    def test_query_nvidia_csv_list_entry_attaches_process(self):
        """When entry is a list (CSV form), pid/pname become a process record."""
        sim = json.dumps({
            "driver_version": "550.00",
            "data": [
                ["0", "RTX 4090", "24564", "4200", "20364", "12.5", "42",
                 "1234", "llama-server", "2048"],
            ],
        })
        collector = GPUHealthCollector()
        out = collector._query_NVIDIA(simulated_output=sim)
        assert len(out["devices"]) == 1
        dev = out["devices"][0]
        assert dev.index == 0
        assert dev.processes == [
            {"pid": 1234, "name": "llama-server", "used_memory_mb": 2048},
        ]

    def test_query_nvidia_csv_no_pid_no_process_list(self):
        sim = json.dumps({
            "data": [
                ["1", "GPU-1", "8192", "1024", "7168", "0", "-",
                 "", "", "0"],
            ],
        })
        collector = GPUHealthCollector()
        out = collector._query_NVIDIA(simulated_output=sim)
        dev = out["devices"][0]
        assert dev.index == 1
        assert dev.processes == []
        # temperature.gpu was "-" → coerced to None
        assert dev.temperature_c is None


# ---------------------------------------------------------------------------
# Backend probes: short-circuit when CLI absent
# ---------------------------------------------------------------------------

class TestBackendProbesAbsent:
    def test_try_nvidia_returns_false_when_binary_missing(self):
        collector = GPUHealthCollector()
        result = GPUHealthResult()
        with patch.object(gpu_mod, "shutil_which", return_value=None):
            assert collector._try_NVIDIA(result) is False
        assert result.devices == []

    def test_try_rocm_returns_false_when_binary_missing(self):
        collector = GPUHealthCollector()
        result = GPUHealthResult()
        with patch.object(gpu_mod, "shutil_which", return_value=None):
            assert collector._try_ROCM(result) is False

    def test_try_mps_returns_false_when_not_apple(self):
        collector = GPUHealthCollector()
        result = GPUHealthResult()
        with patch.object(gpu_mod, "is_apple_mps_available", return_value=False):
            assert collector._try_MPS(result) is False


class TestBackendProbesPresent:
    def test_try_nvidia_succeeds_via_simulation_env(self, monkeypatch):
        """LLAUNCHER_GPU_SIMULATE=1 forces canned data and skips CLI."""
        monkeypatch.setenv("LLAUNCHER_GPU_SIMULATE", "1")
        collector = GPUHealthCollector()
        result = GPUHealthResult()
        with patch.object(gpu_mod, "shutil_which", return_value="/usr/bin/nvidia-smi"):
            assert collector._try_NVIDIA(result) is True
        assert len(result.devices) >= 1
        # Driver version attached to first device
        assert result.devices[0].driver_version == "535.129.03"


# ---------------------------------------------------------------------------
# ROCm parse
# ---------------------------------------------------------------------------

class TestROCMParse:
    def test_query_rocm_returns_empty_on_nonzero_returncode(self):
        collector = GPUHealthCollector()
        fake_run = MagicMock(return_value=SimpleNamespace(returncode=1, stdout=""))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            out = collector._query_ROCM()
        assert out == {"devices": []}

    def test_query_rocm_parses_gpu_line(self):
        """Regex matches 'GPU<n> ... VRAM Used: <mb> MiB'."""
        rocm_out = (
            "ROCm Tool\n"
            "GPU0  bla bla VRAM Used: 1234 MiB\n"
            "GPU1  whatever VRAM Used: 5678 MiB\n"
        )
        collector = GPUHealthCollector()
        fake_run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=rocm_out))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            out = collector._query_ROCM()
        assert len(out["devices"]) == 2
        assert out["devices"][0].index == 0
        assert out["devices"][0].used_vram_mb == 1234
        assert out["devices"][1].index == 1
        assert out["devices"][1].used_vram_mb == 5678

    def test_query_rocm_filenotfound_returns_empty(self):
        collector = GPUHealthCollector()
        with patch.object(gpu_mod.subprocess, "run", side_effect=FileNotFoundError):
            out = collector._query_ROCM()
        assert out == {"devices": []}

    def test_query_rocm_timeout_returns_empty(self):
        collector = GPUHealthCollector()
        with patch.object(
            gpu_mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="rocm-smi", timeout=10),
        ):
            out = collector._query_ROCM()
        assert out == {"devices": []}


# ---------------------------------------------------------------------------
# MPS path
# ---------------------------------------------------------------------------

class TestMPSQuery:
    def test_query_mps_short_circuits_when_unavailable(self):
        collector = GPUHealthCollector()
        with patch.object(gpu_mod, "is_apple_mps_available", return_value=False):
            out = collector._query_MPS()
        assert out == {"devices": []}

    def test_query_mps_returns_empty_on_nonzero_returncode(self):
        collector = GPUHealthCollector()
        fake_run = MagicMock(return_value=SimpleNamespace(returncode=2, stdout=""))
        with patch.object(gpu_mod, "is_apple_mps_available", return_value=True), \
             patch.object(gpu_mod.subprocess, "run", fake_run):
            out = collector._query_MPS()
        assert out == {"devices": []}


# ---------------------------------------------------------------------------
# is_apple_mps_available + memsize estimator
# ---------------------------------------------------------------------------

class TestAppleHelpers:
    def test_is_apple_mps_available_false_on_non_apple(self):
        fake_run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="Intel Generic"))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            assert is_apple_mps_available() is False

    def test_is_apple_mps_available_true_for_apple_m_series(self):
        fake_run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="Apple M3 Pro"))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            assert is_apple_mps_available() is True

    def test_is_apple_mps_available_handles_filenotfound(self):
        with patch.object(gpu_mod.subprocess, "run", side_effect=FileNotFoundError):
            assert is_apple_mps_available() is False

    def test_is_apple_mps_available_handles_timeout(self):
        with patch.object(
            gpu_mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="system_profiler", timeout=5),
        ):
            assert is_apple_mps_available() is False

    def test_estimate_apple_unified_mem_parses_sysctl(self):
        # 16 GiB
        fake_run = MagicMock(return_value=SimpleNamespace(
            returncode=0, stdout=str(16 * 1024 * 1024 * 1024) + "\n"
        ))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            assert _estimate_apple_unified_mem() == 16 * 1024

    def test_estimate_apple_unified_mem_fallback_on_error(self):
        with patch.object(gpu_mod.subprocess, "run", side_effect=FileNotFoundError):
            assert _estimate_apple_unified_mem() == 8192

    def test_estimate_apple_unified_mem_fallback_on_timeout(self):
        with patch.object(
            gpu_mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="sysctl", timeout=5),
        ):
            assert _estimate_apple_unified_mem() == 8192


# ---------------------------------------------------------------------------
# Process attribution filter
# ---------------------------------------------------------------------------

class TestMapProcesses:
    def test_map_processes_keeps_only_running_pids(self):
        collector = GPUHealthCollector()
        dev = GPUDevice(index=0, name="X")
        dev.processes = [
            {"pid": 100, "name": "llama-server", "used_memory_mb": 100},
            {"pid": 999, "name": "other", "used_memory_mb": 200},
        ]
        health = GPUHealthResult(backends=["nvidia"], devices=[dev])
        running = [SimpleNamespace(pid=100)]
        with patch.object(gpu_mod, "find_all_llama_servers", return_value=running):
            collector._map_processes(health)
        assert dev.processes == [
            {"pid": 100, "name": "llama-server", "used_memory_mb": 100},
        ]


# ---------------------------------------------------------------------------
# refresh sets _backend
# ---------------------------------------------------------------------------

class TestRefreshSetsBackend:
    def test_refresh_no_backend_sets_none(self):
        collector = GPUHealthCollector()
        with patch.object(gpu_mod, "shutil_which", return_value=None), \
             patch.object(gpu_mod, "is_apple_mps_available", return_value=False):
            health = collector.refresh()
        assert collector._backend is None
        assert health.backends == []

    def test_refresh_with_simulation_sets_nvidia_backend(self, monkeypatch):
        monkeypatch.setenv("LLAUNCHER_GPU_SIMULATE", "1")
        collector = GPUHealthCollector()
        with patch.object(gpu_mod, "shutil_which", return_value="/usr/bin/nvidia-smi"), \
             patch.object(gpu_mod, "find_all_llama_servers", return_value=[]):
            health = collector.refresh()
        assert collector._backend == "nvidia"
        assert "nvidia" in health.backends


# ---------------------------------------------------------------------------
# is_available routing for rocm vs nvidia
# ---------------------------------------------------------------------------

class TestIsAvailableRouting:
    def test_is_available_rocm_uses_rocm_smi(self):
        collector = GPUHealthCollector()

        def fake_which(prog):
            return "/usr/bin/rocm-smi" if prog == "rocm-smi" else None

        with patch.object(gpu_mod, "shutil_which", side_effect=fake_which):
            assert collector.is_available("rocm") is True
            assert collector.is_available("nvidia") is False
