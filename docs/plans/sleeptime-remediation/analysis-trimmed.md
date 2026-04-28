# Verdict: Plan 02 has the better execution model, Plan 01 has the better artifacts

Take Plan 02's skeleton, port Plan 01's exit-gate commands and Decision Log, and patch the gaps neither plan addresses.

---

## Where the Two Plans Disagree

**Dimension: Worker parallelism**
- Plan 01: 3 workers, ~17-18h critical path (claims Phase C worker A edits gpu.py while worker B runs Phase G3 GPU tests "after gpu.py changes" — this is serialization disguised as parallelism)
- Plan 02: Sequential within phases, ~22-27h. Both plans want to touch gpu.py from multiple angles; Plan 01 puts python-reviewer fixes (shutil.which, _to_float) in Phase C while silent-failure-hunter fixes (bare except, ROCm restructure) are also in Phase C — two workers in the same file at the same time.
- **Who's right: Plan 02.** If Plan 01 were run as written, you'd hit Plan 02's wall-clock anyway, just with worse coordination.

**Dimension: Test timing**
- Plan 01: Phase G runs parallel with code (G can start after Phase C foundation)
- Plan 02: Phase C tests run sequential AFTER all code lands
- **Who's right: Plan 02.** Test assertions reference the production API surface being modified (to_dict() key change, --json → --as-json flag rename, new _degradation_reason field). Tests cannot be authored in parallel with the API changes they assert against.

**Dimension: MEDIUM bundling**
- Plan 01: Phase E "MEDIUM Risk" as standalone
- Plan 02: M1 (timezone) + M2 (_to_float) bundled into Phase B file edits — both are inside files Phase B already opens (model_health.py, gpu.py). A standalone Phase E creates a second commit on the same files for cosmetic gain.
- **Who's right: Plan 02.**

**Dimension: Conflict resolution**
- Plan 01: Explicit Decision Log (D1-D6) with rationale tables
- Plan 02: Implicit — sidesteps via single-owner serialization
- **Who's right: Plan 01.** Future maintainers will want to know why to_dict() returns has_api_key boolean instead of "***". Plan 02 made the call without recording the trade.

**Dimension: Exit gates per phase**
- Plan 01: Explicit grep/pytest commands per phase
- Plan 02: Verbal description of what to verify
- **Who's right: Plan 01.** Verifiable gates beat "verify it works." (Though the commands need regeneration — see Bugs below.)

---

## Bugs in the Plans Themselves

### Plan 01 (hierarchical-01)

1. **Hallucinated project names throughout** — `llaunchr/remote/node.py`, `launcher/cli.py`, `llaunchr/agent/server.py` appear in rollback commands and exit gates. These use wrong or inconsistent token sequences for the actual project name (`llauncher`). The three variants (llauncher, llaunchr, launcher) are hallucination/inconsistent generation, not typos. Executed verbatim, these commands fail silently or revert nothing.

2. **Fabricated grep pattern in Phase B exit gate:** `grep -A1 "_patch_model_health\|_save\|to_dict" llaunchr/remote/node.py` — `_patch_model_health` does not exist anywhere in this codebase. Looks like a hallucinated symbol, likely pattern-matched from training data rather than grounded in actual source.

3. **Broken artifact reference** — references `docs/plans/sleeptime-remediation/plan-sleeptime-consolidated-checklist-07.md`. Actual location is `docs/plans/sleeptime-remediation-hierarchical-01/plan-sleeptime-consolidated-checklist-07.md` (1,522 lines). Treat as draft until audited separately.

4. **"Plan 03 wins" / "Plan 02 wins" labels** — readable only if you cross-reference to source plans. Should say `security-reviewer wins` / `python-reviewer wins`.

### Plan 02 (hierarchical-02)

1. **Visible duplication in artifacts.** In priority-breakdown.md, the P1 table appears twice (lines 31-41 with bundling notes, then lines 43-51 without). In recommendations.md, Phase B items 6-12 are listed twice (lines 135-145 then again 151-157).

2. **complete-review.md is a transcript dump** — 1,063 lines of pasted tool transcripts. Not a review, adds no information beyond what's already in the recommendations.

3. **"Phase F" referenced inconsistently** — recommendations.md mentions "no standalone Phase E needed" but priority-breakdown.md still references the same items as if Phase E exists.

4. **Brief files were not audited** (~199-279 lines each, ~975 total). If workers will execute against those briefs verbatim, they need their own review pass before dispatch — same skepticism applied to qwen output as was applied to the plans themselves.

---

## Gaps Neither Plan Addresses (Add Before Kickoff)

1. **Backup of `~/.llauncher/nodes.json`** before Phase B Step B2. If that file was written under sudo or by a different user, the new chmod(0o600) could lock the operator out. Both plans assume chmod is safe; neither calls for `cp ~/.llauncher/nodes.json ~/.llauncher/nodes.json.bak.$(date +%s)` as a pre-step.

2. **ADT-005/ADR-006 deprecation policy.** Plan 02 says "merge into single Pre-flight Validation Pipeline ADR" but doesn't specify: deprecate-in-place with a "Superseded by ADR-007" header? Delete? Rename? An ADR that disappears violates the immutability principle.

3. **What replaces /dev/memfd.** Both plans say "remove the fabrication." Neither specifies what the actual macOS GPU memory query is. The real answer is `system_profiler SPDisplaysDataType` (already what `_query_MPS` calls — correct in code, wrong only in ADR). Make this explicit so a rewriter doesn't invent a second fabrication.

4. **Full ADR-006 fact-check.** /dev/memfd was the only fabrication caught on an obvious red flag. The architect agent didn't get to fact-check the rest (per-process VRAM attribution claims, ROCm SMI output format claims). Do a pass before rewriting.

5. **Test count correction.** Original commit summary claimed 83 tests; reality is 74. Neither plan addresses whether commit messages should be amended (probably no — git history is git history) or whether a CHANGELOG/release notes correction is owed if those numbers were communicated externally.

6. **Other potential fabrications.** /dev/memfd was the only one caught but it may not be the only one. Both plans treat GPU code as "buggy but functional." If the model fabricated an OS API in the ADR, it could have fabricated function signatures, return shapes, or error codes in the implementation. Do a one-pass audit of `core/gpu.py` against actual nvidia-smi, rocm-smi, and system_profiler documentation before signing off.

7. **No CI gate.** Plan 01 says "no automated PR block yet — add CI gates after first full cycle." Plan 02 doesn't mention CI at all. The patterns being fixed (bare except, naive == on tokens, hardcoded paths in tests) should become lint rules so the next overnight model can't reintroduce them. Add a `.pre-commit-config.yaml` or ruff/bandit ruleset addition to this remediation cycle rather than deferring.

---

## Recommended Synthesis: Build plan-03-execution.md

| Source | Use |
|--------|-----|
| Plan 02 recommendations | Phase structure (A-1 → A-2 → B → C → D-1/D-2), serialization constraint, bundling decisions |
| Plan 01 Decision Log (D1-D6) | Documented rationale — drop into Plan 02 body |
| Plan 01 Phase Exit Gate commands | Drop into Plan 02 phases — **regenerate against real file tree** (fix project name hallucination: s/llaunchr/llauncher/g is necessary but not sufficient; verify every symbol referenced actually exists) |
| Plan 01 consolidated checklist (1,522 lines) | Audit in separate pass before workers consume it; treat as draft |
| Plan 02 brief files (~975 lines total) | Audit before worker dispatch — same skepticism applied to qwen output |
| New additions | nodes.json backup step; ADR-005/006 deprecation policy; macOS replacement text for /dev/memfd; full ADR-006 fact-check; pre-commit ruleset for the patterns that failed us |

---


