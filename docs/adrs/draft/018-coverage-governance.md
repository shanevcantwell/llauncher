# ADR-018: Coverage Governance — Exercised Code with an Audited Deferral Ledger

**Status:** Draft
**Date:** 2026-06-13
**Tracking:** Realizes the `CHANGED-PATH-COVERED` invariant named in `docs/conformance.md` (PR #167), anchored to its violation #156. Relates #69 (Streamlit `AppTest` harness). Supersedes the global-floor coverage decision recorded in `docs/plans/test-coverage-plan.md` and the 2026-05-20 coverage handoff (plan docs, not ADRs — no folder move).
**Doctrine:** `design-docs/ecosystem-ground-physics/` — `PARSE-AT-THE-DOOR` / no-trust-and-degrade; "no invariant without a violation."

## Context

Coverage is currently governed by a **global line-coverage floor**: `pytest.ini`
sets `--cov-fail-under=93` over a non-UI source tree, with `pyproject.toml`
`[tool.coverage.run]` omitting `llauncher/ui/*` and `llauncher/agent/__main__.py`
by wildcard. `branch = false`. There are **zero `# pragma: no cover`** annotations
in the source. There is no CI; the floor is enforced only when an operator runs
`pytest` locally.

This shape has three defects, all instances of the same anti-pattern the
ecosystem constitution rejects on the data plane (`PARSE-AT-THE-DOOR`,
no-trust-and-degrade), here hiding in the test configuration:

1. **A global average is a trust-and-degrade device.** It absorbs the uncovered
   set into a single number so that no individual path is ever named or
   justified. The `93` floor itself bakes in a historical snapshot — the
   non-UI baseline at one past moment of analysis — that no longer has to be
   re-defended as code changes.
2. **`omit` globs make whole subtrees invisible.** `llauncher/ui/*` is not
   *waived with a reason a tool reads* — it is removed from the denominator. The
   judgment ("UI is deferred to #69") lives in a comment nothing enforces.
3. **The average governs the whole, not the delta.** A change can add 50 lines,
   cover 20, and pass while the non-UI average stays above 93. New behavior can
   ship untested while the gate reads green. This is exactly the
   `CHANGED-PATH-COVERED` gap recorded in `docs/conformance.md` as
   named-but-unenforced, anchored to #156.

### Design constraints

1. **No trust-and-degrade.** Every uncovered path is an explicit, justified,
   reviewable decision — never an emergent residue under an average. This
   mirrors the data-plane `PARSE-AT-THE-DOOR` posture in test space.
2. **Deferral is fail-loud and self-graduating.** A path whose verification is
   deferred must force its own promotion when it becomes verifiable, rather
   than rotting silently into a permanent exemption.
3. **Distinguish deferred _execution_ from deferred _verification_.** Deferring
   execution (`omit`, `skip`, `# pragma: no cover`) makes a path invisible —
   it leaves the denominator. Deferring only the _verdict_ (`xfail`) keeps the
   path executed and counted; just the assertion is allowed to fail. The second
   is honest; the first hides the path.
4. **Migration is stageable**, not big-bang.

## Decision

Replace the global-floor model with **"coverage is 100% of exercised code, and
every gap is an explicit, justified entry in a deferral ledger."**

### 1. Measure paths — `pyproject.toml [tool.coverage.run]`

- `branch = true`. Line coverage cannot express "code path"; branch coverage is
  the closest coverage.py gets, and is the precondition for path-level
  governance.

### 2. Floor at 100% of the exercised set — `pytest.ini`

- `--cov-fail-under=100`. The semantics flip from "the average is high enough"
  to "everything executed is covered _except what is explicitly declared not to
  be_." There is no average left for a new uncovered line to hide behind.

### 3. Two deferral registers, both explicit

- **Structural / genuinely unmeasurable** → `[tool.coverage.report]`
  `exclude_also` regex: `if TYPE_CHECKING:`, `@(abc\.)?abstractmethod`,
  defensive `raise NotImplementedError`, the `if __name__ == .__main__.:`
  guard. Categorical, config-level, reviewed as a set.
- **Deferred verification** → `@pytest.mark.xfail(strict=True, reason="…; #NNN")`.
  The test runs, so the code path **executes and is counted**; only the verdict
  is deferred. `strict=True` means that when the path begins to pass, pytest
  reports `XPASS` and **fails the suite** — forcing the marker to be promoted to
  a real assertion. The ledger physically cannot rot (constraint 2).

### 4. Delete the `omit` globs; exercise the UI

- Remove `llauncher/ui/*` and `llauncher/agent/__main__.py` from `omit`.
- Drive `ui/*` through **`streamlit.testing.v1.AppTest`** — in-process, headless:
  set widget values, click buttons, trigger reruns, and assert on
  `session_state` and on the data behind `st.dataframe`/`st.table`. The UI is
  basic start/stop controls plus simple CRUD tables; `AppTest` exercises all of
  it, so those lines enter the covered set. Where `AppTest` cannot yet verify a
  path, mark it `xfail(strict=True)` **transiently during migration** — the UI
  is not a standing deferral register.

### 5. Reason-required deferral — small enforcement check

- A bare `# pragma: no cover` or a bare `xfail` (no `reason` + issue reference)
  is rejected by a lightweight check (grep-gate / pre-commit hook). The deferral
  ledger is only governance if every entry carries its justification — the
  test-layer twin of "no invariant without a violation": no gap without a
  named cause.

### 6. Browser-render tooling out of scope

- Playwright/puppeteer are **explicitly out of scope**. The UI has no
  browser-render or JS-interaction complexity for them to reach that `AppTest`
  cannot. Pixel/CSS rendering is not uncovered _Python_ — it never enters the
  coverage denominator — so it is not a coverage gap to be waived. Revisit only
  if visual-regression testing ever earns its keep.

### Relationship to `CHANGED-PATH-COVERED`

At `--cov-fail-under=100` there is no average to dilute, so **changed paths are
covered-or-explicitly-waived by construction** — patch coverage becomes nearly
intrinsic rather than a separate `diff-cover` gate. This is the structural
enforcement surface the `CHANGED-PATH-COVERED` invariant lacked in
`docs/conformance.md`.

## Consequences

**Positive**

- The uncovered set becomes an **auditable ledger** — a reviewable diff of
  structural exclusions plus reason-bearing `xfail`s — not a smear under a
  number.
- New code is covered-or-waived at merge by construction; the dilution failure
  mode (constraint-3 / #156) is closed without a separate patch-coverage tool.
- Deferred verification is **self-graduating** via strict `XPASS`.
- The UI stops being invisible; `#69`'s harness work is now expressed as
  coverage moving, not as a glob being eventually deleted.

**Costs / honest limits**

- **One-time migration labor.** Turning on `branch = true` will *surface new
  uncovered branches* and the measured number will drop before it is brought to
  100. Reaching 100 means walking the current gap and, per path, either covering
  it or writing a justified deferral.
- **The reason-required check is custom** — coverage.py does not enforce
  justification on `# pragma: no cover`, and pytest does not require a `reason`
  on `xfail`. ~30 lines to build (§4 of the staging below).
- **Still needs CI to be a true _merge_ gate.** The no-CI finding from
  `docs/conformance.md` stands; this model is enforceable locally and in a
  pre-commit hook today, but standing up CI to gate merges is a separate,
  operator-coordinated precondition and is out of scope for this ADR.

## Deferred Work — migration staging

1. `branch = true`; enumerate the real gap (now including branches). Floor
   unchanged at this step.
2. Build the `AppTest` harness for `ui/*` module by module; remove each `omit`
   glob as its module becomes exercised.
3. Annotate the residual gap: structural → `exclude_also`; deferred-verification
   → `xfail(strict=True, reason="…; #NNN")`.
4. Add the reason-required enforcement check (pre-commit / grep-gate).
5. Flip `--cov-fail-under=100` last, once the ledger is complete.
6. (Separate track, operator-coordinated) stand up CI so the floor and the
   deferral ledger gate merges, not just local runs.

## Supersession

Supersedes the **global-floor coverage decision** recorded in
`docs/plans/test-coverage-plan.md` and the 2026-05-20 coverage handoff. Those
are plan/handoff documents rather than ADRs, so there is no ADR folder move; the
`--cov-fail-under=93` value and the non-UI `omit` carve-out are replaced by this
ADR's model on ratification and Phase 5.
