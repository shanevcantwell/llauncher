# v2 Implementation Roadmap

**Date:** 2026-05-02  
**Status:** Active  

## Purpose

Capture the implementation plan for the v2 architecture (ADRs 008–011) so the work can be picked up cold in a future session without reconstructing the planning context.

## Strategy: Parallel Build + Cutover

- Build v2 on a **`v2` branch** of the main repo (`~/github/shanevcantwell/llauncher/`).
- The `main` branch keeps working — daily-driver llauncher does not regress during the build.
- Cutover via merge into `main` (or fast-forward) when v2 reaches feature parity and has been daily-driven without issue.
- ADR-011's "rewrite, not migration" framing applies — no compat-shim layer.

## Pre-Implementation Decisions

| Decision | Resolution |
|----------|-----------|
| CLI naming | **`llauncher`** (closes #41) |
| Build location | **`v2` branch on main** |
| Migration policy | *open* — silent drop only, or also produce a one-time migration log? |

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

### M7 — Cutover

- Migration script (re-import models from old `config.json`; v1 `default_port` field silently dropped; v1 audit logs not preserved).
- Update pi-coding-agent's TypeScript extension if any endpoints renamed.
- Merge `v2` into `main` (or replace `main`'s tip with the `v2` branch).
- Tag the cutover commit; archive the v1 surface as a tag (`v1-final`) for emergency reference.

**Estimate:** ~1–2 sessions.

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

## Open Questions

1. **Migration policy.** Silent drop of old fields (per current v2 stance) only, or also produce a one-time migration log written to `~/.llauncher/v1-to-v2-migration.log`?
2. **`v2` branch lifecycle.** Linear development with rebases against `main`, or branch-and-merge with regular merges of `main` into `v2` to keep it current with v1 hotfixes?

## References

- ADRs 008–011 (`docs/adrs/`)
- ADR-008 amendment notes (2026-05-02)
- ADR-002 (Superseded by ADR-011)
- Orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md`)
- Issues #37–#42
- prompt-prix architecture as adapter-pattern reference (`~/github/shanevcantwell/prompt-prix/docs/ARCHITECTURE.md`)
