# v2 Handoff — Pick Up Cold

**Last updated:** 2026-05-09 (end of session — M4 done; #50 landed, M4 milestone closed)
**Current state:** M1+M2+M3+M4 complete. Pre-M4 cleanup phase done (#57/#58/#59 closed). M4 done end-to-end: #48 node_selector, #51 render_op_result, #49 auto-spawn dropped, and #50 tab restructure (Dashboard/Models/Nodes/Audit + port picker). M5 ADR-013 (logs lifecycle) shipped. The remaining M5 ADRs (#53–#56), late audit cleanup (#60–#62, #64), and #63 can run in any order. Test count: 686 passed / 10 skipped.

A self-contained guide for picking up the v2 architecture work in a fresh context. Read this end-to-end before touching anything.

## Quick Orient

The repo is in the middle of a v2 architecture rewrite per ADRs 008–011. Current `main` carries M1 of the v2 work. The repo is **frozen for v1 work** — no backporting; all changes land directly on `main`. The v1-final state is preserved at the `v1-final` tag.

## Where Things Live

| Artifact | Path |
|----------|------|
| Implementation roadmap | `docs/v2-implementation-roadmap.md` |
| Ratified ADRs | `docs/adrs/008-*.md` … `011-*.md` |
| Orientation spike (live-tree gap analysis) | `docs/reviews/2026-05-02-v2-orientation-spike.md` |
| Reverse-engineered v1 PRD (partial truth) | `docs/PRODUCT_REQUIREMENTS.md` |
| Open Issues | `gh issue list` (see "Open Issues" below) |
| Backend-adapter analysis (vLLM future) | Issue #42 |
| **Code audit reports** | `docs/_audit_synthesis.md` + 5 domain-specific reports in `docs/_audit_*.md` |

## What's Done (M1 + M2 + M3)

| Module | Path | Notes |
|--------|------|-------|
| Settings env vars (`LAUNCHER_RUN_DIR`, `LAUNCHER_AUDIT_PATH`) | `llauncher/core/settings.py` | Volume-mountable per ADR-008 |
| Lockfile (atomic `O_EXCL`, reconciliation rules) | `llauncher/core/lockfile.py` | Internal format; not a public contract |
| Audit log (JSON Lines, commanded vs observed) | `llauncher/core/audit_log.py` | ⚠️ Stub only — no write operations implemented; config CRUD not audited. See audit §H3.
| `ModelConfig` v2 — no `default_port`, has `BackendKind` | `llauncher/models/config.py` | Discriminator scaffolding for #42 |
| Tool-layer operations (split package) | `llauncher/operations/{start,stop,swap,delete,preflight}.py` | Stateless service per ADR-008; re-exported via __init__.py for backward compat |
| Swap + in-flight marker (M2 slice 1) | `llauncher/operations/swap.py`, `llauncher/core/marker.py` | ADR-011 five-phase mechanic, rollback, pluggable pre-flight seams |
| Delete model operation (Issue #37) | `llauncher/operations/delete.py` | Checks active lockfiles before deletion; structured result envelope |
| Pre-flight adapters (M2 slice 2) | `llauncher/operations/preflight.py` | Model health check + VRAM estimation; callable seams for swap() |
| Port-keyed HTTP endpoints (ADR-010) | `llauncher/agent/routing.py` | POST /start/{port}, /swap/{port}, /stop/{port}; legacy model-keyed routes removed |
| MCP tools wired to v2 ops | `llauncher/mcp_server/tools/servers.py` | swap_server() calls operations.swap() — dual-swap problem (C1) resolved |
| Remote node port-keyed ops | `llauncher/remote/node.py` | start_server(model, port), swap_server(model, port); aggregator.swap_on_node() in state.py |
| CLI wired to v2 ops | `llauncher/cli.py` | Four subcommand groups: `model` (list, info), `server` (start, stop, status), `node` (add, list, remove, status), `config` (path, validate). Rich tables + `--json` output. |
| Multi-node infrastructure (M3-scope) | `llauncher/remote/{node,registry,state}.py` | RemoteNode, NodeRegistry, RemoteAggregator. Port-keyed per ADR-010; swap_on_node() for remote eviction parity. |
| Streamlit UI (M4-scope) | `llauncher/ui/app.py`, `ui/tabs/` | Tabs: Dashboard (read-only running view), Models (config CRUD + start/stop/swap verbs), Nodes (peer registry), Audit (local audit-log tail). Sidebar `node_selector` + per-card port picker (no auto-allocation). |

**Tests:** 686 passed, 10 skipped (+6 net this session). Test coverage ~85% overall; gaps in model_health cache edge cases, concurrent lockfile/marker access, and a few corners of the new log-rotation chain.

**Commit chain (most recent first; pre-M4 cleanup → M4 foundations → ADR-013 → M4 Slice 13):**

- `5513d26` — feat(ui): consolidate tabs into Dashboard+Models, add port picker (refs #50)
- `f7b8818` — feat(ui): add Audit tab + sidebar node_selector (refs #50)
- `0d06b89` — feat(ui): drop UI auto-spawn-local-agent (closes #49)
- `1f55f3a` — feat(ui): centralize op-result rendering in render_op_result (closes #51)
- `e993dcc` — feat(ui): reusable node_selector component for M4 (closes #48)
- `9dc2769` — feat(logs): append + rotate + bounded tail per ADR-013 (closes #52)
- `de9f10f` — fix(mcp): refresh state before write-tool reads (closes #59)
- `270a43e` — fix(cli,state): make port required at every boundary (closes #58)
- `b361b60` — fix(operations): lift model-health pre-flight out of state.py (closes #57)
- `717f722` — docs: file M4/M5 punch-list as Issues #48–#62, update handoff + roadmap
- `1907f7f` — docs: M1+M2+M3 complete, close #37/#40/#43/#46, note #47 partial
- `05942a0` — M3 merge: split operations package, port-keyed endpoints, remote swap parity
- `dd5f7dd` — M2 slice 1: `operations.swap()` + `core/marker.py` + 32 tests
- `48e980e` — settings env vars + lockfile + audit_log + 35 tests
- `86712c9` — accept ADRs 008–011, supersede 002

## Completed: M1 + M2 + M3

All single-node and multi-node v2 operations are wired through `operations/` package. Port-keyed endpoints, delete model, pre-flight adapters, remote swap parity — all complete. 612 tests pass.

**Open Issues closed by merge:** #37 (model Delete), #40 (endpoint refactor), #43 (VRAM consolidation), #46 (v1 test cleanup).
**Partially addressed:** #47 (UI migration — remote swap parity done, local UI still uses v1 `state.start_server`).

### Completed (M2 slice 1 — commit `dd5f7dd`)

- ~~[x]~~ **`operations.swap(port, model)`** ✅ — Full ADR-011 five-phase mechanic. All eight action outcomes reachable.
- ~~[x]~~ **In-flight marker file** ✅ — `llauncher/core/marker.py` with atomic `O_EXCL`, JSON persistence, lazy stale-marker reconciliation.

### Completed by merge (`05942a0`):

3. ~~[x]~~ **Model file health check** ✅ — `preflight.py::default_model_health_check()` wired as optional seam on `swap()`. Pass `None` to skip.
4. ~~[x]~~ **VRAM pre-flight** ✅ — `preflight.py::default_vram_check()` + `estimate_vram_mb()` wired as optional seams. Agent consolidated via #43.
5. ~~[x]~~ **Model Delete operation** ✅ — `operations/delete.py` with active-lockfile guard + structured result envelope. Closes #37.
6. ~~[x]~~ **HTTP Agent endpoint refactor** ✅ — Port-keyed routes in place. Legacy model-keyed routes removed. Closes #40.
7. ~~[x]~~ **MCP server tools** ✅ — Port-keyed shape; `swap_server` calls `operations.swap()`. Dual-swap bug resolved.
8. ~~[x]~~ **CLI wired to v2 ops** ✅ — Server start/stop wired through operations layer.
9. ~~[x]~~ **v1 test cleanup** ✅ — Repointed at v2 ops or skipped with ADR-008 refs. Closes #46.

**Delivered in:** M2 slice 1 (`dd5f7dd`) + M3 merge (`05942a0`). Total: ~6 sessions across inference-host agent work.

## Open Issues

### M4 — UI rewrite ✅ done (all 4 slices)
| Issue | Title | Status |
|-------|-------|--------|
| [#48](https://github.com/shanevcantwell/llauncher/issues/48) | M4 Slice 11: Reusable `node_selector` UI component | ✅ closed (`e993dcc`) |
| [#51](https://github.com/shanevcantwell/llauncher/issues/51) | M4 Slice 14: Centralize op-result rendering in `ui/utils.py` | ✅ closed (`1f55f3a`) |
| [#49](https://github.com/shanevcantwell/llauncher/issues/49) | M4 Slice 12: Drop UI auto-spawn (closes audit H2) | ✅ closed (`0d06b89`) |
| [#50](https://github.com/shanevcantwell/llauncher/issues/50) | M4 Slice 13: Restructure UI tabs around verbs + new audit tab | ✅ closed (`5513d26`) |
| [#47](https://github.com/shanevcantwell/llauncher/issues/47) | UI migration umbrella | ✅ closed (`5513d26`) — subsumed by #48–#51 |
| — | **M4 milestone** | ✅ **done (2026-05-09)** |

### M5 — Tier 2 ADRs (1 of 5 done)
| Issue | Title | Status |
|-------|-------|--------|
| [#52](https://github.com/shanevcantwell/llauncher/issues/52) | M5 / ADR-013: Logs lifecycle — append, rotation, bounded tail | ✅ closed (`9dc2769`) |
| [#53](https://github.com/shanevcantwell/llauncher/issues/53) | M5 / ADR-012: Footer contract — `/footer-context/{port}` endpoint | open |
| [#54](https://github.com/shanevcantwell/llauncher/issues/54) | M5 / ADR-014: Cancellation of in-flight start/swap | open |
| [#55](https://github.com/shanevcantwell/llauncher/issues/55) | M5 / ADR-015: Orphan policy — managed flag, list/adopt verbs | open |
| [#56](https://github.com/shanevcantwell/llauncher/issues/56) | M5 / ADR-016: Canonical self-swap integration test | open (depends on #54) |

### Audit cleanup (3 of 6 done)
| Issue | Title | Status |
|-------|-------|--------|
| [#57](https://github.com/shanevcantwell/llauncher/issues/57) | C2: Remove `state.py` import of `core/model_health` | ✅ closed (`b361b60`) |
| [#58](https://github.com/shanevcantwell/llauncher/issues/58) | C3: Port required at all boundaries (ADR-010) | ✅ closed (`270a43e`) |
| [#59](https://github.com/shanevcantwell/llauncher/issues/59) | H1: MCP refresh discipline (scope expanded to write-tool TOCTOU) | ✅ closed (`de9f10f`) |
| [#60](https://github.com/shanevcantwell/llauncher/issues/60) | H3: Persist audit entries on ConfigStore CRUD | open |
| [#61](https://github.com/shanevcantwell/llauncher/issues/61) | H4: Replace BLE001 bare except in `operations/*` | open |
| [#62](https://github.com/shanevcantwell/llauncher/issues/62) | M5-audit: Self-loop short-circuit in `RemoteNode` | open |
| [#64](https://github.com/shanevcantwell/llauncher/issues/64) | Audit tab: remote-node audit log access | open (filed during #50) |

### Side concerns surfaced this session
| Issue | Title | Notes |
|-------|-------|-------|
| [#63](https://github.com/shanevcantwell/llauncher/issues/63) | Log filename sanitization can collide for distinct model names | Filed during #52 — append-mode amplifies the risk; fix belongs in `ConfigStore` (sanitized-name uniqueness check), not in log handling. Low priority. |

### Carried from earlier milestones
| Issue | Title | Notes |
|-------|-------|-------|
| [#38](https://github.com/shanevcantwell/llauncher/issues/38) | Volume-mountable lockfile + audit paths | M1 partially done; ADR-013 (`#52`) added `LAUNCHER_LOG_DIR` to the family. |
| [#39](https://github.com/shanevcantwell/llauncher/issues/39) | Audit log: commanded vs observed | Partially done; `#60` closes the config-CRUD gap. |
| [#42](https://github.com/shanevcantwell/llauncher/issues/42) | Backend adapter (vLLM) | M6. |
| [#10](https://github.com/shanevcantwell/llauncher/issues/10), [#36](https://github.com/shanevcantwell/llauncher/issues/36), [#44](https://github.com/shanevcantwell/llauncher/issues/44), [#45](https://github.com/shanevcantwell/llauncher/issues/45) | Misc | Adjacent to M5 work but not blocking. |

## Audit re-verification (post-2026-05-08 session)

The 2026-05-07 audit's findings as they stand at end-of-session:

| Finding | Status |
|---------|--------|
| C1 (dual-swap) | ✅ Resolved by M3 merge |
| C2 (state→core/model_health) | ✅ **Closed** (`b361b60`) |
| C3 (port auto-allocation) | ✅ **Closed** (`270a43e`) |
| H1 (MCP refresh) | ✅ **Closed** (`de9f10f` — scope expanded to write-tool TOCTOU) |
| H2 (UI auto-spawn) | ✅ **Closed** (`0d06b89`) |
| H3 (audit log on config CRUD) | ❌ Still real; #60 |
| H4 (BLE001) | ❌ Still real (`operations/start.py:144`, `swap.py:90/122/131`); #61 |
| M1 (redundant refresh in agent endpoints) | Open — small slice; not on critical path |
| M2 (logs `"w"` mode) | ✅ **Closed** by ADR-013 (`9dc2769`) |
| M3 (`/models/health` endpoint) | ✅ Audit was stale — endpoint exists at `routing.py:221, 241` |
| M4 (GPU in `/status`) | ✅ Audit was partially stale — data is included (`routing.py:175–182`); only the `?full=true` filter is missing |
| M5 (self-loop short-circuit) | ❌ Still real; #62 |

## What NOT To Do

- **Do not add compatibility shims.** "Rewrite, not migration." Old config data is silently dropped (per `ModelConfig.from_dict_unvalidated`); callers re-specify if they care. Don't try to support both v1 and v2 shapes simultaneously.
- **Do not auto-allocate ports anywhere.** ADR-010 / #58 / #50: port is required at every API, operations, AND UI boundary. The port picker (`ui/components/port_picker.py`) requires explicit user input — no seed, no fallback. Don't reintroduce a fallback.
- **Do not auto-spawn the local agent from the UI.** ADR-009 ratifies the symmetric topology — the user starts `llauncher-agent` themselves. The `show_agent_down_banner` in `ui/app.py` is the only acceptable response to "agent down." (#49 / audit H2 closed this; don't reintroduce.)
- **Do not introduce a `v2/` branch.** All v2 work lands on `main`. The strategy is "direct on `main`, repo frozen for v1 work."
- **Do not refactor `state.py` away yet.** The HTTP Agent (`agent/routing.py`), MCP server read tools (`mcp_server/`), and Streamlit UI still go through `LauncherState` for reads. The eviction-compat path (`_start_with_eviction_impl`) is the main remaining v1 hook — intentionally retained for the eviction-API smoke contract until M5/M6 cleans it up. The model-health pre-flight has already been lifted out (#57); port-auto-allocation has too (#58 + #50).
- **Do not add a `restart` verb.** Considered and explicitly deferred — see ADR-010 §"Considered but Not Implemented: Restart". `stop` then `start` is the substitute.
- **Do not call `state.refresh()` inside hot loops or on every UI rerun.** Read tools refresh per call (#59 made this explicit and enforced); write tools refresh before the read step. Adding more refreshes on top is wasteful.
- **Do not regress the log lifecycle (ADR-013).** Logs are append-mode; the per-run banner (`=== started at <iso> port=<n> ===`) marks boundaries. Rotation is opportunistic at start time. `_tail_file` reads a bounded window. Tests guard all three.

## Known Failures

**None.** 686 tests pass, 10 skipped. Verify with: `python3 -m pytest tests/ -q | tail -3`

## Code Audit Findings (2026-05-07)

A full code-vs-documentation audit was performed using 5 parallel subagent reviews. Full reports in `docs/_audit_*.md`; synthesis in `docs/_audit_synthesis.md`.

### 🔴 Critical — Must Fix Before Next Release

| # | Issue | ADR | Affected Code |
|---|-------|-----|---------------|
| C1 | **Dual-swap problem:** `operations.swap()` is fully implemented per ADR-011 but **no surface calls it**. HTTP Agent and MCP tools still use legacy v1 path (`state._start_with_eviction_impl()`). Concurrency control from ADR-011 is dead code for production callers. | 011 | `mcp_server/tools/servers.py`, `agent/routing.py` |
| C2 | **Layer violation:** `state.py` imports from `core/model_health`, violating documented layer order (State → Core). Tight coupling, harder testing. | — | `state.py:9–14` |
| C3 | **ADR-010 violated:** Port ownership has legacy fallbacks in CLI (`DEFAULT_PORT` env var) and state layer (`start_server()` auto-allocation when port=None). ADR requires port as required parameter everywhere. | 010 | `cli.py`, `state.py:345–407` |

### 🟠 High Priority — Next Sprint

| # | Issue | ADR | Affected Code |
|---|-------|-----|---------------|
| H1 | **MCP stale reads:** MCP read tools never call `refresh()` before returning data. With 4 independent `LauncherState` instances, MCP returns perpetually stale information. | 008 | `mcp_server/tools/models.py`, `servers.py` |
| H2 | **Auto-spawn still present:** M4 says to drop auto-spawn but `NodeRegistry.start_local_agent()` is still called from UI. | — | `remote/registry.py` |
| H3 | **Audit log not persisted:** ADR-008 requires JSON Lines persistence; current implementation is a stub with no write operations. Config CRUD (add/update/remove) has no audit entries. | 008 | `core/audit_log.py`, `core/config.py` |
| H4 | **BLE001 silent failures:** Bare `except Exception:` in `operations.py` cleanup paths (lines 143, 256–258, 397–399) silently swallows all errors including `KeyboardInterrupt`. | — | `operations.py` |

### 🟡 Medium Priority — Following Sprints

| # | Issue | ADR | Affected Code |
|---|-------|-----|---------------|
| M1 | Redundant refresh calls in agent endpoints (`refresh()` then `refresh_running_servers()`) | 008 | `agent/routing.py` |
| M2 | Logs truncated on restart — `"w"` mode instead of `"a"`; no rotation (M5 Item 2) | — | `core/process.py` |
| M3 | Missing `/models/health` endpoint despite ADR-005 specifying it | 005 | `agent/routing.py` |
| M4 | GPU data not wired into `/status?full=true` (ADR-006 collector exists but unused) | 006 | `agent/routing.py`, `core/gpu.py` |
| M5 | No self-loop short-circuit in RemoteNode — always uses HTTP even for local node | 009 | `remote/node.py` |

### ADR Compliance Summary (post-2026-05-08)

| ADR | Title | Status |
|-----|-------|--------|
| 003 | Agent API Authentication | ✅ Compliant |
| 004 | CLI Subcommand Interface | ⚠️ Partial — `swap` subcommand still missing |
| 005 | Model Cache Health | ✅ Compliant — endpoint and operations seam (`#57`) |
| 006 | GPU Resource Monitoring | ⚠️ Partial — collector + status data exist; `?full=true` filter missing |
| 008 | Stateless Facade | ⚠️ Partial — lockfile/audit/log paths all env-configurable; v1 `state.refresh()` retained on read paths (eviction-compat lives in `state._start_with_eviction_impl`) |
| 009 | Hub-Spoke Topology | ✅ Compliant — UI auto-spawn dropped (`#49`) |
| 010 | Port Ownership at Call Site | ✅ Compliant at every API/operations/UI boundary (`#58` + `#50` — UI port picker requires explicit input) |
| 011 | Swap Semantics v2 | ✅ Compliant — all surfaces wired through `operations.swap()` (M3 merge) |
| **013** | **Per-Server Log Lifecycle (NEW)** | ✅ **Accepted** — append/rotate/bounded-tail (`#52`) |

### Recommended Action Order — what's left

1. **Remaining M5 ADRs** (~4 sessions, parallelizable): #53 footer (`/footer-context/{port}` + TTL cache), #54 cancel (marker flag + `POST /cancel/{port}`), #55 orphan (`is_managed` flag + `orphan list/adopt` verbs), #56 self-swap integration test (depends on #54).
2. **Late audit cleanup** (~1 session, parallelizable with M5): #60 (H3 audit-on-CRUD), #61 (H4 BLE001 in `operations/*`), #62 (self-loop short-circuit), #64 (audit-tab remote-node access).
3. **#63** (sanitizer collision) — low priority, file-and-forget unless someone hits it.
4. **M6 / M7** — backend adapter (vLLM, #42), then release.

## Conventions

- **ADR template:** see `docs/adrs/008-*.md`. Sections: Status, Date, (Amended:), Context, Decision, Consequences (Positive / Negative / Open Questions), Supersession (if applicable), Relationship to Other ADRs.
- **ADR statuses:** `Draft` → `Accepted` → optionally `Superseded by ADR-NNN`.
- **Commit style:** Conventional Commits — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`. Multi-paragraph body is fine. Reference issues with `Refs: #N` (links without closing) or `Closes #N` (auto-closes on push).
- **Test layout:** `tests/unit/test_*.py`; pytest auto-discovery; markers `integration` and `live` defined in `pytest.ini`.
- **CLI command name:** `llauncher` (decision pinned in #41 — *not* `llaunch`).
- **Backend identity:** `BackendKind.LLAMA_SERVER` only in M1. vLLM follows in M6 per Issue #42.
- **Process identity sentinel:** currently `--alias <model>` (a llama-server flag). Becomes env-var or per-backend in M6.
- **Lockfile path:** `{LAUNCHER_RUN_DIR}/{port}.lock`. Internal format. Not a public contract — external consumers hit the HTTP Agent.
- **Audit log path:** `{LAUNCHER_AUDIT_PATH}` (default `~/.llauncher/audit.jsonl`). Public contract for the in-container agent → host-state introspection use case.

## Pre-Work Verification

Run these to confirm the state matches this handoff before touching anything:

```bash
# Repo state
git log --oneline -10
git tag -l 'v1-final'   # should print v1-final

# Tests (686 passed, 10 skipped expected — all green)
python -m pytest tests/ -q | tail -3

# v2 modules present (operations is a package now)
ls llauncher/core/lockfile.py llauncher/core/audit_log.py llauncher/core/log_rotation.py
ls llauncher/operations/{start,stop,swap,delete,preflight}.py
ls llauncher/ui/components/{node_selector,port_picker}.py
ls llauncher/ui/tabs/{audit,dashboard,models,nodes}.py

# Open Issues — #53–#56 + #60–#64 expected; M4 (#47/#48/#49/#50/#51) all closed.
gh issue list --state open
```

## Institutional Knowledge (things not in any single artifact)

1. **The v1 PRD (`docs/PRODUCT_REQUIREMENTS.md`) was reverse-engineered by a Qwen3-class model** reading the live code. It is a snapshot of one prior moment and is stale on at least two points (MCP refresh discipline, audit reset on refresh). The v2 ADRs inherited those staleness points; ADR-008 has explicit Amendment Notes correcting them. **Don't re-derive from the v1 PRD blindly** — the orientation spike §4 has the corrections.

2. **The "four LauncherState instances" framing** in ADR-008 §Context #1 is real but mis-named. It's a symptom of "no shared service layer," not a designed-in cardinality. The v2 stateless-facade reframe (`operations.py`) is the cure.

3. **Single-user, single-GPU-per-node, hobby/research scope.** Don't over-engineer for multi-tenant or multi-GPU. Concurrency safety means "single-user with multiple processes" (UI + CLI + agent harness simultaneously), not "adversarial users."

4. **pi-coding-agent** is the canonical agent harness for the self-swap use case. Its TypeScript extension (per ADR-001) is the largest external consumer of the HTTP Agent. The `pi-footer-extension/` subtree lives in this repo; don't break it casually.

5. **`extra_args` is a remote arbitrary-flag-injection vector** when the agent runs unauthenticated on `0.0.0.0`. ADR-003 mitigates with `LAUNCHER_AGENT_TOKEN`, but the default is auth-off with only a warning. Orientation spike §6 flags this.

6. ~~`DEFAULT_PORT=8080` collides with `blacklisted_ports={8080}`~~ — **Fixed alongside #58** (`270a43e`). The hardcoded fallback is now `8081`; `.env.example` updated. The dev's `.env` already had `DEFAULT_PORT=8081` before this change.

7. **The harness footer (pi-coding-agent's status line)** is REST-only — it does not read lockfiles directly. Lockfile format is internal; can change freely. The HTTP Agent composes lockfile + pid-alive checks per request. **#53 (ADR-012) will define a dedicated `/footer-context/{port}` endpoint** so the footer stops paying for the full `/status` payload per token.

8. **Quota economics caveat** (the rationale behind some session-pacing decisions): Anthropic's pricing charges against user quota for prefill on session resume. Long-context conversations are expensive to revive. This document exists in part because resuming this session would otherwise pay a ~225K-token prefill cost for context that fits comfortably in this handoff.

9. **Foundation components for M4 (#48 / #51) live at `llauncher/ui/components/`.** `node_selector.render_node_selector()` writes to `st.session_state["ui.target_node"]` (constant `TARGET_NODE_KEY`) and synthesizes `"local"` as the first option. `ui/utils.py::render_op_result()` translates any `operations/*Result` envelope (or its `.to_dict()` form) into Streamlit feedback via the `OpResultSeverity` ladder (SUCCESS toast / INFO toast / WARNING sticky / ERROR sticky). #50 is the slice that wires both into the new tab structure.

10. **Two `model_card.py` callsites carry `# v2 ops migration (issue #57)` markers** — at lines 167 (eviction → `ops.swap`) and 344 (start → `ops.start`). Don't re-route them through `state.py`. The `_handle_start` signature is now `target_port: int` (required; no default) — the auto-allocation fallback that lived around line 302 has been deleted. The two markers themselves still call `ops.swap` / `ops.start` directly.

11. **Test-mock target for model_health pre-flight is `llauncher.operations.preflight.mh.check_model_health`.** Patching `llauncher.state.check_model_health` no longer works (state.py doesn't import it). Tests that need the *real* implementation should mark themselves `@pytest.mark.real_model_health`.

12. **Logs are append-mode now (ADR-013).** Per-run banner is `=== started at <iso> port=<n> ===`. Rotation is opportunistic at start time, capped at `LAUNCHER_LOG_MAX_BYTES` (default 50 MiB) with `LAUNCHER_LOG_KEEP` (default 3) files retained. `LAUNCHER_LOG_DIR` joins the ADR-008 family of env-configurable paths. `_tail_file` reads a bounded ~32 KiB window — `len(result)` may be less than `lines` for very long log lines, by design.

13. **M4 Slice 13 surfaces (`#50`)**: tab structure is now Dashboard / Models / Nodes / Audit. `dashboard.py` is view-only (read-side only); `models.py` owns config CRUD + per-model verb buttons (start/stop/swap). `model_registry.py` parameter renamed `selected_node` → `target` (string, default `'local'`). The `'All Nodes'` cross-node aggregate view is dropped; a single target is always selected. The Audit tab (`ui/tabs/audit.py`) reads local `LAUNCHER_AUDIT_PATH` only; remote-node audit access is deferred to issue #64.

## Questions With Pinned Answers

If a fresh context is uncertain about any of these, the answer is already in the docs:

- *Is the v2 work on a branch?* → No, on `main`. (`docs/v2-implementation-roadmap.md` §Strategy.)
- *Should I migrate old config data?* → Silent drop only; no migration log. (Roadmap §Pre-Implementation Decisions.)
- *What's the CLI command?* → `llauncher` (Issue #41 closed).
- *Should I add a `restart` verb?* → No. (ADR-010 §"Considered but Not Implemented".)
- *What's `swap` on an empty port?* → Failure with `port_empty`. (ADR-010 + ADR-011.)
- *What's `start` on an occupied port with a different model?* → Failure with `rejected_occupied`. No passive swap. (ADR-010.)
- *Should the v2 ADRs be amended in place when something turns out stale?* → Yes — ADR-008 has an "Amendment Notes" section pattern to follow. Status stays `Accepted`; date the amendment.
- *Should I file new Issues for things I notice?* → Yes, with a reference back to the ADR or spike where the concern surfaced.
