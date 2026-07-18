# Session handoff — Wave 1 + Wave 2 cohort merges, review pass, XS batch closeout (2026-05-23)

Companion to `docs/handoffs/2026-05-21-security-cohort-fanout-and-review.md` (prior session). Pointers to source files inline; artifact contents not pasted here.

## 1. Where we are

Branch `main` is at `b7a8d80` (`chore(security): XS follow-up batch — #107 #111 #112 #113 #114 #115 (#116)`). All security-hardening Waves 1 and 2 merged + the post-Wave-2 XS follow-up batch.

**Plan controls landed post-merge (from `docs/plans/security-hardening-plan.md` §3):** C1, C2, C3, C4, C5, C7, C10, C11, C12. **Remaining from plan:** C8 (#82, user-punted) and C9 (#86, user-punted). The security cohort main thrust is closed; the post-review MEDIUM/LOW shelf is also closed.

v2 refactor milestone state unchanged from prior dossier: M1–M4 complete; M5 has one item left (#56 canonical self-swap integration test, ADR-LLNCH-016); M6/M7 not started.

**Network-trust posture:** loopback default, auth-required-on-non-loopback (C1+C2 via #75), body cap 1 MiB (C3 via #103), no CORS by design (C4 via #108 + pinning tests), C1 hardened so `create_app` constructor cannot silently build no-auth (#110); whitespace-token rejection + real runtime guard on the test-only no-auth escape hatch (#116).

**Coverage posture (post-batch on `b7a8d80`):** non-UI **94.96%** (3056 stmts, 154 missed), suite at **994 pass / 10 skip / 0 fail** — fully green for the first time this session. Pre-batch was 982 pass / 10 skip / 1 known flake (#107, now resolved). Floor still at `--cov-fail-under=93` in `pytest.ini`.

**Follow-up shelf (open issues):** **3 items** — all docs/test polish from the prior session, none security-gate:
- #104 (plan §5.3/§5.4 convention pass)
- #105 (body-cap C3-a test body-size literal)
- #106 (platform-guard nodes.json perm tests for non-POSIX runners)

The 6 issues opened this session (#107, #111–#115) all closed via PR #116 (`b7a8d80`).

## 2. What landed this session

### Wave 1 merges (all from prior session's cohort)

| PR | Issue | Merge SHA | Impact |
|---|---|---|---|
| #99 | #95 | `6b3c42f` | cleanup: `_skip_path_validation` pop removal |
| #100 | #83 | `d59138d` | security: `~/.llauncher/nodes.json` chmod 0600 (C10) |
| #98 | #80 | `1a88da6` | docs: MCP stdio trust-boundary (C5) |
| #103 | #78 | `1ae4bb8` | security: `BodySizeLimitMiddleware` 1 MiB cap (C3) |
| #101 | #81 | `b919233` | security: `extra_args` deny-list (C7) |
| #102 | #84 | `e521830` | security: html-escape audit + regression test (C11) |

### Wave 2 fan-out + merges (new this session)

| PR | Issue | Merge SHA | Impact |
|---|---|---|---|
| #109 | #85 | `7f164e7` | docs: Streamlit `--server.address` loopback guidance + run.sh/run.bat env var (C12) |
| #108 | #79 | `ca3e35a` | tests: CORS regression suite (5 tests pinning absence of `Access-Control-*` on agent responses, C4) |
| #110 | #87 | `d95ff4b` | security: `create_app(auth_token: str)` required; `create_app_unauthenticated()` test-only sibling; `AGENT_API_KEY` monkeypatch removed from `tests/integration/conftest.py` (C1 hardening) |

### XS follow-up batch (new this session, closed in one PR)

| PR | Merge SHA | Issues closed | Impact |
|---|---|---|---|
| #116 | `b7a8d80` | #107, #111, #112, #113, #114, #115 | chore(security): closeout pass on the post-review shelf — see §2.1 below for per-issue change shapes |

#### 2.1 Per-issue changes in #116

- **#107** — `tests/unit/test_phase_d_coverage.py::test_start_server_validation_error_recorded` now patches `llauncher.state.is_port_in_use` so host port-8081 state can no longer leak into the test.
- **#111** — `llauncher/agent/server.py::create_app` rejects whitespace-only `auth_token` (`if not auth_token or not auth_token.strip()`). Parametrized regression test pins five whitespace shapes.
- **#112** — `create_app_unauthenticated` replaces `assert __debug__` with `if not __debug__: raise RuntimeError(...)`. Docstring corrected to match real `-O` semantics. AST-based regression test pins the runtime-guard shape so a future refactor cannot regress to an `assert`.
- **#113** — new `test_post_with_origin_header_no_cors_headers_authed_2xx` against the idempotent `/stop/{port}` endpoint (returns 200 `action=already_empty` on an idle port; no spawn, no port-binding side effect). Replaces the prior bogus-model 4xx/5xx flavor.
- **#114** — preflight `Access-Control-Request-Method` switched from `GET` to `POST`. Matches the real CORS-bypass threat shape (state-changing requests).
- **#115** — shared `_assert_no_cors_signal(resp, label)` helper now asserts both `Access-Control-*` absence and `Vary: Origin` absence on every CORS test. Catches a `CORSMiddleware` that is mounted but declines to echo.

### Issues filed this session (all closed via #116)

- #107 — test isolation: `test_start_server_validation_error_recorded` leaks OS port state (filed Wave 1 post-merge; closed by #116)
- #111 — security(agent): `create_app` accepts whitespace-only `auth_token` (MEDIUM, #110 M1; closed by #116)
- #112 — security(agent): `assert __debug__` paper tripwire + factually wrong docstring (MEDIUM, #110 M2; closed by #116)
- #113 — test(agent): CORS regression test never reaches authed 2xx path (MEDIUM, #108 F1; closed by #116)
- #114 — test(agent): CORS preflight uses GET (LOW, #108 F2; closed by #116)
- #115 — test(agent): CORS tests don't assert absence of `Vary: Origin` (LOW, #108 F3; closed by #116)

### 3-stage flow (write → review → merge) outcome
- **Stage 1 (Wave 2 write, 3 parallel subagents, `isolation: "worktree"`):** all 3 completed clean; PRs opened.
- **Stage 2 (Wave 2 reviewers, 3 parallel subagents):** mixed-success — the `reviewer` subagent type appears to have no Bash permission in this session; 2 of 3 reviewers bailed with explicit "I need Bash access" messages. Constitution's documented fallback (`general-purpose`) also bailed when prompts directed it to raw `git diff`/`git show` (also gated). The reviewer that succeeded did so by improvising to `gh pr diff` (allowed). Lesson: reviewer prompts must direct subagents to `gh pr diff` and `gh pr view --json files` rather than raw git inspection commands. See §5 for the settings-allowlist diagnosis.
- **Stage 3 (merge):** user chose "merge with follow-ups" over "fix before merge" for both #108 and #110 — diverges from the prior session's pattern where CONCERNS verdicts on #101 and #102 got fix-up commits inline before merge. Both decision shapes are valid; this session's choice cleared the cohort faster but loaded the follow-up shelf.

## 3. Coverage baseline (post-batch)

Methodology unchanged from prior dossier (`pytest` config-driven; `[tool.coverage.run]` scope in `pyproject.toml`).

**Non-UI total: 94.96%** (3056 stmts, 154 missed). Pre-session was 94.66% (2999 stmts, 160 missed). The +57 statement growth came from Wave 2; the post-batch -2 miss delta came from #116 (whitespace-rejection branch + runtime-guard branch now covered).

**Suite total: 994 pass / 10 skip / 0 fail** — fully green. The 12-test growth from 982 came from #116's regression additions (5 whitespace cases, 1 AST guard-shape pin, the new 2xx-POST CORS test, the upgraded #107 test, and ancillary path additions).

Top remaining non-UI gaps roughly unchanged: `state.py`, `cli.py`, `core/gpu.py`, `operations/swap.py`. #69 UI harness still gates the wider re-baseline.

## 4. Open spine — work queued for next session

### Immediate follow-up shelf (3 items, all docs/test polish)

All 6 issues opened this session (#107, #111–#115) landed via #116. What remains is the docs/test-polish carryover from the prior session — none of these are security-gate:

- #104 — plan §5.3/§5.4 convention compliance (similar in shape to #98's §5.2 reword)
- #105 — body-cap C3-a test body-size literal alignment with plan's 10 MiB
- #106 — platform-guard nodes.json permission tests for non-POSIX runners

### Alpha-readiness decision joint

The security cohort is post-merge complete for the documented threat model. Two items remain that are alpha-decision-shaped, not engineering-blocked:

- **#82** (`LAUNCHER_MODELS_ROOT` containment, plan C8) — user-punted. Acceptable to defer if alpha targets single-operator/trusted-LAN; not acceptable if alpha targets multi-tenant or cross-organization use.
- **#86** (TLS/mTLS for cross-host remote nodes, plan C9) — user-punted. Acceptable to defer if cross-host trust uses Tailscale / VPN / co-location for alpha; not acceptable if alpha exposes remote-node coordination over untrusted networks.

Picking a posture on these is the next gate for "ship the alpha." Neither has an engineering work-item shape yet; both need design Q&A first.

### Larger threads (unchanged from prior dossier)
- **#56** — M5 canonical self-swap integration test (ADR-LLNCH-016). Single dedicated session.
- **#69** — Streamlit `AppTest` harness. Multi-session; unlocks UI coverage.
- **#82** — `LAUNCHER_MODELS_ROOT` containment (user-punted; needs design Q&A first).
- **#86** — TLS/mTLS scoping (user-punted; design-doc shape).
- **#88(b)** — `ContextVar` refactor of `_skip_path_validation` (thread-safety; (a) shipped via #93).
- **#42** — vLLM adapter (M6, blocked on M5).
- **#96** — `TestNvidiaDriverVersionSecondarySubprocess` branch-coverage verification (investigation-shaped).
- **#91 → #92** — kv-unified pair (single sequential session).
- **#64** — Audit tab remote-node access.
- **#67** — systemd integration (5-min confirmation that #65 is closed).

### Infrastructure debt — grown this session
- **11 locked subagent worktrees** under `.claude/worktrees/agent-*` (8 from prior session + 3 new from Wave 2). Same lock-reason pattern as before. Removable with `git worktree remove -f -f <path>` once the user judges the harness mechanism. The 3 new worktrees are `agent-a8b8ab229d44dd375` (#108 child), `agent-aaa45ee23f1516a2b` (#109 child), `agent-ac84b51496d8db12a` (#110 child).
- **Local branches** from prior session still removable; no new branch debt this session (the XS-batch PR #116 used the main checkout directly, so no worktree-lock).
- **Reviewer-Bash settings gap** — see §5; closeable via either settings edit or prompt-template change.

## 5. Workflow observations

This session generated three classes of substantive procedural signal: a settings-allowlist gate on subagent Bash, a recurrence of the cwd-drift quirk, and a user-supplied principle re: the git subagent.

### Reviewer-Bash settings gate (new finding)

Symptom: subagents dispatched as `reviewer` and `general-purpose` reported `Bash access denied` for `git fetch`, `git diff`, `git show`. Two of the three Wave 2 reviewers bailed; one improvised to `gh pr diff` and succeeded.

Diagnosis: `~/.claude/settings.json` allows `Bash(git fetch:*)`, `Bash(git add:*)`, `Bash(git commit:*)`, `Bash(git push:*)`, etc. `.claude/settings.local.json` allows `Bash(gh pr *)`, `Bash(git worktree *)`, `Bash(git branch *)`, `Bash(git stash *)`, `Bash(git checkout *)`, `Bash(git show <specific-sha>:<specific-file>)` (one-off explicit entries from a prior session). **Neither file has a generic `Bash(git diff:*)` or `Bash(git show:*)`** permission. Interactive permission prompts cannot be answered by a subagent, so the call fails closed.

The `reviewer` subagent type **specifically** appeared to have no Bash at all this session (every Bash call failed regardless of command shape), while `general-purpose` could route through `gh pr *` patterns. The reviewer-type's permission scope may be narrower than its tool list implies; unclear without instrumentation.

**Two closure shapes for this gap:**
1. **Settings edit (project or user scope):** add `Bash(git diff:*)` and `Bash(git show:*)` to allow list. Read-only git inspection is low-risk; project-scope is correct for project-specific reviewer workflows.
2. **Prompt-template fix:** future reviewer dispatches direct subagents to `gh pr diff <N>` and `gh pr view <N> --json files,additions,deletions` rather than raw git. Already-allowed permissions; no settings change.

Both fixes ship value; settings is broader, prompt-template is more reproducible-across-environments.

### Git subagent as primitive for git inspection (user nudge, 2026-05-23)

User note during the gate investigation: "*And the git subagent? The concept is that git operations will never be 'interesting' wrt the orchestration top layer context.*"

Reading this against the Constitution's "Git operations beyond read-only inspection delegate to the git subagent unconditionally" clause — the principle extends *down* the call tree, not just up. When a reviewer or simplifier needs to inspect a diff, the inspection itself is git work; pushing that into a `git` subagent dispatch absorbs the residue (full diff contents) one layer below the reviewer's analytical context. The reviewer then operates on a compressed pointer-summary from the git child.

**Implication for future Wave / cohort flows:** review dispatches should chain `git` subagent → `general-purpose`-or-`reviewer` rather than embed git ops in the reviewer's own context. Adoption deferred to next cohort session; not retrofitted here.

### CWD drift reassertion

Prior dossier §5 reported a one-off where orchestrator bash silently drifted into a stale subagent worktree path. This session it happened again — `pwd` mid-stream returned `/home/shane/github/shanevcantwell/llauncher/.claude/worktrees/agent-ac84b51496d8db12a` (the #110 child's worktree). The drift was detected when `git pull --ff-only origin main` failed with "Not possible to fast-forward, aborting" — the worktree's local branch was on the PR head, not the main checkout's `main`.

The dossier's documented workaround (`git -C <main-checkout-path>` for every git op meant for the main repo) carried this session, but it's a friction tax on every shell call. Mechanism still unclear. Not investigated further this session.

### Worktree-lock recurrence

Every `gh pr merge --delete-branch` failed on local-branch deletion because the originating subagent worktree still owned the branch. Remote branches were deleted normally; PR state went MERGED cleanly. Same as prior dossier — only the local-cleanup half is wedged. The merge of #110 additionally hit `fatal: 'main' is already used by worktree at '/home/shane/github/shanevcantwell/llauncher'` during cleanup, distinct from the PR-branch lock — `gh` was probably trying to checkout `main` somewhere to clean up. Did not block merge.

### Constitutional discipline that held
- **Honest failure** — reviewers (#108 and #110 first-attempts, and #110's second attempt) reported the Bash gate as an unmet precondition rather than fabricating verdicts. The Constitution's "report infrastructure failures as understood findings" clause held.
- **AskUserQuestion at decision joints** — 6 times this session (opener, fix-up vs merge, reviewer-gate response, settings vs prompt fix, session wrap, XS batch trigger). Each was a real call requiring user pacing rather than a default orchestrator could safely take.
- **Provisional Handling on TaskCreate / docs-convention reminders** — fired ~12 times across the session. Dismissed each; PRs + issues + this dossier are the durable record. The docs-convention hook fires on every `Edit` to `docs/handoffs/**`, including the in-session updates that extended this dossier through the XS batch closeout — convention compliance is satisfied by the issue-pointer pattern, not by silencing the hook.
- **Delegation default with inline-when-bounded** — held in both shapes. Wave 2 write/review children delegated (residue: full diffs, gh outputs). The 5 follow-up issue filings were inline because the residue (gh issue create + URL return) was bounded and known-shape. The XS batch closeout itself was inline — 6 narrow edits across 4 files + one suite run + one push + one merge — well within the bounded-residue envelope; a subagent dispatch would have moved the same work into a child window with no salience benefit.

### XS batch closeout (orchestrator-inline path) — execution notes

The 6-issue closeout (PR #116) ran inline rather than via subagent dispatch. The judgment was that the residue was bounded ex ante: each issue had been filed with concrete code locations, the fix sketches were known-shape from the review verdicts, and the test surface to verify was a defined subset (~18 targeted tests + 1 full-suite run). One self-bug surfaced and was caught by the new test it had just landed (the #112 AST regression test originally substring-matched `"assert __debug__"` and tripped on the docstring's own *explanation* of why that pattern is wrong; rewrote the assertion to use `ast.walk` for true syntactic check). The inline path closed cleanly: 994 pass / 10 skip / 0 fail at the merge SHA. This is a useful calibration point for the "inline-when-bounded" half of the delegation default — XS batches with pre-specified locations sit cleanly inside the orchestrator's window without forcing residue accumulation.

## 6. Suggested first move for the *next* session

The security cohort exit is clean. The next move is alpha-readiness shaped, not engineering-execution shaped:

**Option A (recommended for alpha-readiness): commit to a posture on #82 and #86.** These are the two remaining items from the security plan and both are decision-shaped:
- **#82** — does alpha require `LAUNCHER_MODELS_ROOT` hard containment, or is operator-trust the alpha posture? (Hard containment needs design Q&A on opt-in-default vs opt-out-default and on the soft-warning-vs-hard-error semantics from plan §5 Q3.)
- **#86** — does alpha need a TLS/mTLS story for cross-host remote nodes, or is Tailscale / VPN / co-location the alpha posture? (TLS would be architectural, not a small add.)

A single design-Q&A session on both yields either two new engineering tickets with shape (if "yes") or two closures with rationale (if "punt holds for alpha"). Either outcome unblocks the "freeze and ship" decision.

**Option B (lower-priority engineering work): the docs/test-polish carryover** (#104, #105, #106) from the prior session. ~20-30 min as a single sequential pass. Doesn't unblock alpha but clears the post-cohort shelf to zero.

**Option C: close the reviewer-Bash gate** (`.claude/settings.local.json` edit adding `Bash(git diff:*)` + `Bash(git show:*)`, or a reviewer-prompt-template fix to route via `gh pr diff`). The gate doesn't bite until the next cohort fan-out — but the next cohort fan-out is exactly what `#82` or `#86` would trigger if either lands as engineering work.

**Threads still queued at higher cost** (unchanged from prior dossier): #56 (M5 self-swap, single dedicated session), #69 (Streamlit AppTest harness, multi-session, gates UI coverage), #88(b), #42 (blocked on M5), #91→#92, #64, #67, #96.

Optional infrastructure cleanup (defer or do — your judgment):
- `git worktree remove -f -f .claude/worktrees/agent-*` for the 11 stale paths
- `git branch -D sec-81-fix-on-main temp-sec-81-fix` (carried over from prior session)
- Pick a long-term resolution for the cwd-drift quirk (mechanism still opaque)
