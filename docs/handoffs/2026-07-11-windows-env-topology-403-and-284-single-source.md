# Handoff — 2026-07-11 — Windows env-topology 403: root cause closed, #284 fix in flight

Session cut short by operator quota. State below is durable-record-backed; resume via
`/orient` + this file.

## What closed this session

- **Root cause of the operator's Windows UI 403 (`/node-info` 403, `/health` 200) found and
  named.** The operator had been editing `scripts/windows/llauncher-agent.env.example`
  expecting propagation. That file is a **first-run-only seed**: `install.ps1:84-94` reads it
  only when `%USERPROFILE%\.llauncher\agent.env` is absent — every later edit is a silent
  no-op. The only live source is the state-dir `agent.env`; NSSM env and the `agent.token`
  mirror both snapshot from it on each installer run. Operator readback confirmed:
  NSSM == state-dir `agent.env` ≠ template token (dead value).
- **PR #282 landed** (fix for #281 legacy-key migration + fail-loud mirror): gates run this
  session (1456 passed, 100.00% coverage, `bash -n` clean), dispatched Opus review verdict
  MERGE. Squash-merged as `ef64b8d`; #281 auto-closed; worktree
  `.claude/worktrees/issue-281` removed; local branch deleted; shared checkout clean on
  `main` at `ef64b8d`.
- **#284 filed** (`bug auto:fix sev:crit pri:now`): env-topology re-role, **operator-ratified
  design in the issue body** — single live source (`LAUNCHER_STATE_DIR/agent.env` read
  directly by both processes), retire the `agent.token` mirror class, rename template to
  match target, loud on ignored input, LocalSystem `LAUNCHER_STATE_DIR` injection, systemd
  parity. The issue body is the full contract; it is sufficient to re-dispatch from cold.
- **#285 filed** (`bug auto:fix sev:normal pri:next`): from the Opus review of #282 — post-
  migration duplicate keys can resurrect a stale legacy value; the two installers pick
  opposite duplicates. May be absorbed by #284 (check before starting it).

## In flight at cutoff (the one volatile item)

An implementation agent for **#284** was dispatched (sonnet, isolated worktree, branch
`fix/284-env-single-source`, base `ef64b8d`) under the full contract in issue #284, with
instructions to run gates and open a PR titled
`fix(deploy): single live env source — agent.env read directly by both processes, mirror retired (#284)`.

**On resume:** check `gh pr list` for that PR.
- If it exists → dispatch Opus-tier review (contract: the 5 design points in #284 +
  profiled test coverage + PARSE-AT-THE-DOOR), run gates, merge per contract
  (`gh pr merge --squash --delete-branch`, ground close).
- If it does not exist → the dispatch died with the session; re-dispatch from #284's body
  (nothing is lost — the contract is durable there). Check for and remove any orphaned
  worktree/branch first (`git worktree list`, `git branch --list 'fix/284*'`).

## Operator's Windows box — immediate unblock (independent of #284)

1. Put the intended token on the `LLAUNCHER_AGENT_TOKEN=` line in
   `%USERPROFILE%\.llauncher\agent.env` (double-L). That file is the only live source.
2. `git pull` then re-run `.\install.ps1` elevated (post-#282 it migrates legacy keys and
   **Dies** instead of silently skipping the token mirror). Expect
   `[OK] Mirrored token to ...`.
3. Restart the UI → `/node-info` should be 200.
4. Also confirm project-root `.env` carries **no** `LLAUNCHER_AGENT_TOKEN` line — the UI
   loads it via `load_dotenv()` and an env var beats the token file in resolution.
5. Delete/ignore any stray scripts-side `agent.env`-shaped file — nothing reads it.

## Open threads (banked, not urgent)

- **Model-edit read-back doubt** (operator, this session): edits to model configs via the UI
  may not be read back properly. Unexplored by agreement — next thread after auth clears.
  Not yet filed as an issue; file on first repro.
- #285 disposition after #284 lands.
- Windows-box field verification of both #282 and #284 is `user:gate` — the operator's
  hands, post-merge.

## Provenance

- Merge: PR #282 → `ef64b8d`; review + gates readback in this session's dispatch records.
- Issues filed this session: #284 (ratified design), #285 (review finding).
- The reviewer's full #282 verdict (MERGE, one non-blocking finding) is reproduced in
  #285's body where it matters.
