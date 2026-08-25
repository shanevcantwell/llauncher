# Token & Auth Model

A single reference for who needs a token in llauncher, who doesn't, and
how the agent resolves one. If you only read one thing: **the local MCP
server and CLI never use a token; only the HTTP Agent (and its remote /
UI clients) do.**

## Two planes (don't conflate them)

llauncher exposes the same `operations/` verbs over several surfaces, but
only one of them is a network listener with authentication.

| Plane | Transport | Auth | Who talks to it |
|-------|-----------|------|-----------------|
| **HTTP Agent** (`llauncher-agent`, `:8765`) | HTTP/REST | **Required** — `X-Api-Key` token | Streamlit UI, remote nodes, `curl`, the `node`-targeted CLI/MCP paths |
| **MCP server** (`llauncher-mcp`) | stdio, **in-process** | **None** — tokenless | LLM agents / MCP clients on the same host |
| **CLI** (`llauncher …`) | in-process | **None** for local targets | Operators; only `--target <remote-node>` reaches over HTTP |

Why the MCP server has no token: it calls `llauncher.operations` /
`LauncherState` **directly** against local state (lockfiles,
`config.json`, processes). There is no HTTP hop and no network listener —
nothing to authenticate. Its trust boundary is the stdio pipe: it
implicitly trusts whatever process spawned it (your MCP client). See the
README "Trust boundary (stdio only)" note. The MCP server does **not**
import `httpx`/`RemoteNode`; it is not a client of the agent.

So: **needing a token is a property of crossing the HTTP boundary, not a
property of mutating state.** A local MCP/CLI caller mutates state
(start/stop/swap) with no token; a remote `curl` reading `/status` needs
one.

## Agent HTTP auth

Enforced by `AuthenticationMiddleware` (`llauncher/agent/middleware.py`).

- **Header:** `X-Api-Key: <token>`. *Not* `Authorization: Bearer`.
- **Comparison:** constant-time (`hmac.compare_digest`).
- **Responses:** `401` if the header is absent, `403` if present but wrong.
- **Auth is always on.** Post-#87/C1, `create_app()` refuses to build an
  app without a non-empty token, so there is no "unauthenticated agent"
  mode in production — *including on loopback*. (On loopback the token is
  auto-generated rather than operator-supplied; it is still enforced.)

### Exempt paths (no token)

```
/health   /docs   /redoc   /openapi.json
```

Everything else requires the token — **including read GETs**, because every
read leaks something (running models, OS/IP/process info):

- Reads: `/status`, `/models`, `/models/validate`, `/logs/{port}`,
  `/audit`, `/orphans`, `/node-info`, `/footer-context/{port}`
- Mutations: `/start/{port}`, `/swap/{port}`, `/stop/{port}`,
  `/cancel/{port}`, `DELETE /models/{name}`

> Note: when auth is active, `/docs`, `/redoc`, and `/openapi.json` are
> disabled (set to `None`) rather than served, so in a normal deployment
> the only truly open path is `/health`.

## Token resolution precedence

`resolve_agent_token()` (`llauncher/core/agent_token.py`, re-exported from
`llauncher/agent/auth.py`) resolves in this order:

1. **Env** `LLAUNCHER_AGENT_TOKEN` — used if set, non-empty, and not `"-"`.
   An explicit override always wins.
2. **Stdin** — if the env value is exactly `"-"`, read one line from stdin
   (lets you pipe from a secret manager without leaving the token in the
   environment). Empty stdin here is a fatal error, not a fallback.
3. **`agent.env`** (see locations below) — the `LLAUNCHER_AGENT_TOKEN=`
   line, parsed directly with `parse_env_file()`. This is the **single
   live source** (issue #284): both the agent service and the UI/client
   parse this exact file at startup — there is no installer-time
   snapshot and no separate token-mirror file. Parsing matches systemd's
   `EnvironmentFile=` semantics: blank lines and `#`-comments are
   skipped, and a duplicate key's **last** line wins.
4. **Auto-generate** — `secrets.token_urlsafe(32)`, appended to `agent.env`
   as a new `LLAUNCHER_AGENT_TOKEN=` line (file created at mode 0600,
   parent 0700, if it doesn't exist yet) and printed to stderr **once**.

Step 4 only happens when `allow_generate=True`. The agent
(`server.py:run_agent`) permits auto-generation **only on a loopback
bind**. A non-loopback bind calls with `allow_generate=False`: if no token
is found via env/stdin/file it **refuses to start** (`SystemExit(2)`) —
auto-generating a secret nobody outside the host has seen is meaningless
for LAN exposure. Consumers that must never mint a token (the UI, the
registry) also pass `allow_generate=False`.

**Retired (issue #284):** the `agent.token` mirror file. It used to be a
separate on-disk copy an installer had to keep in sync with `agent.env` —
that installer step being skipped/stale was the direct cause of issue
#281's UI-403 split-brain. Nothing in the runtime reads `agent.token` any
more; installers migrate a pre-existing one into `agent.env` once, at the
door, then delete it (`PARSE-AT-THE-DOOR`).

## File locations & modes

| File | Default path | Mode | Purpose |
|------|--------------|------|---------|
| `agent.env` | `~/.llauncher/agent.env` | `0600` (parent `0700`) | The **single live source** for the agent's token and other service-facing config. Parsed directly by both the agent and the local UI. |
| `node_tokens.json` | `~/.llauncher/node_tokens.json` | `0600` (parent `0700`) | Per-**remote**-node tokens, operator-supplied. The `local` entry is excluded by design (its token lives in `agent.env`). |
| `nodes.json` | `~/.llauncher/nodes.json` | — | Peer registry. **Never** carries `api_key` (control C10/#83); tokens live in the sidecar above. |

`~/.llauncher/` is the live default, overridable via `LAUNCHER_STATE_DIR`
(issue #196). In systemd `--system` mode (ADR-LLNCH-018) state relocates under
`LAUNCHER_STATE_DIR=/var/lib/llauncher`; `agent.env` there is mode `0640`,
group `inference`, so the operator UI and a non-admin agent account can
read it in place without copying secrets. On Windows, `install.ps1`
injects `LAUNCHER_STATE_DIR=<installing user's %USERPROFILE%\.llauncher>`
into the NSSM service environment so a service running under
`LocalSystem` (whose `Path.home()` does not resolve to the operator's
profile) still resolves the *same* `agent.env` the operator's UI reads.

## Who needs a token — by consumer

| Consumer | Token? | Source |
|----------|--------|--------|
| **MCP server / local CLI** | No | n/a — in-process, tokenless |
| **CLI targeting a remote node** | Yes | `node_tokens.json` entry for that node |
| **Streamlit UI → local agent** | Yes | `resolve_agent_token(allow_generate=False)` → env, then `agent.env`. The UI is a *separate* process and does **not** inherit the agent's `LLAUNCHER_AGENT_TOKEN` / systemd `EnvironmentFile`; it parses `agent.env` directly. Launched via `ui/launch.py` → `streamlit run app.py`. |
| **Remote UI/CLI (another machine)** | Yes | env `LLAUNCHER_AGENT_TOKEN`, or the per-node `node_tokens.json` |
| **`curl` / scripts → agent** | Yes (unless hitting an exempt path) | send `X-Api-Key` |

## Multiuser / system mode

A common misconception: "the token is what lets a second local user drive
llauncher." It isn't. A second local user (e.g. `claude`) drives via its
**own in-process MCP server**, which is tokenless. What that second user
needs is **shared access to the state** — lockfiles, `config.json`, the
run dir — which is exactly what `LAUNCHER_STATE_DIR=/var/lib/llauncher`
(group `inference`) provides (ADR-LLNCH-018). The group-readable `agent.env`
(0640, group `inference`) is for the *other* plane: the UI and any
remote/HTTP clients that do cross the network boundary.

So in system mode there are two distinct enablers, often conflated:

- **Shared state dir** → enables a second *local* user's MCP/CLI (tokenless).
- **Group-readable `agent.env`** → enables the UI / remote HTTP clients (token-bearing).

## Operating notes

- **Loopback is not "no auth."** Even on `127.0.0.1` the agent enforces
  the token; it just generates one for you on first run (printed once to
  stderr; persisted into `agent.env`).
- **Token is not TLS.** The transport is plain HTTP. Only expose the agent
  on trusted networks (LAN / Tailscale / VPN / SSH tunnel). See README
  "Security Notes" and `docs/operations/run-as-a-service.md`.
- **Rotation:** edit the `LLAUNCHER_AGENT_TOKEN=` line in `agent.env` (or
  export `LLAUNCHER_AGENT_TOKEN` to override), restart the agent, and
  update clients. See the run-as-a-service doc.
- **Roadmap:** ADR-LLNCH-017 (draft) adds opt-in trusted-host *session-token*
  issuance (`POST /session`) on top of this static-token model — it does
  not replace it. Static-token auth remains the fallback.

## Source of truth

| Concern | Code |
|---------|------|
| Header check, exempt paths | `llauncher/agent/middleware.py` |
| Token resolution precedence + env-file parser | `llauncher/core/agent_token.py` (`resolve_agent_token`, `parse_env_file`) |
| Loopback / refuse-to-start guard | `llauncher/agent/server.py` (`run_agent`) |
| Local-UI / remote-node token sourcing | `llauncher/remote/registry.py` |
| Rationale | ADR-LLNCH-003 (static token), ADR-LLNCH-018 (system mode), ADR-LLNCH-017 (session tokens, draft) |
</content>
</invoke>
