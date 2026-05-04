# v2 Implementation Roadmap

**Date:** 2026-05-02  
**Status:** Active  

## Purpose

Capture the implementation plan for the v2 architecture (ADRs 008–011) so the work can be picked up cold in a future session without reconstructing the planning context.

## Strategy: Direct on `main`, Repo Frozen

The repo is frozen for v1 work except for this v2 effort. All v2 commits land directly on `main`. No parallel branch, no cutover ceremony.

- Implication: the daily-driver llauncher will regress during the rewrite (especially during M1–M2 when core data structures change). Accepted tradeoff in exchange for the simpler workflow.
- ADR-011's "rewrite, not migration" framing applies — no compat-shim layer.

**Pre-M1 action:** tag the current `main` HEAD as `v1-final` before any M1 commits, to preserve the last working v1 state for emergency reference.

## Progress

| Milestone | Status | Notes |
|-----------|--------|-------|
| Pre-M1 | ✅ done | `v1-final` tag pushed |
| M1 — Foundation | ✅ done (2026-05-02) | 4 commits, 522 tests passing; see `docs/v2-handoff.md` |
| M2 — Swap + Endpoints | ⏳ next | ADR-011 mechanic + endpoint refactor |
| M3 — Multi-node | — | |
| M4 — UI | — | |
| M5 — Tier 2 ADRs + impl | — | |
| M6 — Multi-backend (vLLM) | — | |
| M7 — Release | — | |

For a self-contained guide a fresh context can use to pick up the work, see [`docs/v2-handoff.md`](v2-handoff.md).

## Pre-Implementation Decisions

| Decision | Resolution |
|----------|-----------|
| CLI naming | **`llauncher`** (closes #41) |
| Build location | Direct on `main` (repo frozen for v1 work) |
| Migration policy | **Silent drop** of old config fields; no migration log |

## Milestones

### M1 — Foundation

**Issues:** #38 (volume-mount paths), #39 (audit commanded vs observed)

- Pydantic models v2: no `default_port`; backend `kind` enum scaffolding (even if only `llama_server` is implemented in M1, the discriminated-union shape is set up for M6).
- `ConfigStore`: load/save with silent drop of old fields.
- Settings: env vars for `LAUNCHER_RUN_DIR`, `LAUNCHER_AUDIT_PATH`.
- Lockfile module: atomic write, reconciliation rules per ADR-008.
- Audit log: JSONL append-only, `commanded` vs `observed_*` events.
- Tool-layer `start_server`, `stop_server` against local infra.
- Minimal CLI (`llauncher start`, `llauncher stop`, `llauncher list`).

**Deliverable:** start/stop a single llama-server model from the CLI; lockfile + audit log behave per ADR-008.  
**Estimate:** ~4–6 sessions.

### M2 — Swap + Endpoints

**Issues:** #37 (model Delete), #40 (endpoint refactor)

- Tool-layer `swap_server` with full ADR-011 mechanic (5 phases, rollback, in-flight marker).
- HTTP Agent (FastAPI) with port-keyed routes per ADR-010: `POST /start/{port}`, `POST /swap/{port}`, `POST /stop/{port}`.
- MCP server with tools mirroring HTTP shape; tool-prompt text from ADR-010 §Tool Prompt Guidance.
- Model Delete operation (closes #37).

**Deliverable:** all three surfaces (CLI, HTTP, MCP) work for single-node ops.  
**Estimate:** ~3–4 sessions.

### M3 — Multi-node

- `nodes.json` per-node peer list (per ADR-009).
- Remote dispatch via httpx in tool layer.
- Self-loop short-circuit when target resolves to this node.
- Auth pass-through (`X-Api-Key`).

**Deliverable:** target a peer from this node.  
**Estimate:** ~2–3 sessions.

### M4 — UI

- Streamlit dashboard: model card, node selector, server list.
- ModelConfig CRUD forms.
- **Drop the auto-spawn-local-agent behavior** — the orientation spike flagged it as fighting ADR-009's symmetric topology. The UI now requires the agent to be running already.

**Deliverable:** browser-driven daily use works.  
**Estimate:** ~2–3 sessions.

### M5 — Tier 2 ADRs + Implementation

Drafting and shipping the five deferred items per spike §5:

- Footer contract (REST shape, polling cadence, response stability).
- Logs lifecycle (rotation, retention; fixes the dead `logs_path` field and the sanitization-collision foot-gun).
- Cancellation of in-flight start/swap.
- Orphan policy (process matches argv but no lockfile).
- Canonical self-swap worked example as an integration test.

**Estimate:** ~5–7 sessions (one per item, ADR + impl).

### M6 — Multi-backend (vLLM)

**Issues:** #42

- ADR-012: backend adapter layer (per #42's outline).
- Discriminated-union `ModelConfig`.
- Extract `LlamaServerAdapter` from existing process-build code.
- New `VLLMAdapter`.
- Amend ADR-005, ADR-006, ADR-008 per #42's notes.

**Estimate:** ~3–5 sessions.

### M7 — Release

- Update pi-coding-agent's TypeScript extension for the renamed endpoints (per ADR-010).
- Tag the v2-complete commit as `v2.0.0`.
- Migration is silent and handled inline by `ConfigStore.load()` (already in M1); no separate script needed.

**Estimate:** ~1 session.

## Total Estimate

**~20–30 sessions** of focused implementation work.

## Critical Path and Pacing

- **M1 → M2** is the critical path. Once M2 lands, you have a working v2; everything after is extensions.
- **M3 → M4** restore daily-driver capability (multi-node + UI).
- **M5 → M7** can run in any order after M4, paced by capacity.

## Issue ↔ Milestone Map

| Issue | Title | Milestone |
|-------|-------|-----------|
| #37 | Add model Delete | M2 |
| #38 | Volume-mount paths | M1 |
| #39 | Audit commanded vs observed | M1 |
| #40 | Endpoint refactor (port-keyed) | M2 |
| #41 | CLI naming | **Resolved: `llauncher`** |
| #42 | Backend adapter (vLLM) | M6 |

## References

- ADRs 008–011 (`docs/adrs/`)
- ADR-008 amendment notes (2026-05-02)
- ADR-002 (Superseded by ADR-011)
- Orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md`)
- Issues #37–#42
- prompt-prix architecture as adapter-pattern reference (`~/github/shanevcantwell/prompt-prix/docs/ARCHITECTURE.md`)
