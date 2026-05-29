# ADR-012: Footer Context Endpoint — Minimal Payload, Short TTL Cache

**Status:** Accepted
**Date:** 2026-05-16
**Relationship to other ADRs:** ADR-001 (pi-coding-agent / TypeScript footer extension) is the consumer. ADR-008 (stateless facade, configurable on-disk paths) defines the lockfile read path this endpoint relies on. ADR-003 (agent API authentication) governs auth handling, which this endpoint matches without exception. Issue [#36](https://github.com/shanevcantwell/llauncher/issues/36) (multi-node footer-budget cache early-return bug) is adjacent but lives on the consumer side and is not addressed here.

**Supersedes:** No prior ADR. The pre-ADR behavior — footers polling `/status` per-token — was an unratified accretion rather than a deliberate choice.

## Context

The pi-coding-agent footer extension (`pi-footer-extension/footer-budget.ts`) polls each known llauncher node to render a per-token status line showing the active model, context window, and parallel slot count. Today it does this by calling `GET /status` on each node.

Three properties of the current arrangement are wrong for this consumer:

1. **Cost per call is large.** `/status` calls `state.refresh_running_servers()` (a process-table scan + per-port lockfile reconciliation) *and* instantiates `GPUHealthCollector().get_health()` (a GPU probe) on every request. Footer cadence is multiple requests per second per watched node; the per-token cost is meaningful CPU on the agent host and meaningful wire bytes across the LAN.
2. **Response shape is ~10× larger than the consumer needs.** The footer reads four fields: `model`, `ctx_size`, `parallel`, `port`. The response delivers the full `ModelConfig.to_dict()` per running server plus the GPU health payload.
3. **The contract is incidental, not pinned.** `/status` is the dashboard's introspection endpoint; its shape will continue to grow as new UI surfaces want to read more. A footer that depends on `/status` is one schema expansion away from a silent break.

The lockfile + `ConfigStore` already hold every byte the footer needs. We do not need a process scan, a GPU probe, or live-pid verification to answer "what does the lockfile say is running on port P." A stale lockfile leads to stale footer rendering at worst, which is bounded by the rendering loop's own cadence — and inference traffic to a dead port fails on the inference channel, not on the footer.

## Decision

### 1. New endpoint: `GET /footer-context/{port}`

Added to `llauncher/agent/routing.py`. Returns a fixed, minimal JSON shape:

```json
{
  "port": 8081,
  "model": "qwen3-coder-30b",
  "ctx_size": 131072,
  "parallel": 4
}
```

When the port has no lockfile, returns **HTTP 404** with body `{"detail": "port_empty"}`. This matches the `port_empty` vocabulary used by `operations.swap` per ADR-011.

When the lockfile exists but the model name it claims is not present in `ConfigStore` (the user deleted the config while a server using it was still running), returns **HTTP 200** with `ctx_size: null, parallel: null`, model preserved from the lockfile. The footer extension already tolerates null `parallel` (`mc.parallel != null ? mc.parallel : 1`); `ctx_size: null` is a new tolerance the TS side must accept (treat as "unknown"). The lockfile is the source of truth for "what is running"; missing config is a degraded-display case, not a not-found case.

The endpoint is **port-keyed**, not name-keyed, per ADR-010.

### 2. TTL cache: `llauncher/agent/footer_cache.py` (new module)

A tiny in-process cache keyed by `port`, with per-entry expiry:

```python
def get_footer_context(port: int) -> FooterContext | None: ...
```

- **TTL:** `LAUNCHER_FOOTER_CACHE_S` seconds (default `1.0`). Set to `0` to disable the cache (every request hits disk).
- **Cache value:** the fully-resolved tuple `(model, ctx_size, parallel, port, expires_at_monotonic)`.
- **Cache miss:** read lockfile, then `ConfigStore.get_model(lockfile.model)`. Both calls are cheap and synchronous; no process scan, no GPU probe.
- **Cache eviction:** lazy. Expired entries are recomputed on the next request for that port. There is no background sweep; the cache size is bounded by the number of ports the agent host has served, which is small (≤ tens for the project's hobby scope).
- **Concurrency:** the cache uses a single `threading.Lock` around the dict. FastAPI's threadpool dispatch can race two reads on the same port; double-read on miss is correct and not worth avoiding.

The cache is intentionally **not** invalidated by `start`/`stop`/`swap` operations. A 1 s window of stale footer state is acceptable; wiring invalidation hooks into the operations layer would couple the agent to the cache module and we don't need it. Operations on a port write a new lockfile; the next footer poll past the TTL picks it up.

### 3. Auth: matches `/status` exactly

The endpoint is registered on the same router as `/status` and is subject to the same `AuthenticationMiddleware`: if `LLAUNCHER_AGENT_TOKEN` is set, `X-Api-Key` is required. `/footer-context/{port}` is **not** added to `_AUTH_EXEMPT_PATHS`. The footer extension must send the token when auth is configured, identically to how it would for `/status`.

We rejected the alternative of exempting the endpoint. The lockfile path on disk is local-only, but the HTTP endpoint is network-reachable; exempting it would create an unauthenticated information channel that lists every model currently loaded on the node. The cost of injecting a token into the TS extension's fetch call is a single header.

### 4. Response shape is a stable contract

Unlike `/status`, the shape of this response is *pinned by this ADR* and may not be extended without superseding ADR-012 or amending it in place. The four-field payload is the contract; consumers may rely on the keys present and the keys absent.

Adding fields requires amending this ADR. Removing fields requires a new ADR. Renaming fields requires a new ADR plus a deprecation window.

### 5. Batch support: rejected for this slice

`docs/m5-design.md` §Open Questions raised the question of a batch variant (`GET /footer-context?ports=8081,8082`). We considered it and decided against:

- The footer extension already issues one HTTP call per node (because each node is a different host:port). Adding a batch shape on a *single* node doesn't reduce the cross-node fan-out.
- Within a single node, the footer would call `/footer-context/{port}` once per *occupied* port — typically 1, occasionally 2. A 1 s cache TTL absorbs the cost.
- A batch shape commits us to a list response, query-param port parsing, and a partial-success policy. None of that is free, and none of it is necessary for the cadence at hand.

If a future deployment surfaces N-port-per-node footers as a real cost, file a separate Issue and amend this ADR.

## Consequences

### Positive

- The footer's per-token cost on the agent host drops from "process scan + GPU probe + ~1 KB" to "dict lookup + ~80 B" once the TTL is warm.
- The footer no longer breaks when `/status` grows new fields.
- The contract is auditable: anyone reading ADR-012 can predict every byte of the response.
- The cache is small enough to be obviously correct (one file: `footer_cache.py`, no background tasks, no invalidation graph).

### Negative

- The footer can show up to `LAUNCHER_FOOTER_CACHE_S` of staleness after a swap. At the default 1 s this is invisible to a human; at higher values (set deliberately to reduce probe traffic) it becomes the user's choice.
- A footer reading from a lockfile whose owning process has died will show ghost data until the next operation on that port reconciles the lockfile. We accept this; the inference channel will fail on the same port and the user will know.
- Pinning the response shape adds a small change-management cost: future maintainers must amend this ADR rather than expanding the response ad-hoc. This is the intended cost.

### Open Questions

- **Should `expires_in_s` be part of the response?** A consumer with its own client-side cache could use it to align retry cadence. Argument against: the consumer already controls its poll interval, and surfacing internal cache TTL leaks an implementation detail across the contract boundary. Deferred; revisit if a real consumer asks.
- **Should the endpoint expose `started_at` from the lockfile?** Useful for "model has been up for 12m" badges. Deferred — the footer doesn't need it today and we don't speculatively expand pinned contracts. Add via amendment when a real consumer surfaces.

## Implementation Notes (2026-05-16)

Touch points (see `git log --grep "closes #53"` for the landing commit):

- `llauncher/agent/footer_cache.py` — new. `FooterContext` dataclass, `get_footer_context(port)` entry point, module-level dict + lock.
- `llauncher/agent/routing.py` — new `GET /footer-context/{port}` handler.
- `llauncher/core/settings.py` — new `LAUNCHER_FOOTER_CACHE_S` env var (float, default 1.0).
- `tests/unit/test_footer_cache.py` — new. Cache hit/miss/expiry, missing-lockfile, missing-config, lock contention.
- `tests/unit/test_agent.py` — new `TestFooterContextEndpoint` class. Happy path, port-empty 404, missing-config-graceful-null, auth-required-when-configured.
- `pi-footer-extension/footer-budget.ts` — separate slice. Switch URL with a 404→`/status` fallback for one release cycle, then drop the fallback.
