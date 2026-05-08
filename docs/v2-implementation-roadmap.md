# v2 Implementation Roadmap

**Date:** 2026-05-02 (updated 2026-05-05)  
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
| M1 — Foundation | ✅ done (2026-05-02) | 4 commits, 555 tests passing (all green); see `docs/v2-handoff.md` |
| M2 — Swap + Endpoints | ✅ done (2026-05-07) | M3 merge wired all surfaces to `operations.swap()`; closes #37, #40, #43, #46. |
| M3 — Multi-node | ✅ done (2026-05-07) | Wired through v2 operations; remote swap parity. |
| M4 — UI rewrite | 📋 planned (2026-05-08) | Punch-list filed: #48–#51 (4 slices). Pre-req cleanup: #57 #58 #59. |
| M5 — Tier 2 ADRs | 📋 planned (2026-05-08) | Punch-list filed: #52–#56 (5 ADRs). Logs (#52) lands first to support M4 smoke testing. |
| M6 — Multi-backend (vLLM) | — | Issue #42 |
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

**Slice 1 (✅ done, commit `dd5f7dd`):**
- [x] Tool-layer `swap_server` with full ADR-011 mechanic (5 phases, rollback, in-flight marker) — `operations.py::swap()` + `core/marker.py`
- [x] Config snapshot at pre-flight for rollback
- [x] Pluggable `model_health_check` and `vram_check` callable seams (not yet wired)
- [x] 32 new tests in `test_operations.py` and `test_marker.py`

**Slice 2+ (remaining):**
- [ ] Wire `core/model_health.py` into swap pre-flight (ADR-005)
- [ ] Wire `core/gpu.py` into swap pre-flight (ADR-006)
- [ ] HTTP Agent endpoint refactor per ADR-010: port-keyed routes `POST /start/{port}`, `POST /swap/{port}`
- [ ] MCP server tools mirror HTTP shape; tool-prompt text from ADR-010 §Tool Prompt Guidance
- [ ] Model Delete operation (closes #37) — `operations.delete_model(name)` with lockfile check
- [ ] CLI swap subcommand — `llauncher server swap <port> <model>`
- [ ] Wire all surfaces to `operations.swap()` (currently MCP uses v1 `state._start_with_eviction_impl()`)

**⚠ Dual-swap warning:** Two swap implementations coexist. `operations.swap()` (v2, ADR-011) is not wired to any surface. The HTTP Agent `/start-with-eviction/` and MCP `swap_server` both use v1 `state._start_with_eviction_impl()`. All surfaces must migrate to `operations.swap()` before M2 is complete.

**Deliverable:** all three surfaces (CLI, HTTP, MCP) work for single-node ops.  
**Estimate:** ~3–4 sessions.

### M3 — Multi-node

**Status: Implemented in pre-v2 code.** The infrastructure exists but is NOT wired to the v2 `operations` layer.

- [x] `nodes.json` per-node peer list (per ADR-009) — `llauncher/remote/registry.py`
- [x] Remote dispatch via httpx — `llauncher/remote/node.py::RemoteNode` with `ping()`, `get_status()`, `start_server()`, `stop_server()`, `get_logs()`
- [ ] Self-loop short-circuit when target resolves to this node
- [x] Auth pass-through (`X-Api-Key`) — `RemoteNode` sends header on all calls
- [ ] Wire multi-node dispatch through v2 `operations.py`

**Deliverable:** target a peer from this node.  
**Estimate:** ~1–2 sessions (infra done, wiring pending).

### M4 — UI rewrite

**Status: planned (2026-05-08).** Pre-v2 UI exists but uses v1 `LauncherState`. Decomposed into 4 slices per `docs/m4-design.md`:

- [ ] **Slice 11** ([#48](https://github.com/shanevcantwell/llauncher/issues/48)) — Reusable `node_selector` component
- [ ] **Slice 12** ([#49](https://github.com/shanevcantwell/llauncher/issues/49)) — Drop auto-spawn-local-agent (closes audit H2)
- [ ] **Slice 13** ([#50](https://github.com/shanevcantwell/llauncher/issues/50)) — Restructure tabs around verbs + new audit tab
- [ ] **Slice 14** ([#51](https://github.com/shanevcantwell/llauncher/issues/51)) — Centralize op-result rendering in `ui/utils.py`

**Pre-req cleanup:** [#57](https://github.com/shanevcantwell/llauncher/issues/57) (C2 layer), [#58](https://github.com/shanevcantwell/llauncher/issues/58) (C3 port required), [#59](https://github.com/shanevcantwell/llauncher/issues/59) (H1 MCP refresh).

**Deliverable:** browser-driven daily use works against v2 operations.  
**Estimate:** ~2–3 sessions (UI exists; this is a rewrite of the call-graph + restructure).

### M5 — Tier 2 ADRs + Implementation

**Status: planned (2026-05-08).** Five ADRs per `docs/m5-design.md`, decomposed into Issues:

- [ ] **ADR-013 — Logs lifecycle** ([#52](https://github.com/shanevcantwell/llauncher/issues/52)): append mode, size-cap rotation, bounded tail. **Lands first** so M4 smoke testing doesn't lose history.
- [ ] **ADR-012 — Footer contract** ([#53](https://github.com/shanevcantwell/llauncher/issues/53)): `/footer-context/{port}` endpoint with 1s TTL cache.
- [ ] **ADR-014 — Cancellation** ([#54](https://github.com/shanevcantwell/llauncher/issues/54)): cancel flag in marker; `POST /cancel/{port}`; MCP tool.
- [ ] **ADR-015 — Orphan policy** ([#55](https://github.com/shanevcantwell/llauncher/issues/55)): `is_managed` flag, `orphan list/adopt` verbs across CLI/HTTP/MCP.
- [ ] **ADR-016 — Self-swap worked example** ([#56](https://github.com/shanevcantwell/llauncher/issues/56)): integration test + prose timeline.

**Late audit cleanup running alongside M5:** [#60](https://github.com/shanevcantwell/llauncher/issues/60) (H3), [#61](https://github.com/shanevcantwell/llauncher/issues/61) (H4), [#62](https://github.com/shanevcantwell/llauncher/issues/62) (self-loop).

**Estimate:** ~5–7 sessions (one per ADR + impl).

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

| Issue | Title | Milestone | Status |
|-------|-------|-----------|--------|
| #37 | Add model Delete | M2 | ✅ closed |
| #38 | Volume-mount paths | M1 | partial |
| #39 | Audit commanded vs observed | M1 | partial (#60 closes config-CRUD gap) |
| #40 | Endpoint refactor (port-keyed) | M2 | ✅ closed |
| #41 | CLI naming | — | ✅ resolved: `llauncher` |
| #42 | Backend adapter (vLLM) | M6 | open |
| #43 | VRAM consolidation | M2 | ✅ closed |
| #46 | v1 test cleanup | M2 | ✅ closed |
| #47 | UI migration umbrella | M4 | subsumed by #48–#51 |
| #48–#51 | M4 slices 11–14 | M4 | open |
| #52–#56 | M5 ADRs 013, 012, 014, 015, 016 | M5 | open |
| #57–#62 | Audit cleanup C2, C3, H1, H3, H4, self-loop | pre/post M4 | open |

## References

- ADRs 008–011 (`docs/adrs/`)
- ADR-008 amendment notes (2026-05-02)
- ADR-002 (Superseded by ADR-011)
- Orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md`)
- Issues #37–#42
- prompt-prix architecture as adapter-pattern reference (`~/github/shanevcantwell/prompt-prix/docs/ARCHITECTURE.md`)
