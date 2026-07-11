# llauncher — agent instructions

**Constitutions, by reference** (read before structural work; cite handles in
PRs and commits):

- `/srv/dev/shanevcantwell/operating-doctrine/ground-physics/CODE_CONSTITUTION.md`
  — ecosystem rules. The *why* is in `GROUND_PHYSICS.md` beside it; the
  alignment plan in `ALIGNMENT_ROADMAP.md`. (Canonical home as of the
  2026-06-22 migration; the old `design-docs/ecosystem-ground-physics/` path
  is a `MOVED.md` tombstone.)
- `docs/ARCHITECTURE.md` — layer map, forbidden import edges, and the audited
  conformance rules against them.

## llauncher's position in the ecosystem physics

- **llauncher is the mint** (`ONE-MINT`): `ModelConfig.name` is the single
  authority for local-model identity across the ecosystem. Everything else —
  port, adapter, log filename, process title, sanitized string — is an
  *envelope*. Envelope defects (e.g. log-filename sanitization collisions,
  #146/#63) are fixed in envelope space, never by bending the name.
- **Keystone obligation** (`EMIT-CANONICAL`) — **satisfied**: the wire reports
  the canonical name. llauncher starts every server with
  `--alias = <ModelConfig.name>` (`core/process.py::build_command`) and keeps
  `--alias` on `DENIED_EXTRA_ARG_FLAGS` so no config can override the minted
  identity (#120/#87/#10, all closed). Verified live 2026-06-19: the resident
  embedding server reports `embeddinggemma-300M-F32-pooled` on `/v1/models` —
  a canonical name, not a path/port-derived string. This was the ecosystem's
  highest-leverage fix (`ALIGNMENT_ROADMAP.md` Phase 1); with it landed,
  downstream re-stringification dialects no longer have llauncher debt as their
  excuse. Audited conformance: `docs/ARCHITECTURE.md` rule 5.

## Local rule: no backwards-compatibility shims

Pre-1.0, single operator, every consumer in-ecosystem. When a persisted shape
changes, **migrate deterministically at the door, once** (rewrite in place),
or **fail loud**. Never dual-parse two shapes of the same artifact; never
trust-and-degrade on an unrecognized one (`PARSE-AT-THE-DOOR`).

- Observed anchors (no invariant without a violation): ADR-003's original
  opt-in-auth compat posture, removed by the security cohort (PR #75/#87);
  ADR-017 first draft's bare-string `node_tokens.json` dual-parse, caught in
  review 2026-06-10.
- Enforcement surface: PR review — a diff that parses two shapes of one
  artifact, or justifies itself as "backcompat" for a persisted format, fails
  review. Prose-backed until a CI gate exists; treat as provisional per
  `CODE_CONSTITUTION.md` §Use.
- Not a shim, unaffected: default-off / opt-in *feature* posture
  (security stance, ADR-003 / ADR-017).

## Autonomy contract (operator-ratified 2026-06-10)

- **Tier labels** are the standing dispatch contract:
  `auto:fix` — agent end-to-end: branch → fix + tests profiled against the
  changed behavior → gates → PR → merge on green after a dispatched review.
  `auto:draft` — agent produces the artifact (ADR, design, schema); operator
  ratifies before implementation begins.
  `user:gate` — operator hands or hardware required.
- **Gates before any merge:** full pytest; non-UI coverage ≥93%
  (`--cov-fail-under=93`); coverage profile maximized over changed paths;
  dispatched code review.
- **Merge mechanics + ground close:** merges land via `gh pr merge --squash --delete-branch`
  (deletes remote + local head, returns the checkout to `main`). A session that touched the
  repo ends with the shared checkout clean on `main` and its scratch worktrees removed or
  banked; session-start is never where the previous session's ground gets dispositioned.
- **Runtime verification:** the live GPU runtime may be driven freely
  (start/stop/swap real servers), including alongside the resident
  :8081/:8082 services.
- **Issue hygiene:** full agent authority — label, milestone, acceptance
  criteria, follow-up filing, close-on-merge.
- **The bounce rule:** an `auto:fix` that uncovers a design decision mid-fix
  surfaces it as a named signal and bounces to `auto:draft` — never absorbs
  it silently.
- **Parked:** Windows-deployment quartet (#127/#128/#130/#132) until the
  operator next touches the Windows box.
