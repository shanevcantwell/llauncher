# ADR-LLNCH-022: llauncher UI under Operator-Scoped `systemd --user` Control

**Status:** accepted
**Accepted:** 2026-06-28
**Date:** 2026-06-28
**Related:** ADR-LLNCH-018 (System Service) — this narrows ADR-LLNCH-018's UI posture;
ADR-LLNCH-003 / ADR-LLNCH-017 (agent auth, token plane).
**Prerequisites:** No code gate (see §Open Questions — corrected).

**Amended by [ADR-LLNCH-023](adr-llnch-023-service-owned-venv-recomposition.md)** (2026-06-28): the UI unit's `ExecStart`/venv mechanism is amended — the shared root-owned `/opt/llauncher/venv` that `ExecStart=/usr/local/bin/llauncher-ui` resolves through is now guaranteed by a **system** `*-ensure-venv` oneshot unit (root) that recomposes it and re-places the `/usr/local/bin` symlinks, with the `--user` UI unit carrying only a detect-and-**fail-loud** `ExecStartPre` backstop (it cannot recompose cross-scope). ADR-LLNCH-023's OQ1 was resolved as the shared-`/opt` fork. All other provisions of this ADR stand.

> This is a **ratification surface**, not an implemented decision. Status is
> `proposed` (the repo's `draft/` folder = "not yet ratified", README:11). It
> records the **decision only**; the build is a separate `auto:fix` (see
> §Downstream Phase). The supersession of ADR-LLNCH-018's UI posture and the doc edit
> to `run-as-a-service.md` take effect **on ratification**, not on this draft.

---

## Context

llauncher exposes two long-running processes with opposite roles:

- The **agent** (`llauncher-agent`, console script `llauncher.agent:main`,
  pyproject.toml:32) is machine infrastructure: it binds `0.0.0.0`
  (`run-as-a-service.md:36`) on port `8765` (`auth.md:15`), other nodes depend
  on it, and ADR-LLNCH-018 correctly made it a **system** service — `User=llauncher`,
  `Group=inference`, state under `/var/lib/llauncher`, `WantedBy=multi-user.target`
  (`scripts/systemd/llauncher-agent.service.system.in`).
- The **UI** (`llauncher-ui`, console script `llauncher.ui.launch:main`,
  pyproject.toml:33) is a Streamlit front-end. It binds **loopback by default**
  (`DEFAULT_UI_HOST = "127.0.0.1"`, `llauncher/ui/launch.py:27`; Streamlit's
  default port `8501`), ships with no built-in auth (launch.py:23-26), and
  `main()` merely shells out to `streamlit run app.py` (launch.py:58-71). It
  only matters when an operator is looking at it.

**Current posture: the UI is hand-launched, by design.** This is stated
explicitly in operations docs — *"the agent is the daemon piece of llauncher;
the UI is interactive and is not service-managed"* (`run-as-a-service.md:3-4`)
and *"The UI process (Streamlit) is separate from the systemd service"*
(`scripts/systemd/install.sh:55`). No `llauncher-ui.service` unit exists
anywhere in the tree (verified: no `*ui*.service*` file present). ADR-LLNCH-018 itself
contains **no literal "UI is not service-managed" sentence**; it holds that
posture only *implicitly*, by treating the UI purely as a token-consuming HTTP
client (`018-llauncher-system-service.md:57-62`) while making only the agent a
managed service. The explicit prose is in `run-as-a-service.md` / `install.sh`.

Two forces make the hand-launch posture now wrong:

1. **Post-delegation-fix, the UI owns nothing.** Commit `29cb2ee` ("route UI
   stop through delegation gate (ADR-LLNCH-018 cross-uid)") completed the move of all
   privileged action out of the UI: it delegates every lifecycle operation to
   the agent and owns no state of its own.
2. **The multiuser migration** (ADR-LLNCH-018) gives each operator their own login on
   a shared host. A localhost-only, no-auth dashboard is inherently a *per-login*
   surface, but it has no lifecycle management at all — no clean restart, no
   status, no journald, no revival on crash.

The question this ADR settles: **what is the correct supervision model for the
UI** — given that it is an unprivileged, loopback, per-operator front-end, not
machine infrastructure?

## Decision

**Run the llauncher UI as a per-operator `systemd --user` unit** —
operator-scoped, unprivileged, session-managed. Not a system unit; not
hand-launched.

1. **Unit location (per operator):** `~/.config/systemd/user/llauncher-ui.service`.
2. **ExecStart:** `/usr/local/bin/llauncher-ui` — the console-script symlink
   placed by `scripts/systemd/install-cli.sh` (the `/usr/local/bin` placement is
   fixed by the operator). `llauncher-ui` resolves to `llauncher.ui.launch:main`
   (pyproject.toml:33), which launches `streamlit run app.py` bound to loopback.
3. **Control plane:** `systemctl --user {restart,status} llauncher-ui`; logs via
   `journalctl --user -u llauncher-ui`. The `user@<uid>.service` manager already
   runs for each logged-in operator, so no sudo enters the restart loop.
4. **Autostart / survive-logout (optional):** `loginctl enable-linger "$USER"`
   plus `systemctl --user enable --now llauncher-ui`, so the dashboard can start
   at boot and persist across logout for operators who want it.
5. **No service account.** The unit runs as the operator's own uid. It reads the
   agent API token from the mirrored `agent.token` file (mode `0640`, owner
   `root`, group `inference` — `install.sh:73-74,198`; ADR-LLNCH-018:57-59) purely via
   the operator's `inference` group membership. No secret is copied into a
   second home; no per-UI account is created.

## Rationale

The agent/UI split is the whole argument. ADR-LLNCH-018 correctly chose a **system**
service for the agent *because* the agent is network-facing machine
infrastructure that other nodes depend on and that must exist at boot. **None of
those properties hold for the UI**, so none of the reasons for a system unit
transfer to it.

### Positive Consequences

- **Scope matches reality.** A `127.0.0.1`-only front-end that matters only when
  an operator is watching is owned by that operator's session, not by the
  machine. (`launch.py:27`, `auth.md:107`.)
- **Stays unprivileged.** Post-`29cb2ee` the UI owns nothing and delegates
  everything to the agent. A `--user` unit keeps it operator-owned and
  unprivileged; it never acquires a privilege it does not need.
- **Real control without root.** `systemctl --user` restart/status + journald,
  no sudo in the loop — the control plane the hand-launch posture lacks
  entirely.
- **Correct multiuser shape.** Each operator gets their own dashboard on their
  own login — exactly the point of the ADR-LLNCH-018 multiuser migration. There is no
  shared system-wide UI to contend over.
- **Token reuse, no duplication.** Group-`inference` readability of the
  `0640 root:inference` token (already provisioned for ADR-LLNCH-018's HTTP plane) is
  exactly what a `--user` unit needs; the UI authenticates in place.

### Negative Consequences

- **Per-operator setup step.** Each operator who wants autostart runs
  `enable-linger` + `enable --now` once. (A system unit would be "install once",
  but at the cost of being wrong on every other axis.)
- **A third template to maintain** alongside ADR-LLNCH-018's two agent unit templates
  (`*.service.in`, `*.service.system.in`).
- **Two operational preconditions** (both ADR-LLNCH-018-mandated, not new code): the unit
  must set `Environment=LAUNCHER_STATE_DIR=/var/lib/llauncher`, and the operator
  must be in group `inference`. See §Open Questions for the corrected analysis.

## Alternatives Considered

### Option A: System unit (mirror `llauncher-agent.service.system.in`)

Install the UI the same way as the agent — a system unit, dedicated/elevated
ownership, `WantedBy=multi-user.target`.

**Why rejected (the deciding factor):** it flips **both** the scope and the
privilege of a loopback-only front-end for **zero payoff**. It would run at boot
when no one is watching, be shared across operators (the wrong model for a
per-login dashboard), and drag a process that owns nothing toward
root-coordinated infrastructure. *An earlier analysis lazily defaulted to this
by mirroring the agent template — the agent/UI distinction recorded in §Context
is exactly why the mirror instinct is wrong. Recorded here so it is not
re-proposed.*

### Option B: GNOME autostart (`~/.config/autostart/*.desktop`)

A `.desktop` entry launches the UI on login.

**Why rejected:** it provides launch-on-login but **no control surface** — no
clean restart, no `status`, no journald capture, no revival on crash. The
`systemd --user` unit **subsumes** it: it gives the same login-time autostart
(via `enable` / `enable-linger`) *plus* a real control plane (restart/status/
logs/Restart=on-failure). Strictly dominated.

## Open Questions

- [ ] **Prerequisites — no code gate.** Contrary to this ADR's first draft, #130
  is *not* a prerequisite for this decision. The auth path works today:

  - The token mirror #130 asked for already shipped — implemented for the
    duplicate **#131** (since CLOSED, PR #133 / `b5101ef`, extended/hardened
    since). #130 is a likely-stale duplicate of #131 pending only an operator
    runtime probe, not new code.
  - `LAUNCHER_STATE_DIR` is honored in the Python token-read path: **#197 is
    MERGED** (ADR-LLNCH-018's "pending #197" Consequence note predates the merge).
    `core/settings.py` reads `LAUNCHER_STATE_DIR` from the environment at module
    import; `core/agent_token.py::default_token_path()` lazily resolves
    `<LAUNCHER_STATE_DIR>/agent.token`.

  Therefore a `--user` UI unit at the operator uid reads the system agent's
  `0640 root:inference` token **in place** — no mirror, no relocation — given
  two preconditions (both already mandated by ADR-LLNCH-018):

  1. **The unit sets `Environment=LAUNCHER_STATE_DIR=/var/lib/llauncher`.** The
     value is fixed at process start, which is exactly when systemd injects the
     unit env. **This is the one thing the unit template must not omit.**
  2. The operator uid is a member of group `inference` (host provisioning, not
     the installer — ADR-LLNCH-018 §installer-vs-host-provisioning;
     `setup-inference-lane.sh`; #196).

  Both residual risks are operational (group membership + the unit's
  `LAUNCHER_STATE_DIR` env), not code-gated. The downstream `auto:fix` (unit
  template + `install-cli.sh` tracking + installer step) is unblocked.
- [ ] **`Restart=` policy and crash-loop guard** for the unit template — whether
  to mirror the agent's `Restart=on-failure` / `StartLimitBurst` shape.
  **Resolution:** decided during the build `auto:fix`, not here.

## Supersession Relationships

**Supersedes (narrowly):** ADR-LLNCH-018's **UI posture** — the implicit framing of
the UI as a non-service-managed token client. This mirrors ADR-LLNCH-018's own
pattern of superseding only ADR-LLNCH-009's *deployment posture*, not its topology.
ADR-LLNCH-018's **agent** system-service decision, state relocation, and token plane
are **unchanged**.

**Superseded by:** TBD.

**On ratification (not now — these are deferred to acceptance):**

- Add a note to ADR-LLNCH-018 (`accepted/adr-llnch-018-llauncher-system-service.md`) that its UI
  posture is narrowed by ADR-LLNCH-022, and update the README index row.
- Update `docs/operations/run-as-a-service.md:3-4` and the comment at
  `scripts/systemd/install.sh:55`, whose "the UI is not service-managed" prose is
  the *explicit* statement of the posture this ADR reverses (ADR-LLNCH-018 states it
  only implicitly).
- `git mv` this file from `draft/` to `accepted/` and set Status `accepted`.

This draft mutates **no other file**; the bidirectional supersession is recorded
here and applied at ratification, because a `proposed` ADR has not taken effect.

## Downstream Phase (scope boundary — NOT built here)

This ADR records the **decision only**. The implementation is a separate
`auto:fix` (unblocked — see §Open Questions), comprising:

1. A unit template `scripts/systemd/llauncher-ui.service.user.in`
   (`ExecStart=/usr/local/bin/llauncher-ui`, loopback Streamlit, operator uid,
   `Restart=` per the open question).
2. **Tracking the currently-untracked `scripts/systemd/install-cli.sh`** (git
   status: `?? scripts/systemd/install-cli.sh`), which installs `llauncher`,
   `llauncher-mcp`, and `llauncher-ui` into a dedicated `/opt/llauncher/venv` and
   symlinks the console scripts into `/usr/local/bin` (`install-cli.sh`
   `SCRIPTS=(llauncher llauncher-mcp llauncher-ui)`, `ln -sfn` into `$BINDIR`).
3. An installer step that renders the unit into `~/.config/systemd/user/` and
   runs `systemctl --user enable --now llauncher-ui` (optionally guiding
   `loginctl enable-linger`).
4. The doc/ADR edits listed under §Supersession (run-as-a-service.md:3-4,
   install.sh:55, ADR-LLNCH-018, README index).

Step granularity (which function, which edit) is `plan`'s job, downstream of
ratification; sub-problem enumeration with acceptance criteria is
`decompose`'s. This ADR sequences and bounds; it does not implement.

## References

- `docs/adrs/accepted/adr-llnch-018-llauncher-system-service.md` — agent system service;
  token plane (lines 57-62); posture this ADR narrows.
- `llauncher/ui/launch.py:27,58-71` — UI loopback bind; console-script `main()`.
- `pyproject.toml:33` — `llauncher-ui = "llauncher.ui.launch:main"`.
- `scripts/systemd/install-cli.sh` — `/usr/local/bin` symlink installer (untracked).
- `scripts/systemd/install.sh:55,73-74,198` — UI-is-separate comment; `0640`
  group-`inference` token mirror.
- `docs/operations/run-as-a-service.md:3-4` — explicit "UI not service-managed" prose.
- `docs/auth.md:15,107` — UI as token-consuming HTTP client of the agent.
- Issue #131 (CLOSED, PR #133 / `b5101ef`) — Linux token mirror (shipped; #130 is a likely-stale duplicate).
- Issue #197 (MERGED) — `LAUNCHER_STATE_DIR` honored in Python token-read path.
- Issue #196 — operator group `inference` provisioning (`setup-inference-lane.sh`).
- Commit `29cb2ee` — UI delegation gate (UI owns nothing).

---
