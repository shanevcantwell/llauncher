# Session handoff — Phase D coverage + non-UI floor (2026-05-20)

Companion to `test-coverage-plan.md` and `docs/handoffs/2026-05-test-coverage-and-security.md` (prior session). Pointers to source files inline; artifact contents not pasted here.

## 1. Where we are

Branch `main` is at `f6a94a8` (post-#89 quick-win deck). PR **#90** open against `main`: `test: Phase D coverage + non-UI --cov-fail-under=93 floor` — branch `phase-d-coverage-floor`, commit `c808f8d`. Pending review/merge.

v2 refactor milestone state unchanged from prior dossier: M1–M4 complete; M5 has one item left (#56 canonical self-swap integration test, ADR-016); M6/M7 not started.

**Network-trust posture:** loopback + auth-on (default since #75, `ec98026`). 10 security follow-ups (#78–#88) remain open and unworked except the quick-win deck cleared in #89.

**Coverage posture (post-#90, pending merge):** non-UI **95%** (2981 stmts, 151 missed), enforced by `--cov-fail-under=93` in `pytest.ini` `addopts`. Suite at 935 passed / 10 skipped. UI tabs (`llauncher/ui/*`) and `agent/__main__.py` are omitted from measurement in `pyproject.toml` `[tool.coverage.run]`; re-baseline when #69 lands.

## 2. What landed this session

| Ref | Type | Impact (one line) |
|---|---|---|
| PR #90 (open) | tests + tooling + docs | `tests/unit/test_phase_d_coverage.py` (32 tests) + `[tool.coverage]` config in `pyproject.toml` + `--cov-fail-under=93` in `pytest.ini` + `test-coverage-plan.md` Phase D / Floor sections annotated landed. Non-UI 92% → 95%. |

Durable plan artifacts updated: `test-coverage-plan.md` §"Phase D" and §"CI Coverage Floor" both now annotated with landed status, the 95%/93% numbers, and residual deferred candidates.

## 3. Coverage baseline (post-#90, non-UI scope)

Methodology: `pytest` (config-driven, no flags) — coverage runs automatically per `pytest.ini` `addopts`, scope set by `pyproject.toml` `[tool.coverage.run]`.

**Non-UI total: 95%** (2981 stmts, 151 missed). Pre-session non-UI was 92% (229 missed).

Top remaining non-UI gaps (by missed-line count):

| Module | Stmts | Miss | Cover | Notable missing |
|---|---:|---:|---:|---|
| `llauncher/state.py` | 233 | 25 | 89% | 216, 225, 249, 300-308, 329, 399, 546-548, 563-598 (readiness-error rollback path inside swap — substantial setup, deferred) |
| `llauncher/cli.py` | 213 | 28 | 87% | various format-output and argparse branches not yet hit |
| `llauncher/core/gpu.py` | 260 | 32 | 88% | 121-122, 124-125 (non-primary-backend success paths), 344-365 (MPS per-line regex never matches single-line, only block-fallback hits) |
| `llauncher/core/process.py` | 255 | 12 | 95% | 60, 65, 243-244, 325, 406-407, 482, 533-536 (terminal process states) |
| `llauncher/remote/state.py` | 85 | 9 | 89% | 112, 151-155, 163-167 |
| `llauncher/remote/node.py` | 220 | 9 | 96% | 292-322 (non-self-loop heartbeat / error translation) |

UI (`ui/tabs/forms.py` 5%, `nodes.py` 5%, `model_registry.py` 8%, `model_card.py` 45%) — still omitted from measurement; unlocked by #69's Streamlit `AppTest` harness.

## 4. Open spine — work queued for next session

Threads from the prior dossier that remain live:

### Small security wins (independent small PRs)
- **#87** — `create_app(auth_token=None)` back-compat fallback tightening. Filed by independent review of #75.
- **#83** — chmod 0600 on `~/.llauncher/nodes.json` (C10).
- **#78** — cap agent HTTP request body size at 1 MiB (C3).
- **#79** — regression test asserting no CORS headers on agent responses (C4).
- **#84** — audit `unsafe_allow_html` and add escaping regression test (C11).

### Medium security
- **#81** — validate `extra_args` against `llama-server` flag deny-list (C7).
- **#82** — optional `LAUNCHER_MODELS_ROOT` enforcement for `model_path` (C8).

### Correctness
- **#88** — `ModelConfig._skip_path_validation` silently no-ops on fresh process; class-level mutation is racy. Real bug, bounded scope, likely one PR with regression test.

### Larger scoping
- **#86** — TLS/mTLS story for cross-host remote nodes (C9). Design-doc shape, not a code PR.

### M5 close
- **#56** — canonical self-swap integration test (ADR-016, still unwritten). Phase C harness (`tests/integration/conftest.py` + stub llama-server) is the substrate. Closes M5 and unblocks M7 (v2.0.0 tag).

### UI / coverage continuation
- **#69** — Streamlit `AppTest` harness. When this lands, drop the `llauncher/ui/*` omit from `[tool.coverage.run]` and re-baseline the floor against the combined measurement.
- **Phase D residual** — `state.py:563-598` (readiness-error rollback inside swap), `core/process.py` terminal states, `core/lockfile.py:104-111`. None blocking; pick up incrementally when touching those modules.

### M6 / M7
- **#42** — multi-backend vLLM adapter. Blocked on M5 closure.
- **M7** — v2.0.0 tag. Blocked on M5 + outstanding security backlog (at minimum #87/#88).

## 5. Workflow observations

- **Per-question framing pushback** — user rejected the initial "aggregate 80% floor" framing and named the actual shape (UI concentrated in the missing 20%). The right move was to reframe the option (non-UI-scoped floor) rather than absorb the rejection silently. Cheap signal to honor.
- **Floor selection at +2pt headroom** — set the floor at 93% against a measured 95% rather than at the measurement itself. Justification: any PR that legitimately touches uncovered code (e.g. a new error-path branch) shouldn't be blocked by the floor; the floor's job is to catch *regression*, not to chase the measured number. Ratchet up when measurement climbs sustainably.
- **`coverage` config in `pyproject.toml` vs `.coveragerc`** — chose `pyproject.toml` because the project already uses it for build + project metadata and the test team is the same as the build team. No separate `.coveragerc` to drift.
- **`addopts` with `--cov` on every run** — adds ~1s to the 21s suite. Acceptable; the alternative (opt-in) defeats the floor.
- **TaskCreate reminders** — fired multiple times; ignored per the constitution's Provisional Handling (durable record satisfies them). No in-window task list was assembled or needed.

## 6. Suggested first move for next session

Two equally-good entry points; depends on appetite:

**(a) #88 ModelConfig race fix** — bounded, real correctness bug, one PR with a regression test. Knocks out the only `bug:` (not `security:`) item on the recent docket.

**(b) Small-security batch** — #87 → #83 → #78 → #79 as sequential small PRs. Each is well-scoped and chips down the post-#75 follow-up list. #87 was filed by the independent review of #75 and is the most pointed.

The larger threads (**#56 M5 close**, **#69 UI harness**, **#86 TLS scoping**) remain queued. M6 / M7 still wait on M5 closure.

If PR #90 needs follow-up before merge, address review there first.

## 7. Closeout — plan reorg + #90 merge + #88(a) (appended 2026-05-20, later that day)

Three changes landed in the same session that this dossier opened:

### Plan-file reorganization
Plan files now live as flat siblings under `docs/plans/`:
- `docs/plans/v2-implementation-roadmap.md` (moved from `docs/`)
- `docs/plans/test-coverage-plan.md` (moved from repo root)
- `docs/plans/security-hardening-plan.md` (already here)

A new `docs/plans/README.md` documents the convention: directory listing is the index; each plan carries a status header; issue state lives in GitHub (no inline lists); dossiers back-reference plans rather than the reverse. Inbound references in `docs/v2-handoff.md`, `docs/m3-design.md`, `docs/_audit_m3_divergence.md`, and the prior `docs/handoffs/2026-05-test-coverage-and-security.md` dossier were rewired. Landed in commit `f8edaf3` (folded into PR #90 squash).

### PR #90 — landed
Merged as `abd84a0` (squash) — "test: Phase D coverage + non-UI --cov-fail-under=93 floor (#90)". Two reviewer nits absorbed before merge: minimal exit-code assertions on two CLI render-branch tests. The `TestNvidiaDriverVersionSecondarySubprocess` verification ask is filed as **#96**. Phase D and the floor are closed from the plan's perspective.

### PR #93 — #88(a) ClassVar fix (merged)
`fix(models): ClassVar-annotate _skip_path_validation (#88a)` — squash-merged as `5759a28`. Annotates `_skip_path_validation` as `ClassVar[bool]` so Pydantic v2 stops treating it as a `PrivateAttr` descriptor; removes the order-dependency priming step in `TestModelConfigPathValidation` and tightens its prior order-tolerant assertion into a direct missing-path raise check. Suite: 935 pass / 10 skip, coverage 94.67%. **The (b) half of #88** (ContextVar refactor for thread-safety) remains queued under the same issue.

Reviewer flagged one stale artifact made obvious by the fix: `llauncher/cli.py:133` has a defensive `cfg_dict.pop("_skip_path_validation", None)` that's now provably dead since ClassVars don't appear in `model_dump()` output. Filed as **#95**.

### Convention amendment
The `docs/plans/README.md` convention was tightened mid-session: small follow-ups now go to **GH Issues**, never to plan files. Plan files document design intent only. The "Phase D test-quality follow-ups" subsection that had been added to `test-coverage-plan.md` during the #90 review was removed; the NVIDIA-test verification is now #96 instead.

### Triage outcomes for the remaining backlog

Pass over the 11 open security follow-ups + Phase 4 items + enhancement pair produced:

- **Parallel-safe cohort (8 issues)**: #78, #79, #80, #81, #83, #84, #85, #87 — all XS/S, no design question, disjoint files. Authorized for parallel-worktree fan-out **next session**. #95 (cli.py:133 dead-code cleanup) is a candidate to join the cohort if scope allows; #96 (NVIDIA test verification) is investigation-shaped and probably wants its own small session. Subagent prompts must include: rebase onto `origin/main` as step 1, save PR body to `/tmp/pr-body-<topic>.md` and use `--body-file` (sandbox-block workaround per the prior dossier §5).
- **Q&A-blocked, punted**: #82 (LAUNCHER_MODELS_ROOT containment posture) and #86 (TLS/mTLS scoping) explicitly punted by the user; stay open, not in scope.
- **Sequential / focused**: #56 (M5 close), #64 (audit tab remote), #92 → #91 (kv-unified pair) — each wants a dedicated single-agent session.
- **#67** (systemd) — triage flagged blocked-on-#65 but #65 is closed; needs a 5-min confirm.

## 8. Suggested first move for the *next* session

Fan out the 8-issue parallel-safe cohort (#78, #79, #80, #81, #83, #84, #85, #87) into worktrees, one subagent per issue, per the dispatch protocol above. Optionally add #95 (cli.py dead-code cleanup) — it's trivial enough to be a ninth slot. Reserve the focused threads (#56 / #64 / #92→#91) and #96 for sessions where they get full attention rather than competing with parallel-cohort review load.

## 9. Closeout — convention enforcement via user-scope hook (appended later 2026-05-20)

After the PR #93 / #94 chain landed, a parallel thread explored *where* the docs convention should live to minimize cold-start friction. Mechanisms compared: system prompt addition, user memory (`~/.claude/CLAUDE.md`), repo `CLAUDE.md`, skill, hook. **Hook won** because the convention's trigger is procedurally detectable (a tool call's `file_path` matching a glob), so trigger-recognition can be externalized to the harness instead of relying on the model to invoke a skill or consult a memory section.

Landed at **user scope**, not repo scope:

- **`~/.claude/hooks/docs-convention.sh`** — bash script reading `tool_input.file_path` from stdin, gated by two checks: project must carry `docs/plans/README.md` (adoption signal), and the path must be under `docs/plans/**` or `docs/handoffs/**`. Emits a one-line reminder via `additionalContext` JSON.
- **`~/.claude/settings.json`** — registers the hook on `PreToolUse` for `Edit|Write|MultiEdit`.

Adoption is opt-in by file existence: any project that drops `docs/plans/README.md` in place gets the hook firing automatically; projects without it stay silent. The `llauncher` repo's `docs/plans/README.md` was annotated with an "Adoption signal" section explaining the linkage.

Implementation plan archived at `~/.claude/plans/draft-out-the-workflow-jiggly-sedgewick.md` (user-scope plan file, not part of this repo).

**End-to-end verified** mid-session: the hook fired correctly on the very Edits that wrote this §9 and the README annotation in `docs/plans/README.md`. The `additionalContext` injection appeared as `PreToolUse:Edit hook additional context: Docs convention: ...` in the next inference call. Both script-level and harness-invocation paths are green.
