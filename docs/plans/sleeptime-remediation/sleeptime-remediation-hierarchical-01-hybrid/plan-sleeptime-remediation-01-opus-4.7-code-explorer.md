# Sleeptime Remediation Plan 01 — Opus 4.7 Code Explorer

**Delivery Range:** `a4d0361..9c73c71` (overnight Orchestration: ADRs 003–006)  
**Verification Source:** Opus 4.7 deep code-explorer verification on origin/main  
**Report Date:** 2025-04-XX  
**Plan Classification:** Internal — remediation hygiene, not a blame exercise  

---

## Executive Summary

The overnight delivery shipped quality code implementing ADRs 003–006 (Authentication, CLI Interface, Model Cache Health Validation, GPU Resource Monitoring). The implementation itself is sound and the architecture decisions are correct. This plan addresses **measurement accuracy** and **documentation hygiene** gaps found during post-delivery verification — specifically four categories of discrepancy:

| # | Issue Category | Severity | Impact |
|---|---------------|----------|--------|
| 1 | Test count overstated (83 claimed, 74 actual — ~11% inflation) | **P0** | Downstream planning and delivery tracking unreliable; test budget misallocated |
| 2 | `_patch_model_health` autouse fixture suppresses real regression risk | **P1** | "Zero regressions" claim depends on test-doubled isolation, not verified stability |
| 3 | Unclaimed behavioral changes (state.py health gate, `/models/health/{model_name}` endpoint) | **P1** | Code changes invisible to future reviewers; undocumented API surface |
| 4 | No verification gate for subagent deliveries | **P2** | Same class of discrepancies likely to repeat on future delivery cycles |

The remediation effort is low-risk and largely documentation/process-focused. Only the conftest fixture evaluation (P1-3) may require test code changes.

---

## Findings by Severity

### P0 — Test Count Correction + Coverage Audit

**Reported:** 83 new tests across 9 files, with breakdown claims of "11 auth", "~39 health/GPU".  
**Actual (verified via grep):** 74 tests. Breakdown: middleware=7, models_health_api=6, gpu_health=8, model_health=8, ttl_cache=9, cli=21, settings_auth=3, remote_node_auth=3, integration=9.

The ~11% gap could be:
- **Counting error** (double-counted parameterized tests or conftest fixtures that generate test cases)
- **Missing coverage** (tests exist in unaccounted files and the sum was wrong, OR tests are genuinely missing for some code paths)

Either way, delivery tracking metrics are inaccurate. This is P0 because downstream planners rely on these numbers for capacity forecasting and regression risk assessment.

### P1 — Conftest Fixture: Regression Masking or Legitimate Isolation?

`tests/conftest.py` added a 23-line `_patch_model_health` fixture (autouse=True) that patches `llauncher.state.check_model_health` to always return valid across all tests. The docstring explains it prevents "small test temp-files from triggering the >1 MB health gate" — but this means every existing state/eviction test that uses a temp model file passes **only because the health check is stubbed**, not because those paths work with real health gates.

The "zero regressions" claim holds true, but only under this fixture injection. Without it, tests would hit real filesystem checks on temp models and likely fail — meaning existing regression baselines were never exercised against the actual ADR-005 gate.

### P1 — Unclaimed Behavioral Changes

Two code changes from the delivery are not documented in any ADR or summary:

**P1-a: state.py pre-flight health gate (line 242)**
```python
# Pre-flight: check model file health (ADR-005)
health = check_model_health(config.model_path)
if not health.valid:
    ...
    return False, f"Model path is invalid ({health.reason}): {config.model_path}", None
```

`start_server()` previously used `Path(config.model_path).exists()`. It now gates on the full `check_model_health()` result — a behavioral change that affects error messages, failure modes, and caller expectations. Not merely an "extension of the routing layer" as the summary implies; server startup itself is now gated behind filesystem health semantics.

**P1-b: `/models/health/{model_name}` endpoint (routing.py line 251)**
```python
@router.get("/models/health/{model_name}")
async def model_health_detail(model_name: str) -> dict:
    """Health status for a single model (ADR-005)."""
    ...
```

A new public API endpoint not mentioned in any ADR, summary, or test count attribution. It is a useful addition — returning per-model health JSON — but represents an undocumented, unclaimed extension of the Agent API surface.

### P2 — No Verification Gate for Future Subagent Deliveries

There was no pre-merge checklist to validate:
- Test counts via `grep` rather than estimation or parameterized counting
- Behavioral changes in non-test files (state.py) were flagged as such
- New API endpoints or interfaces were documented
- "Zero regressions" claims held when fixture injections were temporarily disabled

---

## Remediation Actions

### P0-1: Authoritative Test Count + Gap Analysis

**Action:** Run `grep -c "def test_"` across all files in the delivery range and reconcile against claimed numbers.

```bash
# Step 1: Get authoritative count for each file touched by this delivery
for f in tests/unit/test_agent_middleware.py \
         tests/unit/test_agent_models_health_api.py \
         tests/unit/test_gpu_health.py \
         tests/unit/test_model_health.py \
         tests/unit/test_ttl_cache.py \
         tests/unit/test_cli.py \
         tests/core/test_core_settings_auth.py \
         tests/integration/test_remote_node_auth.py \
         tests/integration/test_adr_cross_cutting.py; do
    count=$(grep -c "def test_" "$f" 2>/dev/null || echo 0)
    printf "%-55s %d\n" "$f" "$count"
done

# Step 2: Compare against claimed numbers and identify the ~11% gap
echo "---"
echo "Total: $(grep -rc "def test_" tests/ | grep -E 'test_agent_middleware|models_health_api|gpu_health|model_health|ttl_cache|test_cli|settings_auth|remote_node_auth|adr_cross_cutting' | awk -F: '{s+=$NF} END{print s}')"
```

**Decision point:** After counting:
- **If count matches 74 exactly:** The gap was a counting error (likely double-counted parameterized tests). Document the discrepancy in commit message or delivery notes, establish `grep -c "def test_"` as canonical counting convention going forward.
- **If some paths lack coverage:** Add missing tests for uncovered paths identified during verification.

### P0-2: Correct Reported Numbers Everywhere

After obtaining the authoritative count, update any artifacts that reference the claimed numbers:
- Session logs / delivery summaries → amend with corrected figures
- Commit messages (if modifiable via reword/fixup) → adjust test count references
- Any ADR deliverable tracking documents in `docs/` → correct

**Rule going forward:** Test counts are derived from grep at PR merge time, not estimated during implementation.

### P1-1: Conftest Fixture Review (`_patch_model_health`)

Evaluate the fixture against two criteria:

**Criterion 1: Is real filesystem testing feasible?**
If tests can create temp model files with valid content (files > 0 bytes, readable), then real `check_model_health()` returns valid and no stubbing is needed. This would make the autouse patch unnecessary.

```python
# Option A: If test fixtures already produce valid temp files...
# Simply remove or comment out the _patch_model_health fixture entirely.
# Regenerate tests; if they pass, the health gate doesn't need masking.
```

**Criterion 2: Is the stubbing for performance/isolation only?**
If real filesystem calls are too slow or would introduce non-deterministic test order dependencies (e.g., temp file cleanup races), keep the patch but change it from autouse to explicit opt-in:

```python
# Option B: Make fixture explicit, not autouse.
@pytest.fixture()  # NOT autouse=True — reviewers see the dependency immediately
def _no_health_check():
    """Opt-in fixture: suppresses check_model_health for tests that 
    create temp files which would fail the >1MB health gate."""
    mock_result = MagicMock(...)
    with patch("llauncher.state.check_model_health", return_value=mock_result):
        yield
```

Tests needing real health checks can call `check_model_health` directly; tests creating valid temp files that pass naturally need no fixture. The autouse behavior must be replaced by explicit test authorship intent.

**Deliverable:** Either remove the fixture (Option A), or convert to opt-in with explanatory docstring + update all affected test files to use it explicitly (Option B). Option B is preferred if any tests rely on it.

### P1-2: Document state.py Health Gate as Behavioral Change

Add a comment/docstring block in `state.py` at the `start_server()` function that explicitly notes the behavioral change from ADR-005:

```python
def start_server(self, model_name: str, caller: str = "unknown", 
                 port: int | None = None) -> tuple[bool, str, subprocess.Popen | None]:
    """Start a server for the given model.

    Behavioral note (ADR-005): Model file health is now validated via
    ``check_model_health()`` before process launch. Previously this was a
    bare ``Path.exists()`` check. The new gate produces richer error messages
    (e.g., "Model path does not exist", "File is empty", "Symlink target 
    missing") and may reject files the old check would have allowed (e.g.,
    very small or non-readable files). Callers should be prepared for a wider
    set of validation_error messages than before ADR-005.
    
    ...existing docstring content...
    """
```

This comment serves two purposes:
1. Future code reviewers understand why `check_model_health()` appears in the startup path
2. Anyone reading git blame or doing manual diff reviews immediately sees this is a behavioral change, not just plumbing

### P1-3: Document Unclaimed API Surface (`/models/health/{model_name}`)

**Preferred approach (ADR supplement):** Write ADR-006b documenting the per-model health detail endpoint as an intentional extension of the Agent API surface from ADR-005. This captures the design decision explicitly.

```
docs/adrs/006-gpu-resource-monitoring-supplement.md
(or 007 if 006 is already taken for a different purpose)

Title: Per-Model Health Detail Endpoint Extension
Related: ADR-003, ADR-005
Date: [delivery date]

## Context
ADR-005 added model health validation to the startup flow. During implementation, 
a natural extension was needed: a per-model health detail endpoint for programmatic 
consumption (dashboard refresh, monitoring probes).

## Decision
Added GET /models/health/{model_name} to routing.py as part of the ADR-005 delivery 
commit batch. This endpoint is documented inline with docstring reference to ADR-005 
and tested in test_agent_models_health_api.py (test_health_detail_* methods).

## Rationale
Provides fine-grained model health data without requiring callers to parse the full 
GET /models/health list response. Enables targeted monitoring probes per-model.

## Trade-offs considered
(a) Add as separate endpoint vs. extending GET /models/health with optional param → chose separate path for clarity and RESTful conventions
(b) Require authentication → inherited from ADR-003 auth middleware policy
```

**Alternative (inline-only):** If writing an ADR supplement feels excessive for one line of new API surface, add a doc comment in routing.py referencing the ADR explicitly:

```python
# NOTE: This endpoint extends ADR-005 model health validation into public API.
# Tested in tests/unit/test_agent_models_health_api.py::TestAgentModelsHealthAPI::test_health_detail_*
@router.get("/models/health/{model_name}")
async def model_health_detail(model_name: str) -> dict:
```

### P2-1: Exposure of `_TTLCache` from `util/__init__.py` (Trivial, P2)

Add a one-line comment explaining the intentional re-export:

```python
"""Utility modules for llauncher."""

# NOTE: _TTLCache is intentionally exposed at package level for access by 
# tests and other subsystems without importing the private cache submodule.
# Leading underscore convention preserved to discourage use in application code.
from llauncher.util.cache import _TTLCache

__all__ = ["_TTLCache"]
```

### P2-2: Verification Gate Checklist (Process Improvement)

Propose a merge-time checklist for future subagent deliveries:

**Subagent Delivery Verification Gate (pre-merge):**

| # | Check | Command / Method | Owner |
|---|-------|------------------|-------|
| 1 | **Test count accuracy** | `grep -c "def test_"` on each new/modified test file; sum must match delivery claim ±0% | Reviewer / Plan author |
| 2 | **Behavioral change log** | Diff non-test files for logic changes (not just plumbing additions); flag any function signature, return value, or error path changes | Code explorer agent |
| 3 | **Unclaimed API surface** | `grep -rn "@router\."` on new/modified routing files; compare against ADR scope | Reviewer |
| 4 | **Regression baseline without fixture masking** | Run tests with `_patch_model_health` (or equivalent autouse fixtures) temporarily disabled; verify existing tests still pass or update them accordingly | Plan author at verification time |
| 5 | **API documentation sync** | New endpoints → ADR supplement or inline doc comment referencing the relevant ADR | Plan author / delivery agent |

This checklist becomes a standard attachment to any multi-file subagent delivery that touches test files, state logic, or routing/API layers.

---

## Implementation Tasks (Prioritized)

### Task Group 0: Test Count + Gap Analysis
| # | File(s) | Action | Est. Effort |
|---|---------|--------|-------------|
| 0-1 | `tests/` tree | Run authoritative `grep -c "def test_"` count; compare to claimed 83 → identify exact gap source | 5 min |
| 0-2 | Any files with uncovered code paths | Add missing tests if gap is real (not just counting error) | TBD (1–4 hrs) |
| 0-3 | Session logs, delivery summaries in `docs/` or session output artifacts | Correct test count from "83" to authoritative number | 5 min |

### Task Group 1: Conftest Fixture Evaluation
| # | File(s) | Action | Est. Effort |
|---|---------|--------|-------------|
| 1-1 | `tests/conftest.py` | Remove or convert `_patch_model_health` from autouse to opt-in (per evaluation in P1-1 above) | 30 min |
| 1-2 | All affected test files | Add explicit `@pytest.mark.usefixtures("_no_health_check")` if fixture converted to opt-in; update docstrings | 15 min |
| 1-3 | Run full test suite after change | Confirm zero regressions with new fixture arrangement | 5 min |

### Task Group 2: Inline Documentation of Behavioral Changes
| # | File(s) | Action | Est. Effort |
|---|---------|--------|-------------|
| 2-1 | `llauncher/state.py` line ~230 | Add behavioral change comment to `start_server()` docstring (P1-2 above) | 10 min |
| 2-2 | `llauncher/agent/routing.py` line ~251 | Add ADR reference doc comment OR write ADR supplement for `/models/health/{model_name}` endpoint | 15–30 min |
| 2-3 | `llauncher/util/__init__.py` | Add explanatory comment for `_TTLCache` re-export (P2-1 above) | 5 min |

### Task Group 3: Process Improvement
| # | File(s) | Action | Est. Effort |
|---|---------|--------|-------------|
| 3-1 | `docs/plans/plan-sleeptime-remediation-01-opus-4.7-code-explorer.md` (this file) | Integrate verification gate checklist as a reusable annex for future deliveries | Copy/paste, finalize formatting |

---

## Verification Gate

After all remediation tasks are complete, verify:

```bash
# 1. Test count matches authoritative number
grep -rc "def test_" tests/unit/test_agent_middleware.py \
          tests/unit/test_agent_models_health_api.py \
          tests/unit/test_gpu_health.py \
          tests/unit/test_model_health.py \
          tests/unit/test_ttl_cache.py \
          tests/unit/test_cli.py \
          tests/core/test_core_settings_auth.py \
          tests/integration/test_remote_node_auth.py \
          tests/integration/test_adr_cross_cutting.py 2>/dev/null

# Expected: per-file counts matching the corrected authoritative number, not "83"

# 2. Full test suite passes (with new fixture arrangement)
python3 -m pytest tests/ -x --tb=short 2>&1 | tail -20

# 3. Behavioral changes are documented
grep -n "ADR-005\|health gate\|behavioral change" llauncher/state.py
grep -rn "ADR-005\|model_health_detail" llauncher/agent/routing.py

# 4. No unclaimed API endpoints (re-scan routing files)
grep -rn "@router\." llauncher/agent/routing.py

# 5. Check that conftest fixture is either removed or explicit (not autouse for health patching)
grep -A2 "_patch_model_health" tests/conftest.py | head -10
```

**Acceptance criteria:**
- [ ] Test count in all artifacts matches `grep` output exactly (±0% tolerance — no more rounding/parameterized inflation)
- [ ] `_patch_model_health` is either removed or explicitly opt-in (not autouse), with updated tests using the fixture where needed
- [ ] Full test suite passes after fixture changes
- [ ] `start_server()` docstring includes behavioral change note referencing ADR-005 health gate
- [ ] `/models/health/{model_name}` endpoint is either documented in an ADR supplement or has inline ADR reference comment
- [ ] `_TTLCache` re-export has explanatory comment
- [ ] Verification gate checklist is finalized as a reusable annex

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fixture change breaks existing tests during P1-1 evaluation | Medium | Low — fixture changes affect only test infrastructure; easy to roll back by restoring autouse behavior | Perform evaluation incrementally: first try removing the patch, then convert to opt-in if needed. Keep git staging area clean for rollback. |
| Adding behavioral change comments creates merge conflicts with concurrent changes to state.py/routing.py | Low | Low — docstring additions at function headers rarely conflict; apply as separate small commits | Stage and commit each inline documentation change independently before merging. |
| New verification gate adds overhead to delivery velocity | Low | Negligible — checklist is 5 items, takes ~10 min to execute via grep/scan commands | The gate runs during review, not during implementation; no added dev time. |
| ADR supplement for `/models/health/{model_name}` scope creep | Low | Medium — avoid writing an overly expansive ADR for a single endpoint; keep it to the 4-section format (Context/Decision/Rationale/Trade-offs) shown above | Keep ADR supplement to <200 words if inline docs prove sufficient. |

---

## Annex: Verification Gate Checklist (Reusable)

*Copy this section into future delivery plans as a pre-merge checklist.*

```
┌─────────────────────────────────────────────────────────┐
│  SUBAGENT DELIVERY VERIFICATION GATE                     │
├──────────┬───────┬──────────────┬──────────────────────┤
│ Check    | Cmd   | Expected     | Status                 │
├──────────┼───────┼──────────────┼──────────────────────┤
│ 1. Test  | grep  | Count matches│ ☐ Pass / ☐ Fail       │
│    count | -c    | claim ±0%    │                      │
├──────────┼───────┼──────────────┼──────────────────────┤
│ 2. Behav-| git   | Logic changes│ ☐ Flagged /          │
│    ional | diff  | documented   │ ☐ Not applicable     │
│    change│       │ as non-      │                      │
├──────────┼───────┼──────────────┼──────────────────────┤
│ 3. Un-   | grep  | No new       │ ☐ Audited /          │
│    claimed| @router│ endpoints  │ ☐ Not applicable     │
│    APIs  │        │ un-          │                      │
├──────────┼───────┼──────────────┼──────────────────────┤
│ 4. Regres-| pytest│ No regressions│☐ Confirmed /         │
│    sion  | (no   │ with fixture │ ☐ Needs investigation│
│    base-│  patch)│ masking      │                      │
│    line  │       │ disabled     │                      │
├──────────┼───────┼──────────────┼──────────────────────┤
│ 5. API   | ADR / │ New endpoints│ ☐ Documented /       │
│    docs  | doc   │ have ADR ref │ ☐ Not applicable     │
│    sync  │ strings│ or inline doc│                      │
└──────────┴───────┴──────────────┴──────────────────────┘

Review sign-off: ___________________ Date: ____________
```
