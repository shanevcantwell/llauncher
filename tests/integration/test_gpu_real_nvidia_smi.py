"""Real-hardware grounding for the nvidia-smi query shape (issue #148).

These tests run the **real** ``nvidia-smi`` binary and skip cleanly on
hosts without one. They exist because #148 was exactly the failure mode a
mocked suite cannot see: every parse-path unit test passed while the real
binary rejected the query string outright (per-process fields mixed into
``--query-gpu`` plus a bogus ``json`` format token), leaving ``devices``
permanently empty and VRAM monitoring non-functional.

Contract pinned here, against the real binary:

- the exact device field list ``core.gpu`` builds is accepted by
  ``--query-gpu``;
- the exact per-process field list is accepted by ``--query-compute-apps``;
- the *old* broken query is rejected (regression pin — if a future
  nvidia-smi started accepting it, the unit-level pin still holds the
  two-query split);
- end-to-end: ``GPUHealthCollector.refresh()`` on real hardware yields a
  populated ``devices`` list with plausible VRAM numbers.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from llauncher.core import gpu as gpu_mod
from llauncher.core.gpu import (
    GPUHealthCollector,
    _NVIDIA_DEVICE_FIELDS,
    _NVIDIA_PROCESS_FIELDS,
)


requires_nvidia_smi = pytest.mark.skipif(
    shutil.which("nvidia-smi") is None,
    reason="real nvidia-smi binary not on PATH",
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["nvidia-smi", *args], capture_output=True, text=True, timeout=10,
    )


@pytest.mark.integration
@requires_nvidia_smi
class TestRealNvidiaSmiAcceptsOurQueries:
    def test_device_query_fields_are_valid(self):
        out = _run([
            "--query-gpu=" + _NVIDIA_DEVICE_FIELDS,
            "--format=csv,noheader,nounits",
        ])
        assert out.returncode == 0, f"device query rejected: {out.stderr!r}"
        rows = gpu_mod._csv_rows(out.stdout)
        assert rows, "expected at least one GPU row on this host"
        assert all(len(r) == 9 for r in rows), rows

    def test_compute_apps_query_fields_are_valid(self):
        out = _run([
            "--query-compute-apps=" + _NVIDIA_PROCESS_FIELDS,
            "--format=csv,noheader,nounits",
        ])
        # Empty stdout is fine (no compute apps running) — the field list
        # itself must be accepted.
        assert out.returncode == 0, f"compute-apps query rejected: {out.stderr!r}"

    def test_old_broken_query_is_rejected(self):
        """Regression pin for the #148 bug shape against the real binary."""
        out = _run([
            "--query-gpu=index,name,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu,pid,process_name,used_memory_gpu",
            "--format=csv,noheader,nounits,json",
        ])
        assert out.returncode != 0, (
            "nvidia-smi now accepts the previously-invalid query; "
            "re-evaluate the regression pin"
        )


@pytest.mark.integration
@requires_nvidia_smi
class TestRealCollectorEndToEnd:
    def test_refresh_populates_devices_with_vram(self):
        health = GPUHealthCollector().refresh()
        assert "nvidia" in health.backends
        assert health.devices, "real GPU present but devices empty (#148 shape)"
        dev = health.devices[0]
        assert dev.total_vram_mb > 0
        assert 0 <= dev.used_vram_mb <= dev.total_vram_mb
        assert 0 < dev.free_vram_mb <= dev.total_vram_mb
        assert dev.driver_version, "driver_version should be attached to device 0"
