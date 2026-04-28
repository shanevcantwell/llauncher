## Worker B Brief: ADRs 005 + 006 (Model Health + GPU Monitoring)

Working directory: /home/node/github/llauncher. Branch: main (up to date with origin/main).

### Code Implementation Tasks

#### Task 2.1: `llauncher/core/model_health.py` (NEW)
Implement ModelHealthResult as Pydantic BaseModel and check_model_health function:
```python
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime

class ModelHealthResult(BaseModel):
    valid: bool = False
    reason: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    exists: bool = False
    readable: bool = False
    last_modified: datetime | None = None

def check_model_health(model_path: str) -> ModelHealthResult:
    """Validate model file exists, is readable, and > 1MB.
    Resolves symlinks via Path.resolve(). Returns invalid result for missing/corrupted/empty files."""
```
Rules: exists → resolve symlink → readable → size > 1024*1024 (heuristic). Set appropriate reason strings ("not found", "unreadable", "too small").

#### Task 2.7: Cache utility `llauncher/util/cache.py` (NEW)
```python
class _TTLCache:
    def __init__(self, ttl_seconds: int = 5): ...
    def get(self, key) -> object | None: ...
    def set(self, key, value) -> None: ...
    def invalidate_all(self) -> None: ...
```

#### Task 2.3: `llauncher/state.py` — Integrate health check into start_server() pre-flight
Call check_model_health() after loading model config, before spawning process. On failure: return early with success=False and descriptive error message.

#### Task 2.4: `llauncher/agent/routing.py` — Add /models/health endpoints
```python
@router.get("/models/health")
async def models_health(): ...
@router.get("/models/health/{model_name}")  
async def model_health_detail(model_name: str): ...
```

#### Task 2.16: `llauncher/core/gpu.py` (NEW)
Implement GPUHealthCollector with TTLCache(5s) for SMI results, auto-detect NVIDIA → ROCm → MPS backends. Methods: _query_NVIDIA(), _query_ROCM(), _query_MPS(). Handle missing tools gracefully.

#### Task 2.18-2.19: `llauncher/agent/routing.py` — Extend /status with GPU data + VRAM pre-flight
Add gpu key to /status response. On POST /start-with-eviction, check vram_sufficient before starting → return 409 if insufficient.

#### Task 2.6: `llauncher/ui/tabs/model_registry.py` (NEW) or extend dashboard.py — Model Registry tab
Table of models with health status indicators using Streamlit.

### Test Tasks

| File | Tests to implement |
|------|-------------------|
| `tests/unit/test_model_health.py` | valid file, nonexistent, empty, symlink resolved, broken symlink, unreadable (6 tests) |
| `tests/unit/test_ttl_cache.py` | basic get/set, expiry, invalidation, separate instances isolated (4-5 tests) |
| `tests/unit/test_agent_models_health_api.py` | GET /models/health list + detail endpoints with mocked filesystem |
| `tests/unit/test_gpu_health.py` | no_backend_returns_empty, simulated_nvidia_parsed, multi_gpu, lifecycle_processes_mapped, vram_consistency (5 tests) |

### Execution Order:
1. Create util/cache.py (TTLCache) — base dependency for both model_health and gpu
2. Create core/model_health.py (ModelHealthResult + check_model_health)
3. Modify state.py to integrate health checks into start_server() pre-flight  
4. Add /models/health endpoints to agent/routing.py
5. Create core/gpu.py (GPUHealthCollector) — uses TTLCache from step 1
6. Extend /status and VRAM pre-flight in agent/routing.py (uses GPU collector + model_health for combined diagnostics)
7. Create Streamlit UI tab for Model Registry
8. Write all test files
9. Run tests: `python3 -m pytest tests/unit/test_model_health.py tests/unit/test_ttl_cache.py tests/unit/test_agent_models_health_api.py tests/unit/test_gpu_health.py -v --tb=short` (all must pass)
10. Run full baseline: `python3 -m pytest tests/unit/ -q` — verify no regressions

### Git Commit Message:
```
feat(core): add model cache health validation and GPU resource monitoring

- Implement ModelHealthResult (Pydantic BaseModel) + check_model_health() with existence/readability/size checks
- Create TTLCache utility in util/cache.py for time-bound result caching
- Integrate health check into state.start_server() pre-flight validation
- Add GET /models/health API endpoint to agent routing
- Implement GPUHealthCollector with NVIDIA SMI, ROCm, and Apple MPS backends
- Auto-detects available hardware; returns clean empty response when no GPUs present
- Extend /status API endpoint with gpu data (per-device vram, utilization, temperature)
- Add pre-flight VRAM check on /start-with-eviction — returns 409 if insufficient VRAM
- Add Model Registry tab to Streamlit dashboard showing file status indicators
- Handle symlinks via Path.resolve(), broken link detection, permission checks

Refs: ADR-005, ADR-006
```

Read the full implementation plan at /tmp/llauncher_implementation_plan.md for additional context and specifications. Follow existing llauncher coding patterns and style."
