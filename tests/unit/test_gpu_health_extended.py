"""Extended unit tests for ``llauncher.core.gpu`` covering parse/error paths.

Targets uncovered branches identified in the Phase B coverage baseline:

- ``_query_NVIDIA`` two-query CSV parsing (devices + compute-apps), including
  uuid-keyed pid/process attribution and the #148 valid-field regression pin.
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
# NVIDIA CSV path: two-query shape, parsing, pid attribution (issue #148)
# ---------------------------------------------------------------------------

_UUID_0 = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_UUID_1 = "GPU-11111111-2222-3333-4444-555555555555"

_DEVICE_CSV_TWO_GPUS = (
    f"0, Quadro RTX 8000, {_UUID_0}, 550.00, 46080, 4200, 41880, 12.5, 42\n"
    f"1, Quadro RTX 8000, {_UUID_1}, 550.00, 46080, 8100, 37980, 45.0, 55\n"
)

_PROCESS_CSV = (
    f"{_UUID_1}, 1234, /usr/local/bin/llama-server, 2048\n"
)


def _fake_smi_run(device_csv: str, process_csv: str):
    """Return a subprocess.run stand-in serving the two nvidia-smi queries."""

    def _run(cmd, **kwargs):
        query_arg = cmd[1]
        if query_arg.startswith("--query-gpu="):
            return SimpleNamespace(returncode=0, stdout=device_csv, stderr="")
        if query_arg.startswith("--query-compute-apps="):
            return SimpleNamespace(returncode=0, stdout=process_csv, stderr="")
        raise AssertionError(f"unexpected nvidia-smi query: {cmd}")

    return _run


class TestNVIDIACSVPath:
    def test_query_nvidia_valid_output_populates_devices(self):
        """Real-shaped CSV from both queries → devices + uuid-mapped process."""
        collector = GPUHealthCollector()
        with patch.object(
            gpu_mod.subprocess, "run",
            side_effect=_fake_smi_run(_DEVICE_CSV_TWO_GPUS, _PROCESS_CSV),
        ):
            out = collector._query_NVIDIA(simulated_output=False)

        assert out["driver_version"] == "550.00"
        assert len(out["devices"]) == 2
        dev0, dev1 = out["devices"]
        assert (dev0.index, dev0.name) == (0, "Quadro RTX 8000")
        assert dev0.total_vram_mb == 46080
        assert dev0.free_vram_mb == 41880
        assert dev0.utilization_pct == 12.5
        assert dev0.temperature_c == 42
        # Process attributed to device 1 via gpu_uuid, not device 0.
        assert dev0.processes == []
        assert dev1.processes == [
            {"pid": 1234, "name": "/usr/local/bin/llama-server",
             "used_memory_mb": 2048},
        ]

    def test_query_nvidia_uses_valid_fields_only(self):
        """Pin the #148 bug shape: no per-process fields or json token in
        the device query; per-process fields go to --query-compute-apps."""
        collector = GPUHealthCollector()
        calls: list[list[str]] = []

        def _capture(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(gpu_mod.subprocess, "run", side_effect=_capture):
            collector._query_NVIDIA(simulated_output=False)

        assert len(calls) == 2
        device_cmd, process_cmd = calls
        assert device_cmd[0] == "nvidia-smi"
        # Format token: CSV only — "json" is not a valid nvidia-smi format.
        assert device_cmd[2] == "--format=csv,noheader,nounits"
        assert process_cmd[2] == "--format=csv,noheader,nounits"
        # Device query carries no per-process fields.
        device_fields = device_cmd[1].removeprefix("--query-gpu=").split(",")
        for bogus in ("pid", "process_name", "used_memory_gpu", "used_gpu_memory"):
            assert bogus not in device_fields
        # Per-process fields live on the compute-apps query.
        assert process_cmd[1] == (
            "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory"
        )

    def test_query_nvidia_empty_output_yields_no_devices(self):
        collector = GPUHealthCollector()
        with patch.object(
            gpu_mod.subprocess, "run", side_effect=_fake_smi_run("", ""),
        ):
            out = collector._query_NVIDIA(simulated_output=False)
        assert out == {"driver_version": None, "devices": []}

    def test_query_nvidia_malformed_rows_skipped(self):
        """Short rows, non-numeric index, and unattributable processes are
        skipped without crashing; valid rows still parse."""
        device_csv = (
            "garbage line without enough fields\n"
            f"NaN, Bad Index GPU, {_UUID_1}, 550.00, 1, 1, 1, 0, 0\n"
            f"1, Good GPU, {_UUID_0}, 550.00, 8192, 1024, 7168, 0, [N/A]\n"
        )
        process_csv = (
            "too, few\n"
            f"{_UUID_1}, 99, ghost-device-process, 10\n"   # uuid not in devices
            f"{_UUID_0}, , no-pid, 10\n"                    # missing pid
        )
        collector = GPUHealthCollector()
        with patch.object(
            gpu_mod.subprocess, "run",
            side_effect=_fake_smi_run(device_csv, process_csv),
        ):
            out = collector._query_NVIDIA(simulated_output=False)

        assert len(out["devices"]) == 1
        dev = out["devices"][0]
        assert dev.index == 1
        assert dev.free_vram_mb == 7168
        # temperature.gpu "[N/A]" → coerced to None
        assert dev.temperature_c is None
        assert dev.processes == []

    def test_query_nvidia_device_query_failure_returns_empty(self):
        """Non-zero returncode on the device query → clean empty result,
        and the compute-apps query is not attempted."""
        collector = GPUHealthCollector()
        fake_run = MagicMock(return_value=SimpleNamespace(
            returncode=6, stdout="", stderr='Field "pid" is not a valid field to query.',
        ))
        with patch.object(gpu_mod.subprocess, "run", fake_run):
            out = collector._query_NVIDIA(simulated_output=False)
        assert out == {"driver_version": None, "devices": []}
        assert fake_run.call_count == 1

    def test_query_nvidia_process_query_failure_keeps_devices(self):
        """Compute-apps query failing must not discard device data."""

        def _run(cmd, **kwargs):
            if cmd[1].startswith("--query-gpu="):
                return SimpleNamespace(
                    returncode=0, stdout=_DEVICE_CSV_TWO_GPUS, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")

        collector = GPUHealthCollector()
        with patch.object(gpu_mod.subprocess, "run", side_effect=_run):
            out = collector._query_NVIDIA(simulated_output=False)
        assert len(out["devices"]) == 2
        assert all(d.processes == [] for d in out["devices"])

    @pytest.mark.parametrize("exc", [FileNotFoundError, PermissionError])
    def test_query_nvidia_process_query_oserror_keeps_devices(self, exc):
        """PR #159 review: an OS-level error (FileNotFoundError /
        PermissionError) on the compute-apps query must not bubble up and
        discard the already-collected device data — attribution degrades
        to empty instead."""

        def _run(cmd, **kwargs):
            if cmd[1].startswith("--query-gpu="):
                return SimpleNamespace(
                    returncode=0, stdout=_DEVICE_CSV_TWO_GPUS, stderr="")
            raise exc("nvidia-smi vanished between queries")

        collector = GPUHealthCollector()
        with patch.object(gpu_mod.subprocess, "run", side_effect=_run):
            out = collector._query_NVIDIA(simulated_output=False)
        assert len(out["devices"]) == 2
        assert all(d.processes == [] for d in out["devices"])


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
