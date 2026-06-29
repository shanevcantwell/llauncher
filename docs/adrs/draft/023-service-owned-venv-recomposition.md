# ADR-023: Service-Owned Venv Recomposition (Re-Coupling Durable References to Their Venv Lifecycle)

**Status:** `proposed`
**Date:** 2026-06-28
**Related:** **amends the `ExecStart` mechanism of** ADR-018 (agent system
service) and ADR-022 (UI `systemd --user` service). Does **not** revert either:
ADR-022's per-operator supervision model and ADR-018's system-service model both
stand; this ADR adds the missing guarantee that the venv each `ExecStart`
resolves into exists. Touches the deliberate shared-install decision encoded in
`scripts/systemd/install-cli.sh` (untracked; tracked as part of ADR-022's
downstream phase).

> This is a **ratification surface** (`auto:draft`), not an implemented decision.
> Status is `proposed` (the repo's `draft/` folder = "not yet ratified",
> README:11; same posture ADR-022 used). It records the **decision and its one
> open fork**; the build is a separate `auto:fix`. The bidirectional amendment
> notes on ADR-018 / ADR-022 and the README index row take effect **on
> ratification**, not on this draft — this draft mutates **no other file**.

---

## Context

Both llauncher long-running services resolve their `ExecStart` into a Python
virtualenv, and the operator's stated disk-hygiene policy treats venvs as
**non-durable** — freely reaped by a disk-space sweep and recomposable from the
git-tracked source. Today nothing re-couples those two facts: a sweep that
deletes a venv leaves a systemd-managed service that will not restart, with no
durable record that it should. The two services are **asymmetric in every
dimension that matters** (privilege scope, venv location, venv build mechanism,
recompose source), so there is no single shared fix.

**Agent (system unit, root-installed, runs as `User=llauncher`):**
- `ExecStart=@VENV_BIN@/llauncher-agent`
  (`scripts/systemd/llauncher-agent.service.system.in:38`), with
  `WorkingDirectory=@PROJECT_DIR@` (`:39`).
- `install.sh` renders `@VENV_BIN@ = $PROJECT_DIR/.venv/bin`
  (`scripts/systemd/install.sh:28`, substituted at `:209`). So the **system
  service depends on a developer working-tree `.venv`** — not an isolated
  system tree.
- **The documented recompose command is disabled.** `install.sh:113` tells the
  operator to "Run `./scripts/run.sh install` first to create the venv," but
  `run.sh install` was **deliberately disabled** (`scripts/run.sh:44`, issue
  #154) because it installed into a disconnected repo-local `.venv`. So the
  preflight at `install.sh:111` points at a dead command; venv (re)creation is
  currently an undocumented manual `pip install -e ".[ui]"`. **There is no
  working, named recompose path for the agent venv today.** This is a latent
  defect this ADR must close, not work around.

**UI (per-operator `systemd --user` unit, runs as operator uid):**
- `ExecStart=/usr/local/bin/llauncher-ui`
  (`scripts/systemd/llauncher-ui.service.user.in:41`), a symlink placed by
  `install-cli.sh:53` (`ln -sfn /opt/llauncher/venv/bin/llauncher-ui …`).
- `/opt/llauncher/venv` is a **root-owned, dedicated, all-accounts** venv built
  from the **public git REF** (`pip install "llauncher[ui] @ git+…@$REF"`,
  `install-cli.sh:27,45-48`), deliberately decoupled from any dev checkout. It
  is made world read/execute (`chmod -R a+rX`, `install-cli.sh:57`) but **only
  root can write/recompose it**.

**Recompose-source reality (verified against `origin/main`).** There is **no
dependency lockfile** anywhere in the tree — no `uv.lock`, `poetry.lock`, or
`requirements*.txt`. (`llauncher/core/lockfile.py` is the **process PID
lockfile**, unrelated.) The only dependency manifest is `pyproject.toml`, whose
dependencies are `>=` **floors**, not pins. Consequently a recompose resolves
versions **fresh** each time and is **not bit-reproducible**: the venv rebuilt
after a sweep can differ from the one that was running. Any ensure mechanism
proposed here must build from `pyproject.toml` (agent: editable local install;
UI: pinned `$REF` via `install-cli.sh`) — **a lockfile-hash staleness check is
not available because the artifact does not exist.**

## Decision

### The invariant (first-class rule, `VENV-OWNED-OR-GUARANTEED`)

> **A durable reference into a venv must own — or guarantee — that venv's
> recomposition, within the same privilege scope that holds the reference.**
> The git-tracked manifest (`pyproject.toml`) is the source of truth; the venv
> is a derived, throwaway materialization (PEP 405: venvs are non-relocatable —
> rebuilt, never moved). The anti-pattern this rule forbids is a
> **lifecycle-decoupled reference**: a long-lived pointer (a systemd unit, a
> `/usr/local/bin` symlink) into a venv that something else (the disk sweep)
> reaps without knowing the pointer exists. Single-owner installers (pipx,
> distro packages) point into venvs legitimately *precisely because one manager
> owns both ends*; this rule re-establishes that single-owner property for both
> llauncher services. **Recompose lives where the venv's write permission
> lives** — root scope for `/opt` and the dev tree, operator scope for any
> per-operator venv. (Family: `PARSE-AT-THE-DOOR` — guarantee or fail loud at
> the door; never start a degraded service.)

### Mechanism (per scope)

1. **Agent (system scope).** Add a root-privileged ensure step that recomposes
   `$PROJECT_DIR/.venv` from `$PROJECT_DIR/pyproject.toml` (editable install)
   **if and only if** the venv's entry point is missing, then close the
   `run.sh install`-disabled gap by giving `install.sh` a real, named recompose
   command to call (replacing the dead `install.sh:113` pointer). The ensure
   step runs as **root** (the agent's `User=llauncher` cannot be relied on to
   own the dev tree), via either `ExecStartPre=+…` or a `Requires=`/`After=`
   oneshot unit — see Alternatives. On recompose failure it **exits nonzero so
   the unit enters `failed`**; it never starts a half-built venv.

2. **UI / CLI (shared root scope).** The legitimate single owner of
   `/opt/llauncher/venv` is `install-cli.sh` (root). Re-couple **in root scope**
   by promoting its existence-check-and-build logic into a **system oneshot
   ensure unit** (`llauncher-cli-ensure-venv.service`, root) that rebuilds the
   shared venv and re-places the `/usr/local/bin` symlinks when missing. The
   per-operator user UI unit — which **cannot** write `/opt` and **cannot**
   `Requires=`/`After=` a system unit (cross-scope forbidden) — gets a
   **detect-and-fail-loud** `ExecStartPre` backstop: if
   `/opt/llauncher/venv/bin/llauncher-ui` is absent, it exits nonzero and the
   unit fails with a journal line naming the remediation
   (`sudo bash scripts/systemd/install-cli.sh`). The operator UI never attempts
   to recompose a root tree.

3. **Existence-check, not staleness.** Both ensure steps key on **presence of
   the entry point**, not a content hash — a lockfile hash is unavailable
   (see Context). Pin/version currency for the UI is governed by `$REF`
   (`install-cli.sh:23`); for the agent, by the editable checkout. Introducing a
   real lockfile for reproducible recompose is deferred to an Open Question.

4. **The `/usr/local/bin/llauncher-ui` symlink survives.** It points into a
   root-owned venv while the UI runs as operator — this ownership *asymmetry is
   correct and intended*: read/execute is world-granted (`install-cli.sh:57`),
   and only **write/recompose** is root-only, which is exactly why recompose
   must live in root scope (per the invariant). The symlink is re-placed by the
   system ensure unit (decision 2), so it is guaranteed alongside the venv. It
   is **not** replaced by a wrapper.

## Rationale

### Positive Consequences

- **Each service guarantees its own venv within its own privilege scope** — the
  one thing a single shared ensure unit cannot do, given the cross-scope
  asymmetry.
- **Fail-loud, never degrade.** A failed recompose surfaces as a `failed`
  systemd unit with a remediation line in journald — honoring
  `PARSE-AT-THE-DOOR` / no-trust-and-degrade.
- **Closes the dead-recompose-command defect** (`install.sh:113` → disabled
  `run.sh:44`) as a side effect, replacing it with a real path.
- **Preserves the deliberate shared-install model** (`install-cli.sh`): the UI
  still consumes one root-owned `/opt` venv shared across accounts; the sweep no
  longer breaks it because root scope now re-guarantees it.

### Negative Consequences

- **First-start-after-reap latency.** A recompose is a network-bound `pip
  install` (tens of seconds to minutes). Inline `ExecStartPre` risks exceeding
  systemd's default `TimeoutStartSec=90s`; a oneshot ensure unit isolates that
  latency and is the safer shape (see Alternatives).
- **Non-reproducible recompose.** With only `>=` floors, the rebuilt venv may
  carry newer transitive deps than were running — a silent drift vector. See
  Risk and the lockfile Open Question.
- **A fourth/fifth unit artifact** (agent ensure + CLI ensure) to maintain
  alongside the three service templates.

## Alternatives Considered

### Mechanism A: inline `ExecStartPre=` recompose vs. a `*-ensure-venv` oneshot

`ExecStartPre=+/…/ensure-venv` (the `+` runs it as root regardless of `User=`)
is the fewest moving parts. **Rejected as the primary shape because** the
recompose's minutes-long latency counts against the service's `TimeoutStartSec`,
and a recompose failure muddies the service's own failed-state diagnostics.
**Chosen: a dedicated oneshot ensure unit** (`Type=oneshot`,
`RemainAfterExit=yes`, its own generous `TimeoutStartSec`) that the agent unit
`Requires=`/`After=`; the failure is attributable and the timeout is isolated.
(For the UI, the *system* ensure unit cannot be a cross-scope dependency of the
*user* unit — so the user side keeps a thin detect-and-fail-loud `ExecStartPre`
as backstop only, not as the recompose engine.)

### Mechanism B: existence-check only vs. lockfile-hash staleness detection

Staleness detection (rebuild when the manifest hash changes) was considered and
**rejected as currently infeasible**: it presupposes a lockfile, which the repo
does not have (Context). Existence-check is the only deterministic signal
available today. Recorded so it is reconsidered if/when a lockfile lands.

### Scope fork — the operator's decision (UI venv ownership)

This is the one genuine fork and is surfaced as an Open Question, not silently
chosen:

- **Fork B-shared (recommended):** UI keeps the shared, root-owned
  `/opt/llauncher/venv`; re-coupling lives in a **system** ensure unit; the user
  unit only detects-and-fails-loud. **Pro:** preserves `install-cli.sh`'s
  deliberate one-install-for-all-accounts model; one streamlit copy. **Con:** the
  UI cannot self-heal autonomously — recompose remains a root action the operator
  triggers (or a root timer/path unit performs).
- **Fork B-peruser (rejected as default, viable alternative):** give the UI its
  own operator-owned venv (e.g. under `$XDG_DATA_HOME/llauncher/venv`, or pipx),
  so a **user-scope** `ExecStartPre` recomposes it autonomously. **Pro:** fully
  self-healing within operator scope; cleanest satisfaction of the invariant for
  the UI. **Con:** abandons the shared `/opt` install for the UI, duplicates
  streamlit per operator, and forks the UI's install path away from
  `install-cli.sh`. **Rejected as default** because it discards a deliberate
  prior decision for autonomy the operator may not value; promoted to an Open
  Question because an operator who *does* value per-operator autonomy should pick
  it.

## Phased Roadmap

Two independently landable phases (agent-side and UI-side), plus an optional
reproducibility phase. Each is a downstream `auto:fix`; step granularity is
`plan`'s job.

### Phase A — Agent venv guarantee (system scope). Independent; unblocks today's latent defect.
- **Delivers:** a named recompose command (replacing the disabled
  `run.sh install` / dead `install.sh:113` pointer) that rebuilds
  `$PROJECT_DIR/.venv` from `pyproject.toml`; a root oneshot
  `llauncher-agent-ensure-venv.service` the agent unit `Requires=`/`After=`
  (`llauncher-agent.service.system.in:38-39`, `install.sh:28,111-113,209`).
- **Depends on:** nothing — the agent's `.venv` and `pyproject.toml` are in the
  one tree; recompose is root-scoped and self-contained.
- **Verifiable when it lands:** delete `$PROJECT_DIR/.venv`, `systemctl restart
  llauncher-agent` → venv recomposes and the unit reaches `active`; corrupt
  `pyproject.toml` → the ensure unit `failed`s and the agent does **not** start
  (fail-loud).

### Phase B — UI/CLI venv guarantee (shared root scope). Gated on the scope fork.
- **Delivers (Fork B-shared):** a root oneshot
  `llauncher-cli-ensure-venv.service` factored from `install-cli.sh:45-57` that
  rebuilds `/opt/llauncher/venv` and re-places the `/usr/local/bin` symlinks when
  missing; a detect-and-fail-loud `ExecStartPre` added to
  `llauncher-ui.service.user.in:41` with a remediation journal line.
  *(Fork B-peruser swaps the system ensure unit for a user-scope per-operator
  venv + user `ExecStartPre`.)*
- **Depends on:** ratification of the scope fork (Open Question 1); tracking of
  the currently-untracked `install-cli.sh` (already in ADR-022's downstream
  phase).
- **Verifiable when it lands:** delete `/opt/llauncher/venv` → the system ensure
  unit recomposes it and the symlink resolves; with the ensure unit disabled, the
  user UI unit `failed`s with the `install-cli.sh` remediation line rather than
  starting broken.

### Phase C (optional) — Reproducible recompose. Gated on the lockfile Open Question.
- **Delivers:** a committed dependency lockfile (e.g. `uv.lock`) so both ensure
  steps rebuild a bit-reproducible venv, and staleness detection (Mechanism B)
  becomes possible.
- **Depends on:** Open Question 2; choice of tool (`uv`/`pip-tools`).
- **Verifiable when it lands:** two recomposes on different days produce
  identical installed version sets.

## Risk and Observability

- **Silent dependency drift (primary risk).** `>=` floors mean a post-sweep
  recompose can pull newer transitive deps than were running — a behavior change
  with no diff. *Surfaced, not solved here* — Phase C / Open Question 2.
  Mitigation until then: `$REF`-pinning bounds the UI; the agent is bounded only
  by the checkout's installed set.
- **First-start latency / boot impact.** Recompose under
  `network-online.target` (units already `After=network-online.target`) on a cold
  boot after a reap delays `active`. Oneshot isolation + a generous
  `TimeoutStartSec` keeps this legible rather than a mysterious start-timeout.
- **Recompose failure is observable and fail-closed.** A failed ensure unit is
  visible via `systemctl is-failed llauncher-agent-ensure-venv` /
  `journalctl -u …`; the dependent service stays down. The UI backstop emits a
  single remediation line to `journalctl --user -u llauncher-ui`. No path starts
  a partially-built venv.
- **Tech-debt vector closed:** the dead `run.sh install` pointer
  (`install.sh:113` → `run.sh:44`) stops being a trap for the next operator.
- **Security note:** the ensure units run `pip install` from the network as root
  (agent: from the local tree; UI: from `git+https://…@$REF`). This is the same
  trust surface `install-cli.sh` already has; no new exposure, but it means a
  recompose is a network-trust event, not a pure local rebuild — relevant to the
  lockfile decision (a hash-pinned lock narrows it).

## Open Questions

- [ ] **OQ1 — UI venv ownership fork (must ratify before Phase B).** Fork
  B-shared (system ensure unit, recommended) or Fork B-peruser (per-operator
  user-owned venv)? **Resolution:** operator decision at ratification of this
  ADR; determines Phase B's shape.
- [ ] **OQ2 — Introduce a dependency lockfile?** Without one, recompose is
  non-reproducible and staleness detection is impossible. **Resolution:** decide
  alongside Phase C; if yes, pick `uv`/`pip-tools` and commit the lock.
- [ ] **OQ3 — Eager re-heal vs. lazy on-restart.** Should a `.path` unit watch
  the venv and recompose immediately on deletion (eager), or only recompose on
  the next service (re)start (lazy)? **Resolution:** decided during the Phase A/B
  `auto:fix`; lazy is the cheaper default.
- [ ] **OQ4 — Ensure-unit `TimeoutStartSec` value.** What recompose ceiling is
  acceptable before treating it as failed? **Resolution:** measured during the
  build, mirroring the crash-loop-guard-by-measurement pattern ADR-022 §Open
  Questions used for `Restart=`.

## Supersession / Amendment Relationships

**Amends (does not supersede):** the **`ExecStart` venv mechanism** of ADR-018
(`accepted/018-llauncher-system-service.md`) and ADR-022
(`accepted/022-llauncher-ui-user-service.md`). Both ADRs' core decisions stand;
ADR-022 is **completed, not corrected** — it chose the right supervision model
but was silent on venv durability under the operator's reap policy. This ADR
fills that gap.

**Superseded by:** TBD.

**On ratification (deferred — applied at acceptance, not on this draft):**
- Add an amendment note to `accepted/018-llauncher-system-service.md` and
  `accepted/022-llauncher-ui-user-service.md` pointing to ADR-023 as governing
  their `ExecStart` venv guarantee.
- Add the README index row (`docs/adrs/README.md`) and `git mv` this file from
  `draft/` to `accepted/` (or `completed/` once Phases A+B land), setting Status
  accordingly.

## References

- `scripts/systemd/llauncher-agent.service.system.in:38-39` — agent
  `ExecStart=@VENV_BIN@/llauncher-agent`, `WorkingDirectory=@PROJECT_DIR@`.
- `scripts/systemd/install.sh:28,111-113,209` — `VENV_BIN=$PROJECT_DIR/.venv/bin`;
  preflight pointing at the disabled command; `@VENV_BIN@` substitution.
- `scripts/run.sh:32,40-48` — `python3 -m venv .venv`; `run.sh install`
  **disabled** (#154).
- `scripts/systemd/llauncher-ui.service.user.in:41` — UI
  `ExecStart=/usr/local/bin/llauncher-ui`.
- `scripts/systemd/install-cli.sh:23,26-27,45-48,53,57` — `$REF` pin; root-owned
  `/opt/llauncher/venv` from `git+https…@$REF`; `ln -sfn` symlinks; `chmod a+rX`.
- `pyproject.toml:6-16,28-33` — `>=` dependency floors (no pins); console-script
  entry points. **No lockfile present in the tree** (verified).
- `docs/adrs/accepted/018-llauncher-system-service.md`,
  `docs/adrs/accepted/022-llauncher-ui-user-service.md` — the two ADRs amended.
- `docs/adrs/README.md:11` — `draft/` = "not yet ratified" status convention.
- PEP 405 — virtual environments are non-relocatable (rebuilt, not moved).

---
