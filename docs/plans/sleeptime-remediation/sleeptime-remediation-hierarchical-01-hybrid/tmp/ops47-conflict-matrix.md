# Opus 4.7 Remediation — Cross-Plan Conflict Matrix & Dependency Map

**Generated:** 2026-04-26  
**Plans Analyzed:** #01 (Code Explorer) · #02 (Python Reviewer) · #03 (Security Reviewer) · #04 (Silent-Failure Hunter) · #05 (PR Test Analyzer) · #06 (Architect)  
**Source Files Under Scrutiny:** 8 implementation files + ~9 test files

---

## Table of Contents

1. [Summary Overview](#summary-overview)
2. [Direct Conflict Table](#direct-conflict-table) — Every plan pair touching the same file with different instructions
3. [Compatibility Matrix](#compatibility-matrix) — Where plans are additive or independent
4. [Consolidation Opportunities](#consolidation-opportunity-map) — Mergable fixes to avoid friction commits
5. [Dependency Chain Map](#dependency-chain-map) — Sequential execution ordering with parallel segments
6. [Test File Cross-Contamination Warnings](#test-file-cross-contamination-warnings)
7. [Recommended Execution Order](#recommended-execution-order)

---

## 1. Summary Overview

| Metric | Count |
|--------|-------|
| **Implementation files touched by ≥2 plans** | 8 |
| **Direct conflicts (different approaches to same file)** | 3 |
| **Overlaps (same concern, different approach/level of sophistication)** | 5 |
| **Compatible / independent touches** | ~12 pairings |

### Conflict Severity Key

| Label | Meaning |
|-------|---------|
| 🔴 **CONFLICT** | Plans propose *mutually exclusive* changes. Must pick one or reconcile. Cannot merge blindly. |
| 🟡 **OVERLAP** | Plans target the same file for related but not identical improvements. Different sophistication levels or aspects of the problem. Can be merged with coordination. |
| 🟢 **COMPATIBLE** | Plans touch the same file but address different, non-overlapping concerns. Merge in any order without conflict. |

---

## 2. Direct Conflict Table

### 🔴 CONFLICT #1: `llauncher/remote/node.py` — `to_dict()` API Key Masking Strategy

| Plan | Approach | Target Code | Intent |
|------|----------|-------------|--------|
| **Plan 02** (Phase 2) | `"api_key": "***"` | Replaces raw key with literal masked string `"***"` | Never reveal the secret value; keep field name and structure identical so consumers need no changes |
| **Plan 03** (Finding #5 / Phase 4) | `"has_api_key": self.api_key is not None` | Replaces entire key entry with a boolean presence flag | Eliminates any implication that the key has content; semantically clearer for display paths |

**Why this is a CONFLICT:** These produce **different JSON structures** in the serialized output:
- Plan 02 → `{"api_key": "***", ...}` (key present, value masked)
- Plan 3→ `{"has_api_key": true/false, ...}` (no `"api_key"` key at all)

Any downstream code that reads `node.to_dict()["api_key"]` will break under Plan 03. Any code expecting `node.to_dict()["has_api_key"]` breaks under Plan 02. The impact audit in Plan 03 claims compatibility for all known consumers — but this is a **risk assessment**, not verification, and `RemoteState.get_snapshot()` is one consumer that would need validation.

**🏆 Priority Decision: Plan 03 approach takes priority.** Rationale:
1. Plan 03's approach (`has_api_key` boolean) is more semantically correct for a display/serialization path — it communicates "presence without content" rather than faking the key has a value.
2. Plan 03 performed a full caller impact audit (4 consumers mapped). Plan 02 did not audit all consumers of `to_dict()` output.
3. The security review finding triggered a deeper investigation into *what* gets serialized and to *whom*.
4. **Mitigation:** Before merging, run `grep -rn "api_key" llauncher/ tests/` to confirm no code reads `dict["api_key"]` from `to_dict()`. If any exists, update the consumer or choose Plan 02's approach (which is backward-compatible since `"***"` still provides a string value at that key).

**Resolution:** Choose one. Recommend Plan 03's boolean flag approach with pre-merge grep verification of all `to_dict()` consumers.

---

### 🔴 CONFLICT #2: `llauncher/remote/registry.py` — `_save()` Key Handling vs File Permissions

| Plan | Approach | Target Code | Intent |
|------|----------|-------------|--------|
| **Plan 02** (Phase 2) | `"api_key": "***"` in the saved JSON dict | Masks key on disk write to `nodes.json` | Plaintext keys never land on filesystem in plaintext form |
| **Plan 03** (Finding #4 / Phase 2) | Preserves `"api_key": node.api_key` (plaintext) + adds `os.chmod(NODES_FILE, 0o600)` | Keeps raw key on disk but restricts file permissions to owner-only read/write | Defense-in-depth: file permissions prevent other users from reading keys; no format change |

**Why this is a CONFLICT:** These are **mutually exclusive security postures**:
- Plan 02: "Hide the key everywhere — in memory serialization AND on disk" → results in nodes *losing their API key* on registry reload (loaded `***` ≠ original key)
- Plan 03: "Protect the file, keep keys usable" → keys persist in plaintext but only readable by owner

Plan 02's own documentation acknowledges this tradeoff: *"nodes lose their API key on registry reload unless they re-provision it. For most use cases this is acceptable since keys are typically provisioned at node-add time and the process stays running."*

Plan 03 explicitly rejects masking-on-disk in favor of permissions hardening, arguing that encryption would be needed for true security anyway (and that's a Phase 2 follow-up).

**🏆 Priority Decision: Plan 03 approach takes priority.** Rationale:
1. Preserving usable API keys on disk is operationally important — operators lose functionality on process restart without key re-provisioning under Plan 02's approach.
2. `chmod(0o600)` provides real security for multi-user systems (the stated threat model).
3. Plan 03 also adds the **startup warning** check for existing files with bad permissions — a detection mechanism that Plan 02 completely omits.
4. Both plans should be consolidated: `chmod(0o600)` from Plan 03 + startup warning from Plan 03, AND the try/except guard around chmod.

**Resolution:** Merge both approaches into one implementation step. The file permission approach is sufficient for V1; document that encryption/keyring integration is a Phase 2 ADR item (both plans agree). Add `get_filtered()` method from Plan 02 to the same change.

---

### 🔴 CONFLICT #3: `llauncher/core/gpu.py` — Simulate Flag Semantics

| Plan | Approach | Target Expression | Behavior for `LLAUNCHER_GPU_SIMULATE=""` (empty/absent) |
|------|----------|-------------------|--------------------------------------------------------|
| **Plan 02** (3.4) | Explicit truthy check: `sim_val in ("1", "true", "yes", "on")` | Strict whitelist of accepted true values | Simulation OFF — correct default |
| **Plan 05** (Phase 1, Code Clarity item) | `bool(simulate_env)` where `simulate_env = os.environ.get("LLAUNCHER_GPU_SIMULATE", "")` | Any non-empty string → True | Simulation OFF when absent; any value set → ON |

**Why this is a CONFLICT:** They differ on edge cases:
- With `LLAUNCHER_GPU_SIMULATE="0"`: Plan 02 → simulation **OFF** (0 not in whitelist); Plan 05 → simulation **ON** ("0" is truthy string)
- With `LLAUNCHER_GPU_SIMULATE="false"`: Plan 02 → OFF; Plan 05 → ON
- With `LLAUNCHER_GPU_SIMULATE="1"`: Both agree → ON

Plan 02's approach is **more restrictive and safer** — it prevents accidental activation from values like `"yes"` being interpreted differently or `"false"`/`"0"` unexpectedly enabling simulation.

**🏆 Priority Decision: Plan 02 approach takes priority.** Rationale:
1. Simulation mode should be explicitly opt-in with a clear, documented whitelist of accepted true values.
2. "False" = ON (under Plan 05's approach) is intuitively wrong and would cause confusion during debugging.
3. Both agree the default (env var absent/empty → off) is correct; only edge cases diverge.

**Resolution:** Adopt Plan 02's whitelist approach (`sim_val in ("1", "true", "yes", "on")`). Plan 05's contribution (the clarity improvement of extracting to a named variable before the function call) can be merged alongside this as the structural wrapper.

---

## 3. Compatibility Matrix

### 🟡 OVERLAP #1: `llauncher/util/cache.py` — Thread Safety vs Sentinel Pattern

| Plan | What it proposes |
|------|-----------------|
| **Plan 02** (Phase 1) | Add `threading.Lock()` guarding all mutations; add public `invalidate(key)` method |
| **Plan 04** (Sub-Task 7) | Add `_MISSING = object()` sentinel to distinguish cache-miss from cached-None; document ambiguity in `get()` return value; defer `set_error()` scaffolding |

**Analysis:** These are **different dimensions of the same class**. Plan 02 addresses concurrency correctness; Plan 04 addresses semantic clarity of return values. They do not conflict — they can be merged into a single, comprehensive cache improvement pass:
- Lock guard (Plan 02) + sentinel pattern (Plan 04) + `invalidate(key)` method (Plan 02, used by plan 04's model_health fix)

**Priority:** Plan 02 takes structural priority because thread safety is a correctness bug. Plan 04's sentinel can be added as documentation/defense-in-depth without changing the public API.

---

### 🟡 OVERLAP #2: `llauncher/core/gpu.py` — Bare Exception Remediation

| Plan | Approach |
|------|----------|
| **Plan 02** (Phase 3, §3.2) | Replace all bare `except:` with `except Exception as e: logger.debug(...)`. Keeps original semantics (returns False). 7 call sites across `_try_NVIDIA`, `_try_ROCM`, `_try_MPS`, NVIDIA driver-version query. |
| **Plan 04** (Sub-Task 2) | Replace with `logger.warning()` + set `_degradation_reason` field on `GPUHealthResult`. Introduces structured degradation taxonomy that propagates through `/status` endpoint. Adds ROCm unbound variable fix. Also fixes NVIDIA driver-version secondary query logging. |
| **Plan 05** (Phase 1, Bug A partial) | Does not directly propose bare-except changes but restructures `_query_ROCM()` and `_query_MPS()` into single-try-block patterns, which inherently eliminates the stacked-catch pattern that Plan 02 identifies as problematic. |

**Analysis:** These are **different levels of sophistication addressing the same root problem**:
- Plan 02: "Add logging so failures are observable" (debug level — no alert impact)
- Plan 04: "Add structured degradation tracking so downstream consumers know health is degraded" (warning level + new response field)

**🏆 Priority Decision: Plan 04's approach takes priority** because it solves the observability problem at the right semantic level. Debug logging still requires explicit debug-level config to see errors; warning logging and a boolean `gpu_degraded` flag in `/status` output give operators immediate visibility without special configuration.

Plan 02's contribution (importing `shutil`, removing hand-rolled `shutil_which()`, fixing `_to_float`, removing unused params) is complementary — it does not touch the exception handling paths and can be applied alongside Plan 04's remediation.

---

### 🟡 OVERLAP #3: `llauncher/core/model_health.py` — Cleanup + Diagnostic Enhancements

| Plan | What it proposes |
|------|-----------------|
| **Plan 02** (Phase 4, §4.5 & Phase 5) | Move `from pathlib import Path` to top level; add UTC timezone to `datetime.fromtimestamp`; replace `_health_cache._store.pop(model_path)` with `_health_cache.invalidate(model_path)` |
| **Plan 04** (Sub-Task 6) | Add `_stat_failed` field and `"metadata_unavailable"` reason string; distinguish stat() OSError from size heuristic "too small" |

**Analysis:** These are **additive** — they fix different aspects:
- Plan 02 fixes structural issues (import placement, timezone correctness, cache API usage)
- Plan 04 fixes semantic accuracy (wrong diagnostic message for permission errors)

The `invalidate(model_path)` change from Plan 02 depends on the new `invalidate(key)` method introduced in Plan 02's Phase 1. Plan 04 can be implemented independently or alongside it.

---

### 🟡 OVERLAP #4: `llauncher/agent/routing.py` — Concurrency vs Dead Code + Degradation Flag

| Plan | What it proposes |
|------|-----------------|
| **Plan 02** (Phase 4, §4.4) | Double-checked locking on `get_state()` to prevent concurrent double-initialization |
| **Plan 04** (Sub-Tasks 5a + 5b) | Add `"gpu_degraded"` flag in `/status` response; remove redundant nested try/except block around model health hint |

**Analysis:** These are **fully compatible and independent**. One guards initialization concurrency, the other improves error visibility and removes dead code. They touch different functions (`get_state()` vs `/status endpoint` and `start-with-eviction`) within the same file but have no overlapping lines or shared mutable state changes.

---

### 🟢 COMPATIBLE Touches (No Action Needed Beyond Independent Execution)

| File | Plans | Nature of Compatibility |
|------|-------|------------------------|
| `llauncher/cli.py` | 02 + 05 | Plan 02 refactors CLI parameters/cleanup; Plan 05 adds edge-case tests. Tests can be written against the refactored API. **Note:** Plan 02 renames `json` → `as_json` params, and Plan 05 adds port-conflict tests — these are independent changes (one structural, one new test). |
| `llauncher/cli.py` | 06 | Documentation only — flags missing dependencies in pyproject.toml. Does not modify source code. Must be resolved before Plan 02's CLI changes can be released but does not conflict with them. |
| `tests/unit/test_agent_middleware.py` | 03 + 05 | Plan 03 rewrites the openapi.json test and adds hmac tests; Plan 05 removes `self=None` parameter bug and adds whitespace-token test. Different lines, different concerns. **But:** Plan 05 mentions fixing a tautological assertion in this file — verify with actual code before executing. |
| `tests/unit/test_gpu_health.py` | 02 + 04 + 05 | All three propose tests for GPU health. Plan 02 adds simulate flag and logging tests; Plan 04 adds bare-except warning verification; Plan 05 fixes tautological assertions and adds ROCm/MPS simulated output tests. **Recommendation:** Merge all new test additions into a single pass to avoid commit friction. |
| `tests/unit/test_model_health.py` | 02 + 04 + 05 | Plan 02 adds UTC timezone verification; Plan 04 verifies `_stat_failed=False`; Plan 05 adds exact-1MB boundary test and fixes weak assertions on `last_modified`. Compatible — additive. |
| `tests/unit/test_cli.py` | 02 + 03 | Plan 02 verifies `--json` flag works after param rename; Plan 03 notes a node-persistence test that should pass unchanged. Independent changes. |
| `docs/adrs/` (all four ADRs) | 01 + 06 | Plan 01 adds inline behavioral change comments in code AND proposes an ADR supplement for the per-model health endpoint; Plan 06 rewrites all four ADRs entirely. **Resolution:** Execute Plan 06's rewrites first, then apply Plan 01's supplement as a minor addendum (or fold it into ADR-005). Plan 01 also proposes documentation of state.py behavioral changes — these are inline code comments, not ADR work. |

---

## 4. Consolidation Opportunity Map

The following groupings should be executed as **single implementation steps** rather than independent plan merges. Each consolidation reduces commit noise, avoids intermediate broken states, and ensures all aspects of a concern are addressed atomically.

### Consolidation Group A: Security — `llauncher/remote/node.py` + `llauncher/remote/registry.py` (Plans 02 & 03)

**Files:** `node.py`, `registry.py`, `server.py`  
**Steps to merge into one atomic change:**

| Step | Plan Source | Action |
|------|-------------|--------|
| A1 | Plan 03, §4.1 | Change `to_dict()` in `node.py`: `"api_key"` → `"has_api_key": self.api_key is not None` |
| A2 | Plan 03, §4.4 | Add `os.chmod(0o600)` to `_save()` in `registry.py` with try/except guard |
| A3 | Plan 03, §2.3c | Add startup warning for insecure file permissions in `server.py::run_agent()` |
| A4 | Plan 02, §4.2 | Add `get_filtered(include_offline: bool = True)` method to `registry.py` |
| A5 | Plan 02 tests + Plan 03 N3/N4 | Combine all new test assertions for key masking and file permissions into a single test suite |

**Order:** A1, A2, A3 (all security), then A4 (API addition) as part of same change. Tests at end.

---

### Consolidation Group B: Cache — `llauncher/util/cache.py` + downstream callers (Plans 02 & 04)

**Files:** `cache.py`, `model_health.py`  
**Steps to merge into one atomic change:**

| Step | Plan Source | Action |
|------|-------------|--------|
| B1 | Plan 02, §1.1 | Add `threading.Lock()` + guard all cache mutations |
| B2 | Plan 02, §1.1 | Add public `invalidate(key) -> bool` method |
| B3 | Plan 04, §7a | Add `_MISSING = object()` sentinel and documentation about None ambiguity |
| B4 | Plan 02, §5/Phase 4c | Replace `_health_cache._store.pop(model_path)` with `_health_cache.invalidate(model_path)` in `model_health.py` (line ~134) |

**Order:** B1-B3 on cache.py first; then B4 as a dependent change in model_health.py. Single PR covering both files.

---

### Consolidation Group C: GPU Overhaul — `llauncher/core/gpu.py` + tests (Plans 02, 04 & 05)

**Files:** `gpu.py`, `test_gpu_health.py`  
**This is the largest consolidation because three plans all touch gpu.py.**

| Step | Plan Source | Action |
|------|-------------|--------|
| C1 | Plan 03/Plan 02 §3.1 | Add `import shutil`; add `import logging` + module-level `logger = logging.getLogger(__name__)`; delete hand-rolled `shutil_which()` function; replace all call sites with `shutil.which()` |
| C2 | Plan 04, §Sub-Task 2a (priority over Plan 02) | Replace ALL bare `except:` handlers with typed catch + `logger.warning()` + `_degradation_reason` assignment on result object |
| C3 | Plan 04, §Sub-Task 2b | Add `_degradation_reason: str \| None = None` field to `GPUHealthResult` dataclass |
| C4 | Plan 05, Bug B (priority over Plan 02 §3.3 for ROCm) | Restructure `_query_ROCM()` — merge stacked try blocks into single pattern with two regex patterns (A + B). **Plan 05's rewrite is more complete.** |
| C5 | Plan 05, Bug C (priority over Plan 02 §3.3 for MPS) | Fix `_query_MPS()` — remove dead regex matches, use real output parsing or clean fallback |
| C6 | Plan 02, §3.4 (priority over Plan 05 clarity item) | Fix simulate flag: use explicit truthy whitelist (`sim_val in ("1","true",...)`) instead of double-negation |
| C7 | Plan 02, §3.5 | Fix `_to_float`: call `str(v).strip()` before comparing against `"-"` (not `v.strip()`) |
| C8 | Plan 02, §3.6 | Remove unused parameters `simulate`, `num_simulated` from `_collect_devices()` signature |

**Test consolidation for this group:** Merge all new tests from Plans 02 (logging tests + simulate flag tests), 04 (bare-except warning verification, ROCm unbound variable fix test, status endpoint degraded flag test), and 05 (ROCm/MPS simulated output tests) into a unified `test_gpu_health.py` with clearly organized test classes.

---

### Consolidation Group D: Routing + Model Health Diagnostics — `routing.py` + `model_health.py` (Plans 02 & 04)

**Files:** `routing.py`, `model_health.py`  
**Steps to merge into one atomic change:**

| Step | Plan Source | Action |
|------|-------------|--------|
| D1 | Plan 02, §4.4 | Add double-checked locking to `get_state()` in routing.py |
| D2 | Plan 04, §5a | Replace bare except in `/status` GPU handler with warning log + `"gpu_degraded": True` flag in response dict |
| D3 | Plan 04, §5b | Remove redundant nested try/except block in `start-with-eviction` (dead code removal) |
| D4 | Plan 02, §4.5 | Move `from pathlib import Path` to top level in model_health.py |
| D5 | Plan 02, Phase 5 | Fix `datetime.fromtimestamp(...)` → add `tz=timezone.utc` parameter |
| D6 | Plan 04, §6a | Add `_stat_failed: bool = False` field to `ModelHealthResult` Pydantic model |
| D7 | Plan 04, §6b | Fix stat() OSError handling: set `_stat_failed=True`, use `"metadata_unavailable"` instead of `"too small"` for size heuristic |

**Rationale:** While these touch two files, they share a common theme — making the health/validation subsystem observable and correct. The double-checked locking change (D1) is independent; D2-D7 form a coherent diagnostic improvement package across routing.py and model_health.py.

---

## 5. Dependency Chain Map

### Sequential Execution Order with Parallel Segments

```
Phase 0: PRE-REQUISITES ──────────────────────────────────────── BLOCKING
│
├── [P0-A] Verify no conflicting merge before start              │  ~10 min
│    (resolve CONFLICT #1 on node.py to_dict)                     │  Plan lead decision
│    (resolve CONFLICT #2 on registry.py chmod vs mask)           │  
│
└── [P0-B] Fix dependency gap: add typer + rich to pyproject.toml │  ~5 min
     Gap G4 from Plan 06 — required before CLI can function       │

Phase 1: FOUNDATION ──────────────────────────────────────────
│    Duration: ~2 hours single-threaded or ~45 min parallel
│
├── [P1-A] Cache thread safety (Plan 02 Phase 1)                  │ DEPENDS ON P0-B? NO
│    File: cache.py                                               │ └→ Required by Plan 04 downstream callers
│    Steps: lock + invalidate(key)                                │    (invalidate replaces _store.pop in model_health.py)
│    NEW TESTS: test_cache_thread_safety.py                       │
│                                                                 │
├── [P1-B] Dependency gap fix (Plan 06 Phase 2b)                  │ INDEPENDENT of P1-A
│    File: pyproject.toml                                         │ └→ Required before Plan 02 CLI changes can ship
│    Steps: add typer + rich to dependencies                      │
│                                                                 │
├── [P1-C] GPU import logging infra (Plan 04 Sub-Task 1)          │ INDEPENDENT — no behavioral change
│    Files: gpu.py, routing.py, model_health.py                   │ └→ Prep only; adds logger = getLogger(__name__)
│                                                                 │
└── [P1-D] Cache sentinel pattern (Plan 04 Sub-Task 7a)           │ CAN RUN PARALLEL WITH P1-A
     File: cache.py                                                │    └→ Docstring addition only (Sub-Task 7b deferred)

Phase 2: SECURITY & GPU CORE ────────────────────────────────
│    Duration: ~3 hours single-threaded or ~1 hour parallel
│
├── [P2-A] Security consolidation — node.py + registry.py         │ DEPENDS ON P0-B (CONFLICT resolution done)
│    Files: node.py, registry.py                                  │ └→ Consolidation Group A steps A1-A4
│    Steps: has_api_key mask + chmod 0o600 + get_filtered +       │
│             startup warning                                     │
│                                                                 │
├── [P2-B] gpu.py overhaul (Consolidation Group C)                │ DEPENDS ON P1-C (logging infra)
│    Files: gpu.py                                                │ └→ Steps C1–C8 — all in one atomic change
│    Includes: shutil.which, bare-except → warning + degradation  │
│              + ROCm/MPS fixes + simulate flag whitelist +       │
│              _to_float fix + unused param removal               │
│                                                                 │
└── [P2-C] middleware.py hmac fix (Plan 03 Phase 1)               │ INDEPENDENT of P2-A and P2-B
     File: llauncher/agent/middleware.py                           │ └→ Add import hmac, replace != with compare_digest,
                                                               │    remove /openapi.json from exempt paths,
                                                               │    rewrite test_openapi_docs_excluded_from_auth
     NEW TEST: test_hmac_compare_digest_used

Phase 3: DIAGNOSTICS & CLEANUP ──────────────────────────────
│    Duration: ~2 hours single-threaded or ~45 min parallel
│
├── [P3-A] Routing + model health consolidation                   │ DEPENDS ON P1-B (invalidate method) + P1-C (logging)
│    Files: routing.py, model_health.py                           │ └→ Consolidation Group D steps D1–D7
│    Includes: double-checked lock on get_state()                 │             -- and P2-A for registry.get_filtered caller update
│              /status gpu_degraded flag                                                           
│              stat() diagnostic fix                              
│              pathlib import move + UTC timezone                                                                                    
│              dead code removal                                  
│                                                                     
├── [P3-B] cli.py cleanup (Plan 02 Phase 4a)                      │ DEPENDS ON P1-A for cache.invalidate usage, but
│    Files: cli.py                                                │             otherwise INDEPENDENT. Can start after conflict resolution.
│    Steps: rename json→as_json params                            │             
│            remove ctx parameters                                │             
│            replace _nodes access with registry.get_filtered()   │             
│            (registry change depends on P2-A)                    │             
└── [P3-C] ADR documentation rewrites (Plan 06 Phases 1-2)        │ INDEPENDENT — docs only
     Files: ADRs 003, 004, 005, 006                               │              but Plan 06's finding on Typer/rich gap must
                                                               │              be flagged for P0-B resolution

Phase 4: TEST REMEDIATION ────────────────────────────────────
│    Duration: ~2 hours parallel (one worker per file)
│
├── [P4-A] test_gpu_health.py — merge tests from Plans 02, 04, 05 │ DEPENDS ON P2-B (gpu.py fixes must be in place for new ROCm/MPS tests to pass)
│         ~3 workers spawn simultaneously                         │             
├── [P4-B] test_model_health.py — merge tests from Plans 02, 04   │ INDEPENDENT  
│     ~1 worker                                                    │             
├── [P4-C] test_agent_middleware.py — fixes from Plans 03 & 05    │ DEPENDS ON P2-C (middleware hmac change)
│         ~1 worker                                                │             
├── [P4-D] test_cli.py — Plan 05 edge case tests                  │ DEPENDS ON P3-B (cli.py param rename must exist first)
│     ~1 worker                                                    │             
├── [P4-E] test_remote_node_auth.py rewrite                       │ INDEPENDENT of most above, but should reference 
│     Plan 02/Plan 05 wire-level test                              │             whichever masking approach wins CONFLICT #1 (node.py to_dict)
├── [P4-F] test_core_settings_auth.py rewrite                     │ INDEPENDENT — env-based fixture pattern
│     Plan 05                                                      │             
└── [P4-G] test_agent_models_health_api.py                        │ DEPENDS ON P3-A (VRAM check behavior change) or at minimum P2-B if GPU fixes affect it
     Monkeypatch VRAM to force 409 + delete unused helper          │             

Phase 5: INTEGRATION ────────────────────────────────────────
│    Duration: ~1 hour  
│
├── [P5-A] Replace test_adr_cross_cutting.py with real E2E tests  │ DEPENDS ON P4 completion (tests must use working code)
│     Plan 05 Phase 4                                              │             
└── [P5-B] Conftest fixture evaluation (Plan 01 Task Group 1)     │ INDEPENDENT — but may cause test failures if executed during P4

Phase 6: DOCUMENTATION FINALIZATION ─────────────────────────
│    Duration: ~30 min  
│
├── [P6-A] Behavioral change comments in code (Plan 01 Task Group 2) │ DEPENDS ON ALL above — adds documentation after code changes land
│     state.py docstring + routing.py inline ADR refs              │             
└── [P6-B] Conftest fixture opt-in conversion                     │ If P5-B determines fixture is needed, convert to explicit (Plan 01 Task Group 1)
     Plan 01                                                        │

Phase N: CROSS-REFERENCE WIRES (Plan 06 Phase 3) ────────────
│    Duration: ~30 min  
│
└── [N] Add cross-references to all four ADRs                     │ DEPENDS ON P6-A/B (ADR content must be final before wiring links)
     Endpoint auth gate matrix + CLI→API dependency map            │             
```

### Estimated Total Wall Time

| Execution Mode | Estimate | Notes |
|---------------|----------|-------|
| **Single-threaded** (one worker, sequential) | ~10–12 hours | All phases execute one after another with no overlap |
| **Parallel-optimized** (3 workers for independent phases) | ~4–5 hours | Phase 1 can parallelize P1-A/P1-B/P1-C; Phase 2/3 have some parallel segments; Phase 4 test remediation spawns 5+ workers simultaneously |

### Parallelizable Segments (can run concurrently without coordination)

| Segment A | Segment B | Segment C | Why Independent |
|-----------|-----------|-----------|-----------------|
| P1-A: Cache thread safety | P1-B: Typer/rich deps gap | P1-C: Logging infra prep | Touches completely different files; no shared mutable state at commit boundary |
| P2-A: Security (node/registry) | P2-C: Middleware hmac fix | — | Different file domains (remote vs agent); Plan 03 Phase 1 says parallel-safe with Phase 2 |

---

## 6. Test File Cross-Contamination Warnings

### ⚠️ High-Frisk Test Files

| Test File | Plans Touching It | Risk Level | Coordination Required |
|-----------|------------------|------------|----------------------|
| `tests/unit/test_gpu_health.py` | **02 + 04 + 05** | 🔴 HIGH — three different plans propose overlapping test additions. Plan 02 adds logging/simulate tests; Plan 04 adds bare-except warning verification; Plan 05 fixes tautologies and adds ROCm/MPS simulated output tests. **Execute as Consolidation Group C's test consolidation.** |
| `tests/unit/test_model_health.py` | **02 + 04 + 05** | 🟡 MEDIUM — additive but must be coordinated to avoid assertion conflicts on new fields (`_stat_failed`, UTC timezone). Execute within same phase that implements model_health.py changes. |
| `tests/integration/test_adr_cross_cutting.py` | **05 (primary) + 01 (indirect)** | 🟡 MEDIUM — Plan 05 rewrites the entire file; Plan 01's fixture change could affect tests run within this file. Execute P5-A only after P5-B conftest evaluation is complete. |
| `tests/unit/test_agent_middleware.py` | **03 + 05** | 🟡 MEDIUM — Plan 03 rewrites openapi test; Plan 05 fixes self=None bug and adds whitespace-token test. Different lines but same file — merge into single worker pass. |
| `tests/conftest.py` | **01 + potentially 05/02** | 🟢 LOW — Plan 01 proposes `_patch_model_health` fixture change; Plan 05 suggests a fresh_settings fixture. These are additive if both keep the existing health patch. Risk: if P1-1 (Plan 01) removes/changes autouse behavior while other tests add dependencies, test order matters. |

### Test Execution Order Recommendation

```
P4-G (test_agent_middleware.py)   ← depends on middleware change landing first
    ↓
P4-A (test_gpu_health.py)         ← depends on gpu.py changes
    ├──→ P4-B (test_model_health.py)  ← depends on model_health.py changes
    └──→ P4-C (test_cli.py)            ← depends on cli.py param rename

Parallel-friendly tests (no inter-dependencies):
    ├──→ P4-D (test_remote_node_auth.py rewrite)
    ├──→ P4-E (test_core_settings_auth.py rewrite)  
    └──→ P4-F (test_agent_models_health_api.py monkeypatch fix)

After all unit tests:
    ↓
P5-A (integration test rewrite)   ← uses working code from above
```

---

## 7. Recommended Execution Order

### Executive Summary: The One-Page Plan

**Execute these phases in order. Within each phase, run parallel segments when listed.**

| Phase | What Happens | Plans Involved | Critical Decision Points |
|-------|-------------|----------------|------------------------|
| **0** | Resolve all 3 CONFLICTS; fix dependency gap | Lead decision + Plan 06 G4 | **Must pick `to_dict()` masking strategy (Plan 02 or 03)** before any worker touches node.py. **Must pick chmod-only approach (Plan 03) for registry.py.** |
| **1** | Foundation: cache thread safety, logging infra, Typer/rich deps gap fix | Plans 02 + 04 + 06 | Cache `invalidate(key)` method must exist before downstream callers can use it. |
| **2** | Security (node/registry mask + chmod) + GPU overhaul + middleware hmac | Plans 02 + 03 + 04 + 05 | gpu.py is the biggest consolidation — merge all three plans' changes into one commit. |
| **3** | Routing diagnostics + model_health fixes + cli.py cleanup | Plans 02 + 04 | `get_filtered()` from registry must exist for cli.py to use it (Plan 02). |
| **4** | Test remediation across all affected test files | All plans contribute tests | Execute within the same phase window as code changes; don't write new tests against stale code. |
| **5+6+N** | Integration tests + inline docs + cross-references | Plans 01 + 05 + 06 | Documentation work after code is stable. |

### Risks if Plan Is Not Followed

| Risk | Scenario | Impact |
|------|----------|--------|
| **Conflicting merge of node.py** | Apply both Plan 02 (`"api_key": "***"`) and Plan 03 (`"has_api_key": ...`) in separate commits | One commit will fail on the other's changed key name; codebase enters broken state temporarily |
| **Registry mask + chmod conflict** | Plan 02 masks key to `"***"` (keys lost on reload), then Plan 03 adds `chmod(0o600)` (redundant but also contradictory intent) | Operations confusion: operators restart and lose keys without understanding why |
| **gpu.py partial merge** | Apply Plan 05's ROCm/MPS restructure, then apply Plan 02's separate `_query_MPS` fix on top of already-modified code | Merge conflicts; Plan 04's warning logging may be lost if only Plan 02 or Plan 05 is applied without the other |
| **Test writes against stale code** | P4 test remediation runs before P2-P3 code changes land | New tests fail because they reference methods/fields that don't exist yet (e.g., `_degradation_reason`, `invalidate()`) |
| **ADR rewrite loses inline comments** | Plan 06 rewrites ADRs while Plan 01 adds behavioral change documentation to state.py and routing.py | Plan 01's inline code comments become orphaned if the behavioral changes are absorbed into different commits during the consolidation passes above |

---

## Appendix A: Complete File → Plan Mapping Matrix

```
File                              | Plans that touch it          | Conflict Status
──────────────────────────────────┼─────────────────────────────│────────────────
llauncher/util/cache.py           | 02, 04                       | OVERLAP (different dimensions)
llauncher/remote/node.py          | 02, 03 🔴                    | CONFLICT — masking strategy
llauncher/remote/registry.py      | 02, 03 🔴                    | CONFLICT — mask vs chmod
llauncher/core/gpu.py             | 02, 04, 05 🟡                | OVERLAP — bare-except sophistication levels
llauncher/agent/routing.py        | 02, 04                       | COMPATIBLE (different functions)
llauncher/core/model_health.py    | 02, 04                       | OVERLAP — structural + diagnostic
llauncher/cli.py                  | 02, 05                       | COMPATIBLE + 06 (docs only)
llauncher/agent/middleware.py     | 03                          | No conflict (single plan)
tests/unit/test_gpu_health.py     | 02, 04, 05                   | OVERLAP — coordinate test additions
tests/unit/test_model_health.py   | 02, 04, 05                   | OVERLAP — coordinate field assertions
tests/unit/test_agent_middleware  | 03, 05                       | COMPATIBLE (different lines)
tests/integration/test_adr_cross_cutting | 05, 01(indirect)    | Watch for conftest fixture interference
docs/adrs/003–006.md              | 06 + 01(supplement)          | Plan 06 dominant; fold 01 supplement in
```

## Appendix B: Decision Log (for Project Manager Review)

| Item | Decision | Rationale | Owner |
|------|----------|-----------|-------|
| `to_dict()` masking strategy | Plan 03's boolean flag (`has_api_key`) wins | More semantically correct for display; requires grep verification of all consumers before merge | Technical Lead |
| Registry `_save()` security posture | chmod(0o600) + plaintext keys (Plan 03) | Preserves key usability across restarts; encryption deferred to Phase 2 ADR | Technical Lead |
| GPU simulate flag semantics | Plan 02's whitelist approach wins | "false" should not enable simulation; explicit true values prevent confusion | Tech Reviewer |
| GPU bare-except logging level | Plan 04's warning + degradation pattern wins over Plan 02's debug-only | Warning is visible without special config; degradation field enables programmatic detection | Tech Reviewer |
| Cache sentinel vs lock ordering | Lock first (Plan 02), then sentinel docstring (Plan 04) | Thread safety is a correctness bug; None ambiguity is documentation/defense-in-depth | Tech Reviewer |

---

*This matrix should be reviewed by the project lead before any execution begins. All CONFLICT decisions require sign-off.*
