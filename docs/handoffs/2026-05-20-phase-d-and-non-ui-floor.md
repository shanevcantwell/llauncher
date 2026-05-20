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
