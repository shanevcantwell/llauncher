# llauncher Code Audit — Consolidated Synthesis

**Date:** 2026-05-07  
**Scope:** Full codebase vs. complete documentation (architecture docs, ADRs, module design, MCP spec, remediation plans)  
**Method:** 5 parallel subagent audits, each covering a distinct domain

---

## Executive Summary

| Domain | Risk Level | Key Finding |
|--------|-----------|-------------|
| Architecture Layers | 🔴 **HIGH** | `state.py` imports from `core/model_health`, violating layer boundaries; MCP read paths return perpetually stale data |
| ADR Compliance | 🟠 **MEDIUM-HIGH** | ADR-LLNCH-010 violated (port ownership), ADR-LLNCH-011 partial (dual-swap problem) |
| Module Design | 🟠 **MEDIUM-HIGH** | `operations.swap()` fully implemented but **no surface calls it**; auto-spawn still present despite M4 saying to remove |
| MCP Server | 🟢 **LOW** | All 11 tools implemented, 96 tests passing; minor output schema drift from docs |
| Operations & Tests | 🟡 **MEDIUM** | BLE001 silent failure patterns in cleanup paths; config changes not audited; ~85% test coverage |

---

## 🔴 Critical Issues (Must Fix Before Next Release)

### C1: Dual-Swap Problem — ADR-LLNCH-011 "Single Entry Point" Violated
**Severity:** Critical  
**Affected:** `operations.py`, `mcp_server/tools/servers.py`, `agent/routing.py`

The v2 `operations.swap()` function is fully implemented per ADR-LLNCH-011 (5-phase mechanic, in-flight marker, rollback), but **no surface calls it**:
- HTTP Agent `/start-with-eviction/{model}` uses legacy `state._start_with_eviction_impl()`
- MCP `swap_server` tool uses legacy `state._start_with_eviction_impl()`

This means the concurrency control (in-flight marker) and structured result envelope from ADR-LLNCH-011 are **dead code** for all production callers.

**Fix:** Wire HTTP Agent and MCP tools to use `operations.swap()`. Add port-keyed routes per ADR-LLNCH-010.

---

### C2: Layer Boundary Violation — State Imports From Core
**Severity:** Critical  
**Affected:** `state.py` line 9–14

```python
from llauncher.core.model_health import check_model_health, ModelHealthResult
```

Per `docs/1-architecture-layers.md`, the layer order is:
```
Endpoint Layer → State Orchestration → Core Layer → Remote Layer
```

State importing from Core creates a tight coupling that makes testing harder and violates the documented architecture.

**Fix:** Move health checks to endpoint level or into `core/process.py`. Remove import from `state.py`.

---

### C3: ADR-LLNCH-010 Violated — Port Ownership Has Legacy Fallbacks
**Severity:** Critical  
**Affected:** `cli.py`, `state.py`

ADR-LLNCH-010 requires port as a required parameter at every API boundary. But:
- CLI allows env var fallback (`DEFAULT_PORT`) instead of requiring explicit port
- `state.start_server()` still has auto-allocation logic when port=None
- Agent endpoints are model-keyed, not port-keyed

**Fix:** Remove all legacy port fallbacks. Make port required at every boundary.

---

## 🟠 High-Priority Issues

### H1: MCP Read Paths Return Perpetually Stale Data
**Severity:** High  
**Affected:** `mcp_server/tools/models.py`, `mcp_server/tools/servers.py`

MCP read tools never call `state.refresh()` before returning data. Since there are 4 independent `LauncherState` instances (per ADR-LLNCH-008), the MCP server's instance is stale relative to config/process changes made via other channels.

**Fix:** Add `state.refresh()` or lightweight `refresh_running_servers()` to every MCP read tool.

---

### H2: Auto-Spawn Still Present — M4 Not Done
**Severity:** High  
**Affected:** `remote/registry.py`

M4 Design says "Drop auto-spawn" but `NodeRegistry.start_local_agent()` still exists and is called from the UI. This contradicts the v2 roadmap.

**Fix:** Remove `start_local_agent()`. Show agent-down banner in UI instead (per M4).

---

### H3: Audit Log Not Persisted — ADR-LLNCH-008 Gap
**Severity:** High  
**Affected:** `core/audit_log.py`

ADR-LLNCH-008 requires persisted JSON Lines audit log. Current implementation is a minimal stub with no write operations. Governance and debugging signals are lost on restart.

Additionally, config CRUD operations (`ConfigStore.add_model`, `update_model`, `remove_model`) have no corresponding audit entries.

**Fix:** Implement JSON Lines writer at configurable path. Add `MODEL_ADDED`, `MODEL_UPDATED`, `MODEL_REMOVED` actions.

---

### H4: BLE001 Silent Failure Patterns
**Severity:** High  
**Affected:** `operations.py` lines 143, 256-258, 397-399

Multiple locations use bare `except Exception:` for "best-effort cleanup":
```python
except Exception:  # noqa: BLE001 — best-effort cleanup
    logger.exception("Failed to terminate...")
```

This silently swallows all errors including `KeyboardInterrupt`, `SystemExit`, and unexpected programming errors.

**Fix:** Replace with scoped exceptions (`psutil.NoSuchProcess`, `OSError`).

---

## 🟡 Medium-Priority Issues

### M1: Redundant Refresh Calls in Agent Endpoints
`agent/routing.py` calls `state.refresh()` then `state.refresh_running_servers()` after mutation. The first call already includes the second. Documented as redundant but persists in production code.

**Fix:** Remove post-mutation `refresh_running_servers()` where redundant with prior `refresh()`.

---

### M2: Logs Truncated on Restart — M5 Item 2 Pending
`process.py::start_server()` opens logs in `"w"` mode instead of `"a"`. Previous run's debug artifacts are destroyed.

**Fix:** Change to append mode + implement size-based rotation per M5 Item 2.

---

### M3: Missing `/models/health` Endpoint — ADR-LLNCH-005 Gap
ADR-LLNCH-005 specifies `GET /models/health` and `GET /models/health/<name>` endpoints. Core function exists but no HTTP endpoint exposes it.

**Fix:** Add health check endpoints to agent routing.

---

### M4: Missing `/status?full=true` GPU Integration — ADR-LLNCH-006 Gap
GPU collector is fully implemented (ADR-LLNCH-006) but not wired into the `/status` endpoint or swap pre-flight.

**Fix:** Extend `/status` with `?full=true` query param. Wire VRAM check into swap pre-flight.

---

### M5: No Self-Loop Short-Circuit — M3 Slice 8 Gap
`remote/node.py` always uses HTTP transport even when target resolves to the local node. Should short-circuit to direct function calls.

**Fix:** Add self-loop detection in `RemoteNode`.

---

## 🟢 Low-Priority Issues

### L1: MCP Output Schema Drift from Documentation
MCP tools return structured output (`identification`, `status` keys) while `MCP.md` documents flat structure. The implementation is actually better — update docs to match code.

### L2: Model Health Cache Edge Cases Untested
No tests for `invalidate_health_cache()` with non-existent keys, empty cache, or concurrent access.

### L3: Temp Instance Anti-Pattern in UI
`ui/tabs/model_card.py` creates a full `LauncherState` + ConfigStore.load() + psutil scan just to check one port. Wasteful but functionally correct.

---

## Test Coverage Summary

| Module | Coverage | Notes |
|--------|----------|-------|
| operations.py | ~90% | Excellent swap coverage; race cleanup paths untested |
| process.py | ~85% | All core functions tested; no concurrent port allocation tests |
| model_health.py | ~85% | `check_model_health()` well-tested; cache edge cases missing |
| audit_log.py | ~80% | Basic operations tested; config changes not audited |
| lockfile.py | ~90% | All paths covered; no concurrent stress tests |
| marker.py | ~90% | All paths covered; no concurrent stress tests |
| MCP tools | ~95% | 96 tests, all passing; excellent edge case coverage |

---

## ADR Compliance Summary

| ADR | Title | Compliance |
|-----|-------|-----------|
| 002 | Swap-with-Eviction Semantics | N/A (superseded by ADR-LLNCH-011) |
| 003 | Agent API Authentication | ✅ Compliant |
| 004 | CLI Subcommand Interface | ⚠️ Partial (missing swap command, port fallback) |
| 005 | Model Cache Health | ⚠️ Partial (core exists, no endpoint) |
| 006 | GPU Resource Monitoring | ⚠️ Partial (collector exists, not wired) |
| 008 | Stateless Facade | ⚠️ Partial (lockfile ✅, audit log ❌, refresh() still present) |
| 009 | Hub-Spoke Topology | ✅ Compliant |
| 010 | Port Ownership at Call Site | ❌ **Violated** (legacy fallbacks persist) |
| 011 | Swap Semantics v2 | ⚠️ Partial (ops layer ✅, surfaces not wired ❌) |

---

## Roadmap Alignment

| Milestone | Status | Notes |
|-----------|--------|-------|
| **M1** — Foundation | ✅ Done | Lockfile, audit_log stub, operations.start/stop |
| **M2** — Swap + Endpoints | ⚠️ Partial | `operations.swap()` implemented but NOT wired to any surface |
| **M3** — Multi-node | ⚠️ Infrastructure only | RemoteNode/Registry exist; not wired to ops; self-loop missing |
| **M4** — UI Redesign | ❌ Not done | Auto-spawn still present; no node selector widget |
| **M5** — Tier 2 ADRs | ❌ Not started | Footer contract, logs rotation, cancellation all pending |
| **M6** — Multi-backend (vLLM) | ❌ Not started | No adapter layer, no discriminated union |

---

## Recommended Action Plan

### Phase 1: Critical Fixes (This Sprint)
1. Wire HTTP Agent and MCP tools to `operations.swap()` — eliminates dual-swap problem
2. Remove `state.py` import of `core/model_health` — fixes layer violation
3. Make port required at all boundaries per ADR-LLNCH-010

### Phase 2: High-Priority (Next Sprint)
4. Add refresh calls to MCP read tools
5. Remove auto-spawn from NodeRegistry
6. Implement audit log persistence + config change entries
7. Replace BLE001 patterns with scoped exceptions

### Phase 3: Medium-Priority (Following Sprints)
8. Wire `/models/health` endpoint (ADR-LLNCH-005)
9. Wire GPU data into `/status?full=true` (ADR-LLNCH-006)
10. Add self-loop short-circuit to RemoteNode (M3 Slice 8)
11. Fix log truncation — append mode + rotation (M5 Item 2)

### Phase 4: Roadmap Items
12. M4 UI redesign (node selector, agent-down banner)
13. M6 discriminated union for ModelConfig
14. vLLM adapter scaffolding

---

## Individual Audit Reports

- Architecture Layers: `docs/_audit_architecture_layers.md`
- ADR Compliance: `docs/_audit_adrs.md`
- Module Design: `docs/_audit_module_design.md`
- MCP Server: `docs/_audit_mcp.md`
- Operations & Tests: `docs/_audit_operations_tests.md`

---

*Synthesized from 5 parallel subagent audits. All file paths are relative to ~/github/shanevcantwell/llauncher/*
