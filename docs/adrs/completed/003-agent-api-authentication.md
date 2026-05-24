# ADR-003: Authentication for Agent API (Port 8765)

**Status:** Accepted  
**Date:** 2026-04-26  

## Context

The llauncher agent exposes a FastAPI HTTP REST API on `0.0.0.0:8765` by default, providing endpoints for starting/stopping model servers, managing nodes, and querying status. Currently **there is zero authentication** — any network-accessible client can issue commands that consume GPU resources, evict active models, or shut down inference services.

A review document (`docs/reviews/2026-04-25-enhancement-no-auth-agent-api.md`) was already authored identifying this gap as critical risk in shared/multi-user environments.

### The Risk
- **Any user on the same machine** can issue HTTP requests to localhost:8765
- **If bound to 0.0.0.0**, any network peer with connectivity can start/stop models
- No audit trail of who changed what — all actions are anonymous
- MCP tools (like `llaunch_swap_server`) operate without auth checks on the target

### Design Constraints from Sessions
1. Must support both **simple** (single shared secret) and **advanced** (per-user API keys with scopes) modes
2. Should be opt-in to preserve backward compatibility with existing setups
3. Node registration in `~/.llauncher/nodes.json` should carry auth credentials so the head dashboard can authenticate when pinging remote nodes
4. Auth must not break local-only usage (127.0.0.1) — security concern is primarily network-accessible or multi-user scenarios

## Decision

### Option Chosen: API Key Authentication with Opt-In Activation

```
┌──────────────────────┐     ┌─────────────────────────┐
│  Pi Extension / UI   │     │  llauncher Agent Node    │
│                      │     │                         │
│  LLM Agent ──► Tool  │──►  │  FastAPI middleware      │
│         ◄── Result   │◄──  │  reads X-Api-Key header  │
└──────────────────────┘     └─────────────────────────┘
```

**Implementation approach:**
1. Add `api_key` field to core settings (`core/settings.py`)
2. Store key in node config: `{ "host": "...", "port": ..., "api_key": "..." }`
3. FastAPI middleware checks `X-Api-Key` header on all write endpoints (`/start`, `/stop`, `/swap`, `/nodes/add`, `/nodes/remove`, `/nodes/status`)

   Exempt read-only endpoints that don't require authentication:
   - `/health` — liveness probe
   - `/status` — running servers status
   - `/models` — list all configured models
   - `/models/health` — model health status
   - `/docs`, `/openapi.json`, `/redoc` — API documentation
4. When `api_key` is empty/None in settings, skip auth entirely (backward compatible)
5. Add `llauncher_add_node` tool support for passing api_key when registering new nodes

### Scope Enum (Future Phase 2 — not in ADR-003 scope)
| Role | Can Do | Cannot Do |
|------|--------|-----------|
| viewer | /status, /health, /models, /logs | start, stop, swap, node management |
| operator | all viewer + /start, /stop, /logs | swap, node add/remove |
| admin | everything | — |

### Testing Requirements
- Unit tests for middleware: valid key passes, missing key rejected, wrong key rejected
- Auth disabled path: empty api_key allows all requests (no regression)
- Integration test: start server with key → call endpoint without → 401; with correct key → 200
- Node registration flow: register node with key → agent responds to authenticated pings

## Consequences

**Positive:**
- Immediate security improvement for multi-user or network-accessible setups
- Backward compatible — existing deployments unaffected unless they opt in
- Foundation for future per-user scoping (Phase 2)
- pi-footer-extension can use authenticated requests when `LAUNCHER_AGENT_TOKEN` is set

**Negative:**
- Adds first non-trivial dependency chain: settings → middleware → all write endpoints
- Client-side changes needed: pi-footer-extension must read api_key from node config and inject header when token is configured
- Session management (login/logout/rotation) deferred to Phase 2 — simpler initial implementation but may leave gaps for shared environments

**Open Questions:**
1. ~~Should default binding change from `0.0.0.0` to `127.0.0.1` when api_key is configured?~~ **Resolved 2026-05-24** by PR #75: default bind is `127.0.0.1`; binding off-loopback now *requires* `LAUNCHER_AGENT_TOKEN` and the agent refuses to start without it.
2. How to handle key rotation without downtime? (Defer to Phase 2 — supports multiple concurrent keys)

## Amendment Notes

**2026-05-24:** Implementation complete per the security hardening
cohort. Token requirement was tightened beyond the original opt-in
design:

- PR #75 — non-loopback bind requires `LAUNCHER_AGENT_TOKEN`; agent
  refuses to start without it. Default bind is loopback.
- PR #87 / C1 — `create_app` requires a non-empty `auth_token`; the
  unauthenticated-by-default posture is closed.
- `llauncher/agent/auth.py` resolves `LAUNCHER_AGENT_TOKEN`; the
  special value `-` reads from stdin. On loopback first-run with no
  token configured, a fresh token is auto-generated to
  `~/.llauncher/agent.token` (mode 0600).
- `llauncher/agent/middleware.py` uses `hmac.compare_digest` for the
  `X-Api-Key` check.
- **Exempt-paths drift:** the original Decision §3 and the
  Implementation Notes below list `/health`, `/status`, `/models`,
  `/docs`, `/openapi.json`, `/redoc` as auth-exempt. The live code in
  `agent/middleware.py:20` exempts a narrower set:
  `{/health, /docs, /redoc, /openapi.json}`. `/status`, `/models`,
  and `/node-info` all require the token. This is consistent with the
  security-cohort posture (every read leaks something — running
  models, OS/IP/process info from `/node-info`) but the ADR was never
  updated to match. Tracked as issue #126 for resolution (narrow the
  docs to match the code, or widen the code to match the docs).
- **Local-node UI auth fix (2026-05-24 follow-up commit):** the UI
  process is separate from the agent and does not inherit
  `LAUNCHER_AGENT_TOKEN`, so the `local` registry entry sourced no
  token and bounced off non-exempt endpoints (notably `/node-info`)
  with 401. Resolved by adding `NodeRegistry._resolve_local_token()`
  (sources via `resolve_agent_token(allow_generate=False)`),
  self-healing the `local` entry on every `_load`, and mirroring the
  token to `~/.llauncher/agent.token` from
  `scripts/windows/install.ps1`. Tests in
  `tests/unit/test_registry_extended.py::TestLocalNodeTokenResolution`.
  See also #125 (self-loop short-circuit for `/node-info`).

## Implementation Notes

- **Middleware**: `llauncher/agent/middleware.py` implements `X-Api-Key` header validation
- **Settings**: `llauncher/core/settings.py` defines `AGENT_API_KEY` from `LAUNCHER_AGENT_TOKEN` env var
- **Exemptions** (per live code at `agent/middleware.py:20`): `/health`, `/docs`, `/openapi.json`, `/redoc`. Note this is narrower than the original Decision §3 listed; see Amendment Notes for the drift. `/status`, `/models`, `/node-info` all require the token.
- **pi-footer-extension**: Currently unauthenticated (token pass-through deferred to future)
