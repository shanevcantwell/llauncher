# v2 Implementation Roadmap

**Date:** 2026-05-02 (updated 2026-05-16 end-of-session)
**Status:** Active

## Purpose

Capture the implementation plan for the v2 architecture (ADRs 008–011) so the work can be picked up cold in a future session without reconstructing the planning context.

## Strategy: Direct on `main`, Repo Frozen

The repo is frozen for v1 work except for this v2 effort. All v2 commits land directly on `main`. No parallel branch, no cutover ceremony.

- Implication: the daily-driver llauncher will regress during the rewrite (especially during M1–M2 when core data structures change). Accepted tradeoff in exchange for the simpler workflow.
- ADR-LLNCH-011's "rewrite, not migration" framing applies — no compat-shim layer.

**Pre-M1 action:** tag the current `main` HEAD as `v1-final` before any M1 commits, to preserve the last working v1 state for emergency reference.

## Progress

| Milestone | Status | Notes |
|-----------|--------|-------|
| Pre-M1 | ✅ done | `v1-final` tag pushed |
| M1 — Foundation | ✅ done (2026-05-02) | 4 commits, 555 tests passing (all green); see `docs/v2-handoff.md` |
| M2 — Swap + Endpoints | ✅ done (2026-05-07) | M3 merge wired all surfaces to `operations.swap()`; closes #37, #40, #43, #46. |
| M3 — Multi-node | ✅ done (2026-05-07) | Wired through v2 operations; remote swap parity. |
| **Pre-M4 cleanup** | ✅ **done (2026-05-08)** | #57 (C2 layer), #58 (C3 port), #59 (H1 MCP refresh). Test count 612 → 621. |
| M4 — UI rewrite | ✅ **done (2026-05-09)** | All 4 slices done. #50 tab restructure + port picker landed in commits `5513d26` (consolidation) and `f7b8818` (Audit tab + node_selector wiring). |
| M5 — Tier 2 ADRs | 🔄 **4/5 done (2026-05-17)** | ADR-LLNCH-013 logs ✅ (#52); ADR-LLNCH-012 footer endpoint ✅ (#53, TS migration deferred); ADR-LLNCH-014 cancellation ✅ (#54); ADR-LLNCH-015 orphan (annotation + list) ✅ (#55, **reduced scope** — adopt verb deferred per ADR-LLNCH-015 §Deferred Work). Remaining: #56 (ADR-LLNCH-016 self-swap). **Phased** — see §Phased Plan below. |
| Audit cleanup | 📋 planned | #60 (H3 audit-on-CRUD), #61 (H4 BLE001), #62 (self-loop), #64 (audit-tab remote). Phase 1 + Phase 4. |
| **Production Hardening** | 🔄 **1/2 done (2026-05-16)** | **Parallel track to M5**, not a v2-architecture milestone. #65 (SIGTERM graceful shutdown) ✅ closed — FastAPI lifespan handler reaps managed llama-server children on SIGTERM and SIGINT. #67 (systemd `.service` units) remaining — Phase 4. |
| M6 — Multi-backend (vLLM) | — | Issue #42 |
| M7 — Release | — | Tag `v2.0.0`. Pre-tag: V1-carryover triage sweep (#10, #14–#27). |

**End-of-2026-05-08 session metrics:** 9 commits on `main` since the prior handoff, 7 issues closed (#48, #49, #51, #52, #57, #58, #59), 1 issue filed (#63), test count 612 → 680 (+68 net), ADR-LLNCH-013 ratified.

**End-of-2026-05-09 session metrics:** 2 commits on `main` since the 2026-05-08 handoff (`f7b8818`, `5513d26`), 2 issues closed (#50, #47), 1 issue filed (#64 — audit-tab remote-node access), test count 680 → 686 (+6 net; planner estimated ~690+, slightly under because more obsolete tests were removed than expected). M4 milestone closed.

**End-of-2026-05-16 session metrics:** ADR-LLNCH-012 ratified and the `/footer-context/{port}` endpoint + per-port TTL cache (`llauncher/agent/footer_cache.py`) landed; #53 closed (TS-side consumer migration deferred). New env var `LAUNCHER_FOOTER_CACHE_S` joins the ADR-LLNCH-008/013 family. **Phase 1 of the phased plan also landed in the same session:** #61 (BLE001 scoped exceptions in `operations/*`), #60 (audit-on-CRUD via ConfigStore, with the layering fix that ConfigStore now owns the `MODEL_REMOVED+SUCCESS` audit while ops layer keeps only operation-level events), #62 (RemoteNode self-loop short-circuit for `ping`/`start`/`stop`/`swap`/`delete_model` verbs). Test count 686 → 722 (+36 net across all four issues).

**End-of-2026-05-16 follow-up session (Phase 2):** #65 closed. FastAPI lifespan handler in `agent/server.py` enumerates `core/lockfile.list_lockfiles()` on shutdown and dispatches each through `operations.stop(caller="agent-shutdown")`. `uvicorn.run(..., lifespan="on")` forces the handler to fire regardless of auto-detection. Symmetric on SIGTERM and SIGINT — behavior change from the pre-#65 bare-`KeyboardInterrupt` path which orphaned children silently (called out in handoff §What NOT To Do). Test count 722 → 728 (+6 net; one new `tests/unit/test_agent_lifespan.py` module).

**End-of-2026-05-16 follow-up session (Phase 3, first half):** ADR-LLNCH-014 ratified and #54 closed. Cancellation verb landed across the stack: `SwapMarker` gained a `cancelled` boolean (back-compat via `data.get("cancelled", False)`); marker module gained `request_cancel()` / `is_cancelled()`; `core/process.wait_for_server_ready` gained an optional `cancel_check` callable for the readiness poll; `operations.start` now takes/releases an in-flight marker (uniform with swap, enabling cancel); `operations.swap` checks cancel at the post-stop checkpoint and during readiness; both verbs surface a `cancel_ignored_post_commit` advisory when a cancel arrives after the lockfile is written. New `POST /cancel/{port}` HTTP endpoint, `cancel_server` MCP tool, and `llauncher server cancel <port>` CLI verb. New `AuditResult.CANCELLED` enum. Test count 728 → 751 (+23 net across `test_marker.py`, `test_operations.py`, `test_agent.py`, `test_servers_tools.py`, `test_cli.py`).

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
- Lockfile module: atomic write, reconciliation rules per ADR-LLNCH-008.
- Audit log: JSONL append-only, `commanded` vs `observed_*` events.
- Tool-layer `start_server`, `stop_server` against local infra.
- Minimal CLI (`llauncher start`, `llauncher stop`, `llauncher list`).

**Deliverable:** start/stop a single llama-server model from the CLI; lockfile + audit log behave per ADR-LLNCH-008.  
**Estimate:** ~4–6 sessions.

### M2 — Swap + Endpoints

**Issues:** #37 (model Delete), #40 (endpoint refactor)

**Slice 1 (✅ done, commit `dd5f7dd`):**
- [x] Tool-layer `swap_server` with full ADR-LLNCH-011 mechanic (5 phases, rollback, in-flight marker) — `operations.py::swap()` + `core/marker.py`
- [x] Config snapshot at pre-flight for rollback
- [x] Pluggable `model_health_check` and `vram_check` callable seams (not yet wired)
- [x] 32 new tests in `test_operations.py` and `test_marker.py`

**Slice 2+ (remaining):**
- [ ] Wire `core/model_health.py` into swap pre-flight (ADR-LLNCH-005)
- [ ] Wire `core/gpu.py` into swap pre-flight (ADR-LLNCH-006)
- [ ] HTTP Agent endpoint refactor per ADR-LLNCH-010: port-keyed routes `POST /start/{port}`, `POST /swap/{port}`
- [ ] MCP server tools mirror HTTP shape; tool-prompt text from ADR-LLNCH-010 §Tool Prompt Guidance
- [ ] Model Delete operation (closes #37) — `operations.delete_model(name)` with lockfile check
- [ ] CLI swap subcommand — `llauncher server swap <port> <model>`
- [ ] Wire all surfaces to `operations.swap()` (currently MCP uses v1 `state._start_with_eviction_impl()`)

**⚠ Dual-swap warning:** Two swap implementations coexist. `operations.swap()` (v2, ADR-LLNCH-011) is not wired to any surface. The HTTP Agent `/start-with-eviction/` and MCP `swap_server` both use v1 `state._start_with_eviction_impl()`. All surfaces must migrate to `operations.swap()` before M2 is complete.

**Deliverable:** all three surfaces (CLI, HTTP, MCP) work for single-node ops.  
**Estimate:** ~3–4 sessions.

### M3 — Multi-node

**Status: Implemented in pre-v2 code.** The infrastructure exists but is NOT wired to the v2 `operations` layer.

- [x] `nodes.json` per-node peer list (per ADR-LLNCH-009) — `llauncher/remote/registry.py`
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

**Status: ✅ done (2026-05-09).** All 4 slices landed.

- [x] **Slice 11** ([#48](https://github.com/shanevcantwell/llauncher/issues/48), `e993dcc`) — Reusable `node_selector` component at `llauncher/ui/components/node_selector.py`. Writes to `st.session_state["ui.target_node"]`. 11 tests.
- [x] **Slice 12** ([#49](https://github.com/shanevcantwell/llauncher/issues/49), `0d06b89`) — Auto-spawn dropped. `NodeRegistry.start_local_agent` deleted; UI shows a passive `show_agent_down_banner` instead. Closes audit H2.
- [x] **Slice 13** ([#50](https://github.com/shanevcantwell/llauncher/issues/50), `f7b8818` + `5513d26`) — **Tab restructure done.** Tabs are now Dashboard / Models / Nodes / Audit. `dashboard.py` is read-only (running view); `models.py` owns config CRUD + start/stop/swap verbs; new `audit.py` tails local audit JSONL. New `ui/components/port_picker.py` requires explicit user input (no fallback) — the `find_available_port(None)` callsite is gone. `manager.py` and `running.py` deleted. `model_registry.py::render_model_registry` parameter renamed `selected_node` → `target`. Remote-node audit access deferred to #64. Also closes #47 (UI migration umbrella).
- [x] **Slice 14** ([#51](https://github.com/shanevcantwell/llauncher/issues/51), `1f55f3a`) — `ui/utils.py::render_op_result()` translates any `operations/*Result` envelope into Streamlit feedback via the `OpResultSeverity` ladder. 32 tests.

**Deliverable:** Done. Browser-driven daily use works against v2 operations.

### M5 — Tier 2 ADRs + Implementation

**Status: 1/5 done (2026-05-08).** ADR-LLNCH-013 ratified; the rest are parallelizable and can run in any order after M4 finishes.

- [x] **ADR-LLNCH-013 — Logs lifecycle** ([#52](https://github.com/shanevcantwell/llauncher/issues/52), `9dc2769`) — Append mode + size-cap rotation + bounded tail. New module `core/log_rotation.py`. New env vars `LAUNCHER_LOG_DIR`, `LAUNCHER_LOG_MAX_BYTES`, `LAUNCHER_LOG_KEEP`. 17 tests including a partial-rename-failure simulation. Filed [#63](https://github.com/shanevcantwell/llauncher/issues/63) for the sanitizer-collision side concern.
- [x] **ADR-LLNCH-012 — Footer contract** ([#53](https://github.com/shanevcantwell/llauncher/issues/53), 2026-05-16): `GET /footer-context/{port}` with per-port TTL cache in `llauncher/agent/footer_cache.py`. Pinned four-field shape `{port, model, ctx_size, parallel}`; reads from lockfile + `ConfigStore` only — no process scan, no GPU probe. New env var `LAUNCHER_FOOTER_CACHE_S` (default 1.0 s). 14 tests. TS-side `pi-footer-extension` migration deferred to a separate slice.
- [x] **ADR-LLNCH-014 — Cancellation** ([#54](https://github.com/shanevcantwell/llauncher/issues/54), 2026-05-16): cancel flag in marker (boolean, back-compat default False); `POST /cancel/{port}` (port-keyed per ADR-LLNCH-010); MCP `cancel_server` tool; CLI `llauncher server cancel <port>`. Phase-boundary polling only (no mid-phase checks, no new threads). Cancel before commit reuses rollback path → `cancelled` action; cancel after commit is a no-op with `cancel_ignored_post_commit=True` advisory. 23 new tests.
- [x] **ADR-LLNCH-015 — Orphan policy (reduced scope)** ([#55](https://github.com/shanevcantwell/llauncher/issues/55), ADR-LLNCH-015): annotation + `orphan list` across CLI/HTTP/MCP, audit emission deduped on first sighting per pid. Adopt verb and `is_managed` field on `RunningServer` deferred — see ADR-LLNCH-015 §Deferred Work.
- [ ] **ADR-LLNCH-016 — Self-swap worked example** ([#56](https://github.com/shanevcantwell/llauncher/issues/56)): integration test + prose timeline. Depends on #54.

**Late audit cleanup running alongside M5:** [#60](https://github.com/shanevcantwell/llauncher/issues/60) (H3 audit-on-CRUD), [#61](https://github.com/shanevcantwell/llauncher/issues/61) (H4 BLE001 in `operations/*`), [#62](https://github.com/shanevcantwell/llauncher/issues/62) (self-loop short-circuit).

**Remaining estimate:** ~4–5 sessions (one per ADR/cleanup, parallelizable).

### M6 — Multi-backend (vLLM)

**Issues:** #42

- ADR-LLNCH-012: backend adapter layer (per #42's outline).
- Discriminated-union `ModelConfig`.
- Extract `LlamaServerAdapter` from existing process-build code.
- New `VLLMAdapter`.
- Amend ADR-LLNCH-005, ADR-LLNCH-006, ADR-LLNCH-008 per #42's notes.

**Estimate:** ~3–5 sessions.

### M7 — Release

- Update pi-coding-agent's TypeScript extension for the renamed endpoints (per ADR-LLNCH-010).
- Tag the v2-complete commit as `v2.0.0`.
- Migration is silent and handled inline by `ConfigStore.load()` (already in M1); no separate script needed.

**Estimate:** ~1 session.

## Total Estimate

**~20–30 sessions** of focused implementation work.

## Critical Path and Pacing

- **M1 → M2** is the critical path. Once M2 lands, you have a working v2; everything after is extensions.
- **M3 → M4** restore daily-driver capability (multi-node + UI).
- **M5 + Production Hardening + audit cleanup** run as a single phased plan after M4 — see §Phased Plan below. Ordering is set by *coupling*, not by issue size, so each phase lands on a foundation the next phase does not refactor.

## Phased Plan (post-M4, 2026-05-16)

The remaining open work spans three tracks (M5 Tier 2 ADRs, audit cleanup, Production Hardening) that share enough touch points — `operations/*`, `core/marker.py`, `remote/node.py`, the audit log contract, the agent lifecycle — that landing them in arbitrary order would force retro-fits. The phasing below sequences them by **dependency direction**: each phase tightens a contract that the next phase consumes.

Tracks are colour-coded in the table below: **[M5]** = Tier 2 ADR, **[AC]** = audit cleanup, **[PH]** = Production Hardening.

### Phase 1 — Foundation tightening

Bedrock smoothing before any new mechanism lands. Three independent slices, one session, three commits.

| Issue | Track | Why this phase |
|-------|-------|----------------|
| [#61](https://github.com/shanevcantwell/llauncher/issues/61) — H4 BLE001 in `operations/*` | [AC] | Threading cancel checks (#54) through bare `except Exception:` blocks is the kind of integration that hides regressions. Scope the exceptions first so #54's cancel-during-cleanup paths are reviewable. |
| [#60](https://github.com/shanevcantwell/llauncher/issues/60) — H3 audit-on-CRUD | [AC] | Pins the audit-event shape that #64 (audit tab remote-node access) will consume. Producer before consumer. |
| [#62](https://github.com/shanevcantwell/llauncher/issues/62) — RemoteNode self-loop short-circuit | [AC] | Stabilizes local-target dispatch before #55's orphan-aggregator builds on it; otherwise #55 routes local-node orphan queries through HTTP unnecessarily and we refactor twice. |

**Exit:** all three closed, audit-cleanup track has only #64 remaining (which lives in Phase 4).

### Phase 2 — Lifecycle correctness ✅ done (2026-05-16)

| Issue | Track | Why this phase |
|-------|-------|----------------|
| ~~[#65](https://github.com/shanevcantwell/llauncher/issues/65)~~ — SIGTERM graceful shutdown | [PH] | ✅ closed. FastAPI lifespan handler + `lifespan="on"` in `agent/server.py`. Dispatches each lockfile through `operations.stop(caller="agent-shutdown")`. Symmetric on SIGTERM/SIGINT. |

**Exit:** ✅ agent reaps child llama-server processes via the lockfile registry on SIGTERM identically to SIGINT; uvicorn 0.35's `capture_signals()` drains in-flight HTTP requests at the transport layer.

### Phase 3 — Capability additions (M5 Tier 2 ADRs)

| Issue | Track | Why this phase |
|-------|-------|----------------|
| ~~[#54](https://github.com/shanevcantwell/llauncher/issues/54)~~ — ADR-LLNCH-014 cancellation | [M5] | ✅ done (2026-05-16). Cancel flag on marker, `cancel_check` callable through readiness poll, `POST /cancel/{port}`. Lands on clean `operations/*` (after #61) and the marker module's existing five-phase contract. |
| [#55](https://github.com/shanevcantwell/llauncher/issues/55) — ADR-LLNCH-015 orphan policy | [M5] | Lands on stable remote dispatch (#62) and correct shutdown (#65). The managed-vs-unmanaged distinction it codifies is then available to the rest of v2. |

#54 and #55 are independent of each other and may interleave if convenient, but both must land before Phase 4.

### Phase 4 — Validation + deployment surface

| Issue | Track | Why this phase |
|-------|-------|----------------|
| [#56](https://github.com/shanevcantwell/llauncher/issues/56) — ADR-LLNCH-016 canonical self-swap test | [M5] | The worked example *of* #54's mechanics. Must be last in the M5 track. |
| [#67](https://github.com/shanevcantwell/llauncher/issues/67) — systemd `.service` units | [PH] | Pure packaging on top of #65. Meaningless without graceful shutdown. |
| [#64](https://github.com/shanevcantwell/llauncher/issues/64) — Audit tab: remote-node audit log access | [AC] | UI consumer of the audit contract pinned in Phase 1 (#60). |

**Exit:** M5 milestone closes; audit-cleanup track closes; Production Hardening track closes.

### Phase 5 — Pre-M6 sweep (deferred until M5 closes)

- V1-carryover triage (#10, #14, #15, #16, #20, #21, #22, #23, #24, #26, #27) — one batched session with explore subagents reading the live code and reporting close-or-keep verdicts. Don't mix into M5 work; cheap to delegate after M5 closes.
- #36 (footer-budget cache early-return on multi-node) — folds into the pi-footer-extension TS migration to `/footer-context/{port}` (M7 territory).
- #63 (log filename sanitizer collision) — file-and-forget unless someone hits it.

### Why this ordering, in one sentence

Each phase tightens a contract — exception scope, audit shape, dispatch path, agent lifecycle — that the next phase's new code consumes; the alternative ordering (cheapest-first) lands new mechanisms on un-tightened foundations and pays the difference in retro-fit work later.

### Session estimate

| Phase | Sessions |
|-------|---------:|
| Phase 1 | 1 |
| ~~Phase 2~~ | ~~1~~ ✅ done |
| Phase 3 | 2 (one each for #54 and #55) |
| Phase 4 | 1–1.5 |
| Phase 5 (post-M5) | 1 |
| **Total remaining to M5 close** | **~4–4.5** |

## Issue ↔ Milestone Map

| Issue | Title | Milestone | Status |
|-------|-------|-----------|--------|
| #37 | Add model Delete | M2 | ✅ closed |
| #38 | Volume-mount paths | M1 | partial — `LAUNCHER_LOG_DIR` added by ADR-LLNCH-013 |
| #39 | Audit commanded vs observed | M1 | partial — #60 closes the config-CRUD gap |
| #40 | Endpoint refactor (port-keyed) | M2 | ✅ closed |
| #41 | CLI naming | — | ✅ resolved: `llauncher` |
| #42 | Backend adapter (vLLM) | M6 | open |
| #43 | VRAM consolidation | M2 | ✅ closed |
| #46 | v1 test cleanup | M2 | ✅ closed |
| #47 | UI migration umbrella | M4 | ✅ closed (`5513d26`) |
| #48 | M4 Slice 11 — node_selector | M4 | ✅ closed (`e993dcc`) |
| #49 | M4 Slice 12 — drop auto-spawn | M4 | ✅ closed (`0d06b89`) |
| #50 | M4 Slice 13 — tab restructure | M4 | ✅ closed (`5513d26`) |
| #51 | M4 Slice 14 — render_op_result | M4 | ✅ closed (`1f55f3a`) |
| #52 | M5 / ADR-LLNCH-013 — logs lifecycle | M5 | ✅ closed (`9dc2769`) |
| #53 | M5 / ADR-LLNCH-012 — footer contract | M5 | ✅ closed (2026-05-16) |
| #54 | M5 / ADR-LLNCH-014 — cancellation | M5 | ✅ closed (2026-05-16, Phase 3) |
| #55 | M5 / ADR-LLNCH-015 — orphan policy (annotation + list) | M5 | ✅ closed — adopt deferred per ADR-LLNCH-015 §Deferred Work |
| #56 | M5 / ADR-LLNCH-016 — self-swap test | M5 | open (depends on #54) — **Phase 4** |
| #57 | Audit C2 — state→core layer | pre-M4 | ✅ closed (`b361b60`) |
| #58 | Audit C3 — port required | pre-M4 | ✅ closed (`270a43e`) |
| #59 | Audit H1 — MCP refresh | pre-M4 | ✅ closed (`de9f10f`) |
| #60 | Audit H3 — config-CRUD audit | audit-cleanup | ✅ closed (2026-05-16, Phase 1) |
| #61 | Audit H4 — BLE001 in operations | audit-cleanup | ✅ closed (2026-05-16, Phase 1) |
| #62 | Audit self-loop short-circuit | audit-cleanup | ✅ closed (2026-05-16, Phase 1) |
| #63 | Log filename sanitization collision | side | open — file-and-forget (filed during #52) |
| #64 | Audit tab — remote-node audit log access | audit-cleanup | open — **Phase 4** |
| #65 | SIGTERM not handled — mid-request termination | Production Hardening | ✅ closed (2026-05-16, Phase 2) |
| #67 | Official systemd service integration | Production Hardening | open — **Phase 4** |
| #64 | Audit tab: remote-node audit log access | post-M4 | open (filed during #50) |

## References

- ADRs 008–011 (`docs/adrs/{accepted,completed}/`) — Tier 1, foundation (008 in `accepted/` — eviction-compat retained; 009–011 in `completed/`)
- ADR-LLNCH-013 (`docs/adrs/accepted/013-logs-lifecycle.md`) — accepted 2026-05-08
- ADR-LLNCH-008 amendment notes (2026-05-02)
- ADR-LLNCH-002 (Superseded by ADR-LLNCH-011)
- Orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md`)
- M4/M5 design docs (`docs/m4-design.md`, `docs/m5-design.md`)
- Code audit synthesis (`docs/_audit_synthesis.md` + 5 domain reports)
- Issues #37–#63
- prompt-prix architecture as adapter-pattern reference (`~/github/shanevcantwell/prompt-prix/docs/ARCHITECTURE.md`)
