# Handoff — Server-Metrics Monitoring Design + Architecture Governance

**Date:** 2026-06-19 (UTC) · **Author:** orchestrator session · **Status:** design + governance complete; implementation not started.

## TL;DR

A "monitor llama-server instances" feature request became three things: (a) llauncher's first governance-grade **`docs/ARCHITECTURE.md`**, (b) a ratified **ADR-LLNCH-019** defining the metrics *surface*, and (c) several reconciliations/boundary corrections. **No monitoring code was written** — ADR-LLNCH-019 implementation is the next step (`auto:fix`).

## Landed on `main` (llauncher, HEAD `2e4de8d`)

| PR | What |
|---|---|
| #172 | `docs/ARCHITECTURE.md` — 7-rule layering+mint invariant, audited Branch-B conformance. Recorded 2 live violations. |
| #173 | `CLAUDE.md` — `EMIT-CANONICAL` reconciled to **satisfied** (it had shipped at `core/process.py:build_command`; the instructions were stale). |
| #177 | deleted stale `pi-footer-extension/` (subsumed by harness-tools). |
| #178 | **ADR-LLNCH-019** (`docs/adrs/accepted/adr-llnch-019-server-metrics-surface.md`) — the metrics surface. |

## Open items

- **design-docs #4** (roadmap "Phase 1 landed") — **STILL OPEN, decision pending.** design-docs `main` advanced to a tombstone commit (`agent-constitution: subsumed into harness-tools`) — design-docs is migrating into harness-tools. Decide: merge #4 into design-docs as-is, or redirect the edit to wherever `ecosystem-ground-physics` lands.
- **Issues (llauncher; tracked, not fixed):** #170 / #171 (architecture violations — remediation), #174 (node-identity mint), #175 (multi-node-at-scale), #176 (Streamlit operator monitor — the deferred "ADR-020").

## The monitoring design (ADR-LLNCH-019 is the durable spec)

Third monitoring layer = **live in-server inference telemetry** (alongside process-liveness and ADR-LLNCH-006 GPU metrics). Shape:

- **`core/server_metrics.py`** — stateless point-in-time reader, peer to `core/gpu.py`; TTL cache; injectable fetch seam (test-only); degraded envelope (`{available:false, reason}`).
- **Two physical tiers:** aggregate (`/health`+`/metrics`+lockfile `started_at`; phase/tok-s/KV%/slots; **no prompt text**) vs slots (`/slots`; **prompt text**, sensitive). Sensitivity = which endpoint/tool, never a flag.
- **Agent endpoints:** `GET /server-metrics/{port}` + `/server-slots/{port}` — port-keyed, pinned, auth = `/status`. **Composes with ADR-LLNCH-012** (`/footer-context/{port}` static context + live activity = full footer view).
- **MCP:** `server_metrics` / `server_slots` — tool def is the permission gate; **local-node by design** (fleet = `remote`/UI path).
- **Flags:** `--metrics` always-on/launcher-owned (like `--alias`); `--slots` opt-in per `ModelConfig`; both in `DENIED_EXTRA_ARG_FLAGS`.
- **Identity:** canonical `ModelConfig.name` + `node_identity()` stub (→#174); series key `(node, port, canonical_name)`.

## Consumer & repo boundary (load-bearing for re-entry)

- **v1 consumer = the pi footer extension**, canonical home **harness-tools** (`pi/extensions/footer-budget`). It is **read-only reference from llauncher** — its code is built *from harness-tools* against this surface. Bugs are the cross-repo channel, but the direction is consumer→provider; do **not** author footer-budget or harness-tools issues from llauncher work.
- **Reachability is solved:** the container reaches the llauncher agent at **:8765** — `pi-jail/squid/squid.conf` already whitelists 8081/8082/**8765** to both `inference-host` and `shane-pc`. Host is derived from the active pi provider's baseUrl (`llauncher-i9`→`inference-host:8765`; `llauncher-pc`→`shane-pc:8765`). **Target the agent (8765), never Streamlit (8501).**
- The shipped footer drifted to polling model servers directly; routing via :8765 (ADR-LLNCH-012 + ADR-LLNCH-019) is a **return to the original requirements intent**.

## Next step

Implement ADR-LLNCH-019 — tracked as **#179** (`auto:fix`). Scope, test plan, gates, and the in-code shortcut-note requirement (#174/#175/#176) live in the issue, not here (docs convention: dossiers document design; tracked work lives in GH Issues).

## Gotchas / environment

- **Stay out of `~/.pi`** — stale; *not* the pi install. pi runs from the volume `~/pi-projects/v3/`.
- **harness-tools** is the chosen central resource (may be renamed); it is canon. **design-docs is being migrated into harness-tools** (tombstones in progress).
- Repos are divided intentionally — don't dilute across them.

## Re-entry pointers

- `docs/ARCHITECTURE.md` — the invariant the work is governed by.
- `docs/adrs/accepted/adr-llnch-019-server-metrics-surface.md` — the spec to implement.
- `docs/adrs/completed/adr-llnch-012-footer-context-endpoint.md` — the composing sibling endpoint.
- Issues #174 / #175 / #176 — tracked shortcuts & deferrals.
