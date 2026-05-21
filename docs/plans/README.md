# Plans

Each file in this directory is an **active or recently-active track of work** on llauncher. Tracks are independent: they may share blockers, but none nests inside another. A new session picks up by reading the directory listing and the status header of each file.

## Current tracks

`ls docs/plans/` is the index. As of this writing:

- `v2-implementation-roadmap.md` — v2 architecture milestones (M1–M7) and the post-M4 phased plan.
- `test-coverage-plan.md` — phased push (A–D) to raise non-UI coverage and install a CI floor.
- `security-hardening-plan.md` — threat model + 12 controls (C1–C12), staged landing.
- `CLI-IMPLEMENTATION.md`, `phase1-verification.md`, `sleeptime-remediation/` — historical artifacts from prior phases; check status header before treating as active.

## Convention

Each plan file's first ~10 lines carry a status block:

- **Status** — one of: `active`, `partial landing`, `paused`, `complete`, `historical`.
- **Scope** — one sentence on what the track owns.
- **Last touched** — date or commit; lets a new session judge staleness.
- **Companion dossier** (optional) — pointer into `docs/handoffs/` for the most recent session capsule.

Beyond the header, plan files document **design** — controls, phases, threat models, milestone definitions. They do **not** maintain an inline issue list. Issues live in GitHub; `gh issue list` is the live view. Plan files may name specific issue numbers when explaining design intent, but the canonical state of any ticket is GitHub.

## Dossiers vs. plans

- **Plans** (this directory) describe intent — what we mean to do and why.
- **Dossiers** (`docs/handoffs/<date>-<topic>.md`) describe what happened in a session — what landed, what's queued, what surprised us. Each dossier names the track(s) it belongs to in its first paragraph; the back-reference goes dossier → plan, not plan → dossier, so plans don't drift as dossiers accumulate.

## Adding a track

Drop a new `<topic>-plan.md` here with the status block above. No registration step. The directory listing is the registry.
