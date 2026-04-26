# ADR-003: Authentication for Agent API (Port 8765)

**Status:** Draft  
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
3. FastAPI middleware checks `X-Api-Key` header on all `/start`, `/stop`, `/swap`, `/nodes/` endpoints (read-only endpoints like `/status`, `/health`, `/models` remain unauthenticated)
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

**Negative:**
- Adds first non-trivial dependency chain: settings → middleware → all write endpoints
- Client-side changes needed: pi TypeScript extension must read api_key from node config and inject header
- Session management (login/logout/rotation) deferred to Phase 2 — simpler initial implementation but may leave gaps for shared environments

**Open Questions:**
1. Should default binding change from `0.0.0.0` to `127.0.0.1` when api_key is configured? (Conservative: keep current behavior, require explicit bind config)
2. How to handle key rotation without downtime? (Defer to Phase 2 — supports multiple concurrent keys)
