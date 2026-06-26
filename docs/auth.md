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

- Reads: `/status`, `/models`, `/models/health`, `/logs/{port}`,
  `/audit`, `/orphans`, `/node-info`, `/footer-context/{port}`
- Mutations: `/start/{port}`, `/swap/{port}`, `/stop/{port}`,
  `/cancel/{port}`, `DELETE /models/{name}`

> Note: when auth is active, `/docs`, `/redoc`, and `/openapi.json` are
> disabled (set to `None`) rather than served, so in a normal deployment
> the only truly open path is `/health`.

## Token resolution precedence

`resolve_agent_token()` (`llauncher/agent/auth.py`) resolves in this order:

1. **Env** `LLAUNCHER_AGENT_TOKEN` — used if set, non-empty, and not `"-"`.
2. **Stdin** — if the env value is exactly `"-"`, read one line from stdin
   (lets you pipe from a secret manager without leaving the token in the
   environment). Empty stdin here is a fatal error, not a fallback.
3. **Token file** — `agent.token` (see locations below), read verbatim.
4. **Auto-generate** — `secrets.token_urlsafe(32)`, written to the token
   file (mode 0600, parent 0700) and printed to stderr **once**.

Step 4 only happens when `allow_generate=True`. The agent
(`server.py:run_agent`) permits auto-generation **only on a loopback
bind**. A non-loopback bind calls with `allow_generate=False`: if no token
is found via env/stdin/file it **refuses to start** (`SystemExit(2)`) —
auto-generating a secret nobody outside the host has seen is meaningless
for LAN exposure. Consumers that must never mint a token (the UI, the
registry) also pass `allow_generate=False`.

## File locations & modes

| File | Default path | Mode | Purpose |
|------|--------------|------|---------|
| `agent.token` | `~/.llauncher/agent.token` | `0600` (parent `0700`) | The agent's own static token. Read by the agent and by the local UI. |
| `node_tokens.json` | `~/.llauncher/node_tokens.json` | `0600` (parent `0700`) | Per-**remote**-node tokens, operator-supplied. The `local` entry is excluded by design (its token lives in `agent.token`). |
| `nodes.json` | `~/.llauncher/nodes.json` | — | Peer registry. **Never** carries `api_key` (control C10/#83); tokens live in the sidecar above. |

`~/.llauncher/` is the live default. In systemd `--system` mode (ADR-018,
in flight via #196/#197) state is meant to relocate under
`LAUNCHER_STATE_DIR=/var/lib/llauncher`, with the token mirrored to
`/var/lib/llauncher/agent.token` at mode `0640`, group `inference`, so the
operator UI and a non-admin agent account can read it in place without
copying secrets. **Caveat:** as of this writing `default_token_path()`
still hardcodes `~/.llauncher/agent.token` and does not yet consult
`LAUNCHER_STATE_DIR`; the Python-side honoring of that knob is pending
#197 (ADR-018 Consequences). Treat `/var/lib/llauncher/agent.token` as the
target state, not the current read path.

## Who needs a token — by consumer

| Consumer | Token? | Source |
|----------|--------|--------|
| **MCP server / local CLI** | No | n/a — in-process, tokenless |
| **CLI targeting a remote node** | Yes | `node_tokens.json` entry for that node |
| **Streamlit UI → local agent** | Yes | `resolve_agent_token(allow_generate=False)` → env, then `agent.token`. The UI is a *separate* process and does **not** inherit the agent's `LLAUNCHER_AGENT_TOKEN` / systemd `EnvironmentFile`; it reads the token file directly. Launched via `ui/launch.py` → `streamlit run app.py`. |
| **Remote UI/CLI (another machine)** | Yes | env `LLAUNCHER_AGENT_TOKEN`, or the per-node `node_tokens.json` |
| **`curl` / scripts → agent** | Yes (unless hitting an exempt path) | send `X-Api-Key` |

## Multiuser / system mode

A common misconception: "the token is what lets a second local user drive
llauncher." It isn't. A second local user (e.g. `claude`) drives via its
**own in-process MCP server**, which is tokenless. What that second user
needs is **shared access to the state** — lockfiles, `config.json`, the
run dir — which is exactly what `LAUNCHER_STATE_DIR=/var/lib/llauncher`
(group `inference`) provides (ADR-018). The group-readable `agent.token`
(0640, group `inference`) is for the *other* plane: the UI and any
remote/HTTP clients that do cross the network boundary.

So in system mode there are two distinct enablers, often conflated:

- **Shared state dir** → enables a second *local* user's MCP/CLI (tokenless).
- **Group-readable token** → enables the UI / remote HTTP clients (token-bearing).

## Operating notes

- **Loopback is not "no auth."** Even on `127.0.0.1` the agent enforces
  the token; it just generates one for you on first run (printed once to
  stderr; persisted at `agent.token`).
- **Token is not TLS.** The transport is plain HTTP. Only expose the agent
  on trusted networks (LAN / Tailscale / VPN / SSH tunnel). See README
  "Security Notes" and `docs/operations/run-as-a-service.md`.
- **Rotation:** change `LLAUNCHER_AGENT_TOKEN` (or the token file),
  restart the agent, and update clients. See the run-as-a-service doc.
- **Roadmap:** ADR-017 (draft) adds opt-in trusted-host *session-token*
  issuance (`POST /session`) on top of this static-token model — it does
  not replace it. Static-token auth remains the fallback.

## Source of truth

| Concern | Code |
|---------|------|
| Header check, exempt paths | `llauncher/agent/middleware.py` |
| Token resolution precedence | `llauncher/agent/auth.py` (`resolve_agent_token`) |
| Loopback / refuse-to-start guard | `llauncher/agent/server.py` (`run_agent`) |
| Local-UI / remote-node token sourcing | `llauncher/remote/registry.py` |
| Rationale | ADR-003 (static token), ADR-018 (system mode), ADR-017 (session tokens, draft) |
</content>
</invoke>
