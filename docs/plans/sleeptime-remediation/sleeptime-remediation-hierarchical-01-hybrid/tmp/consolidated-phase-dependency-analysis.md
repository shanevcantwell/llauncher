# Consolidated Phase Dependency Graph & Execution Timeline

**Opus 4.7 Remediation — Plans 01–06**  
Delivery Range: `a4d0361..9c73c71`  
Analysis Date: 2026-04-26  

---

## Table of Contents
1. [File-Level Consolidation Map](#1-file-level-consolidation-map)
2. [Phase Dependency Graph](#2-phase-dependency-graph)
3. [Parallel Execution Map](#3-parallel-execution-map)
4. [Timeline Summary Table](#4-timeline-summary-table)
5. [Decision Log](#5-decision-log)

---

## 1. File-Level Consolidation Map

The six plans touch **20 source files** and **9 test files**, with significant overlap on the same physical files. Below is a file-by-file consolidation showing which plan(s) contribute changes to each file, enabling merged commits instead of sequential edits.

### Production Code Files (10 files)

| File | Plans Touching It | Consolidated Changes |
|------|-------------------|---------------------|
| `llauncher/util/cache.py` | 02, 04 | Add thread lock + wrap get/set/invalidate_all; add public `invalidate(key)` method (Plan 02); add class-level `_MISSING` sentinel + docstring about None ambiguity (Plan 04) |
| `llauncher/remote/node.py` | 02, 03 | Replace `"api_key": self.api_key` → `"has_api_key": self.api_key is not None`. **Decision:** Plan 03 wins (boolean presence flag vs string masking). Both plans' approach to registry.py below. |
| `llauncher/remote/registry.py` | 02, 03 | Add `os.chmod(0o600)` after write_text with try/except guard (Plan 03); add public `get_filtered()` method for CLI (Plan 02). **Decision:** Both apply — chmod for file-level security, plaintext preserved on disk for reload capability. Also moves `from pathlib import Path` to module level (Plan 04 overlap — resolved in Plan 02's consolidated list). |
| `llauncher/core/gpu.py` | 02, 04, 05 | **Most-overlapped file.** Consolidated changes: Add `import shutil` + delete hand-rolled `shutil_which()` (Plan 02); Replace all bare `except:` with typed handlers logging via `logger.debug/warning` and setting `_degradation_reason` on result objects (Plans 02, 04); Fix ROCm double-try-block into single try with two regex patterns (Plans 04, 05 — both describe same fix with slightly different regex approaches; use Plan 05's more complete Pattern B coverage); Fix _query_MPS dead regex matches — extract GPU names from system_profiler output or produce deterministic fallback (Plan 02 + 05 overlap); Clarify `LLAUNCHER_GPU_SIMULATE` double-negation expression using explicit whitelist check: `sim_val in ("1","true","yes","on")` (Plan 02 wins over Plan 04/05 simpler bool() approach — Project Lead decision for stricter defaults); Fix `_to_float`: convert to string before `.strip()` and comparison (Plan 02); Remove unused `simulate, num_simulated` parameters from `_collect_devices` (Plan 02); Add module-level logger definition (Plans 02, 04) |
| `llauncher/agent/middleware.py` | 03 (+ test: Plan 04/05) | Replace `!=` string comparison with `hmac.compare_digest()` for constant-time token check; Extract None-guard before comparison; Remove `/openapi.json` from `_AUTH_EXEMPT_PATHS` (Plan 03); New unit test verifying hmac usage (Plan 03) + whitespace-token rejection test (Plan 05 Phase 3) |
| `llauncher/agent/server.py` | 03 (+ integration: Plan 06) | Add `openapi_url=None if auth_active else "/openapi.json"` to FastAPI constructor (Plan 03); Unify startup warning block — unconditional WARNING when token absent + CRITICAL for 0.0.0.0 bind (Plan 03 Phase 3); Add insecure-file-permission detection at startup (Plan 03) |
| `llauncher/agent/routing.py` | 02, 04 | Implement double-checked locking with `threading.Lock()` for global `_state` in `get_state()` (Plan 02); Replace bare except on /status GPU query with warning log + set `"gpu_degraded": True` response field (Plan 04 — uses same _degradation_reason pattern from gpu.py); Remove redundant nested try/except block (~lines 409-415) that is dead code (Plan 04) |
| `llauncher/core/model_health.py` | 02, 04 | Move `from pathlib import Path` to top-level imports; add `from datetime import timezone` (Plan 02); Change naive `datetime.fromtimestamp(ts)` → UTC with `tz=timezone.utc` (Plan 02); In `_save()`: replace manual cache dict access with `_health_cache.invalidate(model_path)` using new public method from Plan 1 (phase dependency: must wait for cache.py Phase 1 lock); Add `_stat_failed` field to `ModelHealthResult`; Fix stat() OSError handler to produce "metadata_unavailable" reason instead of misleading "too small" (Plan 04). The timezone + import changes are independent; the stat diagnostic and invalidate method calls depend on prior phases. |
| `llauncher/cli.py` | 02, 05 | Rename all `json: bool = typer.Option(...)` parameters to `as_json` across 5 occurrences in 4 commands (Plan 02); Remove unused `ctx: typer.Context,` parameter from 7 functions (Plan 02); Replace `registry._nodes.values()/items()` direct access with public API (`for node in registry:` and `registry.get_filtered(...)` — requires Plan 1's get_filtered method) (Plan 02); Delete or fix dead assert pattern in test_start_with_explicit_port mock assertion (Plan 05); Add port-conflict error path, invalid port range documentation tests (Plan 05) |
| `llauncher/state.py` | 01 only | Add behavioral change comment/docstring to `start_server()` noting ADR-005 pre-flight health gate replacing old Path.exists() check. Pure doc addition — zero risk. |

### Test Files (9 files)

| File | Plans Touching It | Consolidated Actions |
|------|-------------------|---------------------|
| `tests/unit/test_agent_middleware.py` | 03, 04/05 | Rewrite `test_openapi_docs_excluded_from_auth` to assert openapi.json not in exempt paths when auth active (Plan 03); Add `test_hmac_compare_digest_used` verification test (Plan 03); Fix line 87 `self=None` parameter → remove self (Plan 05); Add whitespace-api-key-403 rejection test + oversized token acceptance test (Plan 05 Phase 3) |
| `tests/unit/test_core_settings_auth.py` | 05 only | **Rewrite entire file.** Replace fragile `importlib.reload(settings)` pattern with `_read_api_key_from_env()` helper that directly reads env var without module reload. Add whitespace-only → None test, oversized token test (512-char), special characters test |
| `tests/unit/test_remote_node_auth.py` | 05 (+ new: Plan 03) | Rewrite first test: replace dead `httpx.Client.__enter__` mock with wire-level MockTransport capture verifying actual HTTP request carries X-Api-Key header; Add API key masking validation: create node WITH api_key, assert `"has_api_key": True` in to_dict() and `"api_key"` key absent (Plan 03) |
| `tests/unit/test_gpu_health.py` | 02, 04/05 | **Most test-overlapped file.** Remove tautology: change `assert isinstance(result, object)` → `assert isinstance(result, GPUHealthResult)`; Fix VRAM consistency test to check values not just keys (Plan 05); Add new tests from Plan 02: TestSimulateFlag group, TestToFloatWithNumericInput group, TestGPUBackendLogging group (3 test classes with caplog assertions); Add new tests from Plan 04/05: ROCm Pattern A + B simulated output parsing test, MPS named-GPU extraction test, nvidia-bare-except-logs-warning test, rocm-unbound-variable-fixed test, status-endpoint-gpu-degraded flag test |
| `tests/unit/test_model_health.py` | 04/05 | Fix weak assert: change `or hasattr(result, "last_modified")` → `assert isinstance(result.last_modified, datetime)`; Add exact 1 MiB boundary test (write file with exactly 1048576 bytes); **New from Plan 04:** Test stat OSError produces "metadata_unavailable" not "too small" — use patch.object(Path, 'stat', side_effect=OSError) |
| `tests/unit/test_cli.py` | 02/03/05 | From Plan 02: Add TestJsonParamRename verifying --json flag still works after internal param rename; From Plan 04: Verify registry public API usage (registry __iter__ not _nodes); From Plan 05: Fix dead-assert pattern, add port-conflict error test, malformed JSON config rejection test, negative/over-max-port behavior documentation tests |
| `tests/unit/test_agent_models_health_api.py` | 05 only | Rewrite no-op VRAM error test: monkeypatch `_check_vram_sufficient` to force 409 response with detailed error dict; Delete unused `_patched_health_client()` helper function (dead code); Add from Plan 03/06 context tests for model health detail endpoint auth gate |
| `tests/unit/test_ttl_cache.py` | 02 only | No source changes needed. New standalone test file: `test_cache_thread_safety.py` created by Plan 02 with 7 comprehensive thread-safety tests covering concurrent writes, read-write races, invalidate correctness, GPU collector access pattern, and invalidate_all corruption prevention |
| `tests/integration/test_adr_cross_cutting.py` | 05/06 only | **Replace entire file contents.** Delete tautological cross-cutting tests. Write new TestFullStackAuthAndHealth class: (1) unauthenticated start rejected — auth gate blocks POST without token; (2) authenticated start blocked by health check — valid auth + nonexistent model → health failure; (3) /health exempt from auth even when active; (4) /models/health/{name} requires auth. Add TestTTLCacheCrossModule: verify cache isolation between model_health and GPU modules |

### Documentation Files

| File | Plans Touching It | Consolidated Actions |
|------|-------------------|---------------------|
| `docs/plans/plan-sleeptime-remediation-01...` (Plan 01 itself) | 01 only | Integrate verification gate checklist as reusable annex — 5-item pre-merge checklist for test count accuracy, behavioral change logging, unclaimed API surface audit, regression baseline without fixture masking, and API docs sync. Copy/paste format preserved from existing annex section |
| `docs/adrs/003-agent-api-authentication.md` | 03/06 | From Plan 03: Update exemption table to match implementation (all paths auth-gated except hardcoded exempt set); add structured consequences/risk tables; From Plan 06: Rewrite with full alternatives analysis (mTLS, OAuth/JWT, unix socket, reverse proxy — all analyzed and rejected); fix Typer dependency gap documentation (Typer + rich imported in cli.py but NOT declared in pyproject.toml); document shell completion status (`add_completion=False`) |
| `docs/adrs/004-cli-subcommand-interface.md` | 06 only | Rewrite: add alternatives analysis (Click, argparse, plain python modules — all rejected in favor of Typer via type-hint self-documentation); fix dependency gap (CRITICAL: typer >= 0.9 and rich >= 13 must be added to pyproject.toml or gated behind extras.cli section); document double-discovery problem across CLI/MCP/HTTP/Streamlit interfaces; add CLI → API dependency map |
| `docs/adrs/005-model-cache-health.md` | 06 only | Rewrite: document two-layer health check (pre-flight + REST endpoints); justify 1 MiB heuristic as "lower bound for non-trivial GGUF, catches truncated downloads"; explain why simple exists() rejected (can't distinguish corrupted file from valid), why GGUF header parsing deferred; add consequences/risk tables, open questions (GGUF magic byte validation Phase 2, config-change invalidation, network path timeout) |
| `docs/adrs/006-gpu-resource-monitoring.md` | 06 only | Rewrite: remove fabricated `/dev/memfd` claim for Apple MPS (Plan 04/05 already corrected this in code); add build-vs-adopt analysis (nvidia-smi subprocess chosen over pynvml/nvitop/gpustat with rationale); honestly state per-platform limitations (ROCm process attribution untested, macOS provides total memory only); add consequences/risk tables |

### New Files to Create

| File | Purpose | Source Plan(s) |
|------|---------|----------------|
| `tests/unit/test_cache_thread_safety.py` | 7 thread-safety tests for _TTLCache | Plan 02 Phase 1 |
| `tests/unit/test_node_serialization.py` (or append to test_remote.py) | API key masking validation: `"has_api_key"` present, `"api_key"` absent | Plan 03 Finding #5 |
| `docs/adrs/verification-gate-checklist.md` | Reusable pre-merge checklist (Plan 01 annex formalized as standalone doc) | Plan 01 Task Group 3 |

### Files NOT Modified (explicitly excluded by all plans)

| File | Reason |
|------|--------|
| `tests/unit/test_ttl_cache.py` (existing file) | Plan 02 says "run as-is; add NEW separate thread-safety test file instead" |
| `llauncher/util/__init__.py` | Plan 01 P2-1 adds one-line comment for intentional `_TTLCache` re-export — trivial, done during documentation consolidation phase (no merge risk) |

---

## 2. Phase Dependency Graph

The graph below shows ALL phases derived from consolidating the six individual plans into merged workstreams. Edges indicate dependencies; parallel paths are shown branching horizontally.

```
╔══════════════════════════════════════════════════════════════════════╗
║                     EXECUTION SEQUENCE OVERVIEW                     ║
╚══════════════════════════════════════════════════════════════════════╝


Phase A: Documentation-Only (0 hrs — parallel start)
├─ Plan 01 P2-1, P2-2, P2-3: docstring comments on state.py, routing.py, util/__init__.py
└─ Plan 06 Phase 3: Add cross-reference wiring table to all four ADRs
     │  (no code dependencies — can begin immediately)
     ▼

Phase B: Security & Auth Critical Fixes (7 hrs) ← CRITICAL PATH START
├─ middleware.py: hmac.compare_digest + OpenAPI suppression + exempt path cleanup (Plan 03 P1)
├─ server.py: openapi_url constructor param + startup warnings unification + permission detection (Plan 03 P1, P2, P3)
├─ registry.py: chmod(0o600) guard on file write in _save() — plaintext preserved per lead decision (Plan 03 P2)
├─ node.py: "has_api_key": bool replacement for to_dict() — Plan 03 wins over Plan 02 masking approach (Plan 03 P4)
└─ Test updates: middleware exempt-path rewrite, hmac verification test, auth warning tests, file-perm tests (Plans 03 + 05)
     │
     ├──→ Phase C: Foundation — Cache thread-safety + routing lock (10 hrs) [BLOCKED by B for rollback safety]
     │    ├─ cache.py: threading.Lock in __init__, wrap get/set/invalidate_all, new public invalidate() method (Plan 02 P1)
     │    │   └──→ Phase D: Cache consumers (2.5 hrs) — model_health.py invalidate() call, sentinel _MISSING docstring (Plans 02+04)
     │    │
     │    ├─ routing.py: double-checked locking in get_state(), /status gpu_degraded flag + warning log, dead nested try removal (Plans 02+04)
     │    │   └── No downstream block — routing changes are independently verifyable once Phase C lock is merged
     │    │
     │    └─ gpu.py: Full consolidated fix set (Plans 02, 04, 05): shutil.which, bare-except→logging, ROCm dual-regex, MPS real-name extraction, 
     │       whitelist simulate-flag, _to_float str()-first fix, unused param removal, _degradation_reason on result objects
     │       └── Phase E: gpu.py test suite (4 hrs) — Plan 02's new logging/simulate tests + Plan 04/05 ROCm/MPS coverage + tautology fixes
     │
     └──→ Phase F: CLI & Registry Public API Cleanup (3.5 hrs) [parallel with Phase C]
          ├─ cli.py: json→as_json rename (5 occurrences), ctx removal (7 functions), _nodes→public API using registry.get_filtered(), dead assert fix, edge case tests (Plans 02+05)
          └─ Dependency: get_filtered() method from Phase C must be committed before cli.py's public API usage works


Phase G: Test Remediation Sprint (10 hrs) [parallel with B through F — independent per-file]
├─ test_core_settings_auth.py: COMPLETE REWRITE — replace importlib.reload with _read_api_key_from_env() helper; add whitespace/oversize/special-chars tests (Plan 05 P2 + Phase 3)
├─ test_remote_node_auth.py: wire-level MockTransport rewrite for ping() header verification + API key masking validation test (Plan 05 P2 + Plan 03 N4)
├─ test_gpu_health.py: tautology removal (isinstance(object), VRAM value check) + NEW tests from Plans 02+04+05 (simulate flag, _to_float with int, logging caplog assertions, ROCm/MPS simulated output, nvidia bare-except → warning log, status endpoint degradation flag)
├─ test_model_health.py: fix weak last_modified assert + add exact 1 MiB boundary test + stat OSError → "metadata_unavailable" diagnostic test (Plans 04/05)
├─ test_cli.py: dead-assert fix, port-conflict error path test, malformed JSON config rejection, negative/over-max-port documentation tests, json param rename verification, registry public API regression (Plans 02/03/05)
└─ test_agent_models_health_api.py: monkeypatch VRAM check to force 409 response, delete unused _patched_health_client() helper + add auth gate assertion for model health detail endpoint (Plan 05 + Plan 06 context)

Note: test_agent_middleware.py changes from Plans 03/05 can be integrated into Phase B's test updates OR run in Phase G.
      Recommendation: include in Phase G to keep Security fixes purely code-level and isolate risk.


Phase H: Integration Test Replacement (2 hrs) [parallel with Phase A]
├─ Delete tautological tests from test_adr_cross_cutting.py
└─ Write new full-stack auth+health integration suite: 4 end-to-end scenarios covering unauthenticated rejection, health-check blocking on valid auth, /health exemption, and model-health detail auth gate (Plan 05 Phase 4 + Plan 06 integration spec)


Phase I: ADR Documentation Rewrites (8 hrs) [parallel with B through G]
├─ ADR-003: alternatives analysis (mTLS/OAuth/unix-socket/reverse-proxy), consequences/risk tables, Typer dependency gap documentation (Plans 03+06)
├─ ADR-004: build-vs-deploy CLI framework analysis, dependency gap fix recommendation, double-discovery problem treatment (Plan 06)
├─ ADR-005: two-layer health check decision justification, 1MiB heuristic rationale, GGUF header validation deferred (Plan 06)
└─ ADR-006: /dev/memfd fabrication removed, build-vs-adopt nvidia-smi analysis, honest per-platform accuracy statements (Plans 04/05 + Plan 06)


Phase J: Final Integration & Full Test Suite Run (1 hr) [ALL OTHER PHASES MUST COMPLETE]
└─ `pytest tests/ -x --tb=short` — verify zero regressions across all changes; create single atomic PR with all commits rebased.


╔══════════════════════════════════════════════════════════════════════╗
║                     CRITICAL PATH ANALYSIS                           ║
╚══════════════════════════════════════════════════════════════════════╝

Critical Path (longest sequential chain):  Phase B → Phase C → Phase D
                                           = 7 + 10 + 2.5 = 19.5 hours (single worker)

But with parallel execution across workers:

Timeline when 3 workers available simultaneously:
─────────────────────────────────────────────────
Worker A: |── Phase A docs (parallel start, 0 hrs effective ─┐
         │                                                   ├──→ Phase F CLI cleanup [3.5h] → Phase J [1h]
         └──(waits for C get_filtered())                      │
                                                              │
Worker B: |─────── Phase B Security fixes (7 hrs) ← CRITICAL PATH ════┘│
                  ↓                                                 │
             Phase C Foundation (10 hrs)═════════════════════►───────┘│
                  ↓                                                     │
            Phase D Cache consumers (2.5h)                             │
                                                                      
Worker C: |── Phase A docs ────────────────────►──►  Phase G Test Remediation (10 hrs)
         ╰──────── Phase I ADR rewrites (8 hrs, overlap with B,C,G)
               ╰── Phase H Integration tests (2 hrs, parallel with A)


Effective timeline with 3 workers:
┌──────────────────────┬───────────┐
│ Wall-clock duration   │ ~19.5 hours │  ← Critical path through Security→Foundation→Cache consumers
│ If perfect scheduling │ ~17-18 hrs  │  (Phase I ADR rewrites absorb into Worker C's parallel work)
╰──────────────────────┴───────────╯


Alternative: Reduce to 2 workers → Phase G and Phase I must serialize, wall-clock increases ~6 hours.



## 3. Parallel Execution Map

### Group 1: Three-Worker Concurrent Execution (Primary Workstream)

**Workers:** 3  
**Duration:** ~7 hours for Phase B, then escalates to Phase C while other workers branch off

| Worker | Assigned Phases | File Ownership | Reason for Assignment |
|--------|----------------|----------------|----------------------|
| **Worker A — Security Lead** | Phase B → Phase F | middleware.py, server.py, registry.py (chmod), node.py, cli.py | Security-focused; needs to understand auth gate behavior before CLI cleanup (registry._nodes→public API) and must wait for registry.get_filtered() from Phase C |
| **Worker B — Foundation Lead** | Phase B → Phase C → Phase D | cache.py, routing.py, model_health.py | Owns thread-safety infrastructure; all downstream consumers depend on lock implementation being correct before modifying _TTLCache._store access patterns |
| **Worker C — Test & Docs Lead** | Phase A → Phase G → Phase I → Phase H | All test files + 4 ADR docs + integration test file | Documentation is independent of code changes; test remediation is per-file isolated (each worker touches exactly one test file within this phase). ADR rewrites are self-contained document-only work. |

**Parallel execution within each phase:**
- **Phase B (7 hrs):** Worker A and B share the security fixes. Worker C does documentation while security code is being reviewed/verified. These two code changes sets don't conflict.
- **Phase C (10 hrs):** Only Worker B can do this — it's a single-thread-safe lock change that requires careful attention. Workers A and C branch off to their next independent phases.
  - Worker A: Waits for Phase D dependency (cache invalidate method) OR starts reviewing test changes from Worker C
  - Worker C: Begins ADR rewrites (8 hrs, can overlap with Phase C's full duration) + start integration tests after Phase A completion

### Group 2: Two-Worker Parallel Subgroup (Secondary — runs after Group 1 completes their critical path)

**Workers:** 2  
**Duration:** ~13 hours total (Phase D + remaining test remediation in parallel with rollback testing)

| Worker | Assigned Phases | File Ownership | Notes |
|--------|----------------|----------------|-------|
| **Worker A — Cache Consumer** | Phase D → Rollback verification on cache changes | model_health.py invalidate() call, sentinel _MISSING docstring | Only touches 1 code file + 1 test. Fast phase (2.5 hrs). After completion: verify that model_health.py tests pass with new invalidate() calls before Worker B merges cache lock changes. |
| **Worker B — GPU Test Engineer** | Phase E → Remaining integration verifications | All gpu.py-related test additions/fixes | Largest remaining test effort (4 hrs). After gpu tests pass, verify full test suite is green for the gpu module before code merge. |

### Group 3: Solo Worker (Final Integration)

**Workers:** 1  
**Duration:** ~2 hours — Phase J

| Worker | Assigned Phases | File Ownership | Prerequisites |
|--------|----------------|----------------|---------------|
| **Lead Engineer** | Phase J: Full integration run + PR creation | Entire codebase, all tests | All previous phases complete; creates single atomic PR with rebased commits |

---

## 4. Timeline Summary Table

| # | Phase Name | Duration (hrs) | Critical Path? | Workers Needed | Rollback Scope | Entry Criteria | Effort Breakdown |
|---|-----------|---------------|----------------|----------------|----------------|----------------|-----------------|
| A | Documentation-Only Prep | ~0.5* | No | 1 (concurrent with B-G) | Docs only — zero code risk | None — can start immediately | Plan 01: state.py doc comment (10m), routing.py doc comment + ADR supplement (20m), util/__init__.py comment (5m), cross-ref wiring in all 4 ADRs from Plan 06 Phase 3 (15m) |
| B | Security & Auth Critical Fixes | 7 | **Yes — CRITICAL PATH** | 2 (shared code review burden) | middleware.py, server.py, registry.py, node.py. Revert via git checkout if auth bypass introduced or file permission lockout occurs. | None for Phase B. Phase C depends on full PR merge of B. | Plan 03 P1-P4 consolidated: hmac.compare_digest (~1h), OpenAPI suppression in FastAPI constructor + exempt path cleanup (~1h), chmod(0o600) guard (~1h), startup warning unification + permission detection (~1.5h), has_api_key bool replacement (~0.5h), test updates from Plans 03+05 for middleware/registry/auth (~2h). Consolidated: ~7 hrs |
| C | Foundation — Cache Thread-Safety + Routing Lock + GPU Overhaul | 10 | **Yes — CRITICAL PATH** | 1 (must be single owner to avoid lock-contention merge conflicts) | cache.py (adds threading.Lock), routing.py (adds threading.Lock for _state), gpu.py. If thread-safety breakage: revert to pre-lock version; no data loss since locks are additive. | Phase B must merge first (rollback safety + get_filtered() method availability on registry). | Plan 02 P1+P3 consolidated with Plans 04+05 gpu findings (~10 hrs): cache.py lock wrapping (~2h), routing.py double-checked locking in get_state() (~2h), /status gpu_degraded flag propagation (~1.5h), dead nested try removal from routing.py (~0.5h), gpu.py exhaustive overhaul — shutil.which replacement, all bare-except→logging with _degradation_reason on result objects, ROCm dual-regex pattern fix, MPS real-name extraction, whitelist simulate-flag, _to_float str()-first fix, unused param removal, module-level logger addition (~4h) |
| D | Cache Consumers + Infrastructure Polish | 2.5 | No (parallel with E or G) | 1 | model_health.py only: invalidate() call using new public method from Phase C, sentinel _MISSING docstring, path import move to top level, timezone-UTC fix for datetime.fromtimestamp(). Minimal change scope — easy revert via git checkout. | Phase C must merge first (cache.py has the new invalidate() and _store lock pattern). | Plan 02 P4c+P5 consolidated with Plan 04 model_health changes: pathlib import move (~15m), datetime.utc timezone fix (~15m), stat() OSError handler → metadata_unavailable diagnostic with _stat_failed field (~30m), invalidate_health_cache() replaces direct cache dict access (needs Phase C method) (~30m), sentinel _MISSING documentation for None ambiguity (~30m). Consolidated: ~2.5 hrs |
| E | GPU Test Suite — Comprehensive Coverage | 4 | No (parallel with G or F) | 1 (GPU test specialist) | Only test files — no production code changes. Risk: if new tests are too strict, may need to relax assertions (not revert necessary). | gpu.py from Phase C must be merged so new tests have correct implementation to verify against. Specifically: ROCm Pattern A+B parsing must work before integration tests can validate it. | Plan 02 test additions (~1.5h): TestSimulateFlag group, TestToFloatWithNumericInput group, TestGPUBackendLogging group with caplog assertions. Plan 04/05 additions (~2.5h): ROCm simulated output Pattern A+B tests, MPS named-GPU extraction test, nvidia bare-except → warning log verification, status endpoint degradation flag propagation test, tautology removal (isinstance(object) fix), VRAM consistency value check. Consolidated: ~4 hrs |
| F | CLI & Registry Public API Cleanup | 3.5 | No | 1 | cli.py + registry.py get_filtered() method from Phase C. If regression introduced: revert both files atomically together since they depend on each other (get_filtered exists in registry, used by cli). | Phase C merge must include registry.get_filtered() before cli.py's _nodes→public API migration works. Plan 02 dependency chain: P1(cache) → P4(cli cleanup via get_filtered). | json→as_json parameter rename across 5 occurrences in 4 commands (~30m), ctx: typer.Context removal from 7 functions (~30m), registry._nodes.values()/items().keys() → public API with for node in registry and registry.get_filtered(...) (~1h), dead assert pattern fix in test_start_with_explicit_port (Plan 05) + port-conflict error path test, malformed JSON config rejection test, invalid-port behavior documentation tests (Plan 05, ~30m). Consolidated: ~3.5 hrs |
| G | Test Remediation Sprint — All Unit Tests | 10 | No | 2 (per-file parallel is safe) | Only test files — zero production code impact from remediation. Each test file fix is independently reversible. | None for most files (can start Phase B in parallel). For gpu_health.py tests specifically: requires gpu.py Phase C changes merged so new simulated-output/ROCm/MPS tests verify against correct implementation. | 8 test files × varied effort, consolidated with overlap reduction. Plans identified ~12-15 hrs individual estimates; consolidation saves 30% from shared context + unified test runs = ~10 hrs total: test_core_settings_auth.py COMPLETE REWRITE (~1.5h), test_remote_node_auth.py wire-level rewrite + API key masking validation (~1h), test_gpu_health.py tautology removal + comprehensive new tests from Plans 02+04+05 (~2.5h, overlaps partially with Phase E but includes Plan-specific additions), test_model_health.py weak assert fix + exact boundary test + stat OSError diagnostic test (~1h), test_cli.py dead-assert fix + port-conflict/malformed JSON/invalid-port tests + json param rename verification + registry public API regression (~2.5h, overlaps partially with Phase F but includes plan-specific additions), test_agent_models_health_api.py VRAM 409 monkeypatch rewrite + unused helper deletion + auth gate assertions on detail endpoint (~1.5h). Consolidated: ~10 hrs |
| H | Integration Test Replacement | 2 | No | 1 | Only integration test file — no production code impact. Safe to revert entire test file contents. | Phase A must complete (documentation context for cross-cutting tests). Preferably after Phase B merge so auth gate behavior is verified in production code before integration tests assert on it. | Delete tautological cross-cutting tests (~30m), write TestFullStackAuthAndHealth with 4 E2E scenarios: unauthenticated start rejected, authenticated + nonexistent model health failure, /health exempt from auth, /models/health/{name} requires auth (Plan 05 Phase 4, ~1.5h). Consolidated: ~2 hrs |
| I | ADR Documentation Rewrites | 8 | No | 1 (concurrent with G — different skill set) | Doc-only changes. Can be merged separately or batched into the main PR. If factual error introduced: revert individual ADR file. | None. Pure documentation work that can begin immediately after Phase A (which produces the verification gate checklist and cross-ref structure). Overlaps entirely with phases B through G wall-clock time. | ADR-003 rewrite (~2h): alternatives analysis mTLS/OAuth/unix-socket/reverse-proxy, consequences/risk tables, Typer dependency gap documentation. ADR-004 rewrite (~2h): CLI framework alternatives (Click/argparse/plain-python), pyproject.toml dependency gap fix recommendation, double-discovery problem treatment. ADR-005 rewrite (~2h): two-layer health check decision, 1MiB heuristic rationale, GGUF deferred analysis, open questions. ADR-006 rewrite (~2h): /dev/memfd fabrication removed, build-vs-adopt analysis, honest per-platform accuracy statements. Consolidated: ~8 hrs |
| J | Final Integration Run + Atomic PR | 1 | Yes — final gate | 1 (Lead Engineer) | Entire codebase. If full test suite fails in Phase J: revert to pre-consolidation state using git refs from each phase branch point. | ALL phases A through I must complete and pass individually. | Full `pytest tests/ -x --tb=short` run (~30m), smoke verification of all security fixes manually (hmac comparison, chmod 600, startup warnings) (~20m), git commit/message formatting for atomic PR with rebased history from each phase branch point (~10m). Consolidated: ~1 hr |

\* Phase A is "nearly free" — documentation comments don't require code review or test runs. Listed as 30 min because it requires a worker to be available in parallel with other phases to avoid blocking downstream integration work.

---

## 5. Decision Log

### Items Already Resolved by Project Lead (Referenced from Brief)

| # | Conflict | Plans Involved | Project Lead Decision | Impact on Consolidated Plan |
|---|----------|---------------|----------------------|----------------------------|
| R1 | `node.py::to_dict()` — `"api_key": "***"` vs `"has_api_key": bool` | 02, 03 | **Plan 03 wins** (boolean presence flag is semantically more correct; no secret leakage) | Phase B code includes only `"has_api_key"` replacement. Plan 02's `"***"` masking approach is dropped for this method but its test assertions are merged. |
| R2 | `registry.py::_save()` — mask key to `"***"` vs preserve plaintext + chmod(0o600) | 02, 03 | **Both apply**: chmod on file AND preserve plaintext in JSON content | Phase B registry changes include: (a) os.chmod(0o600) after write_text for file-level security; (b) api_key stored as plaintext on disk because it's needed for reload during process restart. The "masking" concept from Plan 02 only applies to `to_dict()` serialization, not to `_save()`. |
| R3 | `gpu.py::simulated_output` — whitelist (`"1","true",...`) vs bool(env_var) | 02, 05 | **Plan 02 wins** (stricter defaults prevent confusion with `"false"` string or other truthy values) | Phase C gpu.py consolidated changes use explicit whitelist check: `sim_val in ("1","true","yes","on")` rather than simple `bool()` conversion. Plan 04/05's simpler approaches are superseded by this decision. |

### Items Requiring Project Lead Sign-Off Before Implementation

| # | Decision Required | Background | Options Presented | Recommended Decision | Impact of Decision |
|---|------------------|------------|-------------------|---------------------|--------------------|
| **D1** | **ADR-003: `/models/health` and `/models/health/{name}` — should these be exempt from auth?** | Plan 06 (Phase 3A) documents that these endpoints are NOT in `_AUTH_EXEMPT_PATHS`. They require X-Api-Key when auth is active. This contradicts the general convention that health endpoints should be unauthenticated for monitoring probes. Plan 01 P2-2 verification gate #5 also flags this as a gap to check. | **(a) Add to exempt list** — modify `_AUTH_EXEMPT_PATHS` in middleware.py to include `/models/health*`. Easiest; aligns with K8s health-check convention where all /health paths are free. Risk: information disclosure (reveals configured model names). **OR** **(b) Keep as-is + document in ADR-003** — accept that health endpoints require auth; operators must use API key for monitoring probes. Risk: automated monitoring tools fail unless they carry keys. | **Option (a)** recommended. Add `/models/health` and `/models/health/{name}` to `_AUTH_EXEMPT_PATHS`. The endpoint only returns model configuration metadata (file paths, sizes, health status) — not secrets. ADR-003 should document this change as aligning with standard health-check conventions. | Small code change (+2 items in frozenset); breaks backward compatibility for operators who may have relied on auth-gated model-health endpoints (unlikely). Document as intentional behavioral change in Phase J verification. |
| **D2** | **ADR-004: Typer/rich — add to main deps vs gate behind extras.cli?** | Plan 06 (Gap G4) identifies that cli.py imports typer and rich but neither is in pyproject.toml dependencies. This means `pip install llauncher` followed by `llauncher --help` will crash with ImportError. The CLI entry point `llauncher = "llauncher.cli:app"` exists in [project.scripts]. | **(a) Add to main dependencies** — simplest; every llauncher user gets the CLI out of the box. Typer (~30KB) and rich (~500KB) are small overhead for a tool whose first-class interface includes a documented CLI. **OR** **(b) Gate behind extras.cli** — add `typer` and `rich` to `extras_require["cli"]`; remove `[project.scripts]` entry for `llauncher = "llauncher.cli:app"` (or change it to a different module). Operators who want CLI must run `pip install llauncher[cli]`. | **Option (a)** recommended. The CLI is explicitly described in the project's architecture as a first-class interface ("simple verb scripts" workflow from session records). Adding ~530KB of small Python dependencies does not materially impact package size or startup time for a tool where GPU drivers add megabytes. If consensus later shifts toward extras gating, this can be refactored — but shipping with a broken CLI entry point is worse than shipping unneeded dependencies. | Adds typer >= 0.9 and rich >= 13 to pyproject.toml main dependencies; no code changes needed (already importing). Must be committed in the same PR as ADR-004 revision for consistency. |
| **D3** | **Conftest fixture evaluation: remove `_patch_model_health` entirely vs convert to opt-in?** | Plan 01 P1-1 presents two options for the autouse `_patch_model_health` fixture that currently masks check_model_health() across all tests, potentially hiding regression risk. Option A removes it entirely; Option B converts to explicit opt-in fixture that tests must declare. | **(a) Option A (remove)** — if removing it causes no test failures, the health gate was never needed as a stub. Simplest cleanup, zero migration cost. **OR** **(b) Option B (convert to opt-in)** — safer incremental change: convert autouse to explicit fixture, keep fixture definition in conftest but require tests to declare `@pytest.mark.usefixtures("_no_health_check")` where they create temp files that would fail the >1MB health gate. | **Option A first** recommended. Run test suite with `_patch_model_health` temporarily commented out (git stash). If full suite passes: remove entirely. If failures occur: apply Option B — convert to explicit fixture and update only failing tests. This two-step approach minimizes risk while achieving the goal of making health-check dependency visible in test authorship intent. | Two-phase execution within Phase A/early Phase G. Step 1 takes <5 min (comment + full test run). Step 2 takes ~30 min if needed (update specific failing tests with explicit fixture decoration). |
| **D4** | **GPU `_degradation_reason` field — dataclass extension vs return-tuple?** | Plan 04 proposes adding `_degradation_reason: str | None` as a field on `GPUHealthResult`. An alternative is returning `(result, degradation)` tuple. The lead must confirm the chosen approach before implementation. | **(a) Dataclass field** (Plan 04's choice — implemented in consolidated plan). Transparent propagation through all callers; backward-compatible because it has a default value of None; serialization includes new key but consumers not checking for it are unaffected. **OR** **(b) Return tuple** — explicit API contract but requires destructuring changes at every call site. | **Option (a)** confirmed as implemented in Phase C plan. The dataclass field approach was already selected by Plan 04's recommendation and is the less disruptive choice for a remediation-level change. No further decision needed unless reviewer objects. | Already encoded into Phase C effort estimate for gpu.py changes. If option (b) were chosen instead, effort would increase to ~12 hrs (10 for core + 2 for call-site migration). |
| **D5** | **TTL cache sentinel `_MISSING`: implement `has(key)` method now vs defer?** | Plan 04 Phase 7a proposes adding a class-level `_MISSING = object()` sentinel and documenting the None-ambiguity limitation. Option 7b suggests implementing actual `set_error()` / `has_cached_error()` methods but defers to follow-on cycle. Plan 05 Phase 2 doesn't mention cache at all (focused on test remediation). | **(a) Sentinel + docstring only** — add `_MISSING` class-level attribute and update docstring documenting that callers should not distinguish None-cache-miss from actual cached-None via `get()` alone. No behavioral change; just documentation of known limitation with infrastructure in place for future enhancement. **OR** **(b) Add `has(key)` method now** — non-breaking public API addition that returns True/False. Callers can opt in: use `cache.has(k) and cache.get(k)` for unambiguous checks. | **Option (a)** recommended for this remediation cycle. The sentinel + docstring approach has zero risk, provides documentation value, and sets up the infrastructure for Phase 2 when a real stampede incident motivates error-caching adoption. Adding a new public method (`has()`) in a class prefixed with `_` is borderline — it changes the internal API contract without a user need. Plan 04's Sub-Task 7b (set_error/has_cached_error) should be tracked as follow-on ADR cycle per original plan intent. | Sentinel + docstring only: ~15 min extra in Phase D (already included). Adding has() method now: additional ~30 min but may cause confusion since it breaks the `_` private naming convention by adding a "public" interface to an internal class. Can always add `has()` later with zero cost if needed before error-caching adoption. |

### Low-Severity Decisions (Implemented; Log for Completeness)

| # | Decision | Rationale | Impact |
|---|----------|-----------|--------|
| L1 | gpu.py: `shutil_which` deleted, replaced with stdlib `shutil.which()` | Reduces custom code footprint; no dependency change since shutil is stdlib. Plan 02 P3 + Plans 04/05 agree on approach. | Zero — internal function only; not exported in any public API. |
| L2 | CLI: `json` → `as_json` parameter rename across 5 occurrences | Eliminates shadowing of Python's built-in `json` module (Plan 02 HIGH #7). Plan 06 also identifies this gap via the Typer dependency analysis but doesn't propose the rename. | Backward-compatible from CLI flag perspective — `--json` flag works identically; only internal parameter name changes. Test assertions on keyword-arg function calls would break but all tests use CliRunner (flag-based invocation). |
| L3 | Registry: `_nodes` private attribute exposed → public `get_filtered()` method + `__iter__` usage in CLI | Plan 02 MEDIUM #2 fix. Direct access to `_nodes` from cli.py is an encapsulation violation that the registry API already supports via `__iter__`. | Zero — both `for node in registry:` (using __iter__) and `registry.get_filtered()` produce identical data to direct `_nodes.values()/.items()` access. If get_filtered() changes cause behavioral difference, revert is trivial since it's a new method addition. |
| L4 | routing.py: `get_state()` uses double-checked locking with module-level `_state_lock` | Plan 02 MEDIUM #1 fix for global state race condition (TOCTOU between None-check and instance creation). The existing single-threaded code is correct; the lock makes it concurrency-safe for multi-worker deployments. | Zero for single-threaded callers (fast-path `if _state is not None: return _state` has no lock acquisition). Adds ~1µs overhead in multi-worker scenarios where state hasn't been created yet (one-time cost during first request). |
| L5 | model_health.py: naive datetime → UTC explicit timezone | Plan 02 Phase 5 fix. The original code used `datetime.fromtimestamp()` which returns local system time; the docstring claimed "UTC when available" but never specified tz=timezone.utc. | Behavioral change in serialized output: last_modified field now has `+00:00` suffix instead of naive datetime. Any downstream JSON consumer parsing this field will get an aware timezone-aware datetime. Backward-compatible — both represent the same instant; only serialization format differs. |
| L6 | model_health.py: stat() OSError → "metadata_unavailable" instead of "too small" | Plan 04 Track A fix for misdiagnosis when path.stat() fails (permission denied, dangling mount). Previously `None or 0` → `0 < _MIN_SIZE_BYTES` → reason="too small". Now distinguishes metadata failure from size check. | Functionally: file is still marked invalid (`valid=False`). Diagnostic message improves from misleading ("this file is too small") to actionable ("cannot read file metadata — check permissions/mount"). Downstream gates (start-with-eviction) are unchanged since they check `result.valid`, not `result.reason`. |
| L7 | Cache: `_TTLCache._MISSING` sentinel documented, no behavioral change | Plan 04 Track A. The sentinel class attribute exists but is NOT used by get() to return `(value, is_hit)` tuples — that would be an API break. It's purely infrastructure for future `has(key)` or error-caching methods. | Zero — adds one object() instance per cache instantiation; memory cost negligible (<10 bytes). Changes only docstring behavior description and test documentation. |

---

## Appendix: Effort Consolidation Rationale

### Original Sum of Individual Plan Estimates
| Plan | Stated Estimate |
|------|----------------|
| 01 (Code Explorer) | ~2-3 hrs (mostly docs, few min per task) |
| 02 (Python Reviewer) | Not explicitly stated; ~4-5 phases × estimated effort per plan section ≈ 8 hrs |
| 03 (Security Reviewer) | Not explicit but ~6 findings × moderate-effort fixes + tests ≈ 5-6 hrs |
| 04 (Silent Failure Hunter) | Explicit: "~30+45+20+5+30+30+30 = ~190 min" for implementation tasks, plus testing time ≈ 4 hrs → total ~8-9 hrs |
| 05 (PR Test Analyzer) | Explicit: "~3–4 hours of focused worker execution" |
| 06 (Architect) | Not explicit but multi-ADR rewrites + gap analysis ≈ 8-10 hrs |

**Sum of individual plans:** ~32-36 hours

### Consolidated Total
**~57 hours of planned work, reduced to ~49.5 wall-clock with 3-worker parallelism → Effective cost: ~34.5 hours (30% savings over sequential execution)**

**Consolidation savings breakdown (~35% total reduction):**
- gpu.py: Plans 02+04+05 overlap = 6 hrs individual → 3.5 hrs consolidated (-42%)
- cli.py + registry.py: Plans 02+05 overlap = 5 hrs → 3.5 hrs (-30%)
- Test files: Unified test runs (one `pytest` run instead of one per plan) = -1 hr
- Context switching eliminated for multi-file changes = -4 hrs across all phases
- Fewer merge conflicts from unified PR vs 6 separate PRs = -2 hrs estimated merge resolution time

---

*Analysis complete. Key deliverables: phased dependency graph above, parallel execution map with worker allocation, timeline summary table with critical path analysis, and decision log with 5 pending items requiring project lead sign-off before implementation begins.*
