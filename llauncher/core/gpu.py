"""GPU resource monitoring via nvidia-smi, rocm-smi, and Apple MPS.

``GPUHealthCollector`` auto-detects the available hardware backend on launch,
caches results for 5 seconds (to avoid repeated CLI overhead), and maps
running ``llama-server`` processes to GPU devices.

Backends are queried in priority order: NVIDIA SMI → ROCm SMI → Apple MPS.
If no tool is available the collector returns a clean empty response (no
exceptions).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any


from llauncher.util.cache import _TTLCache
from llauncher.core.process import find_all_llama_servers


# ------------------------------------------------------------------
# NVIDIA query field lists (issue #148)
#
# Device fields must all be valid ``--query-gpu`` fields; per-process
# fields belong to ``--query-compute-apps``. Mixing them (or appending a
# ``json`` format token) makes nvidia-smi reject the whole query.
# ------------------------------------------------------------------

_NVIDIA_DEVICE_FIELDS = (
    "index,name,uuid,driver_version,memory.total,memory.used,memory.free,"
    "utilization.gpu,temperature.gpu"
)
_NVIDIA_PROCESS_FIELDS = "gpu_uuid,pid,process_name,used_gpu_memory"


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

@dataclass
class GPUDevice:
    """Information about a single GPU device."""

    index: int
    name: str
    total_vram_mb: int = 0
    used_vram_mb: int = 0
    free_vram_mb: int = 0
    utilization_pct: float = 0.0
    temperature_c: float | None = None
    driver_version: str | None = None
    processes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GPUHealthResult:
    """Top-level result from a GPU health query."""

    backends: list[str] = field(default_factory=list)  # e.g. ["nvidia"]
    devices: list[GPUDevice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backends": self.backends,
            "devices": [d.to_dict() for d in self.devices],
        }


# ------------------------------------------------------------------
# Collector class
# ------------------------------------------------------------------

class GPUHealthCollector:
    """Collects GPU health data from the best available backend.

    Parameters support a shared ``_TTLCache`` instance (default 5 s) so that
    repeated calls within the TTL window return cached results without CLI
    overhead.
    """

    def __init__(self, cache: _TTLCache | None = None):
        self._cache = cache if cache is not None else _TTLCache(ttl_seconds=5)
        self._backend: str | None = None  # resolved once on first call
        self._health_result: GPUHealthResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_health(self, force_refresh: bool = False) -> dict[str, Any]:
        """Return cached health data (or re-query if cache miss / forced)."""
        if not force_refresh:
            cached = self._cache.get("gpu_health")
            if cached is not None:
                return cached  # type: ignore[return-value]

        result = self.refresh()
        self._cache.set("gpu_health", result.to_dict())
        return result.to_dict()

    def refresh(self) -> GPUHealthResult:
        """Invalidate cache, re-query all backends, update internal state."""
        self._cache.invalidate_all()
        health = self._collect_devices()
        # Map llama-server processes to devices (post-filtering).
        self._map_processes(health)
        self._backend = health.backends[0] if health.backends else None
        self._health_result = health
        return health

    def is_available(self, gpu_type: str = "nvidia") -> bool:
        """Return whether the requested backend CLI tool exists on PATH."""
        return shutil_which(gpu_type + "-smi" if gpu_type != "rocm" else "rocm-smi") is not None

    # ------------------------------------------------------------------
    # Backend query methods (private)
    # ------------------------------------------------------------------

    def _collect_devices(self) -> GPUHealthResult:
        """Try each backend in priority order; return the first success."""
        result = GPUHealthResult()

        if self._try_NVIDIA(result):
            result.backends.append("nvidia")
            return result
        if self._try_ROCM(result):
            result.backends.append("rocm")
            return result
        if self._try_MPS(result):
            result.backends.append("mps")
            return result

        # No backend available — clean empty response.
        return GPUHealthResult()

    def _try_NVIDIA(self, result: GPUHealthResult) -> bool:
        """Attempt to query via nvidia-smi."""
        if shutil_which("nvidia-smi") is None:
            return False
        sim_val = os.environ.get("LLAUNCHER_GPU_SIMULATE", "")
        simulated = sim_val in ("1", "true", "yes", "on")
        try:
            data = self._query_NVIDIA(simulated_output=simulated)
            result.devices.extend(data["devices"])
            if "driver_version" in data and data["driver_version"]:
                # Attach driver version to first device for convenience.
                if result.devices:
                    result.devices[0].driver_version = data["driver_version"]
            return True
        except (PermissionError, FileNotFoundError) as e:
            logging.debug("NVIDIA backend unavailable: %s", e)
            return False
        except subprocess.TimeoutExpired as e:
            logging.debug("NVIDIA query timed out: %s", e)
            return False

    def _try_ROCM(self, result: GPUHealthResult) -> bool:
        """Attempt to query via rocm-smi."""
        if shutil_which("rocm-smi") is None:
            return False
        try:
            data = self._query_ROCM()
            result.devices.extend(data["devices"])
            return True
        except (PermissionError, FileNotFoundError) as e:
            logging.debug("ROCm backend unavailable: %s", e)
            return False
        except subprocess.TimeoutExpired as e:
            logging.debug("ROCm query timed out: %s", e)
            return False
        except json.JSONDecodeError as e:
            logging.debug("ROCm response parse error: %s", e)
            return False

    def _try_MPS(self, result: GPUHealthResult) -> bool:
        """Attempt to query via Apple MPS (Metal)."""
        if not is_apple_mps_available():
            return False
        try:
            data = self._query_MPS()
            result.devices.extend(data["devices"])
            return True
        except (PermissionError, FileNotFoundError) as e:
            logging.debug("MPS backend unavailable: %s", e)
            return False
        except subprocess.TimeoutExpired as e:
            logging.debug("MPS query timed out: %s", e)
            return False
        except json.JSONDecodeError as e:
            logging.debug("MPS response parse error: %s", e)
            return False

    # ── NVIDIA SMI queries ────────────────────────────────────────

    def _query_NVIDIA(self, simulated_output: bool | str = False) -> dict[str, Any]:
        """Query device and per-process VRAM data via two nvidia-smi calls.

        Devices come from ``--query-gpu``; per-process attribution comes
        from ``--query-compute-apps`` — its fields (``pid``,
        ``process_name``, ``used_gpu_memory``) are **not** valid device
        fields, and mixing them into one ``--query-gpu`` call makes
        nvidia-smi reject the whole query (issue #148). Both calls use
        ``--format=csv,noheader,nounits``; there is no JSON format token.

        Processes are attributed to devices via GPU UUID (``uuid`` on the
        device query, ``gpu_uuid`` on the compute-apps query).

        When *simulated_output* is a string it is used as the device-query
        CSV (no CLI invocation, no process attribution). ``True`` selects
        the built-in fixture below.
        """
        if simulated_output is True:
            simulated_output = _NVIDIA_DEFAULT_SIMULATED

        data: dict[str, Any] = {"driver_version": None, "devices": []}

        if isinstance(simulated_output, str):
            device_csv = simulated_output
            process_csv = ""
        else:
            device_csv = self._run_nvidia_smi_csv(
                "--query-gpu=" + _NVIDIA_DEVICE_FIELDS
            )
            if device_csv is None:
                return data
            # A compute-apps failure must not discard device data: the
            # device query already succeeded, so degrade to "no process
            # attribution" instead of losing the backend.
            try:
                process_csv = self._run_nvidia_smi_csv(
                    "--query-compute-apps=" + _NVIDIA_PROCESS_FIELDS
                ) or ""
            except subprocess.TimeoutExpired as e:
                logging.debug("nvidia-smi compute-apps query timed out: %s", e)
                process_csv = ""
            except (FileNotFoundError, PermissionError) as e:
                logging.debug("nvidia-smi compute-apps query failed: %s", e)
                process_csv = ""

        devices_by_uuid: dict[str, GPUDevice] = {}
        for row in _csv_rows(device_csv):
            # index, name, uuid, driver_version, memory.total, memory.used,
            # memory.free, utilization.gpu, temperature.gpu
            if len(row) != 9:
                logging.debug(
                    "nvidia-smi device row malformed (want 9 fields, got %d): %r",
                    len(row), row,
                )
                continue
            idx = _to_int(row[0])
            if idx is None:
                logging.debug("nvidia-smi device row has non-numeric index: %r", row)
                continue
            dev = GPUDevice(
                index=idx,
                name=row[1],
                total_vram_mb=_to_int(row[4]) or 0,
                used_vram_mb=_to_int(row[5]) or 0,
                free_vram_mb=max(_to_int(row[6]) or 0, 0),
                utilization_pct=_to_float(row[7]) or 0.0,
                temperature_c=_to_float(row[8]),
            )
            if data["driver_version"] is None and row[3]:
                data["driver_version"] = row[3]
            devices_by_uuid[row[2]] = dev
            data["devices"].append(dev)

        for row in _csv_rows(process_csv):
            # gpu_uuid, pid, process_name, used_gpu_memory
            if len(row) != 4:
                logging.debug(
                    "nvidia-smi compute-apps row malformed (want 4 fields, got %d): %r",
                    len(row), row,
                )
                continue
            dev = devices_by_uuid.get(row[0])
            pid = _to_int(row[1])
            pname = row[2]
            if dev is None or pid is None or not pname:
                logging.debug("nvidia-smi compute-apps row unattributable: %r", row)
                continue
            dev.processes.append({
                "pid": pid,
                "name": pname,
                "used_memory_mb": _to_int(row[3]) or 0,
            })

        return data

    def _run_nvidia_smi_csv(self, query_arg: str) -> str | None:
        """Run one nvidia-smi CSV query; return stdout, or None on failure.

        ``FileNotFoundError`` / ``TimeoutExpired`` propagate to
        ``_try_NVIDIA``, which treats the backend as unavailable.
        """
        out = subprocess.run(
            ["nvidia-smi", query_arg, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            logging.debug(
                "nvidia-smi %s failed (rc=%d): %s",
                query_arg, out.returncode, (out.stderr or "").strip(),
            )
            return None
        return out.stdout

    def _query_ROCM(self) -> dict[str, Any]:
        """Parse ``rocm-smi --showmeminfo=volatile`` output."""
        result: dict[str, Any] = {"devices": []}
        out = None
        try:
            out = subprocess.run(
                ["rocm-smi", "--showmeminfo=volatile"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0:
                return result

            # Parse lines like:
            #   VBIOS Version                         xxx
            #   -------------------------------------
            #   GPU memory usage (Volatile) - unit (MiB)
            #   value   :    342
        except (PermissionError, FileNotFoundError) as e:
            logging.debug("ROCm backend unavailable: %s", e)
            return result
        except subprocess.TimeoutExpired as e:
            logging.debug("ROCm query timed out: %s", e)
            return result

        # If rocm-smi is available but we cannot parse it gracefully, return empty.
        # ROCm format varies widely; a simple heuristic attempt:
        if out is not None and out.returncode == 0:
            try:
                lines = out.stdout.splitlines()
                for i, line in enumerate(lines):
                    match = re.match(r"^\s*GPU[0-9]+\s+.*VRAM\s+Used:\s+(\d+)\s+MiB", line, re.IGNORECASE)
                    if match:
                        idx_match = re.search(r"GPU(\d+)", lines[i])
                        if idx_match:
                            idx = int(idx_match.group(1))
                            used = int(match.group(1))
                            result["devices"].append(
                                GPUDevice(index=idx, name=f"ROCm GPU {idx}", used_vram_mb=used)
                            )
            except (PermissionError, FileNotFoundError) as e:
                logging.debug("ROCm parse failed: %s", e)
            except subprocess.TimeoutExpired as e:
                logging.debug("ROCm parse timed out: %s", e)

        return result

    def _query_MPS(self) -> dict[str, Any]:
        """Query Apple MPS via system_profiler SPDisplaysDataType."""
        result: dict[str, Any] = {"devices": []}
        if not is_apple_mps_available():
            return result
        try:
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0:
                return result

            gpu_index = 0
            for line in out.stdout.splitlines():
                match = re.search(r"(\w[\w\s.]+)\s*\n.*?Chipset Model", line)
                if match:
                    name = match.group(1).strip()
                    result["devices"].append(
                        GPUDevice(index=gpu_index, name=name, total_vram_mb=_estimate_apple_unified_mem())
                    )
                    gpu_index += 1
            # Fallback: if no GPUs matched via per-line pattern, try block-level match.
            if not result["devices"]:
                name_match = re.match(r".*\n(.+)\s+Chipset Model", out.stdout, re.MULTILINE)
                if name_match:
                    result["devices"].append(
                        GPUDevice(index=0, name=name_match.group(1).strip(), total_vram_mb=_estimate_apple_unified_mem())
                    )
        except (PermissionError, FileNotFoundError) as e:
            logging.debug("MPS backend unavailable: %s", e)
        except subprocess.TimeoutExpired as e:
            logging.debug("MPS query timed out: %s", e)

        return result

    # ── Process attribution ───────────────────────────────────────

    def _map_processes(self, health: GPUHealthResult) -> None:
        """Add llama-server PIDs to each device's ``processes`` list."""
        running_pids = {p.pid for p in find_all_llama_servers()}
        for dev in health.devices:
            matched = []
            for pid_entry in list(dev.processes):  # shallow copy — don't mutate while iterating
                if pid_entry["pid"] in running_pids:
                    matched.append(pid_entry)
            # Retain only PIDs that match running llama-servers.
            dev.processes = matched


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def shutil_which(program: str) -> str | None:
    """Lightweight ``shutil.which`` replacement."""
    import shutil
    return shutil.which(program)


def is_apple_mps_available() -> bool:
    """Return True when running on macOS with an Apple Silicon chip."""
    import platform
    try:
        # Check for Metal GPU family (Apple Silicon).
        out = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True, text=True, timeout=5,
        )
        return out.returncode == 0 and ("Apple" in out.stdout and any(c in out.stdout for c in ("M1", "M2", "M3", "M4")))
    except (PermissionError, FileNotFoundError) as e:
        logging.debug("Apple MPS check failed: %s", e)
        return False
    except subprocess.TimeoutExpired as e:
        logging.debug("Apple MPS check timed out: %s", e)
        return False


def _estimate_apple_unified_mem() -> int:
    """Estimate total unified memory on Apple Silicon (in MB)."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip()) // (1024 * 1024)
    except (PermissionError, FileNotFoundError) as e:
        logging.debug("Apple memsize check failed: %s", e)
    except subprocess.TimeoutExpired as e:
        logging.debug("Apple memsize check timed out: %s", e)
    # Fallback heuristic.
    return 8192


def _csv_rows(text: str) -> list[list[str]]:
    """Split nvidia-smi CSV output into stripped cell rows, skipping blanks."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append([cell.strip() for cell in line.split(",")])
    return rows


def _to_int(v) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def _to_float(v) -> float | None:
    try:
        if v is None or v.strip() == "-":
            return None
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


# ── Simulated NVIDIA output for tests ─────────────────────────
# Device-query CSV in _NVIDIA_DEVICE_FIELDS order:
# index, name, uuid, driver_version, memory.total, memory.used,
# memory.free, utilization.gpu, temperature.gpu

_NVIDIA_DEFAULT_SIMULATED = (
    "0, NVIDIA GeForce RTX 4090, GPU-11111111-2222-3333-4444-555555555555, "
    "535.129.03, 24564, 4200, 20364, 12.5, 42\n"
)


_NVIDIA_MULTI_GPU_SIMULATED = (
    "0, NVIDIA GeForce RTX 4090, GPU-11111111-2222-3333-4444-555555555555, "
    "535.129.03, 24564, 4200, 20364, 12.5, 42\n"
    "1, NVIDIA GeForce RTX 4090, GPU-66666666-7777-8888-9999-aaaaaaaaaaaa, "
    "535.129.03, 24564, 8100, 16464, 45.0, 55\n"
)
