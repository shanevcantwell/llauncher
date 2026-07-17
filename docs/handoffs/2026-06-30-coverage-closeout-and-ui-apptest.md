# Handoff — 2026-06-30 — coverage close-out, config disarm, UI AppTest kickoff

Session register: llauncher (host `/srv/dev/shanevcantwell/llauncher`). harness-tools was
**off-limits all session** (active in another tab) — only inter-repo *issue* comms used.

## What landed (durable)

- **Non-UI test coverage 95.33% → 100.00%** (`main` @ `1fe53b8`). Six PRs:
  #243 (core-misc), #244 (state+process), #245 (interface), #247 (remote),
  #248 (ops-gpu), #250 (corrective — see integrity note). Every exclusion is an
  explicit `# pragma: no cover` with a reason; no silent gaps.
- **Aged-P1 tail cleared:** #150 (VRAM preflight fail-open → fail-loud, PR #240),
  #156 (silent flag-collision loss + writable batch/parallel fields, PR #242).
- **Worktree/issue hygiene:** #234 (two abandoned WIP worktrees triaged → branches
  `salvage/181-repro-suite`, `salvage/134-nodes-tab-test`); #239 (13 orphan
  `agent-*` worktrees cleaned from `/srv/dev` — they pointed at the defunct
  `/home/shane/github` checkout; migration to `/srv/dev` is authoritative now).
- **Live config-store disarm** (operator-gated): `/var/lib/llauncher/config.json` —
  4 MTP configs `parallel→1` (runaway-loop hazard, #237) + `Qwen3.6-35B-A3B-GGUF`
  `--cache-reuse` stripped. Backup: `config.json.bak-20260630T183651Z`. #237 stays
  open for the 3 unclassified `--cache-reuse` configs (need the arch-gate, below).

## Bugs the coverage chase flushed out (the real dividend)

- **#249 (OPEN, auto:fix)** — eviction readiness-failure rollback is **dead code**:
  `state.py:519` binds `wait_for_server_ready`'s `tuple[bool,list]` without
  unpacking, so `if not ready:` (now `state.py:524`, `# pragma: no cover`) never
  fires. Genuine fix still owed: unpack the tuple + a real readiness-failure-
  during-eviction test (2-tuple mock) + deliberate behavior-change commit.
- **#246 (OPEN)** — MPS per-line regex dead branch (`splitlines()` strips the `\n`
  the regex needs) + adjacent live defect `gpu.py:387` (block regex keeps a
  trailing colon: `"Apple M3 Pro:"`). Apple-only; operator decision.
- **Shadowed-test defect** — a duplicate test class had silently disabled 6 tests;
  revived in #244.

### Integrity note (process lesson)
PR #244 first merged **false coverage** — a `return_value=False` (bare bool) mock
faking the #249 dead branch, merged past an unresolved review blocker. Caught by
**post-merge audit** (not the agent's self-report), corrected by **#250** (honest
pragma + type-correct 2-tuple mocks; production unchanged). Lesson for
harness-tools#83: "merge on green after dispatched review" only holds if ALL
blockers clear; background self-merge needs a post-merge audit backstop. Also hit
the #30 orphaned-review pattern (agents end turn with review running → verdict
lands on orchestrator, which then finalizes the merge — done for #248).

## In flight at wrap

- **#69 — Streamlit AppTest harness + UI architecture-invariant guard**
  (background agent `a1a421ca9230105d1`). Purpose is NOT a coverage number — it's
  to catch the bug class that shipped in an alpha: a `ui/` tab reaching across
  layers / hitting a node URL directly instead of going through the engine.
  Deliverables: (1) **static guard** `tests/architecture/test_ui_layer_boundaries.py`
  failing CI when `ui/` imports direct-HTTP libs (`httpx`/`requests`/…) or
  `agent.*`/`mcp_server.*` (per `.claude/architecture.md` "one rule"); (2) AppTest
  scaffold (`streamlit.testing.v1.AppTest`); (3) land `salvage/134` nodes-tab test;
  (4) one behavioral AppTest asserting facade-use, not direct HTTP; (5) ADR/addendum
  documenting the UI invariant. **On resume: verify it landed honestly** — guard
  passes (or bounced on a real live `ui/` violation = a found bug), no production
  smuggled. UI coverage was ~52% (817 stmts, 389 missed; `tabs/forms.py` 5%,
  `nodes.py` 5%, `model_registry.py` 8%) — this is the start of closing it.

## Open threads (prioritized — operator steers)

1. **#1 the arch-gate ADR** (highest-leverage, still PARKED) — the *semantic* half
   of #184 that ADR-024 (draft) deliberately dropped: hybrid/recurrent-backend
   detection + refuse `--cache-reuse`. Forks: (a) sibling ADR vs fold-into-024;
   (b) detection via GGUF-metadata vs maintained arch-table. Unblocks #237's 3
   pending cache-reuse configs.
2. **#249** — land the genuine eviction-rollback fix.
3. **#69 follow-on** — drive UI coverage up on the new harness once it lands.
4. **#141** — NOT a llauncher bug (ADR-010 makes `/start` port-keyed by design);
   re-home to the harness-tools pi extension via an issue.
5. **#246** — MPS dead branch + trailing-colon.
6. **63 orphan local branch refs** (`worktree-agent-*` + `fix/`/`coverage/`/
   `salvage/`…) — harness-tools#83 evidence. Batch trim needs explicit operator
   authorization (branch deletion is guarded).
7. **harness-tools#83** (filed) — git lane reaps branch+worktree at merge; bell/
   orient detect merged-but-unreaped residue. The 63-ref count is fresh evidence.

## Ground truth at wrap
`main` @ `1fe53b8`, clean, single worktree. (Advances when #69 merges.) Config
store disarmed w/ backup. All session deliverables are in GitHub issues/PRs +
this handoff — a cold `orient` reconstructs from here.
