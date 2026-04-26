# ADR-006: GPU Resource Monitoring and VRAM Tracking

**Status:** Draft  
**Date:** 2026-04-26  

## Context

llauncher currently has **no visibility into GPU resource usage**. Operators start model servers blind — they don't know available VRAM, current utilization, or temperature. This makes critical scheduling decisions impossible:
- Starting two large models that together exceed total VRAM (OOM crash)
- Not knowing which ports/models are most resource-intensive
- Cannot make informed eviction decisions during swap operations

The Pi footer extension (`llauncher_ctx_size`) attempted to derive GPU context info from llauncher's `/status` endpoint, but `/status` only reports process PIDs and basic state — no hardware metrics. This gap was noted in sessions `019dc8ad` and `019dc634`.

Sessions also showed research into llama-server's `-np` flag (parallel slot count) which determines how many KV cache pages a model allocates — directly tied to VRAM consumption per port/model combination.

## Decision

### Option Chosen: Backend-agnostic GPU Metrics Collector with Per-Process VRAM Attribution

Add a `GPUHealthCollector` module that queries available hardware backends and attributes VRAM usage to llauncher-managed llama-server processes.

**Architecture:**
```
llauncher/
├── core/
│   └── gpu.py              # GPUHealthCollector — backend-agnostic interface
├── agent/
│   └── server.py           # /status endpoint extends response with gpu_data
└── ui/tabs/
    └── dashboard.py        # adds VRAM chart/metric widget
```

**GPUHealthCollector API:**
```python
from llauncher.core.gpu import GPUHealthCollector

collector = GPUHealthCollector()  # auto-detects available backend

# Returns: {
#   "backends": ["nvidia"],         # available hardware backends
#   "devices": [
#     {
#       "index": 0,
#       "name": "NVIDIA A100",
#       "total_vram_mb": 40526,
#       "used_vram_mb": 32768,      # tracked via SMI or per-process mapping
#       "free_vram_mb": 7758,
#       "temperature_c": 55,
#       "utilization_pct": 90.2,
#       "processes": [               # llauncher-managed processes only
#         {"port": 8081, "model_name": "mistral-7b", "pid": 45231},
#         {"port": 8082, "model_name": "llama3-8b", "pid": 45892}
#       ]
#     }
#   ]
# }
```

**Supported backends (prioritized):**
| Backend | Detection Method | Availability |
|---------|-----------------|--------------|
| NVIDIA GPU | `nvidia-smi` CLI + Python bindings | Linux with CUDA drivers |
| AMD GPU | `rocm-smi` or `AMD_smi` library | Linux with ROCm |
| Apple MPS | Process memory mapping (`/dev/memfd`) | macOS only |

**API integration:**
```
GET /status              → extends current response with top-level:
                           { ..., "gpu": {...} }
GET /status?full=true    → includes per-GPU detail breakdowns

POST /start-with-eviction/{model}?port={p}
    → pre-flight checks available VRAM before starting
    → returns 409 Conflict if insufficient VRAM, with details:
       {"error": "insufficient_vram", "required_mb": 8192, "available_mb": 7758}
```

### Testing Requirements
- Unit test: GPUHealthCollector handles missing backend gracefully (returns empty backends list, no crash)
- Mock test: simulated nvidia-smi output parsed correctly for multi-GPU setups
- Integration: start server → check /status shows port-to-pid mapping in gpu.processes
- Pre-flight test: attempt to start model when VRAM is "exhausted" (mocked) → 409 Conflict response

### UI Impact
New elements on the Streamlit dashboard's running tab:
- Per-GPU VRAM gauge/meter showing used vs total
- Per-server-row display of estimated/actual VRAM usage
- Warning badge when two servers on same GPU would exceed capacity (during swap decisions)

## Consequences

**Positive:**
- Operators can make informed scheduling decisions about which models to run where
- Pre-flight VRAM check prevents OOM crashes before they happen (better UX than reactive failure)
- Enables the "swap with eviction" flow to intelligently choose whether current model's freed VRAM is enough for new one
- Foundation for future auto-scaling policies (e.g., "don't start if >80% full")

**Negative:**
- New dependency on hardware-specific tooling: `nvidia-smi`, ROCm tools — must handle gracefully when unavailable
- Per-process VRAM attribution is imprecise: multiple processes share GPU, and llama-server may overcommit vs. actual usage
- Adds ~150-200 lines to core with backend detection logic (small but non-trivial)
- Apple MPS approach is experimental — memory mapping may change across macOS versions

**Open Questions:**
1. Should VRAM estimates come from SMI queries or from parsing llama-server's own startup output? (Recommendation: SMI for current usage, model-size heuristics for pre-flight estimates)
2. How often to refresh GPU metrics in /status — every request, or cached with configurable TTL? (Recommendation: cache per-request via memoize, 5s default, to avoid SMI overhead on high-frequency polling)
