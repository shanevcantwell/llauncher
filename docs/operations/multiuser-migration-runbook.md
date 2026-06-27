# Multiuser / systemd Migration Runbook

The single durable record of moving llauncher from a single-user
`systemd --user` agent (running as the operator out of `$HOME`) to a
boot-time **system service** running as a dedicated, unprivileged
`llauncher` account out of `/var/lib/llauncher`, on a multiuser host
where a second account (the `claude` agent) must also drive the runtime.

This runbook distills the scattered session handoffs (see
[`../handoffs/HANDOFF_2026-06-25-multiuser-migration-llauncher-systemd.md`](../handoffs/HANDOFF_2026-06-25-multiuser-migration-llauncher-systemd.md)
and the companion [`../handoffs/BUGFIX_ROADMAP_2026-06-25.md`](../handoffs/BUGFIX_ROADMAP_2026-06-25.md))
into one place. For the decision rationale see
[ADR-018](../adrs/accepted/018-llauncher-system-service.md); for the
day-to-day install/operate surface see
[`run-as-a-service.md`](run-as-a-service.md); for the token/auth model
across both planes see [`../auth.md`](../auth.md).

**Status as of 2026-06-26: LIVE.** `llauncher-agent.service` is active
as a system unit, `User=llauncher`, state at `/var/lib/llauncher`
(`2770 llauncher:inference`). The code-side enablers (#197
`LAUNCHER_STATE_DIR`, #198 `--system` installer) are merged.

---

## 1. The decision (Option B1)

llauncher runs as a **system unit under a dedicated `llauncher` service
account**, not as a `systemd --user` unit owned by a human login. This is
ADR-018's "Option B1" and it **supersedes ADR-009's deployment posture**
(local agent as a deliberately-user-started peer). ADR-009's symmetric
hub/spoke *topology* still holds; only the "user-started peer, state under
`$HOME`" framing is replaced.

Why B1, and why it is viable now:

- **A second local user must drive the runtime.** The `claude` agent
  account needs to start/stop/swap servers. It does so through its **own
  tokenless, in-process MCP server** — that plane crosses no network
  boundary and needs no token. The real enabler is therefore **shared
  access to state** (lockfiles, `config.json`, the run dir), which a
  per-user `0700` `$HOME` install cannot provide. The blocker was state
  *locality*, not the token (see [`../auth.md`](../auth.md) "Multiuser /
  system mode").
- **State must leave any human's `$HOME`.** A single relocation knob,
  `LAUNCHER_STATE_DIR=/var/lib/llauncher`, moves config, node registry,
  token, run dir, audit, and logs out of `$HOME`. This resolves #191.
- **Real boot-time semantics.** `[Install] WantedBy=multi-user.target`;
  no `enable-linger` hack, no scoping to a user manager.
- **No redeploy tax.** B1 became viable once the migration moved the
  executables into `/srv/dev` (built in place), so there is no
  copy-into-home step to redo.

### Ownership split: in-repo installer vs. host provisioning

- The **in-repo installer** (`scripts/systemd/install.sh --system`) only
  *renders the unit and writes config*. Its `--system` preflight requires
  root and asserts provisioning already exists, erroring out and pointing
  at the host script otherwise.
- **Host provisioning** (identity, dirs, ACLs, polkit) is a separate,
  privileged, one-time concern that lives in **harness-tools**, not in
  this repo. See §3.

---

## 2. Live ground truth (verified 2026-06-26)

| Fact | Value |
|------|-------|
| Service | `llauncher-agent.service`, **active**, system unit |
| Runs as | `User=llauncher`, `Group=inference` |
| State dir | `/var/lib/llauncher`, mode `2770 llauncher:inference` |
| Log dir | `/var/log/llauncher`, `2750 llauncher:inference` |
| Agent bind | `0.0.0.0:8765`, `X-Api-Key` token auth |
| Token | `/var/lib/llauncher/agent.token`, `0640 root:inference` (group-readable) |
| Env file | `/var/lib/llauncher/agent.env`, `0640 root:inference` |
| Code-side knob | #197 `LAUNCHER_STATE_DIR` — **merged** |
| Installer | #198 `install.sh --system` — **merged** |

The old `~/.llauncher` (operator home) was **copied, never moved**, so
single-user state is intact for rollback. It is now legacy; see Open
Items.

---

## 3. Host provisioning (cross-linked, not vendored here)

The privileged host setup is **owned by harness-tools** and applied as
root. It is *not* copied into this repo — these are pointers. The
in-repo `--system` installer depends on this provisioning already being
in place and fails loudly if it is missing.

Absolute paths on the host (`claude/host-config/` in harness-tools):

| Concern | Script |
|---------|--------|
| Create `inference` group, `llauncher` svc account, `/var/lib/llauncher` + `/var/log/llauncher` + setgid/default ACLs, exec ACLs, optional polkit | `.../host-config/setup-inference-lane.sh` |
| One-shot cutover: migrate state, seed env, carry token, repoint `LLAMA_SERVER_PATH`, stop the `--user` agent, run `install.sh --system`, verify | `.../host-config/cutover-llauncher-system.sh` |
| System-wide state-dir export for *interactive* local accounts | `.../host-config/etc/profile.d/llauncher.sh` |
| Companion record: moving the orchestrator front-end to run as user `claude` | `.../host-config/claude-as-user-migration.md` |

Full paths:

- `/srv/dev/shanevcantwell/harness-tools/claude/host-config/setup-inference-lane.sh`
- `/srv/dev/shanevcantwell/harness-tools/claude/host-config/cutover-llauncher-system.sh`
- `/srv/dev/shanevcantwell/harness-tools/claude/host-config/etc/profile.d/llauncher.sh`
- `/srv/dev/shanevcantwell/harness-tools/claude/host-config/claude-as-user-migration.md`

### The two access lanes (advisor principle)

Provisioning keeps "who edits the source" separate from "what drives the
runtime":

| Group / account | Purpose | Members |
|---|---|---|
| `assistant` *(exists)* | edit `/srv/dev` source | shane, claude |
| `inference` *(new)* | drive + observe the model runtime | shane, claude, `llauncher` |
| `llauncher` *(new svc acct, nologin)* | run the daemon | (member of `inference`) |

Access model:

- `/var/lib/llauncher` and `/var/log/llauncher` = `2770`/`2750`
  `llauncher:inference`, **setgid + default ACL** so anything the daemon
  writes inherits group `inference`. The group gets `rwX` (not `r-x`):
  the `claude` agent drives via its own in-process MCP server and must
  **write** lockfiles/registry edits as itself — a read-only grant would
  fail those writes.
- **Secrets are clamped back to group-read.** The installer writes
  `agent.token` / `agent.env` at `0640`, tightening the ACL mask to `r--`
  on those files specifically, so in-group consumers (the UI, remote HTTP
  clients) can read the token in place with no secret copied into a
  second home.
- **Exec code is read-only to the daemon.** `llauncher` gets
  `u:llauncher:rX` (read + traverse, never write) on `/srv/dev/llama.cpp`
  and `/srv/dev/shanevcantwell/llauncher`, plus the `--x` search bit on
  the `/srv/dev` parents so a `WorkingDirectory` chdir succeeds. A
  buggy/compromised daemon can run the code but cannot rewrite its own
  source.
- **polkit (optional):** an `inference`-group member may
  start/stop/restart/reload exactly `llauncher-agent.service` with no
  sudo — safe **only because** the unit runs as the unprivileged
  `llauncher` account.

### System-wide state-dir config for interactive accounts

The systemd unit gets `LAUNCHER_STATE_DIR=/var/lib/llauncher` from its own
`Environment=` line. *Interactive* sessions of human/operator accounts
(the `llauncher` CLI, the Streamlit UI, the local MCP server) need the
same pointer so their tooling sees the same live state. This is what
`etc/profile.d/llauncher.sh` provides:

```sh
export LAUNCHER_STATE_DIR=/var/lib/llauncher
```

Install (root):

```bash
sudo install -m 0644 \
  /srv/dev/shanevcantwell/harness-tools/claude/host-config/etc/profile.d/llauncher.sh \
  /etc/profile.d/llauncher.sh
```

**Read precondition:** because `/var/lib/llauncher` is `2770
llauncher:inference`, a consuming account must be in the `inference`
group:

```bash
sudo usermod -aG inference <user>   # then re-login / `newgrp inference`
```

(For non-login / service contexts, the broader alternative is
`LAUNCHER_STATE_DIR=/var/lib/llauncher` in `/etc/environment`.)

---

## 4. Cutover flow

Run **as root**, in order. Both host scripts are idempotent and support
`DRY_RUN=1 sudo -E bash <script>` to preview.

1. **Provision the runtime lane** —
   `sudo bash setup-inference-lane.sh`. Creates the `llauncher` account,
   `inference` group + memberships, `/var/lib/llauncher` and
   `/var/log/llauncher` with setgid + default ACLs, exec ACLs on the code
   tree, and (if `LLAUNCHER_UNIT` is set) the polkit rule.
2. **Operator/agents pick up the new group** — log out/in or
   `newgrp inference`. Group membership is fixed at login; pre-existing
   sessions won't carry `inference` until refreshed.
3. **Cutover** — `sudo bash cutover-llauncher-system.sh`. This does the
   host-specific glue the in-repo installer deliberately does not:
   - migrate durable state `~/.llauncher → /var/lib/llauncher` (copy,
     no-clobber; lockfiles skipped, `run/` recreated);
   - seed `/var/lib/llauncher/agent.env` (`0640 root:inference`) —
     carrying the existing token if found, else generating one;
   - set `LLAMA_SERVER_PATH` to the relocated binary under
     `/srv/dev/llama.cpp` and `LD_LIBRARY_PATH` to its sibling `.so`
     directory (the binary's baked RUNPATH points at the dead
     pre-migration path, so the loader needs this or the spawned server
     dies on startup);
   - stop + disable the `--user` agent (frees `:8765`);
   - run `install.sh --system` (renders
     `/etc/systemd/system/llauncher-agent.service`, mirrors the token to
     `agent.token`, daemon-reload + enable + start);
   - verify: unit active, `/health` reachable, operator **and** `claude`
     can read the token and **write** state.
4. **Bring up model servers** — none are started by the cutover; drive
   them via the agent / MCP once the system agent owns the runtime.

### Rollback

The old `~/.llauncher` was copied, never moved, so single-user state is
intact:

```bash
sudo systemctl disable --now llauncher-agent.service
sudo -u <operator> XDG_RUNTIME_DIR=/run/user/<uid> \
  systemctl --user enable --now llauncher-agent.service
```

Once confident in the system unit, archive the old `~/.llauncher` (see
Open Items).

---

## 5. Operate (system mode)

```bash
systemctl status llauncher-agent          # system unit (no --user)
sudo systemctl restart llauncher-agent    # or, for inference-group + polkit: no sudo
journalctl -u llauncher-agent -f          # manager log is journald

$EDITOR /var/lib/llauncher/agent.env      # EnvironmentFile read at start
sudo systemctl restart llauncher-agent    # pick up env edits
```

Token rotation in system mode: edit `LLAUNCHER_AGENT_TOKEN` in
`/var/lib/llauncher/agent.env`, restart the unit, update clients. See
[`run-as-a-service.md`](run-as-a-service.md#token-rotation) and
[`../auth.md`](../auth.md).

---

## 6. Open Items

- **Group-scheme ratification.** The `assistant` / `inference` /
  `llauncher` scheme in §3 was proposed and is now applied on the host,
  but the *design* was filed as "pending operator ratification" in the
  2026-06-25 handoff. Confirm the scheme is the intended durable model
  (vs. setgid-everywhere or a single combined group) and record the
  ratification.
- **Agent-token rotation.** The `agent.token` / `node_tokens.json` briefly
  sat world-readable (`0777`) in a transient `/srv/dev` copy during the
  migration session. Rotate the agent token to be clean: regenerate in
  `agent.env`, restart the unit, update clients. The token guards `:8765`
  on `0.0.0.0`.
- **Legacy `~/.llauncher` cleanup.** The operator's old `~/.llauncher`
  (and `~/.config/llauncher/agent.env`) were copied, not moved, and remain
  as the rollback source. Once the system unit is trusted, archive/remove
  them so there is one source of truth.
- **Backup-scheme gap.** Host-local secrets/state under
  `/var/lib/llauncher` are not captured by any backup yet — parked for a
  separate context, noted here so it is not lost.

---

## Related documents

- [ADR-018 — llauncher as a System Service](../adrs/accepted/018-llauncher-system-service.md)
  — the decision and its supersession of ADR-009's deployment posture.
- [`run-as-a-service.md`](run-as-a-service.md) — install/operate surface
  for both `--user` and `--system` modes.
- [`../auth.md`](../auth.md) — the two-plane token/auth model; why a
  second local user needs shared state, not a token.
- Session handoffs (now tracked in `../handoffs/`):
  [HANDOFF_2026-06-25](../handoffs/HANDOFF_2026-06-25-multiuser-migration-llauncher-systemd.md),
  [BUGFIX_ROADMAP_2026-06-25](../handoffs/BUGFIX_ROADMAP_2026-06-25.md).
- Host-layer provisioning (harness-tools, not in this repo):
  `setup-inference-lane.sh`, `cutover-llauncher-system.sh`,
  `etc/profile.d/llauncher.sh`, `claude-as-user-migration.md`.
