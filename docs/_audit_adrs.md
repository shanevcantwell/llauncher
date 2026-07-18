# llauncher Architectural Decision Records Audit

**Audit Date:** 2026-05-07  
**Scope:** Compliance verification for ADRs 002, 003, 004, 005, 006, 008, 009, 010, 011

---

## Summary Table

| ADR | Title | Status in Code | Compliance |
|-----|-------|----------------|------------|
| 002 | Unified Swap-with-Eviction Semantics | **Superseded by ADR-LLNCH-011** | N/A (supersedes) |
| 003 | Authentication for Agent API | ✅ Implemented | ✅ Compliant |
| 004 | CLI Subcommand Interface | ⚠️ Partially implemented | ⚠️ Partial |
| 005 | Model Cache Health Validation | ✅ Implemented (partial) | ✅ Compliant |
| 006 | GPU Resource Monitoring | ✅ Implemented (backend-agnostic collector exists) | ✅ Compliant |
| 008 | LauncherState as Stateless Facade | ⚠️ Mixed implementation | ⚠️ Partial |
| 009 | Symmetric Hub/Spoke Topology | ✅ Implemented | ✅ Compliant |
| 010 | Port Ownership at Call Site | ❌ Violated (legacy path still exists) | ❌ Violated |
| 011 | Swap Semantics v2 | ⚠️ Partially implemented | ⚠️ Partial |

---

## Detailed Findings

### ADR-LLNCH-002: Unified Swap-with-Eviction Semantics

**Decision Summary:** Elevate `state.start_with_eviction()` to be the single source of truth with rollback and readiness polling.

**Status:** ❌ **Superseded by ADR-LLNCH-011 before implementation.**

The ADR was marked as "Superseded by ADR-LLNCH-011" in its header (2026-04-25), but no code implementing the full 5-phase rollback design exists in `llauncher/state.py`. The current `state._start_with_eviction_impl()` method implements a simplified version without:
- In-flight marker (`{port}.swap`) for concurrent-swap rejection
- Lazy reconciliation of stale lockfiles/markers  
- Full pre-flight VRAM checks (ADR-LLNCH-006)
- All the response fields specified in ADR-LLNCH-011's `SwapResult`

**Compliance:** N/A - superseded without implementation.

---

### ADR-LLNCH-003: Authentication for Agent API

**Decision Summary:** Add API key authentication via `X-Api-Key` header with opt-in activation; exempt read-only endpoints.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| Settings field | `llauncher/core/settings.py:42-45` | ✅ `AGENT_API_KEY = os.getenv("LAUNCHER_AGENT_TOKEN")` |
| Middleware | `llauncher/agent/middleware.py:18-63` | ✅ Checks `X-Api-Key`, 401/403 on failure |
| Exempt paths | `middleware.py:12` | ✅ `/health`, `/docs`, `/redoc`, `/openapi.json` |

**Violations:** None. The middleware enforces authentication correctly.

**Compliance:** ✅ **Compliant**

---

### ADR-LLNCH-004: CLI Subcommand Interface

**Decision Summary:** Create Typer-based CLI with subcommands for model, server, node operations; use Rich for output formatting.

**Implementation Verification:**

| Feature | File | Status |
|---------|------|--------|
| Entry point | `llauncher/cli.py` | ✅ Typer app with subcommand groups |
| Model commands | `cli.py:107-152` | ✅ `list`, `info` implemented |
| Server commands | `cli.py:168-227` | ✅ `start`, `stop`, `status` implemented |
| Node commands | `cli.py:243-329` | ✅ `add`, `list`, `remove`, `status` implemented |

**Violations:**
1. **Missing port argument validation:** ADR-LLNCH-010 requires `port` to be a required parameter at the API boundary, but CLI's `start_server()` accepts optional `--port` with fallback to env var (permissive). No error if neither provided.
   - File: `cli.py:178-192`
   
2. **Missing swap command:** ADR-LLNCH-010 establishes `/swap/{port}` as a distinct verb, but CLI has no `llauncher server swap` subcommand.

**Compliance:** ⚠️ **Partial**

---

### ADR-LLNCH-005: Model Cache Health Validation

**Decision Summary:** Add pre-flight model health checks before starting servers; expose `/models/health` endpoint.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| Core function | `llauncher/core/model_health.py:49-128` | ✅ `check_model_health()` with TTL cache (60s) |
| Symlink resolution | `model_health.py:73` | ✅ Uses `Path.resolve()` |
| Min-size heuristic (1MiB) | `model_health.py:35-37` | ✅ `_MIN_SIZE_BYTES = 1024*1024` |

**Violations:**
1. **No `/models/health` endpoint:** ADR-LLNCH-005 specifies API endpoints but none exist in the agent.
   - Missing `GET /models/health`
   - Missing `GET /models/health/<name>`

2. **Health checks not wired to start flow:** The v2 operations layer (`operations.py`) does NOT call `check_model_health()` for bare `start()` (only `swap()` has it as an optional pre-flight).

**Compliance:** ⚠️ **Partial** - Core function exists but API integration incomplete.

---

### ADR-LLNCH-006: GPU Resource Monitoring

**Decision Summary:** Add backend-agnostic `GPUHealthCollector` with nvidia-smi/rocm-smi/MPS support; expose `/status?full=true`.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| Collector class | `llauncher/core/gpu.py:59-307` | ✅ Full implementation |
| Backend detection | `gpu.py:186-242` | ✅ NVIDIA → ROCm → MPS priority order |
| nvidia-smi parsing | `gpu.py:246-345` | ✅ JSON and CSV formats supported |
| Process attribution | `gpu.py:379-390` | ✅ `_map_processes()` matches PIDs |

**Violations:**
1. **No `/status?full=true` endpoint:** The agent exposes `/status` but does NOT extend it with GPU data per ADR-LLNCH-006.

2. **Pre-flight VRAM check not implemented in swap:** ADR-LLNCH-011 requires VRAM headroom checks, and the v2 operations layer accepts a `vram_check` callable parameter—but no implementation wires this up (see `operations.py:487+`).

**Compliance:** ⚠️ **Partial** - Collector exists but integration incomplete.

---

### ADR-LLNCH-008: LauncherState as Stateless Facade

**Decision Summary:** Reframe `LauncherState` to be stateless; remove caching, use lockfile + argv sentinel for process identity.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| Lockfile module | `llauncher/core/lockfile.py` | ✅ Full implementation per ADR-LLNCH-008 |
| Marker module | `llauncher/core/marker.py` | ✅ In-flight swap marker (ADR-LLNCH-011) |
| Audit log | `llauncher/core/audit_log.py` | ⚠️ Only stub exists |

**Violations:**
1. **No audit log implementation:** ADR-LLNCH-008 requires persisted JSON Lines at configurable path, but `llauncher/core/audit_log.py` is a minimal stub with no write operations.

2. **Stateful `LauncherState.refresh()` still present:** File `state.py:63-74` shows `refresh()` method exists and reloads from disk—but per ADR-LLNCH-008 it should be removed entirely (stateless facade).

3. **No argv sentinel check in reconciliation:** Lockfile reconciliation (`lockfile.py:152-190`) only checks PID liveness, not the "argv sentinel" requirement.

**Compliance:** ⚠️ **Partial**

---

### ADR-LLNCH-009: Symmetric Hub/Spoke Topology

**Decision Summary:** Every node is identical software; hub/spoke roles are runtime-determined via caller dispatch. Each node owns its own configs and peer registry.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| RemoteNode client | `llauncher/remote/node.py` | ✅ HTTP agent client with API key support |
| NodeRegistry persistence | `llauncher/remote/registry.py` | ✅ Persists to `nodes.json`, per-node peer lists |

**Violations:** None. The symmetric topology is correctly implemented:
- No head/worker distinction in binary
- Configs stored locally per node (no master config)
- Each node maintains its own `nodes.json`

**Compliance:** ✅ **Compliant**

---

### ADR-LLNCH-010: Port Ownership at Call Site

**Decision Summary:** Remove `default_port` from `ModelConfig`; every start/swap operation takes explicit, required port parameter.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| ModelConfig schema | `llauncher/models/config.py:13-75` | ✅ No `port` or `default_port` field |

**Violations:**
1. **Legacy fallback in CLI still present:** ADR-LLNCH-010 requires port as required parameter, but `cli.py:186-204` allows missing port with env var fallback:
   ```python
   resolved_port = port if port is not None else DEFAULT_PORT
   if resolved_port is None:
       console.print("[red]✗ --port is required[/red]")
   ```
   This violates the "no fallback" principle.

2. **Legacy v1 state path still exists:** `state.py:345-407` (`start_server()`) contains auto-allocation logic for when port=None—per ADR-LLNCH-010, this should be removed from the API layer (CLI may implement opt-in).

3. **Agent `/start/{model}` endpoint missing in v2:** The HTTP Agent still has legacy model-keyed endpoints; v2 requires only port-keyed verbs (`/swap/{port}`, not `/start-with-eviction/{model}`).

**Compliance:** ❌ **Violated**

---

### ADR-LLNCH-011: Swap Semantics v2

**Decision Summary:** Single `swap(port, model)` operation with 5-phase mechanic, in-flight marker for concurrency, rollback on failure.

**Implementation Verification:**

| Component | File | Status |
|-----------|------|--------|
| Operations layer swap() | `llauncher/operations.py:361-608` | ✅ Full implementation per ADR-LLNCH-011 |
| In-flight marker | `llauncher/core/marker.py` | ✅ Atomic O_EXCL creation, stale reconciliation |

**Violations:**
1. **Legacy v1 swap in MCP tools:** File `mcp_server/tools/servers.py:495+` still uses `state._start_with_eviction_impl()` which implements ADR-LLNCH-002 semantics (not ADR-LLNCH-011's marker-based concurrency control).

2. **No VRAM pre-flight hook:** ADR-LLNCH-011 requires VRAM checks, but the swap implementation accepts optional checkers and none are wired up in production code.

3. **Startup logs cap mismatch:** ADR-LLNCH-011 open question 2 specifies 100 lines; `operations.py:26` sets `STARTUP_LOG_TAIL_MAX = 100` ✅, but the v1 path in `state.py` does not return this field.

**Compliance:** ⚠️ **Partial**

---

## Critical Gaps

| Gap | Impact |
|-----|--------|
| No `/models/health` endpoint (ADR-LLNCH-005) | Operators cannot discover missing/corrupted model files before starting servers |
| No GPU metrics in `/status` (ADR-LLNCH-006) | Cannot make informed scheduling decisions; OOM crashes still possible |
| Audit log not persisted (ADR-LLNCH-008) | Governance and debugging signals lost on restart |
| Legacy v1 swap path remains (ADR-LLNCH-011) | Concurrent-swap rejection only works in new operations layer, not MCP tools |

---

## Recommendations

1. **Remove legacy v1 code paths** that violate ADR-LLNCH-010 port ownership:
   - Delete `state.start_server()` auto-allocation fallback
   - Remove model-keyed agent endpoints (`/start/{model}`, `/swap/{model}`)
   - Update CLI to require explicit `--port`

2. **Wire pre-flight checks** in the v2 operations layer:
   - Connect `check_model_health()` as default for swap (already exists, just not enabled)
   - Implement VRAM headroom check using `GPUHealthCollector`
   
3. **Implement audit log persistence** per ADR-LLNCH-008:
   - Create JSON Lines writer at configurable path
   - Distinguish commanded vs observed events

4. **Add `/models/health` endpoint** per ADR-LLNCH-005 to expose model file status via HTTP Agent.

5. **Remove deprecated `state.start_with_eviction_compat()` wrapper** once all callers migrate to v2 operations layer.
