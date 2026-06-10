# Module Design Audit Report

**Date:** 2026-05-07  
**Scope:** Code audit against M3–M6 design docs and v2 implementation roadmap  

---

## Executive Summary

| Category | Status |
|----------|--------|
| Agent API surface | ⚠️ Partial - Missing port-keyed routes per ADR-010, no `/footer-context` (M5) |
| Data models | ✅ Complete - `ModelConfig` has `kind` discriminator, missing discriminated union for M6 |
| Process management lifecycle | ✅ Complete - Lockfile/marker patterns match design docs |
| Remote operations (hub-spoke) | ⚠️ Partial - Infrastructure exists but not wired to v2 ops layer; no self-loop short-circuit |
| Swap semantics | ✅ Complete in `operations.py`, but **dual-swap problem**: HTTP/MCP still use v1 path |
| GPU monitoring | ✅ Complete per ADR-006 spec, but VRAM check not wired into swap pre-flight (M2 slice 2) |
| Roadmap alignment | ⚠️ M3 done (v2 ops), M4 UI incomplete (auto-spawn still present), M5/M6 pending |

**Critical finding:** The `operations.py::swap()` function exists and is fully implemented per ADR-011, but **no surface calls it**. HTTP Agent and MCP use the v1 path through `state._start_with_eviction_impl()`. This contradicts ADR-011's "single entry point" requirement.

---

## 1. Agent Module

### Design Doc References
- M3: UI migration, port-keyed verbs
- ADR-010: Port ownership at call site (port-keyed routes)
- M5 Item 1: Footer contract (`/footer-context/{port}`)

### Implementation Status: ⚠️ Partial

#### Current State

**Agent server.py**
- ✅ HTTP Agent structure correct
- ✅ Authentication middleware present
- ❌ Routes still use model-keyed endpoints: `/start/{model_name}`, `/stop/{port}`
- ❌ Missing port-keyed POST routes per ADR-010:
  - `POST /swap/{port}` body `{model}`
  - Should drop `/start-with-eviction/{model}`

**Agent routing.py**
- ✅ VRAM pre-flight helper `_check_vram_sufficient()` exists (ADR-006)
- ✅ Model health check endpoint: `/models/health` and `/models/health/{name}` (ADR-005)
- ❌ Still uses `state._start_with_eviction_impl()` for eviction start
- ❌ No `/footer-context/{port}` endpoint per M5 Item 1

**Agent middleware.py**
- ✅ Authentication correctly implemented with API key support

#### Specific Gaps

| File | Gap | Design Doc |
|------|-----|------------|
| `agent/routing.py` | Uses model-keyed routes instead of port-keyed | ADR-010 §"Verb Space" |
| `agent/routing.py` | No `/footer-context/{port}` endpoint (M5 Item 1) | M5 Design §Footer Contract |
| `agent/server.py` | Not directly at fault, but no new endpoints added | N/A |

#### Roadmap Alignment Notes

- **M3 Slice 7:** UI migrated to ops layer ✅
- **M4 Slice 12 (drop auto-spawn):** NOT DONE - M4 design says to remove `start_local_agent` from NodeRegistry and show banner when agent is down. Current code still has it.
- **M5 Item 1 (Footer Contract):** Not started

---

## 2. Core Module

### Design Doc References
- ADR-008: Lockfile as authoritative claim, stateless facade
- ADR-011: Swap semantics v2 with in-flight marker
- ADR-006: GPU monitoring per device
- M5 Item 2 (Logs lifecycle): Rotation, bounded tail

### Implementation Status: ✅ Complete

#### Current State

**Process management**
- `process.py::build_command()` - Correctly builds llama-server argv
- `process.py::start_server()` - Creates log file in `"w"` mode (truncate) ❌ per M5 Item 2 (should be append)
- `process.py::stop_server_by_port()` / `_by_pid()` - Correct termination pattern
- `process.py::find_all_llama_servers()` - Returns list of processes ✅

**Lockfile (`lockfile.py`)**
- ✅ Atomic write with `O_EXCL`
- ✅ Staleness reconciliation via `is_pid_alive()`
- ✅ JSON persistence format matches ADR-008 spec
- ⚠️ No env-var sentinel pattern (still uses argv check) - deferred to M6 per ADR-012

**Marker (`marker.py`)**
- ✅ In-flight swap marker with `O_EXCL` atomicity
- ✅ Stale-marker reconciliation via `llauncher_pid`
- ✅ JSON format matches ADR-011 spec

#### Specific Gaps

| File | Gap | Design Doc |
|------|-----|------------|
| `core/process.py` | Log files opened in `"w"` mode (truncate) instead of `"a"` (append) | M5 Item 2: "Logs survive restarts" |
| `core/lockfile.py` | No env-var sentinel (`LLAUNCHER_OWNED_PID`) yet - still argv-based | ADR-012 Amendment Notes for ADR-008 |

#### Roadmap Alignment Notes

- **M5 Item 2 (Logs lifecycle):** Partially done - bounded tail in `stream_logs()` ✅, but no rotation/append mode ❌
- **ADR-006:** GPU collector fully implemented per spec ✅
- **ADR-011:** Marker module complete ✅

---

## 3. Models Module

### Design Doc References
- ADR-010: No `default_port`, port at call site
- Issue #42 / M6: Backend discriminator (`kind` enum)
- M6 Slice 19: ModelConfig as discriminated union

### Implementation Status: ⚠️ Partial (M6 incomplete)

#### Current State

**config.py**
```python
class ModelConfig(BaseModel):
    kind: BackendKind = BackendKind.LLAMA_SERVER  # ✅ discriminator in place
```

- ✅ `BackendKind` enum exists with `LLAMA_SERVER`
- ✅ No `default_port`, no `port` field (ADR-010)
- ❌ **Not a discriminated union** - M6 Slice 19 not done

#### Specific Gaps

| File | Gap | Design Doc |
|------|-----|------------|
| `models/config.py` | Not a Pydantic discriminated union with `oneOf` on `kind` field | M6 Design §Slice 19 |

**Required for M6:**
```python
# What M6 needs (simplified):
class ModelConfig(BaseModel):
    name: str
    model_path: str
    kind: BackendKind

class LlamaServerConfig(ModelConfig):
    kind: Literal[BackendKind.LLAMA_SERVER]
    mmproj_path: str | None = None
    n_gpu_layers: int
    # ... llama-server-specific fields

class VLLMConfig(ModelConfig):
    kind: Literal["vllm"]
    tensor_parallel_size: int
    gpu_memory_utilization: float
    # ... vLLM-specific fields
```

#### Roadmap Alignment Notes

- **M6 Slice 18 (adapter scaffolding):** Not started - no `backends/` module exists
- **M6 Slice 19 (discriminated union):** NOT STARTED - config is flat, not a union

---

## 4. Remote Module

### Design Doc References
- ADR-009: Symmetric hub-spoke topology, per-node sovereignty
- M3 Slice 8: Self-loop short-circuit when target resolves to this node
- v2-handoff: Multi-node infrastructure exists but NOT wired to v2 operations layer

### Implementation Status: ⚠️ Partial (M3 incomplete)

#### Current State

**remote/node.py**
- ✅ `RemoteNode` with ping, get_status, start_server, stop_server, get_logs
- ✅ HTTP dispatch via httpx with auth header (`X-Api-Key`)
- ❌ No self-loop short-circuit - always uses HTTP even for "local" node

**remote/registry.py**
- ✅ `NodeRegistry` loads/saves `nodes.json`
- ✅ Auto-spawn still present: `start_local_agent()` method (M4 says to delete)
- ⚠️ No integration with v2 ops layer

**remote/state.py**
- ✅ `RemoteAggregator` aggregates state across nodes
- ❌ Methods like `start_on_node()`, `stop_on_node()` call remote endpoints directly
- ❌ No dispatch to local via `operations.swap()` / `operations.start()` when target is this node

#### Specific Gaps

| File | Gap | Design Doc |
|------|-----|------------|
| `remote/node.py` | No self-loop detection - always uses HTTP transport | M3 Slice 8: "Self-loop short-circuit" |
| `remote/registry.py` | Auto-spawn `start_local_agent()` still present (M4 says delete) | M4 Design §Slice 12 |
| `remote/state.py` | No dispatch to local via v2 ops layer | v2-handoff: "Not yet wired to v2 operations" |

#### Roadmap Alignment Notes

- **M3:** Multi-node infrastructure ✅, but NOT wired to v2 operations ❌
- **M4:** Auto-spawn removal NOT DONE - still present in `NodeRegistry.start_local_agent()`
- **Missing per M6:** Remote swap dispatch should use local `operations.swap()` when target is this node

---

## 5. Operations Module (`operations.py`)

### Design Doc References
- ADR-008: Stateless facade pattern
- ADR-010: Port-keyed verbs with structured result envelope
- ADR-011: Swap semantics v2 five-phase mechanic
- M2 Slice 2: Wire model health and VRAM pre-flight into swap

### Implementation Status: ✅ Complete (functionality), ⚠️ Partially Wired (integration)

#### Current State

**start()**
```python
def start(model_name: str, port: int, *, caller: str) -> StartResult:
```
- ✅ Returns `StartResult` with ADR-010 envelope (`action`, `success`, etc.)
- ✅ Reconciles stale lockfiles before starting
- ✅ Records audit entries per ADR-008

**stop()**
```python
def stop(port: int, *, caller: str) -> StopResult:
```
- ✅ Returns `StopResult` with structured envelope
- ✅ Handles stale lockfile reconciliation
- ✅ Audit logged

**swap()**
```python
def swap(model_name: str, port: int, *, caller: str,
         model_health_check=None, vram_check=None) -> SwapResult:
```
- ✅ Five-phase mechanic per ADR-011:
  - Phase 1: Pre-flight validation (model exists, lockfile valid, no marker)
  - Phase 2: Take in-flight marker
  - Phase 3: Stop old model
  - Phase 4 + 5: Start new + readiness poll
  - Rollback on failure
- ✅ Returns `SwapResult` with all eight action values per ADR-011 table:
  - `swapped`, `already_running`, `rolled_back`, `failed`
  - `rejected_preflight`, `rejected_stop_failed`, `rejected_in_progress`, `rejected_empty`
- ⚠️ **Pluggable pre-flight seams not wired** (M2 Slice 2):
  - `model_health_check` defaults to `None`
  - `vram_check` defaults to `None`

#### Specific Gaps

| Gap | Status | Design Doc |
|-----|--------|------------|
| VRAM check wiring into swap pre-flight | ⚠️ Not done (seam exists, not wired) | M2 Slice 2: "Wire core/gpu.py" |
| Model health check wiring into swap pre-flight | ⚠️ Not done (seam exists, not wired) | M2 Slice 2: "Wire core/model_health.py" |

#### Roadmap Alignment Notes

- **M1:** `operations.start()` and `stop()` ✅
- **M2 Slice 1:** `operations.swap()` five-phase mechanic ✅ (commit dd5f7dd)
- **M2 Slice 2:** Model health + VRAM pre-flight wiring ⚠️ NOT DONE (seam exists but not wired)

---

## 6. GPU Monitoring (`gpu.py`)

### Design Doc References
- ADR-006: Backend-agnostic collector with per-process attribution

### Implementation Status: ✅ Complete

#### Current State

- ✅ `GPUHealthCollector` auto-detects backend (nvidia-smi → rocm-smi → MPS)
- ✅ Caching via `_TTLCache(5 seconds)` to avoid SMI overhead
- ✅ Process attribution maps llama-server PIDs to GPU devices
- ✅ Returns structured health data matching ADR-006 spec

**Supported backends:**
| Backend | Detection | Status |
|---------|-----------|--------|
| NVIDIA | `nvidia-smi` | ✅ Implemented |
| AMD ROCm | `rocm-smi` | ⚠️ Partial (basic parsing) |
| Apple MPS | system_profiler | ⚠️ Basic implementation |

#### Specific Gaps

- **ROCm:** Basic parsing - may not handle all ROCm output formats
- **Apple MPS:** Simplified unified memory estimation

#### Roadmap Alignment Notes

- **ADR-006:** Fully implemented per spec ✅
- **M2 Slice 2:** VRAM check callable exists but NOT wired into swap pre-flight ⚠️

---

## 7. Config Store (`core/config.py`)

### Design Doc References
- ADR-010: No `default_port`, silent migration of legacy fields

### Implementation Status: ✅ Complete

#### Current State

- ✅ `from_dict_unvalidated()` drops port-related legacy fields per ADR-010
- ✅ Silent drop policy (no migration log)
- ✅ Atomic save via temp file + rename

---

## 8. Roadmap Alignment Summary

| Milestone | Status | Notes |
|-----------|--------|-------|
| **M1** - Foundation | ✅ Done | Lockfile, audit_log, operations.start/stop |
| **M2** - Swap + Endpoints | ⚠️ Partial | `operations.swap()` implemented but NOT wired to any surface (HTTP/MCP still use v1) |
| **M3** - Multi-node | ⚠️ Infrastructure only | RemoteNode/Registry exist, not wired to ops; self-loop short-circuit missing |
| **M4** - UI Redesign | ❌ Not done | Auto-spawn removed? NO. Node selector widget? NOT DONE. Tab restructure? NOT DONE. |
| **M5** - Tier 2 ADRs | ⚠️ Not started | Footer contract, logs rotation, cancellation, orphan policy all pending |
| **M6** - Multi-backend (vLLM) | ❌ Not started | No adapter layer, no discriminated union |

---

## Critical Issues

### Issue #1: Dual-Swap Problem (BLOCKING M2)
The v2 `operations.swap()` function is fully implemented per ADR-011 but **no surface calls it**:
- HTTP Agent `/start-with-eviction/{model}` uses `state._start_with_eviction_impl()`
- MCP `swap_server` tool uses `state._start_with_eviction_impl()`

**ADR-011 §"Single Entry Point" violation.**

### Issue #2: Auto-Spawn Still Present (BLOCKING M4)
M4 Design says "Drop auto-spawn" but `NodeRegistry.start_local_agent()` still exists and is called from UI.

### Issue #3: Logs Truncated on Restart (M5 Item 2 Pending)
`process.py::start_server()` opens logs in `"w"` mode instead of `"a"` - destroys previous run's debug artifacts.

---

## Recommendations

1. **Immediate:** Wire all surfaces to `operations.swap()`
   - HTTP Agent: Add port-keyed routes `/swap/{port}`, drop model-keyed
   - MCP tools: Update to use v2 operations
   
2. **M4 Priority 1:** Drop auto-spawn, add agent-down banner

3. **M5 Priority 1:** Logs rotation (append mode + size-based rollover)

4. **M6 Blocking:** Implement discriminated union for `ModelConfig` before adding vLLM adapter

---

*Report generated from code review against M3–M6 design docs and v2 roadmap.*

