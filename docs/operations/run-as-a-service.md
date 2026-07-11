# Running `llauncher-agent` as a Service

The agent is the daemon piece of llauncher. The UI's service posture is governed by [ADR-022](../adrs/accepted/022-llauncher-ui-user-service.md): it runs as a per-operator `systemd --user` unit, installed via [`scripts/systemd/install-ui.sh`](#ubuntu--linux-ui-systemd-user-unit) (see the UI section below). Two operator/host steps sit outside that installer: the `/usr/local/bin/llauncher-ui` symlink (placed by `install-cli.sh`, as root) and the caller's `inference`-group membership (host provisioning). This doc covers persistent installs on:

- Linux (systemd, user-mode)
- Windows (NSSM-wrapped service)

These installers remove the "start it deliberately every morning" step
for the local agent. (The user-mode posture documented here traces to
ADR-009's "every node is a deliberately-started peer" framing, which
**ADR-018 superseded** with a real boot-time system-service mode —
`--system`, dedicated `llauncher` account, state under
`/var/lib/llauncher`. This doc still covers the `--user` install; for the
token/auth model across both planes see [`../auth.md`](../auth.md).)

## What gets installed

Both installers do the same work in OS-appropriate idioms:

1. Render a service definition pointing at the venv's `llauncher-agent`.
2. Create an env file at a per-user location with mode 0600 / restricted
   ACL, seeded from `*.env.example` with a freshly generated token.
3. Configure auto-restart on failure with a backoff (so a transient port
   collision doesn't lock you out, but a misconfigured token doesn't
   crash-loop forever).
4. Enable + start.

Re-running the installer is safe: env files are preserved, unit/service
config is refreshed. This is the right way to pick up a `git pull` that
touches the unit template.

### Migrating from pre-#139 installs

Commit `9f098d9` (#138/#139) renamed the agent's env vars from
`LAUNCHER_AGENT_*` to `LLAUNCHER_AGENT_*` (double-L). If your env file
(`~/.config/llauncher/agent.env` on Linux,
`%USERPROFILE%\.llauncher\agent.env` on Windows) predates that rename,
it still carries the old single-L keys. Both installers now detect and
migrate these keys **in place, automatically, on re-run**: matching
`LAUNCHER_AGENT_*` lines are rewritten to `LLAUNCHER_AGENT_*` with
values preserved byte-for-byte, and the installer reports which keys it
migrated. If no usable `LLAUNCHER_AGENT_TOKEN` line remains after
migration, the installer refuses to proceed (exits non-zero) rather than
installing a service with a broken token — see
[#281](https://github.com/shanevcantwell/llauncher/issues/281). Simply
re-running `install.sh` / `install.ps1` is enough to migrate an existing
deployment; no manual edit is required unless the installer tells you
one is missing.

As defense in depth, the agent itself also refuses to start (exit code
2) if it finds a legacy `LAUNCHER_AGENT_TOKEN` in its environment with
no `LLAUNCHER_AGENT_TOKEN` — that combination only ever means a stale
pre-#139 environment, and starting anyway would silently mint a token
the operator never configured (see the troubleshooting entry below).

## Security note up front

The agent's only auth layer is the `X-Api-Key` token in
`LLAUNCHER_AGENT_TOKEN` (see [`../auth.md`](../auth.md)). Both installers default the bind to `0.0.0.0`
in the generated env file — that's the right answer for the
multi-workstation case the service install implies, but **only** if the
network between those workstations is trusted (LAN, Tailscale, VPN). If
you're on an untrusted network, edit the env file to bind to `127.0.0.1`
or a specific interface before starting the service. The token alone is
not a substitute for network scoping.

---

## Ubuntu / Linux (systemd user unit)

### Install

```bash
./scripts/run.sh install         # if you haven't already
./scripts/systemd/install.sh
```

The installer:

- Writes the rendered unit to `~/.config/systemd/user/llauncher-agent.service`.
- Writes the env file to `~/.config/llauncher/agent.env` (mode 0600).
- Runs `systemctl --user daemon-reload`, `enable`, and `restart`.

### Autostart at boot without an active login

User units only run while you're logged in unless you enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

Run this once per machine.

### Operate

```bash
systemctl --user status llauncher-agent
systemctl --user restart llauncher-agent
journalctl --user -u llauncher-agent -f       # live logs
```

### Pick up env-file edits

`EnvironmentFile=` is read at service start, so:

```bash
$EDITOR ~/.config/llauncher/agent.env
systemctl --user restart llauncher-agent
```

### Uninstall

```bash
./scripts/systemd/install.sh --uninstall
```

The env file is left in place; remove it manually if you want a fully
clean state.

---

## Ubuntu / Linux (UI systemd --user unit)

The Streamlit UI is a per-operator front-end, not machine infrastructure
(loopback-only, no built-in auth, matters only while an operator is
watching). [ADR-022](../adrs/accepted/022-llauncher-ui-user-service.md)
runs it as a `systemd --user` unit owned by your login session — never a
system unit, never root.

### Operator/host preconditions (NOT done by the installer)

1. **The `/usr/local/bin/llauncher-ui` symlink** the unit's `ExecStart`
   points at. Place it once, as root:

   ```bash
   sudo bash scripts/systemd/install-cli.sh
   ```

   This installs `llauncher` (+ `llauncher-mcp`, `llauncher-ui`) into a
   dedicated `/opt/llauncher/venv` and symlinks the console scripts into
   `/usr/local/bin`.

2. **`inference`-group membership** for your account. The UI reads the
   system agent's token (`/var/lib/llauncher/agent.token`, mode `0640`
   `root:inference`) in place via group membership — no copy. Group
   provisioning is a host step (harness-tools
   `setup-inference-lane.sh`):

   ```bash
   sudo usermod -aG inference "$USER"   # then re-login
   ```

`install-ui.sh` *warns* (does not block) if either is missing, so the
unit can be rendered now and becomes functional once provisioning lands.

### Install

```bash
./scripts/systemd/install-ui.sh
```

The installer (as your own account, NOT root):

- Copies the fixed unit template to
  `~/.config/systemd/user/llauncher-ui.service`. The template hardcodes
  `Environment=LAUNCHER_STATE_DIR=/var/lib/llauncher` — the one line that
  makes the UI read the system agent's token instead of `~/.llauncher`;
  it is **not** overridable from the installer's environment.
- Runs `systemctl --user daemon-reload`, `enable --now`.

It is idempotent — re-run it to pick up a `git pull` that touches the
template.

### Autostart at boot / survive logout (optional)

```bash
loginctl enable-linger "$USER"
```

Optional and your call — the installer prints this as guidance but never
runs it.

### Operate

```bash
systemctl --user status llauncher-ui
systemctl --user restart llauncher-ui
journalctl --user -u llauncher-ui -f      # live logs
```

### Uninstall

```bash
./scripts/systemd/install-ui.sh --uninstall
```

---

## Windows (NSSM)

### Prerequisites

- Project venv created via `scripts\run.bat install`.
- [NSSM](https://nssm.cc/) on PATH. The easiest path is one of:
  ```cmd
  choco install nssm
  scoop install nssm
  ```
  Or download the zip from https://nssm.cc/download and either add
  `nssm.exe` to PATH or set `$env:NSSM` to its full path.
- An **elevated** PowerShell window (right-click → Run as Administrator).
  Windows service registration requires this.

### Install

```powershell
.\scripts\windows\install.ps1
```

The installer:

- Locates NSSM, fails fast with install instructions if missing.
- Writes the env file to `%USERPROFILE%\.llauncher\agent.env` with an
  ACL restricting access to the current user.
- Registers the service via `nssm install`, pointing at
  `.\.venv\Scripts\llauncher-agent.exe`.
- Configures auto-start, restart-on-failure with a 5 s backoff,
  rotating log files at `%USERPROFILE%\.llauncher\logs\agent.{out,err}.log`,
  and a 30 s graceful-stop window (NSSM sends Ctrl-Break first, which
  uvicorn handles like SIGINT).
- Starts the service.

### Operate

```powershell
Get-Service llauncher-agent
Restart-Service llauncher-agent
Get-Content "$env:USERPROFILE\.llauncher\logs\agent.out.log" -Tail 50 -Wait
```

### Pick up env-file edits

```powershell
notepad "$env:USERPROFILE\.llauncher\agent.env"
.\scripts\windows\install.ps1       # re-applies env to NSSM, restarts
```

(Editing the env file alone is not enough — NSSM holds the env in
service config, not by re-reading the file. The installer re-applies it.)

### Uninstall

```powershell
.\scripts\windows\install.ps1 -Uninstall
```

### Why NSSM and not pywin32 or Task Scheduler?

- **pywin32 service wrapper** would require llauncher itself to
  implement the Windows service-control protocol — heavy coupling for
  no portability gain.
- **Task Scheduler** has no built-in restart-on-crash and treats
  "service" as "scheduled task that happens to run forever" — workable
  but inferior to a real service for a long-running daemon.
- **NSSM** is the well-trodden path: it does exactly one thing
  (wrap-a-console-exe-as-a-service) and does it well.

---

## Verifying the install

From any machine that can reach the agent's host:port:

```bash
# /health is auth-exempt — proves only reachability + liveness:
curl -sS http://<host>:8765/health

# /status requires the token — proves the token matches too. The header
# is X-Api-Key (NOT Authorization: Bearer); see docs/auth.md:
curl -sS -H "X-Api-Key: $LLAUNCHER_AGENT_TOKEN" \
    http://<host>:8765/status
```

A 200 on `/status` confirms the service is up, the token matches, and the
bind interface is reachable. A 401/403 there means the token is missing or
wrong (`/health` would still return 200 — it skips auth).

## Troubleshooting

### 403 on `/node-info` or `/start` while `/health` returns 200

This is a token mismatch, not a reachability problem — `/health` skips
auth entirely, so its 200 only proves the process is up and reachable.
The distinction between 401 and 403 tells you what the client sent:

- **401** = the client sent no `X-Api-Key` header at all.
- **403** = the client sent a header, but the value doesn't match the
  agent's token.

A 403 with a working `/health` almost always means the UI/client and the
agent resolved to *different* tokens. Check, in order:

1. **Legacy env keys.** Open the agent's env file
   (`~/.config/llauncher/agent.env` on Linux,
   `%USERPROFILE%\.llauncher\agent.env` on Windows) and look for
   pre-#139 `LAUNCHER_AGENT_*` (single-L) key names instead of
   `LLAUNCHER_AGENT_*`. Re-run the installer to migrate them
   automatically (see "Migrating from pre-#139 installs" above).
2. **A self-generated token under the wrong profile.** When the agent
   can't resolve a token from the environment, it auto-generates one at
   `~/.llauncher/agent.token` under `Path.home()` **of the account the
   agent process runs as** — for a Windows service that's the service
   account, not the interactively logged-in operator. If the service is
   silently minting its own token under a profile you never look at,
   the UI's configured token will never match it. Confirm the service's
   token source (env var vs. auto-generated file) and which account's
   home directory it actually wrote to.

## Token rotation

1. Generate a new token:
   ```bash
   python -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```
2. Update the env file on the host.
3. Restart the service (`systemctl --user restart llauncher-agent` or
   `.\scripts\windows\install.ps1` on Windows — Windows needs the
   re-apply to push the new value into the service config).
4. Update any clients (UI on other workstations) with the new token.
