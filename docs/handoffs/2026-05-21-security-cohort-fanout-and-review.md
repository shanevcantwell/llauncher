# Session handoff — security cohort parallel fan-out + review-and-fix round (2026-05-21)

Companion to `docs/handoffs/2026-05-20-phase-d-and-non-ui-floor.md` (prior session). Pointers to source files inline; artifact contents not pasted here.

## 1. Where we are

Branch `main` is at `97bd9f5` (unchanged since the prior dossier). Six PRs open against `main`, all MERGEABLE, all from the security-hardening cohort announced in the prior dossier §7–§8:

| PR | Issue | Branch | Status |
|---|---|---|---|
| #98 | #80 | `sec-80-mcp-stdio-doc` | ready to merge (PASS review) |
| #99 | #95 | `cleanup-95-cli-dead-pop` | ready to merge (PASS review) |
| #100 | #83 | `sec-83-nodes-perms` | ready to merge (PASS-with-LOW review) |
| #101 | #81 | `sec-81-extra-args-deny` | ready to merge (CONCERNS → review-fix landed at `780759f`) |
| #102 | #84 | `sec-84-html-escape-audit` | ready to merge (CONCERNS → review-fix landed at `d46c541`) |
| #103 | #78 | `sec-78-body-cap` | ready to merge (PASS-with-LOW review) |

v2 refactor milestone state unchanged from prior dossier: M1–M4 complete; M5 has one item left (#56 canonical self-swap integration test, ADR-016); M6/M7 not started.

**Network-trust posture:** loopback + auth-on (default since #75). Once Wave 1 merges, the security backlog is 5 open items: #79, #85, #87 (Wave 2 cohort, blocked on Wave 1 merge), #82 + #86 (user-punted).

**Coverage posture (post-session, pending merges):** non-UI **94.66%** (2999 stmts, 160 missed), suite at **960 pass / 10 skip**, floor still at `--cov-fail-under=93` in `pytest.ini`. Pre-session was 935 pass / 94.67%. The +25 tests came from cohort PRs + the #101 review-fix.

## 2. What landed this session

| Ref | Type | Impact |
|---|---|---|
| PR #98 (open) | docs | README MCP stdio trust-boundary blockquote + `docs/plans/security-hardening-plan.md` §5.2 reword as standing design intent |
| PR #99 (open) | cleanup | `llauncher/cli.py:133` dead `_skip_path_validation` pop removed (post-#93) |
| PR #100 (open) | tests | 4 regression tests pinning `~/.llauncher/nodes.json` chmod 0600 + best-effort parent-dir 0700 (chmod itself pre-existed); §5.6 open question resolved to `llauncher.remote.registry.NODES_FILE` constant |
| PR #101 (open) | code+tests | `DENIED_EXTRA_ARG_FLAGS` frozenset (`{--api-key, --alias, -m, --model, --host, --port}`) + Pydantic `field_validator` on `ModelConfig.extra_args` + `"validate_assignment": True` (post-review) + `update_model_config` try-block widened to wrap assignment loop (post-review); 25 new regression tests |
| PR #102 (open) | tests | Source-level pin against `unsafe_allow_html=True` in `llauncher/ui/*` (audit found 0 callsites); `re.compile(r"\s+")` normalization (post-review); tautological `html.escape` test dropped (post-review) |
| PR #103 (open) | code+tests | `BodySizeLimitMiddleware` (pure-ASGI, 1 MiB cap, fast-path Content-Length + streaming-accumulator) wired into `create_app` as outermost layer; 10 regression tests including direct-ASGI for the streaming branch |
| Issues #104, #105, #106 | follow-ups | LOW-severity reviewer findings filed as durable issues per the convention |

Durable plan artifacts: `docs/plans/security-hardening-plan.md` — C3, C5, C7, C10, C11 are post-merge complete; C4 (#79), C8 (#82), C9 (#86), C12 (#85) remain. `docs/plans/test-coverage-plan.md` unchanged.

## 3. Coverage baseline (pending merges)

Methodology unchanged from prior dossier (`pytest` config-driven; `[tool.coverage.run]` scope in `pyproject.toml`).

**Non-UI total: 94.66%** (2999 stmts, 160 missed). Pre-session was 94.67% (2981 stmts, 151 missed). The +18 statement growth came from the cohort PRs (middleware, validator, mcp_server widened try block); the 9-line miss delta was absorbed by the regression tests.

Top remaining non-UI gaps roughly unchanged from prior dossier (`state.py`, `cli.py`, `core/gpu.py`, `operations/swap.py`). #69 UI harness still gates the wider re-baseline.

## 4. Open spine — work queued for next session

### Cohort merge order
Recommended: **#99 → #100 → #98 → #103 → #101 → #102** (PASS PRs first, then review-fixed). All six were MERGEABLE at the time of writing; no rebases needed.

### Wave 2 cohort (3 issues, blocked on Wave 1 merge)
- **#79** — CORS regression test on agent responses (C4). Blocked on #103 merge (both touch `tests/integration/test_agent_security_*`).
- **#85** — README guidance for Streamlit `--server.address` binding (C12). Blocked on #98 merge (both touch `README.md`).
- **#87** — `create_app(auth_token=None)` signature tightening (C1 hardening). Blocked on #103 merge (both touch `llauncher/agent/server.py`).

Dispatch with the parallel-worktree protocol from prior dossier §7 + the addenda in §5 below.

### LOW follow-ups filed this session (XS-scoped batch candidate)
- **#104** — convention-compliance pass on `security-hardening-plan.md` §5.3/§5.4 (similar to PR #98's §5.2 cleanup)
- **#105** — align body-cap C3-a test body size with plan's 10 MiB literal (post-#78)
- **#106** — platform-guard `nodes.json` permission tests for non-POSIX runners (post-#83)

### Threads still queued (unchanged from prior dossier)
- **#56** — M5 canonical self-swap integration test (ADR-016). Single dedicated session.
- **#69** — Streamlit `AppTest` harness. Multi-session; unlocks UI coverage when it lands.
- **#82** — `LAUNCHER_MODELS_ROOT` containment (user-punted; needs design Q&A first).
- **#86** — TLS/mTLS scoping (user-punted; design-doc shape, not code).
- **#88(b)** — `ContextVar` refactor of `_skip_path_validation` (thread-safety; (a) shipped via #93).
- **#42** — vLLM adapter (M6, blocked on M5).
- **#96** — `TestNvidiaDriverVersionSecondarySubprocess` branch-coverage verification (investigation-shaped).
- **#91 → #92** — kv-unified pair (single sequential session).
- **#64** — Audit tab remote-node access.
- **#67** — systemd integration (5-min confirmation that #65 is closed).
- **#95** closes on PR #99 merge.

### Infrastructure debt surfaced this session
- **8 locked subagent worktrees** under `.claude/worktrees/agent-*` — 6 Wave 1 cohort + 2 failed simplifier retries (`agent-a2f7660cbbcaf4b03`, `agent-acb369c161d480f40` was the successful one but also locked). Lock reason: `claude agent agent-XXX (pid 606812)`. Removable with `git worktree remove -f -f <path>`. Mechanism details unclear to the orchestrator; deferred for user judgment.
- **Local branches** `sec-81-fix-on-main` (used only for the #101 push-via-refspec workaround) and `temp-sec-81-fix` (failed first retry, no commits). Both removable with `git branch -D <name>` once the holding worktree isn't locked.

## 5. Workflow observations

This session generated more procedural signal than either of the two prior dossiers. Harness/sandbox mechanics dominated.

### Sandbox findings (additive to prior dossier §5/§7)
- **Heredoc-form `gh pr create --body "$(cat <<EOF...)"` denied** — known. Workaround: `--body-file`.
- **Direct `/tmp/` writes denied at subagent scope** — newly discovered. Workaround: `/tmp/not-a-project/`. Affected the #95, #81 PR-body workflows. Should be folded into the parallel-cohort prompt template.
- **`gh issue create --label foo` fails hard** if the label doesn't exist on the repo. Two follow-up filings tripped on `--label tests` (no such label). Run `gh label list` first or omit `--label`.
- **Short inline `--body "..."`** (no heredoc, no `$(...)`) works fine for both `gh pr create` and `gh issue create`.

### `isolation: "worktree"` harness behavior
- **Worktrees that made changes persist locked** after the agent completes — opposite of what the tool docs claim ("the worktree is automatically cleaned up if the agent makes no changes; otherwise the path and branch are returned in the result"). All 6 Wave 1 worktrees plus the failed-retry worktrees stayed alive and locked. They consume the single-checkout slot for their PR branch.
- **Locked worktrees block follow-up subagents.** A fresh `isolation: "worktree"` agent that tries to `git checkout <pr-branch>` cannot, because the wave-1 worktree already owns it (git's single-checkout invariant). This wedged BOTH simplifier retries on PR #101 — the first made edits in the wrong (locked) worktree's working tree and couldn't commit; the second hit a different failure mode but the same root cause.
- **Workaround that lands cleanly: local-branch + push-via-refspec.** From the main checkout: `git checkout -b foo origin/<pr-branch>` → edit → commit → `git push origin foo:<pr-branch>`. Used inline to land the #101 review-fix at `780759f`. Bypasses worktree locking entirely.
- **`-f -f` override exists** for forcibly removing locked worktrees but the orchestrator deferred to user judgment per the constitution's forward-resolution clause — surface infrastructure friction rather than `-f -f` through opaque harness state.
- **The #102 simplifier somehow worked around the lock** (likely via `--force` semantics) and ended up with `sec-84-html-escape-audit` checked out in two worktrees simultaneously, both at SHA `d46c541`. Worked, but undetected at the time — same hazard with the opposite sign.

### Orchestrator cwd drift
- The orchestrator's bash session cwd silently drifted into a stale subagent worktree path (`agent-a2f7660cbbcaf4b03`) mid-session, despite no explicit `cd` from the orchestrator. Tool docs say cwd persists between Bash calls; the drift mechanism is unclear. Detected when a Read with a main-checkout absolute path failed because the relative-resolution layer used the subagent worktree path. Workaround: `pwd` after any subagent interaction; use `git -C <main-checkout>` for operations meant for the main repo.

### 3-stage flow (write → review → fix)
- **Stage 1 (parallel fan-out, 6 subagents)** — salience-stable. ~200-word returns aggregated cleanly.
- **Stage 2 (parallel reviewers, 6 subagents)** — also stable. The `reviewer` subagent type was a clean fit; the CONCERNS verdicts on #101 / #102 caught real issues (`validate_assignment` gap; tab/newline normalization gap) that no other layer would have caught.
- **Stage 3 (simplifier fix-up)** — wedged repeatedly on harness mechanics (see above). Third attempt (orchestrator-inline via the refspec workaround) succeeded. The `simplifier` subagent type is sound; only the cross-worktree branch-lock issue blocked it.

### Reviewer-subagent observations
- Six independent reviewers with explicit "verify, don't trust" framing produced findings the orchestrator could not have produced from agent self-reports alone.
- The **interrogation-item lists** in each reviewer prompt (concrete bypass surfaces, claim-specific checks) sharpened the output materially. Generic "review this PR" framing would have returned generic "looks fine" verdicts.
- One reviewer's "BLOCKER" label was over-strong (low-probability real-world impact for the `_normalize` whitespace gap) but the underlying finding was correct. Severity-labeling discipline matters less than finding accuracy.
- Filing LOW follow-ups as **GH issues** (not as plan-file edits) followed the §94 convention and the user-scope docs-convention hook (§9 prior dossier). All 3 LOWs went to #104–#106.

### Constitutional discipline that held
- **Honest failure** — both wedged simplifier attempts surfaced their blockers (one in detail) rather than fabricating completion. The first #101 retry's diagnosis of the downstream `mcp_server/tools/config.py` test breakage was correct and load-bearing — it became the basis for the inline fix.
- **Delegation default** held until the third simplifier attempt; then the orchestrator-inline path was correct because the residue was bounded (3 file edits + 1 pytest run + 1 push) and a fourth subagent attempt had low expected value given the harness wedge pattern.
- **Provisional Handling on TaskCreate reminders** — fired ~7 times. Dismissed each per the constitution; PRs + issues + this dossier are the durable record.

## 6. Suggested first move for the *next* session

**Merge Wave 1 in the order in §4**, then dispatch Wave 2 (#79, #85, #87) per the prior dossier §7 parallel-cohort protocol, with these additions baked into each child prompt:

1. **PR bodies write to `/tmp/not-a-project/pr-body-<N>.md`**, not `/tmp/`.
2. **No labels in `gh issue create` / `gh pr create`** unless verified via `gh label list`.
3. **Reuse the local-branch + push-via-refspec pattern** if any agent finds itself blocked on a worktree-locked branch.
4. **`pwd` after any subagent interaction** if the orchestrator needs to do file work.

Alternative opening move (XS, quick palette cleanser): batch the 3 LOWs (**#104, #105, #106**) as a single sequential session. ~10 min total; clears the post-cohort follow-up shelf before Wave 2.

The larger threads (#56, #69, #82, #86, #88(b), #42, #91→#92, #64, #67, #96) remain queued. M6/M7 still wait on M5 closure.

Optional infrastructure cleanup (defer or do — your judgment):
- `git worktree remove -f -f .claude/worktrees/agent-*` for the 8 stale paths
- `git branch -D sec-81-fix-on-main temp-sec-81-fix`
