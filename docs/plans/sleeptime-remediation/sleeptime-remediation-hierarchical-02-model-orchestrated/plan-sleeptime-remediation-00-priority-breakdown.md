# Sleeptime Remediation — Priority Breakdown

Generated from the Opus 4.7 complete review (a4d0361..9c73c71).
Risk-scoped per reviewer verdict: the bones are mostly fine, but bugs are concentrated and urgent.

---

## Synthesis Methodology

Issues were grouped by **workstream** then ordered by a composite of:
- **Severity weight**: CRITICAL = 4, HIGH = 3, MEDIUM = 2, LOW = 1
- **Impact breadth**: How many downstream components are affected if this fails in production
- **Detectability**: Is the failure visible (logged/error) or silent?

The composite produces a ranked priority ladder. "Blocker" items must be resolved before promotion; everything else is tiered by risk tolerance.

---

## P0 — Blocker (Do Not Ship Without These Fixes)

| # | Severity | Subsystem | File:Line | Source(s) | What |
|---|----------|-----------|-----------|-----------|------|
| B1 | CRITICAL | Security | `middleware.py:54` | security-reviewer + python-reviewer | Timing attack: naive `!=` string comparison. Replace with `hmac.compare_digest`. |
| B2 | CRITICAL | Security / Data Leak | `registry.py:55-63`, `node.py:280` | security-reviewer + python-reviewer | API key written in plaintext to world-readable JSON (664 perms). Keys also leak via `to_dict()`. Fix: chmod 0o600, mask keys in serialization. |
| B3 | CRITICAL | Silent Failure / GPU Pre-flight | `gpu.py:141-142`, `151-152`, `161-162` + `routing.py:73-74` | silent-failure-hunter | Bare `except: return False` in every GPU backend query. When nvidia-smi/rocm-smi fails, system reports "no GPUs" → VRAM pre-flight never fires → OOM launch silently allowed. Replace with scoped exception + logging. |
| B4 | CRITICAL / Security | Exposed Schema | `server.py:128-129` + `middleware.py:13` | security-reviewer | `/openapi.json` is exempt from auth, leaking full route schema when token IS set. Even authenticated users can enumerate every endpoint — parameter names, response shapes, internal routes. Fix: set `openapi_url=None`, remove from exempt paths. |

---

## P1 — High Risk (Fix Before Production Promotion)

| # | Severity | Subsystem | File:Line | Source(s) | What |
|---|----------|-----------|-----------|-----------|------|
| B5 | HIGH / Silent Failure | Unbound Variable Cascade | `gpu.py:263-292` (ROCM parser) | silent-failure-hunter + python-reviewer | First try/except swallows exception, second block references unbound `out`. Outer bare except swallows the UnboundLocalError silently. Always returns empty result. **Ownership:** silent-failure-hunter brief owns ROCm restructure; python-reviewer only touches lines outside 263-292 in gpu.py. |
| H1 | HIGH | Concurrency | `cache.py:10-54` + `model_health.py:134` | python-reviewer + silent-failure-hunter | `_TTLCache` has no thread safety AND no public `invalidate()` method. TOCTOU race on dict. Fix: add `threading.Lock` (python-reviewer) AND expose `invalidate(key)` public method that uses the lock (silent-failure-hunter). **Ownership:** silent-failure-hunter owns the complete cache fix; python-reviewer verifies Lock exists in acceptance criteria. |
| H2 | HIGH | Silent Failure / GPU | `gpu.py:193`, `208-209` (NVIDIA driver-version call) + `routing.py:177-183` | silent-failure-hunter + python-reviewer | Parse failures return empty device list. `/status` endpoint silently drops GPU field on exception with no degraded flag. Add logging + degraded response field. |
| H3 | HIGH | Logic Bug / GPU Simulation | `gpu.py:134` | python-reviewer + silent-failure-hunter | `simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""` inverts logic — simulation activates when env var is *absent*. Rewrite to explicit boolean. |
| H4 | HIGH | Silent Failure / MPS Parser | `gpu.py:308-318` (MPS backend) | python-reviewer | Dead loop assigns to `match`, never used. Result always appends one device regardless of actual GPU count. Broken on multi-GPU Apple systems. |
| H5 | HIGH | Logging Gap / Auth Warning | `server.py:168-173` | security-reviewer | No-token warning only fires when binding to `0.0.0.0`. Network-reachable bind addresses (e.g., 192.168.x.x) silently allow all traffic without any startup warning. |
| H6 | MEDIUM → High Risk | Dead Code / Shadowing | `cli.py:103`, `119`, `183`, `248` | python-reviewer | Every command shadows stdlib `json` with a `--json` boolean parameter. Fragile accident — rename to `as_json`. Also 7 unused `ctx` params and 4 direct `_nodes` accesses. **Bundle note:** Will be bundled into the file edit touched by Phase A-1 or B. |
| H7 | MEDIUM → High Risk | VRAM Heuristic Gap | `routing.py:409-415` + `test_agent_models_health_api.py:162` | silent-failure-hunter + pr-test-analyzer | `_estimate_vram_mb` has no unit tests. Hardcoded absolute path in integration test will fail on any non-author machine. Test passes vacuously when no GPU present.

| # | Severity | Subsystem | File:Line | Source(s) | What |
|---|----------|-----------|-----------|-----------|------|
| H1 | HIGH | Concurrency | `cache.py:10-54` | python-reviewer + silent-failure-hunter | `_TTLCache` has no thread safety. Module-level singleton hit from multiple FastAPI worker threads. TOCTOU race on dict. Fix: add `threading.Lock`. |
| H2 | HIGH | Silent Failure / GPU | `gpu.py:193`, `208-209` (NVIDIA driver-version call) + `routing.py:177-183` | silent-failure-hunter + python-reviewer | Parse failures return empty device list. `/status` endpoint silently drops GPU field on exception with no degraded flag. Add logging + degraded response field. |
| H3 | HIGH | Logic Bug / GPU Simulation | `gpu.py:134` | python-reviewer + silent-failure-hunter | `simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""` inverts logic — simulation activates when env var is *absent*. Rewrite to explicit boolean. |
| H4 | HIGH | Silent Failure / MPS Parser | `gpu.py:308-318` (MPS backend) | python-reviewer | Dead loop assigns to `match`, never used. Result always appends one device regardless of actual GPU count. Broken on multi-GPU Apple systems. |
| H5 | HIGH | Logging Gap / Auth Warning | `server.py:168-173` | security-reviewer | No-token warning only fires when binding to `0.0.0.0`. Network-reachable bind addresses (e.g., 192.168.x.x) silently allow all traffic without any startup warning. |
| H6 | MEDIUM → High Risk | Dead Code / Shadowing | `cli.py:103`, `119`, `183`, `248` | python-reviewer | Every command shadows stdlib `json` with a `--json` boolean parameter. Fragile accident — rename to `as_json`. Also 7 unused `ctx` params and 4 direct `_nodes` accesses. |
| H7 | MEDIUM → High Risk | VRAM Heuristic Gap | `routing.py:409-415` + `test_agent_models_health_api.py:162` | silent-failure-hunter + pr-test-analyzer | `_estimate_vram_mb` has no unit tests. Hardcoded absolute path in integration test will fail on any non-author machine. Test passes vacuously when no GPU present. |

---

## P2 — Medium Risk (Fix Before Production, After P0/P1)

| # | Severity | Subsystem | File:Line | Source(s) | What |
|---|----------|-----------|-----------|-----------|------|
| M1 | MEDIUM | Timezone | `model_health.py:93` | python-reviewer | `datetime.fromtimestamp()` returns naive local time, not UTC as docstring says. Add `tz=timezone.utc`. |
| M2 | MEDIUM | Type Safety | `gpu.py:389` (`_to_float`) | python-reviewer | `.strip()` called before type check — will `AttributeError` on int/float inputs from JSON nvidia-smi output. |
| M3 | MEDIUM | Scope Leak | `routing.py:13-22` | python-reviewer | Global mutable `_state` in routing — add proper accessor or `@lru_cache(maxsize=1)`. |
| M4 | MEDIUM | ADR Misalignment | `ADR-003 lines 38-43` vs `middleware.py:13, 49` | security-reviewer | ADR claims `/status`, `/models` unauthenticated; implementation protects them. Docs are wrong — must be corrected to match code. |
| M5 | N/A | Unclaimed Changes | `routing.py`, `state.py`, `util/__init__.py`, `conftest.py` | code-explorer | New `/models/health/{model_name}` detail endpoint, state-layer integration, cache module exposure, shared test fixtures — all unmentioned in original summary. Verify these are intentional. |

---

## P3 — Low Risk (Nice-to-Have / Polish)

| # | Severity | Subsystem | File:Line | Source(s) | What |
|---|----------|-----------|-----------|-----------|------|------|
| L1 | LOW | Dead Parameters | `gpu.py:112` | python-reviewer | `_collect_devices` has unused `simulate`, `num_simulated` params. |
| L2 | LOW | Import Placement | `model_health.py:81` | python-reviewer | `from pathlib import Path` inside function body — move to module top. |
| L3 | MEDIUM (operational risk) | ADR Quality — operational liability | All 4 ADRs | architect | SHALLOW/RUBBER-STAMP verdict on all four. **Not cosmetic** — wrong ADR-003 causes operators to misconfigure monitoring tools that probe `/status` without auth credentials because the ADR falsely claims it's unauthenticated. Need proper alternatives sections, honest consequences, cross-references. Merge ADR-005+006 into single Pre-flight Validation Pipeline ADR. |

---

## Test Quality Summary (Independent Track)

Test quality **depends on code fixes** — it cannot run meaningfully in parallel with production code changes because test assertions reference the API surface being modified (e.g., `to_dict()` key removal, parameter renames). Test overhaul begins only after Phase A+B verification completes.

| File | Verdict | Action Required |
|------|---------|-----------------|
| `test_agent_middleware.py` | STRONG | Retain as-is (fix dead `self` param) |
| `test_ttl_cache.py` | STRONG | Retain as-is |
| `test_model_health.py` | ADEQUATE | Test exact 1 MB boundary; tighten last_modified assertion |
| `test_cli.py` | ADEQUATE | Add port-conflict error path; fix conditional assertion that passes silently |
| `test_core_settings_auth.py` | WEAK | Replace all 3 tests with whitespace-only, oversized-token, post-import-reload cases |
| `test_remote_node_auth.py` | WEAK/TAUTOLOGICAL | Rewrite to actually exercise HTTP header attachment on wire (not just `_get_headers()`) |
| `test_gpu_health.py` | WEAK | Add ROCm/MPS backend tests; replace tautological assertions (`isinstance(x, object)`); cover env-var logic inversion path |
| `test_agent_models_health_api.py` | WEAK | Remove conditional skip on no-GPU CI; add real VRAM heuristic unit tests |
| `test_adr_cross_cutting.py` | WEAK | Replace hardcoded paths with relative; eliminate tautologies; write real integration test that boots full stack |

**Critical gaps not covered by any existing test:**
- RemoteNode HTTP calls never verified with headers on the wire
- VRAM pre-flight `_estimate_vram_mb` for 3B/14B/70B model sizes
- ROCm backend parsing (`_query_ROCM` regex)
- `ping()` status update behavior on success/failure
- CLI server start port-conflict error path

---

## Risk Summary Table

| Tier | Count | Estimated Effort | Blocking? |
|------|-------|------------------|-----------|
| P0 Blocker | 4 items | ~3 hours (worker, sequential sub-phases) | **Yes** — do not ship without these |
| P1 High | 8 items (+ demoted B5) | ~5 hours (worker) | Yes — fix before production promotion |
| P2 Medium + Bundled M1/M2 | 6 items | ~2 hours (worker) | After P0/P1; M1/M2 bundled with their respective phase edits, no standalone Phase E needed |
| Test Overhaul | 9 files, ~20 new tests | ~8-10 hours (worker, sequential after code fixes) | **Sequential** — begins only after Phase A+B commits verified. Estimate includes re-execution time when test assertions must update for changed production API surfaces. |
| ADR Restructure | 4 documents → 3 merged | ~4 hours (planner review cycle) | Documentation — non-blocking but required for production audit trail. ADR-004 rewrite can begin in parallel after Phase A; merged 005+006 and rewritten 003 wait for code verification. |

**Total estimated effort: ~22-27 hours of focused work across sequential-within-phases execution.**

---

## Recommended Execution Order (Revised per Strategic-Planner Review)

**Critical coordination constraint: No two workers may edit the same file simultaneously.** File-level ownership is tracked below.

1. **Phase A-1** (Sequential — security files): Worker fixes P0 items on `middleware.py`, `server.py`, `registry.py`, `node.py` — timing attack, OpenAPI suppression, chmod 0o600, key redaction in `to_dict()` and `_save()`. **Ownership: single worker handles all P0 except gpu.py.**
2. **Phase A-1 verification** (`pytest tests/unit/test_agent_middleware.py -v` + grep for remaining `!= self.expected_token`)
3. **Phase A-2** (Sequential — GPU/routing files): Worker fixes B3/B5 on `gpu.py`, `routing.py` — bare-except restructuring, ROCm UnboundLocalError fix, degraded status flags, logging.
4. **Phase A-2 verification** (`pytest tests/unit/test_gpu_health.py -v` + grep for remaining bare `except:` in gpu.py)
5. **Phase B** (Sequential, after Phase A full verification): Worker fixes P1 items — cache Lock+invalidate merged into single atomic change owned by silent-failure-hunter brief; MPS parser fix; simulate-flag rewrite; startup warnings; CLI json rename; ctx param cleanup. Bundled M1 (timezone) and M2 (`_to_float`) into their respective file edits during Phase B — no standalone Phase E needed.
6. **Phase B verification** (`pytest tests/` — full suite)
7. **Phase C: Test Overhaul** (Sequential, after Phase B commits merged): Worker rewrites 5 weak/tautological test files + adds ~20 gap tests. Estimate includes time for updating assertions to match changed production API surfaces.
8. **Phase D-1**: Planner begins ADR-004 rewrite immediately (non-blocking — references stable CLI architecture)
9. **Phase D-2** (After Phase B verification): Planner begins rewritten ADR-003 and merged Pre-flight Validation Pipeline ADR using verified code as source material
10. **Strategic-planner iterative review cycle** on all rewritten ADRs until authentic approval without prompting
11. **Phase F**: Final verification — re-run ground-truth check against remediated commits
