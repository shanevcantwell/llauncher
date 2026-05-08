# v2 Handoff — Pick Up Cold

**Last updated:** 2026-05-08 (M4/M5 punch-list filed)
**Current state:** M1 + M2 + M3 complete. Audit re-verified against current tree (some findings already shifted — see §"Audit re-verification"). M4/M5 work decomposed into 15 epic Issues (#48–#62). Ready to start M4 (UI redesign) — recommended sequence: pre-M4 audit cleanup → M5 logs → M4 UI → remaining M5 ADRs → late audit cleanup.

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
| Streamlit UI (M4-scope) | `llauncher/ui/app.py`, `ui/tabs/` | Dashboard, Nodes, Model Registry tabs. Pre-v2 code; auto-spawn-local-agent still present despite M4 saying to drop it.

**Tests:** 612 tests pass, 12 skipped (+57 new tests vs pre-merge). Test coverage ~85% overall; gaps in model_health cache edge cases, concurrent lockfile/marker access, and config-change audit paths.

**Commit chain (most recent first):**

- `05942a0` — M3 merge: split operations package, port-keyed endpoints, remote swap parity
- `b256c2d` — docs: M3 Slice 7 completion + bug report for remote swap parity
- `dd5f7dd` — M2 slice 1: `operations.swap()` five-phase mechanic + `core/marker.py` in-flight marker + 32 tests
- `ecd94bf` — CLI wired to v2 operations
- `e94718d` — `operations.py` (start, stop) + 12 tests
- `30bd907` — drop `default_port`, add `BackendKind`, source/test cascade
- `48e980e` — settings env vars + lockfile + audit_log + 35 tests
- `42af291` — roadmap final decisions (silent drop, direct on `main`)
- `85f1093` — roadmap doc + close #41 (CLI: `llauncher`)
- `ac7c873` — orientation spike + ADR-008 amendment
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

### M4 — UI rewrite
| Issue | Title |
|-------|-------|
| [#48](https://github.com/shanevcantwell/llauncher/issues/48) | M4 Slice 11: Reusable node_selector UI component |
| [#49](https://github.com/shanevcantwell/llauncher/issues/49) | M4 Slice 12: Drop UI auto-spawn-local-agent (closes audit H2) |
| [#50](https://github.com/shanevcantwell/llauncher/issues/50) | M4 Slice 13: Restructure UI tabs around verbs + new audit tab |
| [#51](https://github.com/shanevcantwell/llauncher/issues/51) | M4 Slice 14: Centralize op-result rendering in ui/utils.py |
| [#47](https://github.com/shanevcantwell/llauncher/issues/47) | UI migration umbrella (subsumed by #48–#51) |

### M5 — Tier 2 ADRs
| Issue | Title |
|-------|-------|
| [#52](https://github.com/shanevcantwell/llauncher/issues/52) | M5 / ADR-013: Logs lifecycle — append, rotation, bounded tail |
| [#53](https://github.com/shanevcantwell/llauncher/issues/53) | M5 / ADR-012: Footer contract — /footer-context/{port} endpoint |
| [#54](https://github.com/shanevcantwell/llauncher/issues/54) | M5 / ADR-014: Cancellation of in-flight start/swap |
| [#55](https://github.com/shanevcantwell/llauncher/issues/55) | M5 / ADR-015: Orphan policy — managed flag, list/adopt verbs |
| [#56](https://github.com/shanevcantwell/llauncher/issues/56) | M5 / ADR-016: Canonical self-swap integration test |

### Audit cleanup (file before/around M4–M5)
| Issue | Title |
|-------|-------|
| [#57](https://github.com/shanevcantwell/llauncher/issues/57) | C2: Remove state.py import of core/model_health (layer violation) |
| [#58](https://github.com/shanevcantwell/llauncher/issues/58) | C3: Make port required at all boundaries — drop DEFAULT_PORT fallback |
| [#59](https://github.com/shanevcantwell/llauncher/issues/59) | H1: MCP read tools must call refresh() before returning |
| [#60](https://github.com/shanevcantwell/llauncher/issues/60) | H3: Persist audit entries on ConfigStore CRUD |
| [#61](https://github.com/shanevcantwell/llauncher/issues/61) | H4: Replace BLE001 bare except in operations/* |
| [#62](https://github.com/shanevcantwell/llauncher/issues/62) | M5-audit: Self-loop short-circuit in RemoteNode |

### Carried from earlier milestones
| Issue | Title | Notes |
|-------|-------|-------|
| [#38](https://github.com/shanevcantwell/llauncher/issues/38) | Volume-mountable lockfile + audit paths | M1 partially done; full closure when consumed everywhere |
| [#39](https://github.com/shanevcantwell/llauncher/issues/39) | Audit log: commanded vs observed | Partially done; #60 closes the config-CRUD gap |
| [#42](https://github.com/shanevcantwell/llauncher/issues/42) | Backend adapter (vLLM) | M6 |
| [#10](https://github.com/shanevcantwell/llauncher/issues/10), [#36](https://github.com/shanevcantwell/llauncher/issues/36), [#44](https://github.com/shanevcantwell/llauncher/issues/44), [#45](https://github.com/shanevcantwell/llauncher/issues/45) | Misc | Adjacent to M5 work but not blocking |

## Audit re-verification (2026-05-08)

Spot-checked the 2026-05-07 audit findings against the current tree:

| Finding | Status |
|---------|--------|
| C1 (dual-swap) | ✅ Resolved by M3 merge |
| C2 (state→core/model_health) | ❌ Still real; #57 |
| C3 (port auto-allocation) | ❌ Still real (cli.py:164–166, state.py:345–407); #58 |
| H1 (MCP refresh) | ⚠️ Partially fixed — some paths refresh, some don't; #59 |
| H2 (UI auto-spawn) | ❌ Still real (registry.py:200, app.py:62); #49 |
| H3 (audit log stub) | ⚠️ audit_log writes work; ConfigStore CRUD doesn't call them; #60 |
| H4 (BLE001) | ❌ Still real (start.py:144, swap.py:90/122/131); #61 |
| M1 redundant refresh | Open |
| M2 logs `"w"` mode | ❌ Still real (process.py:197); #52 |
| M3 `/models/health` endpoint | ✅ **Audit was stale** — endpoint exists at routing.py:221, 241 |
| M4 GPU in `/status` | ✅ **Audit was partially stale** — data is included (routing.py:175–182); just lacks `?full=true` filter |
| M5 self-loop short-circuit | ❌ Still real; #62 |

## What NOT To Do

- **Do not add compatibility shims.** "Rewrite, not migration." Old config data is silently dropped (per `ModelConfig.from_dict_unvalidated`); callers re-specify if they care. Don't try to support both v1 and v2 shapes simultaneously.
- **Do not auto-allocate ports at the API or operations layer.** Per ADR-010, port is always supplied by the caller. The CLI may default from `DEFAULT_PORT` env, but `operations.start(name, port)` requires an explicit `port` argument.
- **Do not introduce a `v2/` branch.** All v2 work lands on `main`. The strategy is "direct on `main`, repo frozen for v1 work."
- **Do not refactor `state.py` away yet.** The HTTP Agent (`agent/routing.py`), MCP server (`mcp_server/`), and Streamlit UI still go through v1 `LauncherState`. M2 replaces the HTTP and MCP entry points; the UI rewrite is M4. `state.py` itself stops being load-bearing somewhere around M3 or M4 and can be removed in M5/M6.

  **RESOLVED (M3 merge):** Dual-swap bug fixed. MCP `swap_server` now calls `operations.swap()`. All three surfaces (CLI, HTTP Agent, MCP) routed through v2 operations layer per ADR-011.
- **Do not add a `restart` verb.** Considered and explicitly deferred — see ADR-010 §"Considered but Not Implemented: Restart". `stop` then `start` is the substitute.

## Known Failures

**None.** 612 tests pass, 12 skipped (+57 new tests from M3 merge). Verify with: `python3 -m pytest tests/ -q | tail -3`

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

### ADR Compliance Summary

| ADR | Title | Status |
|-----|-------|--------|
| 003 | Agent API Authentication | ✅ Compliant |
| 004 | CLI Subcommand Interface | ⚠️ Partial — missing swap command, port fallback |
| 005 | Model Cache Health | ⚠️ Partial — core exists, no endpoint |
| 006 | GPU Resource Monitoring | ⚠️ Partial — collector exists, not wired |
| 008 | Stateless Facade | ⚠️ Partial — lockfile ✅, audit log ❌, `refresh()` still present |
| 009 | Hub-Spoke Topology | ✅ Compliant |
| 010 | Port Ownership at Call Site | ❌ **Violated** — legacy fallbacks persist |
| 011 | Swap Semantics v2 | ⚠️ Partial — ops layer ✅, surfaces not wired ❌ |

### Recommended Action Order (updated 2026-05-08)

C1 was resolved by the M3 merge. Remaining sequence:

1. **Pre-M4 cleanup** (~2 sessions): #57 (C2 layer), #58 (C3 port), #59 (H1 MCP refresh) — get the boundaries clean before UI work touches them.
2. **M5 logs first** (~1 session): #52 (ADR-013) — land before M4 testing so logs aren't lost during UI smoke runs.
3. **M4 UI rewrite** (~2–3 sessions): #48 → #51 → #49 → #50, in that order. Component + result-renderer first, then drop auto-spawn, then tab restructure.
4. **Remaining M5 ADRs** (~4–5 sessions, parallelizable): #53 footer, #54 cancel, #55 orphan, #56 self-swap.
5. **Late audit cleanup** (~1 session): #60 (H3 audit-on-CRUD), #61 (H4 BLE001), #62 (self-loop).

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

# Tests (555 expected, all pass)
python -m pytest tests/unit/ -q | tail -3

# v2 modules present
ls llauncher/core/lockfile.py llauncher/core/audit_log.py llauncher/operations.py

# Open Issues (#37, #38, #39, #40, #42 expected; #41 closed)
gh issue list --state open
```

## Institutional Knowledge (things not in any single artifact)

1. **The v1 PRD (`docs/PRODUCT_REQUIREMENTS.md`) was reverse-engineered by a Qwen3-class model** reading the live code. It is a snapshot of one prior moment and is stale on at least two points (MCP refresh discipline, audit reset on refresh). The v2 ADRs inherited those staleness points; ADR-008 has explicit Amendment Notes correcting them. **Don't re-derive from the v1 PRD blindly** — the orientation spike §4 has the corrections.

2. **The "four LauncherState instances" framing** in ADR-008 §Context #1 is real but mis-named. It's a symptom of "no shared service layer," not a designed-in cardinality. The v2 stateless-facade reframe (`operations.py`) is the cure.

3. **Single-user, single-GPU-per-node, hobby/research scope.** Don't over-engineer for multi-tenant or multi-GPU. Concurrency safety means "single-user with multiple processes" (UI + CLI + agent harness simultaneously), not "adversarial users."

4. **pi-coding-agent** is the canonical agent harness for the self-swap use case. Its TypeScript extension (per ADR-001) is the largest external consumer of the HTTP Agent. The `pi-footer-extension/` subtree lives in this repo; don't break it casually.

5. **`extra_args` is a remote arbitrary-flag-injection vector** when the agent runs unauthenticated on `0.0.0.0`. ADR-003 mitigates with `LAUNCHER_AGENT_TOKEN`, but the default is auth-off with only a warning. Orientation spike §6 flags this.

6. **`DEFAULT_PORT=8080` collides with `blacklisted_ports={8080}`** — a fresh user with no overrides hits "blacklisted" on first start. One-line fix; not yet done. Could land any time as a small slice.

7. **The harness footer (pi-coding-agent's status line)** is REST-only — it does not read lockfiles directly. Lockfile format is internal; can change freely. The HTTP Agent composes lockfile + pid-alive checks per request.

8. **Quota economics caveat** (the rationale behind some session-pacing decisions): Anthropic's pricing charges against user quota for prefill on session resume. Long-context conversations are expensive to revive. This document exists in part because resuming this session would otherwise pay a ~225K-token prefill cost for context that fits comfortably in this handoff.

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
