# Handoff — 2026-08-26 (Windows seat): the Edit/Save waits, halved; and ADR-LLNCH-028

Nominal target: "the waits are localized to Edit and Save Changes." Explained
and halved — not eliminated. Second track: the operator's finding that eight
sampling/threads flags had "quietly" migrated into `extra_args` under
ADR-LLNCH-026 / #477, resolved by amendment (ADR-LLNCH-028, PR #495) rather
than revert.

## Ground at handoff

- `main` @ `637576a` = `origin/main` (`fix(ui): Edit / Save Changes run the
  script once … (#494) (#499)`; #499's merge commit is `637576a`, verified).
- One shared checkout on `main`; this handoff authored in a locked scratch
  worktree that is removed at close. No stash.
- 21 stale `worktree-*` branches reaped; none remain but this session's.
- `fix/494-edit-save-double-run` deleted local + remote.
- Unmerged branches remaining: `docs/adr-028-typed-flags-amendment` (PR #495,
  open), `docs/2026-08-24-windows-seat-handoff`,
  `test/windows-integration-real-lane`,
  `origin/docs/multiuser-migration-consolidation`.

## Track 1 — the Edit/Save waits (#494, closed by #499)

**Root cause, measured** (evidence in #494's comments): `Edit` / `Cancel` /
`Save Changes` each ended in an explicit `st.rerun()` → **two** script runs per
click; each run pays `model_registry.py:48`'s `state.refresh()` = 2 `psutil`
walks (~6 s each on this box) → 4 walks ≈ 25 s.

**Fix (#499):** `on_click` / `on_submit` callbacks replace the trailing
`st.rerun()`; named Save toast; sticky validation error; edit-form keys
namespaced by model. Scan-cache TTL **stays 3 s** — a proposed 15 s widening
was reverted in review because #418 (process-local scan cache) makes the TTL
the only staleness bound on the delegated topology.

**Live post-merge, harness against the installed code** (#309 comment):
Edit 8.2 s / 2 walks, Save 8.3 s / 2 walks. Full Models-tab render is still
4 walks (17–19 s).

Spawned:

- **#497** (`auto:fix`) — `dashboard.py:54` + `model_registry.py:48` both call
  `state.refresh()` per script run under `st.tabs`; hoist to one refresh per
  run in `app.py`. This is the other half of the render tax.
- **#498** (`auto:fix`) — audit the remaining `st.rerun()` sites for the #494
  shape.
- **#496** (`auto:fix`) — mcp lazy-singleton tests each pay a real 12 s
  `psutil` scan and one writes the real `~/.llauncher/nodes.json` (isolation
  leak; the suite's slowest tests).

Also measured: cold `GET /status` after a fresh agent start = **12,562 ms**
(confirms live the prediction #468 carried from #464), warm ~130 ms, 272 procs.

## Track 2 — ADR-LLNCH-028 (PR #495, open)

Operator finding: the eight sampling/threads flags migrated into `extra_args`
without a named decision.

**Record read.** #477's directive was verbatim "disable pydantic for
`extra_args`". The 16-field drop and the ADR-LLNCH-024 Phase 2 withdrawal were
scope *beyond* that, ratified in one decision (the issue body posed the wider
scope as a question). The original defect has an anchor bug: **#156**
(first-wins `extra_args` collision); the "silent row" was #156's warn-on-load
mitigation, `af5f0f7`.

**Deep revert assessed and declined.** #477's forward migration destroyed
provenance in `config.json` — authored vs. materialized flags are now
indistinguishable — so no reverse migration reproduces the pre-#477 file, only
a different loss; and the ADR canon has no `Reverted` status.

**Chosen: amend.** ADR-LLNCH-028 — *mirror nothing the launcher doesn't reason
about; type everything it does — or the operator designates it a per-entry
control.* Admitted set: `-ctk` / `-ctv` (as `str`), `--spec-type`,
`--spec-draft-model`, `--spec-draft-n-max`, `--kv-unified`. Primitives never
enums (operator: "leave open and surface syntax errors on failed run attempts")
→ **#376** is the enforcement surface, known-fragile, carried as a hard AC on
**#467**. `--cache-reuse` ruled out (moot under `--kv-unified`) — bears on
**#184** / **#237**. Decision B (`--spec-draft-n-max`) remains open, default
admit.

Also in #495: ADR-LLNCH-026 moved `draft/` → `accepted/` (misfiled); amendment
notes on 024 and 026; README index.
**The branch does not contain `637576a`** — rebase before merge.

Doctrine gap banked: **operating-doctrine#76** — scope widening inside an
`auto:draft` has no enforcement surface at ratification; proposes a "Scope vs.
the ask" ADR section, a ratification readback, and a `SCOPE-WIDENED` signal.

## Live box state (per session report, verifier, ~09:26)

- NSSM service `llauncher-agent` was found **Running on stale code** —
  contradicts **#493**, noted there — and was stopped for the reinstall.
- Editable install now `0.5.0a0` at the checkout.
- A **foreground user-session agent** runs (PID 318992, `127.0.0.1:8765` only —
  a manual start does not read `agent.env`); logs `agent.out.manual.log` /
  `agent.err.manual.log` in the repo root, gitignored.
- No llauncher-managed model running before or after. LM Studio's own
  `llama-server` on :61747 untouched.

## Open, with owners

- **`user:gate` — hosting posture. Not decided.** Three options as put:
  (1) re-enable NSSM against the reinstalled checkout (known-working,
  `0.0.0.0`, reads `agent.env`); (2) build the user-session ritual (**#493**;
  `scripts/run.bat` has no `agent-bg` target — **#501**, `auto:fix`; note
  **#500** is an open duplicate of #501, dedupe at next triage);
  (3) leave as-is (non-durable).
- **`auto:fix`, ready:** #497, #498, #496, #501.
- **`auto:draft` in flight:** PR #495 — decision B open; rebase needed.
- **Umbrella:** #467 (recurring `extra_args` vocabulary → first-class fields),
  with #376, #184, #237, #487 hanging off it.

## Orient-brief residue, not dispositioned (operator did not pick)

Re-surfaces at next orient:

- `docs/2026-08-24-windows-seat-handoff` unmerged; its pull-back trigger (#463
  landing) has expired. It is the fire-map HTML's only home.
- `test/windows-integration-real-lane` and
  `origin/docs/multiuser-migration-consolidation` — unattributed.
- `CLAUDE.md` doctrine pointers stale on this host: the Linux `/srv/dev/...`
  path, and `ALIGNMENT_ROADMAP.md` lives at
  `harness-tools/docs/ground-physics/need_review/`, not in `operating-doctrine`.

## Completion ring (as written)

As of handoff authoring; **the session-end ring in chat supersedes this table.**

| | Condition | Readback | |
|---|---|---|---|
| a | Worktree clean | `git status --porcelain` → empty (this worktree, before this file was written) | 🟢 |
| b | HEAD is the merged tip | `git log -1 --oneline` → `637576a fix(ui): Edit / Save Changes run the script once … (#494) (#499)` | 🟢 |
| c | Shared checkout on `main` at the same tip | `git worktree list` → `C:/Users/Shane/github/llauncher  637576a [main]` (its own `status --porcelain` is not observable from an isolated worktree) | 🟡 |
| d | No stash | `git stash list` → empty | 🟢 |
| e | No stray `worktree-*` branches | `git branch -a --list '*worktree-*'` → only `worktree-agent-a7d2f7aef4ebb153c` (this session's, locked, removed at close) | 🟢 |
| f | Unmerged branches accounted for | `git branch -a --no-merged main` → `docs/adr-028-typed-flags-amendment` (PR #495), `docs/2026-08-24-windows-seat-handoff`, `test/windows-integration-real-lane`, `origin/docs/multiuser-migration-consolidation`, plus `archive/*` and `fix/151-llauncher-env-rename` — the residue set undispositioned | 🟡 |
| g | Nominal target verified at the surface | **Not performed.** Timings came from a harness against the installed code, not from live browser clicks; hosting posture undecided | ⚪ |

## Next step

Pick the hosting posture (`user:gate`, the three options above) — it gates
whether the next session opens against a durable agent or rebuilds one. Then,
in order: rebase and land PR #495 (decision B: default admit unless the
operator rules otherwise), and take #497 — the remaining half of the
Models-tab render tax that the #494 work only measured.
