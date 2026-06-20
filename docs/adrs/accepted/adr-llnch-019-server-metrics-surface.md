# ADR-LLNCH-019: Server-Metrics Surface — Live In-Server Inference Telemetry

**Status:** Accepted (ratified 2026-06-19; implementation not yet begun)
**Date:** 2026-06-19
**Relationship to other ADRs:**
- **ADR-012 (Footer Context Endpoint)** is the *static* sibling: it serves model/ctx_size/parallel from the lockfile + ConfigStore and **deliberately never contacts the live server**. ADR-LLNCH-019 supplies the *live* telemetry ADR-012 explicitly excludes. They **compose** — static context + live activity = the full footer view. ADR-LLNCH-019 does not modify ADR-012's pinned `/footer-context/{port}` contract.
- **ADR-006 (GPU Resource Monitoring)** is the collector-pattern precedent: `core/server_metrics.py` is a peer to `core/gpu.py` (stateless collector, short TTL cache, degraded envelope, injectable backend seam).
- **ADR-003 (Agent API Authentication)** governs the sensitive endpoint's auth (matches `/status`, no exemption).
- **ADR-009 (Hub/Spoke)** — fleet aggregation is the `remote`/UI path, not MCP. **ADR-010** — endpoints are port-keyed.
- **`docs/ARCHITECTURE.md`** — rule 3 (stateless facade), rules 4–5 (mint / EMIT-CANONICAL: payload keyed by canonical name), and the explicit out-of-scope clause (managed llama-server internals are a *new core consumer*, not a layering exception).

## Context

llauncher has three monitoring layers; two exist, the third is the gap:

| Layer | Source | Status |
|---|---|---|
| Process liveness | lockfile + PID table | `server_status` (exists) |
| Hardware | `nvidia-smi` | ADR-006 (exists) |
| **In-server inference telemetry** | the model server's own HTTP endpoints | **this ADR** |

llama-server exposes `/health` (ready/loading/error), `/metrics` (Prometheus: prompt & generation tok/s, KV-cache usage, requests processing/deferred — only when started with `--metrics`), and `/slots` (per-slot state + prompt text, gated by `--slots`). Nothing in llauncher reads them.

**Driving use case (read-only reference; consumer lives in another repo).** Replace "tail three folders of per-model logs to confirm the active model is doing something" with a minimal **activity indicator**: phase (idle → prompt-processing → generating), prediction tok/s, a coarse timer, and the canonical model name. The v1 consumer is the pi footer extension (canonical home: the harness-tools repo), which reaches llauncher's **agent at port 8765** (host derived from the active provider's baseUrl; squid already whitelists 8765). Per the repo boundary, that consumer is **read-only reference here** — llauncher builds the surface; the consumer is built from its own repo against this contract.

**Why ADR-012 doesn't cover it.** ADR-012's endpoint is cheap *because* it never touches the live server (lockfile + config only). Live activity (tok/s, slot phase) requires contacting the model server, a different cost class — folding it into `/footer-context/{port}` would break that endpoint's pinned, probe-free premise. Hence a separate surface.

## Decision

### 1. `core/server_metrics.py` — a stateless, point-in-time reader (peer to `core/gpu.py`)

- **Stateless per call.** Each read polls the target model server over localhost HTTP and returns a snapshot. No cross-call accumulation (rule 3). A short TTL cache (default ~2 s, `LLAUNCHER_METRICS_CACHE_S`) absorbs poll cadence, mirroring ADR-006's collector and ADR-012's footer cache.
- **Injectable fetch seam** (test-only, production-inert) — mirrors `core/gpu.py`'s `nvidia-smi` mock. The Prometheus-text parser is unit-tested without a live server.
- **Outbound HTTP from `core` is not an upward import** — it's a client call, exactly as `gpu.py` shells out to `nvidia-smi`. The layer rule holds.

### 2. Two capability tiers — physically separate, not a flag

| Tier | Reads | Returns | Sensitivity |
|---|---|---|---|
| **aggregate** | `/health` + `/metrics`, + lockfile `started_at` | `{state, phase: idle\|prompt\|generating, gen_tok_s, prompt_tok_s, kv_cache_pct, slots_busy, slots_total, requests_deferred, started_at}` | safe — no prompt text |
| **slots** | `/slots` | per-slot detail **including prompt text** | sensitive |

Sensitivity is enforced by *which method/endpoint/tool* you call, never by a `include_prompts=true` argument on a shared call. `started_at` is sourced from the lockfile (the field ADR-012 deferred) and folded into the aggregate payload — a coarse uptime/timer at no extra probe cost.

### 3. Degraded envelope (PARSE-AT-THE-DOOR)

Each tier returns `{available: false, reason: "loading" | "no-metrics-flag" | "unreachable"}` rather than crashing or trust-and-degrading. A server mid-load (`/health` 503), or one started without the flag, or unreachable, each yields a structured, pure-function-of-current-state result.

### 4. Identity — canonical, node-aware

Each snapshot is stamped with the canonical `ModelConfig.name` (EMIT-CANONICAL — the wire already reports it) plus a `node_identity()` resolver. **Series identity = `(node, port, canonical_name)`**; `port` is the within-node instance discriminator, `canonical_name` keeps a swap from corrupting a series. `node_identity()` returns the agent self-report (`/status.node`) today — the simple authority — with the mint hardening tracked at **#174** (the resolver is the single swap point).

### 5. Agent endpoints — port-keyed, pinned, auth = `/status` (ADR-012 DNA)

- `GET /server-metrics/{port}` — aggregate tier (safe).
- `GET /server-slots/{port}` — sensitive tier; returns `404 slots_disabled` if the server was not started with `--slots`.
- **Node = the connection target** (the agent serves its own node); no node in the path, consistent with ADR-010/-012.
- **Separate from `/status`** so the cross-node-aggregated `/status` and the pinned `/footer-context/{port}` stay cheap — per-server `/metrics` round-trips don't burden them.
- Same `AuthenticationMiddleware` as `/status`; **not** auth-exempt (the slot tier especially must not be an unauthenticated prompt-text channel).
- Response shapes are **pinned by this ADR** (ADR-012 precedent): extend only by amendment.

### 6. MCP tools — `server_metrics(port)` / `server_slots(port)`

- Mirror `get_server_logs` (port-keyed, state-refreshed read tools).
- **The tool definition is the permission gate.** A client granted `server_metrics` need not hold `server_slots` — clean allow/deny for prompt-text exposure.
- **MCP is local-node by design** (stdio, this node only), like `server_status`/`get_server_logs`. Fleet monitoring is the `remote`/UI path (ADR-009), not MCP — stated explicitly so a port-keyed MCP tool is never read as fleet-wide.

### 7. Flag policy (`ModelConfig` / `build_command`)

- **`--metrics` always-on, launcher-owned** — emitted unconditionally in `core/process.build_command` (like `--alias`), so availability is uniform and the reader is a pure function of current state (no hidden "was it started with the flag?" mode).
- **`--slots` opt-in per `ModelConfig`** (default off; it exposes prompt text on a shared host).
- Both added to `DENIED_EXTRA_ARG_FLAGS` so the policy is single-sourced (no config override).

### 8. Composition with ADR-012

The footer's full view = ADR-012 `/footer-context/{port}` (model, ctx_size, parallel — static) **+** ADR-LLNCH-019 `/server-metrics/{port}` (phase, tok/s — live). Two cheap, pinned, port-keyed endpoints on the same agent; the consumer composes them.

## What this ADR does NOT cover (scope)

- **The consumer indicators.** The pi-footer activity indicator is built in the harness-tools repo against this surface; it is read-only reference here.
- **llauncher's own Streamlit monitor + accumulation/ring-buffer** — deferred (**#176**; the would-be ADR-020).
- **Server-side SSE streaming** — v1 is point-in-time; consumers poll and accumulate. SSE deferred.
- **Multi-node fleet aggregation at scale** — v1 assumes local + ≤1 remote (**#175**).
- **Node-identity mint hardening** — `node_identity()` returns the simple authority now (**#174**).

## YAGNI shortcuts & extensibility stubs

Each shortcut ships the simple path, stubs the extensible shape, carries an in-code note at its seam, and is tracked:

| Shortcut (assume) | Ships now | Stub seam | Tracked |
|---|---|---|---|
| ≤1 remote node | local + ≤1 remote | `(node,port,name)` key + aggregator iteration | #175 |
| node identity un-minted | `node_identity()` → agent self-report | the one resolver = swap point | #174 |
| point-in-time only | poll; consumer accumulates | payload shape is SSE/history-ready | #176 |
| single consumer | structured payload + thin agent/MCP | any consumer reads the same contract | footer (harness-tools) |

## Testing

- **Unit:** Prometheus-text parser via injected fetch seam (no live server) — phase derivation, tok/s, `kv_cache_pct`, slot counts; mirrors `gpu.py` mock tests.
- **Unit:** degraded envelope per reason (loading 503, no `--metrics`, unreachable); `404 slots_disabled` when `--slots` absent.
- **Unit:** identity stamping — canonical name + `node_identity()` resolver.
- **Unit:** flag policy — `--metrics` always emitted; `--slots` only when configured; both rejected from `extra_args`.
- **Integration:** start a server (runtime driving is permitted), poll `/server-metrics/{port}`, assert phase/rate; auth required when `LLAUNCHER_AGENT_TOKEN` set.
- Coverage profile maximized over changed paths; non-UI ≥93% gate.

## Consequences

### Positive
- Activity is visible without log-tailing; the footer can return to consuming llauncher (its original ADR-012-era intent) instead of polling model servers directly.
- Single-authority canonical identity on the metrics (mint), and tiered, tool-gated prompt-text exposure.
- Stateless — no facade violation; accumulation lives in consumers.

### Negative
- Per-server HTTP round-trip latency (mitigated: TTL cache + separate endpoint keeps `/status` and `/footer-context` cheap).
- `--slots` is a prompt-text surface (mitigated: opt-in + separate endpoint/tool + auth).
- Depends on llama-server exposing `/metrics` (mitigated: always-on launcher-owned flag).

### Resolved at ratification (2026-06-19)
- **`started_at` included.** The aggregate payload carries `started_at` (from the lockfile — the field ADR-012 deferred), giving the coarse uptime/timer both consumers want.
- **No batch variant.** `/server-metrics?ports=…` is rejected (ADR-012 §5 reasoning: one occupied port is typical, the TTL absorbs the cost). Revisit only if a real N-port-per-node cost surfaces.

## Relation to the decision record

| ADR / decision | Relationship |
|---|---|
| ADR-012 footer-context | static sibling; ADR-LLNCH-019 adds the live telemetry it excludes; composes; does not alter its pinned contract |
| ADR-006 gpu monitoring | collector pattern (`core/server_metrics.py` ⟷ `core/gpu.py`) |
| ADR-003 agent auth | governs the sensitive slot endpoint |
| ADR-009 hub/spoke | fleet path is remote/UI, not MCP |
| ADR-010 port ownership | endpoints port-keyed |
| ARCHITECTURE rules 3/4/5 + out-of-scope | stateless reader; canonical identity; managed-server internals as a new core consumer |
| Issues #174 / #175 / #176 | tracked shortcuts & deferrals |
