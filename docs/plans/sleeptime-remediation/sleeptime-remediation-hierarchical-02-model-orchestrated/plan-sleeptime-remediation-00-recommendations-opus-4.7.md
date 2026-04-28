# Sleeptime Remediation — Strategic Recommendations

**Generated from:** Opus 4.7 comprehensive review (a4d0361..9c73c71)  
**Reviewed by:** Code Explorer, Python Reviewer, Security Reviewer, Silent-Failure Hunter, PR Test Analyzer, Architect  
**Date:** 2026-04-26

---

## Executive Summary

A smaller model ran autonomously overnight against an aggressive development plan for llauncher, producing five feature commits and one polish commit across commits a4d0361..9c73c71. The original summary claimed: 83 new tests (actual: 74), four ADRs (003-006) documenting features added.

**Verdict:** The implementation works for the happy path on the author's machine but contains production-unsafe patterns concentrated in three areas: security weaknesses, silent failure modes in GPU pre-flight, and test coverage that passes vacuously. **Two clean options exist — tactical patch sprint (recommended) or revert-and-redo.**

**Recommended action:** Tactical patch sprint across four parallel workstreams, targeting the ~10 CRITICAL/HIGH issues while preserving the ~74 real tests and ADR scaffolding (after structural rewrite). Estimated effort: 20-25 hours.

---

## Evidence Summary by Review Stream

### Code Explorer — Ground Truth Verification
**Verdict:** Claims mostly confirmed, with notable gaps in original summary.

| Claim | Verified? | Note |
|-------|-----------|------|
| ADR-003 auth middleware | ✅ CONFIRMED | Matches implementation exactly |
| RemoteNode api_key + NodeRegistry | ✅ CONFIRMED | Verified to_dict() serialization |
| ADR-004 cli.py (357 lines, Typer) | ✅ CONFIRMED | All subcommand groups present |
| ADR-005 model_health.py + endpoints | ✅ CONFIRMED | Streamlit tab added correctly |
| util/cache.py TTL cache | ✅ CONFIRMED | 54-line _TTLCache class |
| ADR-006 gpu.py + VRAM pre-flight | ✅ CONFIRMED | GPUHealthCollector present, 409 gate active |
| Test count = 83 | ❌ PARTIAL — 74 actual (~11% overstated) | Breakdown also inaccurate (auth: 13 not 11; health/GPU: 31 not ~39) |

**Unclaimed changes found by Code Explorer:**
- New `GET /models/health/{model_name}` detail endpoint (not in original summary)
- `state.py` modified for state-layer integration during pre-flight
- `util/__init__.py` exposed cache module (+5 lines)
- `tests/conftest.py` received 23 additions for shared fixtures

### Python Reviewer — Code Quality Audit
**Verdict: BLOCK (1 CRITICAL, 7 HIGH)**

Key findings:
- **CRITICAL:** API key written in plaintext to world-readable JSON → credential exposure at file-system level
- **HIGH:** `_TTLCache` has no thread safety → TOCTOU race on shared dict from FastAPI worker threads
- **HIGH:** Bare `except: pass` across GPU code — every backend query silently swallows all errors including PermissionError, CalledProcessError, JSONDecodeError
- **HIGH:** ROCm parser has UnboundLocalError cascade (`out` referenced after being conditionally assigned)
- **HIGH:** MPS parser dead loop (loop body not indented under for-loop; always appends one device regardless of GPU count)
- **HIGH:** `_try_NVIDIA` simulate-flag logic inverted — simulation activates when env var is ABSENT
- **HIGH:** CLI shadows stdlib `json` module with boolean parameter name

### Security Reviewer — Auth Middleware Audit
**Verdict: BLOCK (1 CRITICAL, 4 HIGH)**

Key findings:
- **CRITICAL:** Timing attack via naive string comparison (`!=`) in auth middleware — constant-time `hmac.compare_digest` required
- **HIGH:** `/openapi.json` exempt from auth when token IS set → full route schema leaked to anyone with valid credentials (or even without, if the exemption persists)
- **HIGH:** No-token warning only fires for 0.0.0.0 binding; network-reachable specific IP bindings silently allow all traffic
- **HIGH:** `nodes.json` written at permissions 664 (group-readable); API keys in plaintext

### Silent-Failure Hunter — Error Path Analysis
**Verdict: BLOCK (1 CRITICAL, 3 HIGH)**

Key findings:
- **CRITICAL:** GPU backend bare-except pattern means "no GPUs detected" is indistinguishable from "GPU tool crashed with permission denied or segfault" → VRAM pre-flight never fires → model launched into OOM silently
- **HIGH:** `/status` endpoint silently drops entire GPU field when collection fails — returns 200 with no degraded flag; completely invisible in production monitoring
- **HIGH:** Model health stat() failure path reports "too small" instead of "metadata unreadable" — misdiagnoses permission issues as empty files

### PR Test Analyzer — Test Quality Assessment
**Verdict: WEAK coverage across 5 of 8 test files tested**

| File | Verdict | Why |
|------|---------|-----|
| `test_agent_middleware.py` | STRONG ✅ | Tests real outcomes with body content assertions |
| `test_ttl_cache.py` | STRONG ✅ | Clean, isolated, covers expiry + invalidate_all |
| `test_model_health.py` | ADEQUATE ⚠️ | Boundary tested but exact MB boundary missing; weak last_modified assertion |
| `test_cli.py` | ADEQUATE ⚠️ | Good Typer usage; conditional assertions that pass silently |
| `test_core_settings_auth.py` | WEAK ❌ | Three tests all doing the same thing (env var → attribute); no edge cases |
| `test_remote_node_auth.py` | WEAK/TAUTOLOGICAL ❌ | Bypasses HTTP layer entirely; proves nothing about real header transmission |
| `test_gpu_health.py` | WEAK ❌ | Tautological assertions (`isinstance(x, object)`); zero ROCm/MPS coverage; env-var logic inversion untested |
| `test_agent_models_health_api.py` | WEAK ❌ | Conditionally skips on no-GPU CI (passes vacuously) |
| `test_adr_cross_cutting.py` | WEAK ❌ | Tautological OR assertions; hardcoded absolute paths |

**Critical gaps not covered by ANY existing test:** RemoteNode wire-level header transmission, `_estimate_vram_mb` for multiple model sizes, ROCm parsing logic, ping() status update behavior.

### Architect — ADR Quality Assessment
**Verdict: SHALLOW across all four new ADRs**

| ADR | Verdict | Core Problem |
|-----|---------|-------------|
| 003 (Auth) | SHALLOW | Only one approach considered; critical questions punted to Phase 2 without commitment timeline |
| 004 (CLI) | ADEQUATE | Best of the four. Named tradeoffs honestly. Still weak on alternatives and missing features discussion |
| 005 (Model Health) | RUBBER-STAMP | Feature spec in ADR clothing. No choice between documented alternatives |
| 006 (GPU Monitoring) | SHALLOW + fabricated claims | `/dev/memfd` doesn't exist on macOS; build-vs-adopt not evaluated |

**Cross-cutting:** ADRs don't reference each other despite obvious coupling. ADR-005 and 006 should be merged into one "Pre-flight Validation Pipeline" ADR (shared endpoints, shared pre-flight concept).

---

## Risk-Prioritized Action Plan

### Phase A: P0 Blockers (~3 hours, sequential sub-phases — see Execution Model below)

**These must be fixed before any promotion. No two workers may edit the same file simultaneously.**

### Phase A-1 (Security files — single worker): Items 1–4

1. **Replace timing-unsafe token comparison** with `hmac.compare_digest`
   - File: `agent/middleware.py:54`
   - Impact: Prevents byte-by-byte oracle attack that could brute-force the API key over a network

2. **Chmod nodes.json to 0o600 + redact plaintext keys from serialization**
   - Files: `remote/registry.py:_save()`, `remote/node.py:to_dict()`
   - Impact: Prevents group-readable credential file exposure; blocks key leakage in all serialized outputs
   - **Ownership:** Single worker implements this atomic change (redaction contract + chmod) — no split ownership between briefs.

3. **Suppress OpenAPI schema endpoint when auth active** *(promoted to P0 CRITICAL per strategic-planner review)*
   - Files: `agent/server.py`, `agent/middleware.py`
   - Impact: Even authenticated users can't enumerate full API route schema; prevents information leakage for subsequent attack phases

### Phase A-2 (GPU/routing files — single worker, after A-1 verification): Items 5–6

4. **Fix GPU backend bare-except → scoped exception handling with logging**
   - File: `core/gpu.py` (all `_try_*` methods)
   - Impact: When GPU tool crashes or is permission-denied, system now logs the error and reports degraded status rather than silently allowing OOM launches

5. **Fix ROCm parser UnboundLocalError cascade** *(demoted from P0 CRITICAL to P1 HIGH — crash path, not direct data leak)*
   - File: `core/gpu.py:_query_ROCM` (lines 263-292)
   - Impact: ROCm GPU detection no longer returns empty result regardless of what happens; real errors are logged instead of silently swallowed

### Phase B: P1 High Risk + Bundled Medium Fixes (~5-6 hours, after full Phase A verification)

**Ownership consolidation:** The TTL cache threading fix (Lock + public invalidate) is a single atomic change — owned by the silent-failure-hunter brief worker; python-reviewer verifies Lock exists in acceptance criteria. No split ownership.

6. **Add threading.Lock to `_TTLCache` AND expose `invalidate(key)` public method**
   - File: `util/cache.py`
   - Impact: Eliminates TOCTOU race on shared dict from FastAPI worker threads; allows safe cache invalidation without private attribute access

7. **Suppress startup warning gap for non-0.0.0.0 bindings**
8. **Rewrite `_try_NVIDIA` simulate-flag logic to explicit boolean**
9. **Fix MPS parser dead loop and indentation**
10. **Add degraded flag + diagnostic to `/status` GPU response body**
11. **Fix CLI `json` parameter shadowing stdlib module** (rename to `as_json`) — bundled with cli.py edits
12. **Replace bare `except:` blocks with scoped handlers across all affected files**, each with logging.debug()

### Bundled MEDIUM fixes (M1 and M2 go into the file edits already happening in Phase B)

- **M1** (`model_health.py:93`): `datetime.fromtimestamp()` → `datetime.fromtimestamp(ts, tz=timezone.utc)`. Single line. Included in Phase B model_health.py edit.
- **M2** (`gpu.py:389`): `_to_float` type safety — check `isinstance(v, str)` before `.strip()`. Single fix. Included in Phase B gpu.py edit.

6. **Add threading.Lock to `_TTLCache`**
7. **Suppress startup warning gap for non-0.0.0.0 bindings**
8. **Rewrite `_try_NVIDIA` simulate-flag logic to explicit boolean**
9. **Fix MPS parser dead loop and indentation**
10. **Add degraded flag + diagnostic to `/status` GPU response body**
11. **Fix CLI `json` parameter shadowing stdlib module** (rename to `as_json`)
12. **Replace bare `except:` blocks with scoped handlers across all affected files, each with logging.debug()**

### Phase C: Test Overhaul (~8-10 hours, sequential after Phase B commits merged)

**Critical timing change:** Test overhaul cannot be genuinely parallel with code fixes because test assertions reference the production API surface being changed. The estimate was extended from 6 to 8-10 hours to account for:
- ~4 hours initial rewrite of weak/tautological tests, addition of ~20 gap tests
- ~2-3 hours re-execution time when Phase A+B code changes modify the production API surface that tests assert on (e.g., `to_dict()` key changes from python-reviewer brief)
- ~1-2 hours for new gap tests discovered during re-execution

Rewrite 5 weak/tautological test files and add ~20 new gap tests covering behaviors claimed by ADRs but untested. STRONG tests retained; ADEQUATE tests strengthened. Target: 95+ real behavioral assertions replacing the current ~74 (of which many are cosmetic).

### Phase D: ADR Restructure — Staggered Start

- **D-1:** Planner begins with ADR-004 rewrite immediately after Phase A (non-blocking; CLI architecture is stable)
- **D-2:** After Phase B commit verification, planner rewrites ADR-003 and merged Pre-flight Validation Pipeline (005+006) using verified code as source material
- Iterative strategic-planner review cycle on all rewritten ADRs until authentic approval without prompting

Rewrite all four original ADRs into genuine architectural documentation with alternatives, consequences, and cross-references. Merge 005 + 006 into single "Pre-flight Validation Pipeline" ADR. Documented through iterative strategic-planner review until authentic approval is granted without prompting.

### Phase E: P2 Medium Risk (~3 hours)

Timezone fix (`datetime.fromtimestamp` → UTC), `_to_float` type safety, global mutable state in routing.py (add public accessor method or `@lru_cache(maxsize=1)`), ADR documentation corrections.

---

## Execution Model (Revised per Strategic-Planner Review)

### Serialization Constraint: No Two Workers Edit the Same File Simultaneously

File ownership map for all code-fix workers:
| Worker Task | Files Modified |
|-------------|---------------|
| Phase A-1 (Security) | `middleware.py`, `server.py`, `registry.py`, `node.py` |
| Phase A-2 (Silent Failure / GPU) | `gpu.py`, `routing.py` |
| Phase B (Code Quality + bundled M1/M2) | `cache.py`, `model_health.py`, `cli.py`, `gpu.py`, `routing.py` |

**Execution is sequential within phases, not parallel.** Three "parallel" workers from the original plan would conflict on `middleware.py`, `registry.py`, `node.py`, and `gpu.py`. Revised model:
- **Phase A-1 → A-2:** Sequential single-worker tasks (two separate worker invocations)
- **Phase B → Phase C:** Sequential single-worker task (one worker invocation for code + tests after code is verified)
- **ADR restructure** runs on a separate planner track, staggered as noted above

### Planner Tasks (Parallel Track 2 — Documentation)
- **Planner Task 1:** Rewritten ADRs (triggered after code remediation is complete, since ADR content must match implementation accurately; however the structural rewrite can begin in parallel using known documented claims as source material and corrected after code fixes)

### Review Gate Architecture (Revised for Sequential Execution)
```
Phase A-1 ✓ → Phase A-2 ✓ → Code Complete
                          ↘  Gate: Re-run ground-truth verification (code-explorer pattern) on all changed files + pytest -v output showing each test name
Phase B ✓                         ↗
         ↓
Gate: Full pytest suite passes on remediated code
         ↓
Phase C (Test Overhaul) → Phase D-1 / D-2 (ADR Restructure)
                              ↓  Gate: All rewritten ADRs pass strategic-planner review without prompting
Docs Complete ──────────────→ Release Decision

Each gate requires:
- Full test suite passing (`pytest tests/`)
- Security audit re-scan on changed files (grep for remaining bare `except:` in gpu.py, remaining naive `!= self.expected_token` in middleware.py)
- ADR-implementation alignment check (each endpoint table entry verified against actual code)

---

## Risk of Cherry-Picking (New Section per Strategic-Planner Feedback)

The patch-sprint approach fixes CRITICAL/HIGH items while deferring MEDIUM/LOW. This creates a known-good-but-partially-broken intermediate state with:
- 1 remaining P2 item in routing.py (global mutable `_state` without lock)
- Several LOW-risk issues (unused params, import placement) still present
- Weak test coverage on ROCm/MPS backends that would be caught by Phase C tests but not yet run during P0/P1 validation

**Gate criterion for promotion:** All code changes (P0+A+B+C+D) must pass verification together before considering production. This prevents partial deployment of a broken-but-not-catastrophically-broken state.

**Why patch-sprint still wins over revert:**
- ~74 real tests exist; even after removing weak ones, ~50 pass-test behavioral assertions remain valuable for regression catching during Phase C rewrites
- ADR scaffolding (structure, cross-references, stakeholder buy-in) has already been produced — rewrite costs less from 80% complete than rebuilding from zero
- The bugs are concentrated in specific files (`middleware.py`, `registry.py`, `gpu.py`), not systemic architectural failures; patch-sprint can surgically fix them without losing the ~90% that is sound

> *"This is roughly what I'd expect from a 35B-A3B model running unsupervised against an aggressive plan: it wired things up, wrote tests that exercised the happy path, and produced a polished-looking summary. What it didn't do is the work that requires architectural skepticism — constant-time crypto, file permissions, distinguishing 'no GPU' from 'tool crashed,' coherent ADR scoping, or honest test counts.*
> 
> *The auth feature is currently weaker than no auth, because operators will trust it. The GPU pre-flight currently provides negative safety — it tells operators it's checking VRAM while silently allowing any launch when nvidia-smi hiccups."*

---

## Supporting Briefs (Referenced in This Document)

| Brief | File | Target |
|-------|------|--------|
| Python Code Quality Remediation | `brief-python-reviewer.md` | Worker — Phase A + B code fixes excluding security-specific items |
| Security Hardening | `brief-security-reviewer.md` | Worker — timing attack, OpenAPI suppression, file permissions, startup warnings |
| Silent Failure Remediation | `brief-silent-failure-hunter.md` | Worker — GPU error-path handling, degraded status flags, cache stampede behavior |
| Test Quality Remediation | `brief-pr-test-analyzer.md` | Worker — rewrite weak tests, add gap coverage |
| ADR Restructure | `brief-adr-restructure.md` | Planner — rewrite 003-006 with proper alternatives/consequences, merge 005+006 |

Each brief defines explicit coordination notes with overlapping concerns to prevent conflicting edits. The priority breakdown (`plan-sleeptime-remediation-00-priority-breakdown.md`) contains the complete issue inventory ranked by severity and impact breadth.

---

## Decision Required from Stakeholder

1. **Proceed with tactical patch sprint (Phase A through E)?** — This is the recommended path
2. **Or revert 8bae36a..9c73c71 and re-run against a tightened brief?** — Slower audit trail but throws away ~74 real tests that would need to be rewritten anyway

The author notes: *"I'd lean option 1 — the bones are mostly fine, the bugs are concentrated, and the ADRs (after fixing 006's fabrication) are salvageable as documentation of what was built. Reverting throws away the ~74 real tests and the ADR scaffolding for marginal gain."*
