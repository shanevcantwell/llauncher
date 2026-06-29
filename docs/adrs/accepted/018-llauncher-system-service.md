# ADR-018: llauncher as a System Service

**Status:** Accepted
**Date:** 2026-06-25

**Superseded in part by [ADR-022](022-llauncher-ui-user-service.md)** (2026-06-28): the UI control-plane decision (hand-launched → per-operator systemd --user) is now governed by ADR-022. All other provisions of this ADR stand.

**Amended by [ADR-023](023-service-owned-venv-recomposition.md)** (2026-06-28): the agent unit's `ExecStart`/venv mechanism is amended — the `$PROJECT_DIR/.venv` that `ExecStart=@VENV_BIN@/llauncher-agent` resolves into is now guaranteed by a **root** `*-ensure-venv` oneshot unit that recomposes it from `pyproject.toml` (fail-loud on failure). All other provisions of this ADR stand.

**Supersedes:** ADR-009 (Symmetric Hub/Spoke Topology) — only its *deployment
posture* (local agent as a user-started peer). The symmetric hub/spoke
*topology* of ADR-009 still holds; this ADR replaces only the framing that the
agent is a deliberately-user-started peer rather than a privileged daemon.

**Resolves:** #191 (state/logs outside any user home).
**References:** #194 (this `--system` install mode), #196 (host provisioning
lane), #191.

## Context

ADR-009 established fully symmetric nodes and, in passing (and in the
`llauncher-agent.service.in` header), framed the local agent as "a peer started
deliberately by the user, not a privileged daemon," with all state under
`$HOME` (`~/.config/llauncher`, `~/.llauncher`, `LAUNCHER_RUN_DIR`). The
systemd installer therefore shipped a `--user` unit only.

That posture breaks down for the multiuser host:

- **A second local user must be able to drive the runtime.** A separate,
  unprivileged account (e.g. `claude`) needs to start/stop/swap servers. It does
  so through its **own tokenless, in-process MCP server** — the MCP/CLI plane
  crosses no network boundary and needs no token (see [`../../auth.md`](../../auth.md)).
  So the real enabler is **shared access to state**: lockfiles, `config.json`,
  the run dir. With a per-user install that state lives in the *operator's*
  private home (mode 0700), unreachable by the second user. The blocker is
  state locality, not the token.
- **State trapped in a home dir.** Logs, audit records, the run dir, the node
  registry, and the token all sit under one human's `$HOME`. That couples the
  service's lifecycle and visibility to a login session and a specific user
  account, and is exactly what #191 flags as wrong.
- **No clean boot/independence story.** `systemd --user` needs lingering
  enabled and is scoped to a user manager, not a real boot-time system service.

## Decision

### Option Chosen: A dedicated `llauncher` system account + shared state lane

Add a `--system` install mode (alongside the retained `--user` default) that
installs a real system service:

- **Dedicated account.** The unit runs as `User=llauncher`, `Group=inference`
  — a non-login service account, not any human's login. No operator home is in
  the trust path.
- **State outside any home.** `Environment=LAUNCHER_STATE_DIR=/var/lib/llauncher`
  relocates config, node registry, token, run dir, audit, and logs to
  `/var/lib/llauncher` (the single relocation knob; Python support lands in
  parallel PR #197). This directly resolves #191.
- **Group-readable secrets for the HTTP plane.** The env file
  (`/var/lib/llauncher/agent.env`) and the mirrored token
  (`/var/lib/llauncher/agent.token`) are written mode `0640`, group
  `inference`. This serves the *other* plane: the Streamlit UI and any
  remote/HTTP clients, which cross the agent's network boundary and so **do**
  need the `X-Api-Key` token (see [`../../auth.md`](../../auth.md)).
  Group-readability lets those in-group consumers read the token in place, with
  no secret duplicated into a second home. (The tokenless local MCP/CLI plane
  above does not touch this.) (UI control plane superseded by ADR-022.)
- **Boot-time service.** `[Install] WantedBy=multi-user.target`; no
  `enable-linger` step.

### Separation of concerns: installer vs. host provisioning

The systemd installer (`scripts/systemd/install.sh --system`) only *renders the
unit and writes config*. It does **not** create the `llauncher` user, the
`inference` group, `/var/lib/llauncher`, ACLs, or polkit rules — that is the
host provisioning script's job (harness-tools
`claude/host-config/setup-inference-lane.sh`, tracked under #196). The
installer's `--system` preflight requires root and asserts that provisioning
already exists, erroring out and pointing the operator at the host script
otherwise. This keeps the privileged, one-time host setup in one place and the
repeatable per-clone render in another.

### `--user` mode is retained

Single-user installs are still first-class. With no flag, the installer behaves
exactly as before: `~/.config/systemd/user` unit, state under `$HOME`,
`systemctl --user`. `--system` is strictly additive.

## Consequences

**Positive:**

- A non-admin agent account can drive the runtime via the group-readable token
  with no secret copying.
- All mutable state lives in one well-known, home-independent location
  (`/var/lib/llauncher`), resolving #191 and simplifying backup/audit.
- Real boot-time service semantics; no lingering hack.
- Clear ownership split: host script provisions identity/dirs once; the
  installer renders config per clone, idempotently.

**Negative:**

- Two unit templates to maintain (`*.service.in` and `*.service.system.in`).
- `--system` depends on out-of-band host provisioning (#196); the installer
  fails loudly if it is missing, which is correct but adds a setup step.
- The state-dir relocation is only fully effective once `LAUNCHER_STATE_DIR`
  support lands in the Python layer (#197); on this branch the env var is set
  but the code honoring it is pending.

## Supersession

This ADR **supersedes ADR-009's deployment posture.** ADR-009's Status is set
to `Superseded by ADR-018` and the document is moved to `superseded/`. The
symmetric hub/spoke *topology* decisions of ADR-009 (config sovereignty, per-
node registry, self-loop dispatch, identity resolution) remain in force and are
not re-litigated here — only the "user-started peer, state under `$HOME`"
framing is replaced by "dedicated system service, state under
`/var/lib/llauncher`."
