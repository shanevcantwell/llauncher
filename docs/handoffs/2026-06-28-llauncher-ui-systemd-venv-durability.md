# Handoff — llauncher UI systemd control + venv-durability contract (2026-06-28)

**Context owner:** Claude Code (host), orchestrator — the "llauncher-side" tab.
**Scope:** ONLY items this context owns. The other tab's open PRs appear *solely* as
coordination flags, never as owned work.
**Verified against:** `origin/main @ 8480290` (fetched 2026-06-28, US/Mountain afternoon).
Local checkout at `11f2c7a` (4 behind — intentionally not pulled; see Coordination).

> **UPDATE 2026-06-29 (single context took this over; the other tab was closed):** the build is no
> longer "not started." **Phase A landed** (PR #230 → `#227`: agent `*-ensure-venv` oneshot) and the
> **Phase B fail-loud backstop landed** (PR #232 → `#228`). The **#219 / #168** coordination gates were
> resolved (#219 merged; seams encoded into issue state). The **auto-recompose half of Phase B**
> (root `/opt` ensure oneshot) turned out to be a *distinct, unbuilt* deliverable — Phase A ensures the
> agent `.venv`, the UI uses `/opt/llauncher/venv` — now tracked as **#233**. Full session through-line:
> `harness-tools/docs/handoffs/2026-06-29-completion-bell-and-adr023-phases.md`. The status below is
> superseded by that handoff + issues #229/#231/#233/#234.

---

## TL;DR
This context took the llauncher UI from hand-launched to a **ratified `systemd --user`
deployment contract**, and established the cross-cutting **venv-durability invariant**.
Three PRs merged to `main` (#224, #225, #226). The implementation build (the ensure-venv
units) is the **one open item this context owns, and it has NOT been started.**

---

## Shipped (durable — all merged to origin/main)
1. **ADR-LLNCH-022 — UI under operator-scoped `systemd --user`.** PR #224 (merge `e5a1231`) +
   conformance `de0dbd9`. Accepted. Supersedes ADR-LLNCH-018's UI hand-launched posture.
2. **#225 build** (merge `f5847ee`): `scripts/systemd/llauncher-ui.service.user.in`
   (user unit; `Environment=LAUNCHER_STATE_DIR=/var/lib/llauncher` is the mandatory line),
   `scripts/systemd/install-ui.sh`, newly-tracked `scripts/systemd/install-cli.sh`, doc
   updates. Gates green (1183 passed, non-UI coverage 95.33%); independent review verdict
   MERGE-READY.
3. **ADR-LLNCH-023 — service-owned venv recomposition.** PR #226 (merge `8480290`). Accepted.
   Invariant `VENV-OWNED-OR-GUARANTEED` (a durable reference into a venv must guarantee that
   venv's recomposition within its own privilege scope). **OQ1 resolved by operator = shared
   `/opt` venv.** Amends ADR-LLNCH-018 + ADR-LLNCH-022.

## Decided (operator input captured)
- **OQ1 = shared `/opt`**: one root-owned `/opt/llauncher/venv`; a **system** `*-ensure-venv`
  oneshot recomposes it; the `--user` UI unit consumes it read-only with a fail-loud
  `ExecStartPre` backstop (it cannot recompose cross-scope). Per-operator venv rejected for now.

---

## OPEN — owned by THIS context (tracked in GH Issues)
The ADR-LLNCH-023 implementation build (`auto:fix`) is filed as issues — per repo convention
(`docs/plans/README.md`), tracked work lives there, not in this dossier:
- **#227 — Phase A: agent venv ensure-unit (system scope).** Carries the OQ2 lockfile bounce
  and the **#154 / PR #219 coordination** (do not double-fix the agent install path).
- **#228 — Phase B: UI venv fail-loud backstop (user scope).** OQ1 = shared `/opt` baked in.

Open questions OQ2 (no lockfile → non-reproducible recompose; bounce-to-operator candidate),
OQ3 (eager/lazy re-heal), OQ4 (`TimeoutStartSec`) live on **#227**.
**Status:** not started — awaiting operator **"build" / "hold"** steer.

---

## Findings (this context)
- **#130 is a stale duplicate of CLOSED #131.** The token-mirror it requests already shipped
  (`b5101ef`, for #131). Disposition: operator runtime probe → close as resolved-by-#131. Not a
  blocker for anything. (It was wrongly carried as ADR-LLNCH-022's "hard prerequisite"; corrected.)
- **venv-durability anti-pattern (now ADR-LLNCH-023's invariant):** a durable reference (systemd
  unit, `/usr/local/bin` symlink, cron) into a venv whose recomposition it does not own is
  fragile — a disk-space sweep reaps the venv and the service won't restart. Single-owner
  installers (pipx, distro packages) are the sanctioned exception (they own both ends).

---

## ⚠️ Coordination flags — the OTHER tab / open-PR landscape (NOT owned here)
Listed only because they overlap this context's merged or pending work:
- **#214 (`feat/systemd-cli-installer` / `install-cli.sh`)** — #225 already tracked
  `install-cli.sh` (byte-identical, sha256 `26844ebd`). **#214 is now redundant.** A
  "superseded by #225 — recommend close" comment was posted on #214; left **open** for the
  operator / other tab to close. Do not double-land the file.
- **#219 (`fix/154 run.sh install honest`)** — overlaps **ADR-LLNCH-023 Phase A** (the agent
  recompose path / #154). If both proceed, Phase A and #219 **will collide.** Coordinate /
  reconcile before building Phase A.
- **#168 (ADR-LLNCH-018 coverage governance, auto:draft awaiting ratification)** — touches ADR-LLNCH-018,
  which ADR-LLNCH-023 just amended (`8480290`). Re-check the ADR-LLNCH-018 amendment note doesn't conflict
  when #168 merges.

---

## Operator deliverables (this context only)
- **Install of the UI service is `user:gate` and PAUSED** until Phase A/B lands (the units
  currently point `ExecStart` into a disposable venv — the exact fragility ADR-LLNCH-023 fixes).
  Preconditions already satisfied: `shane ∈ inference` ✓; `/usr/local/bin/llauncher-ui` symlink
  exists ✓ (→ `/opt/llauncher/venv`).
- **Interim:** UI remains hand-launched; the v0.4.1 delegation fix goes live via
  `git pull` + relaunch (`.venv/bin/llauncher-ui`).
- **One steer pending:** **"build"** (start Phase A) or **"hold."**

## State facts
- `origin/main @ 8480290`. Local checkout `@ 11f2c7a` (4 behind — pulling is safe; the
  *install* is what must wait).
- This context's branches/worktrees: all cleaned — PRs #224/#225/#226 merged, their branches
  deleted, scratch worktrees pruned. No adr-022/023 scratch worktrees remain.
- **This handoff file is uncommitted** (untracked). Land it via a worktree (collision-safe
  given the second tab) when convenient — don't strand it.

---

## Seams encoded this session (2026-06-28, after the body above was written)
The cross-tab coordination seams were resolved into machine-readable issue state (verified via `gh`),
so an autonomous run sees them without this dossier. This **supersedes** the point-in-time
"#214 left open" / "#130 disposition pending" notes above:
- **#214 — CLOSED** (redundant; `install-cli.sh` already on `main` via #225).
- **#130 — CLOSED** as resolved-by-#131 (mirror shipped `b5101ef`; #197 merged). Residual: a Linux
  runtime probe is advisory, not new code.
- **#227 / #228 — labeled `blocked`** (+ explanatory comments): not auto-runnable until the operator
  **build/hold** steer; #227 additionally gated on the **#219 / #154** collision.
- **#168 — merge-order comment** posted (rebase after #226's ADR-LLNCH-018 amendment `8480290`).
- **Net:** the only seam left to the operator is the **build/hold steer**. Everything else is now
  visible to an autonomous run as issue state.
- **Caveat:** issue **#154** stays open + `auto:fix` + unblocked but has an in-flight PR (**#219**) —
  an autonomous run should still skip it (linked PR), a seam not expressible as a label here.
