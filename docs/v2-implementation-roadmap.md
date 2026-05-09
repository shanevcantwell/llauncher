# v2 Implementation Roadmap

**Date:** 2026-05-02 (updated 2026-05-08 end-of-session)
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
| **Pre-M4 cleanup** | ✅ **done (2026-05-08)** | #57 (C2 layer), #58 (C3 port), #59 (H1 MCP refresh). Test count 612 → 621. |
| M4 — UI rewrite | 🔄 **3/4 slices done (2026-05-08)** | #48 node_selector ✅, #51 render_op_result ✅, #49 auto-spawn dropped ✅. **Only #50 (Slice 13: tab restructure) remains.** |
| M5 — Tier 2 ADRs | 🔄 **1/5 done (2026-05-08)** | ADR-013 logs ✅ (#52). Remaining: #53 (ADR-012 footer), #54 (ADR-014 cancel), #55 (ADR-015 orphan), #56 (ADR-016 self-swap). Parallelizable. |
| Late audit cleanup | 📋 planned | #60 (H3 audit-on-CRUD), #61 (H4 BLE001), #62 (self-loop). Parallelizable with M5. |
| M6 — Multi-backend (vLLM) | — | Issue #42 |
| M7 — Release | — | Tag `v2.0.0`. |

**End-of-2026-05-08 session metrics:** 9 commits on `main` since the prior handoff, 7 issues closed (#48, #49, #51, #52, #57, #58, #59), 1 issue filed (#63), test count 612 → 680 (+68 net), ADR-013 ratified.

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

### Pre-M4 cleanup (✅ done 2026-05-08)

Three audit findings the v2-handoff and m4-design called out as boundary-tightening prerequisites for M4:

- [x] **C2** ([#57](https://github.com/shanevcantwell/llauncher/issues/57), `b361b60`) — model-health pre-flight lifted out of `state.py` into `operations/preflight.py`. `operations.start()` gained the same `model_health_check` seam `operations.swap()` had. `_start_status_code` maps `rejected_preflight` → 409 (not 500).
- [x] **C3** ([#58](https://github.com/shanevcantwell/llauncher/issues/58), `270a43e`) — port required at every API/operations boundary. CLI `--port` is required; `state.start_server` and `state.can_start` no longer accept `port=None`. `DEFAULT_PORT` env-var default 8080 → 8081 (handoff §6 institutional knowledge fix landed in passing).
- [x] **H1** ([#59](https://github.com/shanevcantwell/llauncher/issues/59), `de9f10f`) — MCP write tools (`update_model_config`, `add_model`) refresh before reading `state.models`. Read tools were already refreshing (audit was stale on that point); regression guards added.

### M4 — UI rewrite

**Status: 3/4 slices done (2026-05-08).** Foundation slices and the auto-spawn removal landed; only the tab restructure remains.

- [x] **Slice 11** ([#48](https://github.com/shanevcantwell/llauncher/issues/48), `e993dcc`) — Reusable `node_selector` component at `llauncher/ui/components/node_selector.py`. Writes to `st.session_state["ui.target_node"]`. 11 tests.
- [x] **Slice 12** ([#49](https://github.com/shanevcantwell/llauncher/issues/49), `0d06b89`) — Auto-spawn dropped. `NodeRegistry.start_local_agent` deleted; UI shows a passive `show_agent_down_banner` instead. Closes audit H2.
- [ ] **Slice 13** ([#50](https://github.com/shanevcantwell/llauncher/issues/50)) — **Tab restructure** (next session). Merge dashboard+running, merge forms+model_registry, delete `manager.py`, add `audit.py` tab, rewire `app.py` routing. Consumes both #48 and #51. Cleans up the `find_available_port(None)` fallback in `model_card.py:302` once a port picker exists.
- [x] **Slice 14** ([#51](https://github.com/shanevcantwell/llauncher/issues/51), `1f55f3a`) — `ui/utils.py::render_op_result()` translates any `operations/*Result` envelope into Streamlit feedback via the `OpResultSeverity` ladder. 32 tests.

**Deliverable:** browser-driven daily use works against v2 operations.
**Remaining estimate:** ~1 session (Slice 13 only).

### M5 — Tier 2 ADRs + Implementation

**Status: 1/5 done (2026-05-08).** ADR-013 ratified; the rest are parallelizable and can run in any order after M4 finishes.

- [x] **ADR-013 — Logs lifecycle** ([#52](https://github.com/shanevcantwell/llauncher/issues/52), `9dc2769`) — Append mode + size-cap rotation + bounded tail. New module `core/log_rotation.py`. New env vars `LAUNCHER_LOG_DIR`, `LAUNCHER_LOG_MAX_BYTES`, `LAUNCHER_LOG_KEEP`. 17 tests including a partial-rename-failure simulation. Filed [#63](https://github.com/shanevcantwell/llauncher/issues/63) for the sanitizer-collision side concern.
- [ ] **ADR-012 — Footer contract** ([#53](https://github.com/shanevcantwell/llauncher/issues/53)): `/footer-context/{port}` endpoint with 1s TTL cache.
- [ ] **ADR-014 — Cancellation** ([#54](https://github.com/shanevcantwell/llauncher/issues/54)): cancel flag in marker; `POST /cancel/{port}`; MCP tool.
- [ ] **ADR-015 — Orphan policy** ([#55](https://github.com/shanevcantwell/llauncher/issues/55)): `is_managed` flag, `orphan list/adopt` verbs across CLI/HTTP/MCP.
- [ ] **ADR-016 — Self-swap worked example** ([#56](https://github.com/shanevcantwell/llauncher/issues/56)): integration test + prose timeline. Depends on #54.

**Late audit cleanup running alongside M5:** [#60](https://github.com/shanevcantwell/llauncher/issues/60) (H3 audit-on-CRUD), [#61](https://github.com/shanevcantwell/llauncher/issues/61) (H4 BLE001 in `operations/*`), [#62](https://github.com/shanevcantwell/llauncher/issues/62) (self-loop short-circuit).

**Remaining estimate:** ~4–5 sessions (one per ADR/cleanup, parallelizable).

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
| #38 | Volume-mount paths | M1 | partial — `LAUNCHER_LOG_DIR` added by ADR-013 |
| #39 | Audit commanded vs observed | M1 | partial — #60 closes the config-CRUD gap |
| #40 | Endpoint refactor (port-keyed) | M2 | ✅ closed |
| #41 | CLI naming | — | ✅ resolved: `llauncher` |
| #42 | Backend adapter (vLLM) | M6 | open |
| #43 | VRAM consolidation | M2 | ✅ closed |
| #46 | v1 test cleanup | M2 | ✅ closed |
| #47 | UI migration umbrella | M4 | subsumed by #48–#51; closes with #50 |
| #48 | M4 Slice 11 — node_selector | M4 | ✅ closed (`e993dcc`) |
| #49 | M4 Slice 12 — drop auto-spawn | M4 | ✅ closed (`0d06b89`) |
| #50 | M4 Slice 13 — tab restructure | M4 | **open — only M4 slice left** |
| #51 | M4 Slice 14 — render_op_result | M4 | ✅ closed (`1f55f3a`) |
| #52 | M5 / ADR-013 — logs lifecycle | M5 | ✅ closed (`9dc2769`) |
| #53 | M5 / ADR-012 — footer contract | M5 | open |
| #54 | M5 / ADR-014 — cancellation | M5 | open |
| #55 | M5 / ADR-015 — orphan policy | M5 | open |
| #56 | M5 / ADR-016 — self-swap test | M5 | open (depends on #54) |
| #57 | Audit C2 — state→core layer | pre-M4 | ✅ closed (`b361b60`) |
| #58 | Audit C3 — port required | pre-M4 | ✅ closed (`270a43e`) |
| #59 | Audit H1 — MCP refresh | pre-M4 | ✅ closed (`de9f10f`) |
| #60 | Audit H3 — config-CRUD audit | post-M4 | open |
| #61 | Audit H4 — BLE001 in operations | post-M4 | open |
| #62 | Audit self-loop short-circuit | post-M4 | open |
| #63 | Log filename sanitization collision | side | open (filed during #52) |

## References

- ADRs 008–011 (`docs/adrs/`) — Tier 1, foundation
- ADR-013 (`docs/adrs/013-logs-lifecycle.md`) — accepted 2026-05-08
- ADR-008 amendment notes (2026-05-02)
- ADR-002 (Superseded by ADR-011)
- Orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md`)
- M4/M5 design docs (`docs/m4-design.md`, `docs/m5-design.md`)
- Code audit synthesis (`docs/_audit_synthesis.md` + 5 domain reports)
- Issues #37–#63
- prompt-prix architecture as adapter-pattern reference (`~/github/shanevcantwell/prompt-prix/docs/ARCHITECTURE.md`)
