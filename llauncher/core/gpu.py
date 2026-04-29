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
        except json.JSONDecodeError as e:
            logging.debug("NVIDIA response parse error: %s", e)
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
        """Parse ``nvidia-smi --query-gpu=… --format=json`` output.

        When *simulated_output* is a string, use it directly instead of
        invoking the CLI (useful for tests).  If ``True``, fall back to
        built-in test fixtures below.
        """
        if simulated_output is True:
            simulated_output = _NVIDIA_DEFAULT_SIMULATED

        data: dict[str, Any] = {"driver_version": None, "devices": []}

        if isinstance(simulated_output, str):
            parsed = json.loads(simulated_output)
        else:
            try:
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,name,memory.total,memory.used,memory.free,"
                        "utilization.gpu,temperature.gpu,pid,process_name,used_memory_gpu",
                        "--format=csv,noheader,nounits,json",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                parsed = json.loads(out.stdout) if out.returncode == 0 else {"data": []}
            except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
                return data

        driver_version = None
        # Also try the text-based nvidia-smi for driver version.
        if simulated_output is True or not isinstance(simulated_output, str):
            try:
                out2 = subprocess.run(
                    ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if out2.returncode == 0:
                    lines = [l.strip() for l in out2.stdout.splitlines()]
                    driver_version = lines[0] if lines else None
            except (PermissionError, FileNotFoundError) as e:
                logging.debug("NVIDIA driver_version query failed: %s", e)
            except subprocess.TimeoutExpired as e:
                logging.debug("NVIDIA driver_version query timed out: %s", e)

        data["driver_version"] = driver_version or (parsed.get("driver_version") if isinstance(parsed, dict) else None)
        devices_data = parsed.get("data", []) if isinstance(parsed, dict) else parsed

        for entry in devices_data:
            # CSV format uses positional list; JSON format uses dict keys.
            if isinstance(entry, list):
                idx, name, total_mb, used_mb, free_mb, util, temp, pid, pname, gpu_used = (
                    str(entry[0]),  # index
                    str(entry[1]),  # name
                    _to_int(entry[2]) or 0,   # memory.total
                    _to_int(entry[3]) or 0,   # memory.used
                    _to_int(entry[4]) or 0,   # memory.free
                    _to_float(entry[5]) or 0.0, # utilization.gpu
                    _to_float(entry[6]),       # temperature.gpu (may be None)
                    str(entry[7]) if entry[7] else "",  # pid
                    str(entry[8]) if entry[8] else "",  # process_name
                    _to_int(entry[9]) or 0,   # used_memory_gpu (optional)
                )
            elif isinstance(entry, dict):
                idx = str(entry.get("index", "0"))
                name = entry.get("name", "Unknown")
                total_mb = _to_int(entry.get("memory.total")) or 0
                used_mb = _to_int(entry.get("memory.used")) or 0
                free_mb = _to_int(entry.get("memory.free")) or 0
                util = _to_float(entry.get("utilization.gpu")) or 0.0
                temp = _to_float(entry.get("temperature.gpu"))
                pid = str(entry.get("pid", ""))
                pname = entry.get("process_name", "") or ""
                gpu_used = _to_int(entry.get("used_memory_gpu")) or 0

            dev = GPUDevice(
                index=int(idx), name=name, total_vram_mb=total_mb,
                used_vram_mb=used_mb, free_vram_mb=max(free_mb, 0),
                utilization_pct=util, temperature_c=temp if temp else None,
            )
            # Process info: nvidia-smi returns per-device process lists.
            # When using CSV with PIDs we build a simple list below.
            if pid and pname:
                dev.processes.append({
                    "pid": int(pid),
                    "name": pname,
                    "used_memory_mb": gpu_used or used_mb,
                })

            data["devices"].append(dev)

        return data

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

_NVIDIA_DEFAULT_SIMULATED = json.dumps({
    "driver_version": "535.129.03",
    "data": [
        {
            "index": "0",
            "name": "NVIDIA GeForce RTX 4090",
            "memory.total": "24564",
            "memory.used": "4200",
            "memory.free": "20364",
            "utilization.gpu": "12.5",
            "temperature.gpu": "42",
            "pid": "",
            "process_name": "",
        }
    ],
})


_NVIDIA_MULTI_GPU_SIMULATED = json.dumps({
    "driver_version": "535.129.03",
    "data": [
        {
            "index": "0",
            "name": "NVIDIA GeForce RTX 4090",
            "memory.total": "24564",
            "memory.used": "4200",
            "memory.free": "20364",
            "utilization.gpu": "12.5",
            "temperature.gpu": "42",
        },
        {
            "index": "1",
            "name": "NVIDIA GeForce RTX 4090",
            "memory.total": "24564",
            "memory.used": "8100",
            "memory.free": "16464",
            "utilization.gpu": "45.0",
            "temperature.gpu": "55",
        },
    ],
})
