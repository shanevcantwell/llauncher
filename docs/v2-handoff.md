# v2 Handoff — Pick Up Cold

**Last updated:** 2026-05-02  
**Current state:** M1 complete. M2 is the next milestone.

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

## What's Done (M1)

| Module | Path | Notes |
|--------|------|-------|
| Settings env vars (`LAUNCHER_RUN_DIR`, `LAUNCHER_AUDIT_PATH`) | `llauncher/core/settings.py` | Volume-mountable per ADR-008 |
| Lockfile (atomic `O_EXCL`, reconciliation rules) | `llauncher/core/lockfile.py` | Internal format; not a public contract |
| Audit log (JSON Lines, commanded vs observed) | `llauncher/core/audit_log.py` | Append-only, never truncated |
| `ModelConfig` v2 — no `default_port`, has `BackendKind` | `llauncher/models/config.py` | Discriminator scaffolding for #42 |
| Tool-layer operations (start, stop) | `llauncher/operations.py` | Stateless service per ADR-008 |
| CLI wired to v2 ops | `llauncher/cli.py` | `server start` / `server stop` only |

**Tests:** 522 unit tests pass; 4 pre-existing v1 failures (see "Known Failures").

**Commit chain (most recent first):**

- `ecd94bf` — CLI wired to v2 operations
- `e94718d` — `operations.py` (start, stop) + 12 tests
- `30bd907` — drop `default_port`, add `BackendKind`, source/test cascade
- `48e980e` — settings env vars + lockfile + audit_log + 35 tests
- `42af291` — roadmap final decisions (silent drop, direct on `main`)
- `85f1093` — roadmap doc + close #41 (CLI: `llauncher`)
- `ac7c873` — orientation spike + ADR-008 amendment
- `86712c9` — accept ADRs 008–011, supersede 002

## What's Next (M2 — Swap + Endpoints)

**Goal:** all three surfaces (CLI, HTTP, MCP) work for single-node ops with the v2 architecture.

**Open Issues that close in M2:** #37 (model Delete), #40 (endpoint refactor).

**Work items, in suggested order:**

1. **`operations.swap(port, model)`** — implement ADR-011's 5-phase mechanic (pre-flight → in-flight marker → stop old → start new → readiness poll, with rollback). The mechanic spec is in `docs/adrs/011-swap-semantics-v2.md`. Action enum from §"Response Shape": `swapped | already_running | rolled_back | failed | rejected_preflight | rejected_stop_failed | rejected_in_progress | rejected_empty`.
2. **In-flight marker file** at `{LAUNCHER_RUN_DIR}/{port}.swap` with atomic `O_EXCL` create — same pattern as the lockfile in M1, just a different filename.
3. **Model file health check** — wire `core/model_health.py` into the swap pre-flight (ADR-005 reference).
4. **VRAM pre-flight** — wire `core/gpu.py`'s collector into the swap pre-flight (ADR-006 reference).
5. **Model Delete operation** — `operations.delete_model(name)` refusing if the model has a live lockfile on any port. Audit-logged. Closes #37.
6. **HTTP Agent endpoint refactor** — `POST /start/{port}` body `{model}`, `POST /swap/{port}` body `{model}`, `POST /stop/{port}`, `DELETE /models/{name}`. Drop the model-keyed `/start/{model}` and `/start-with-eviction/{model}`. Closes #40 (alongside MCP).
7. **MCP server tools** — mirror the HTTP shape: `start_server(model, port)`, `swap_server(port, model)`, `stop_server(port)`, `delete_model(name)`. Tool descriptions per ADR-010 §"Tool Prompt Guidance" — be explicit so the LLM picks the right verb without guessing.
8. **Update existing v1 tests** that go through `state.py.start_with_eviction` to either (a) test the new `operations.swap` directly, or (b) be skipped pending the v1 path's removal in M3.

**Estimate (from roadmap):** ~3–4 sessions.

## Open Issues

| Issue | Title | Milestone |
|-------|-------|-----------|
| [#37](https://github.com/shanevcantwell/llauncher/issues/37) | Add model Delete (CRUD symmetry with nodes) | M2 |
| [#38](https://github.com/shanevcantwell/llauncher/issues/38) | Volume-mountable lockfile + audit paths | M1 (partially done; full closure when consumed) |
| [#39](https://github.com/shanevcantwell/llauncher/issues/39) | Audit log: commanded vs observed | M1 (partially done; closure when v1 paths drop) |
| [#40](https://github.com/shanevcantwell/llauncher/issues/40) | Endpoint refactor (port-keyed) | M2 |
| [#42](https://github.com/shanevcantwell/llauncher/issues/42) | Backend adapter layer (vLLM) | M6 |

## What NOT To Do

- **Do not add compatibility shims.** "Rewrite, not migration." Old config data is silently dropped (per `ModelConfig.from_dict_unvalidated`); callers re-specify if they care. Don't try to support both v1 and v2 shapes simultaneously.
- **Do not auto-allocate ports at the API or operations layer.** Per ADR-010, port is always supplied by the caller. The CLI may default from `DEFAULT_PORT` env, but `operations.start(name, port)` requires an explicit `port` argument.
- **Do not introduce a `v2/` branch.** All v2 work lands on `main`. The strategy is "direct on `main`, repo frozen for v1 work."
- **Do not refactor `state.py` away yet.** The HTTP Agent (`agent/routing.py`), MCP server (`mcp_server/`), and Streamlit UI still go through v1 `LauncherState`. M2 replaces the HTTP and MCP entry points; the UI rewrite is M4. `state.py` itself stops being load-bearing somewhere around M3 or M4 and can be removed in M5/M6.
- **Do not add a `restart` verb.** Considered and explicitly deferred — see ADR-010 §"Considered but Not Implemented: Restart". `stop` then `start` is the substitute.

## Known Failures (Pre-existing v1)

These were already failing before M1 started; they are *not* blockers and should be left alone unless explicitly tackled in a separate slice. The orientation spike §6 documents the underlying causes.

- `tests/unit/mcp/test_phase1_lazy_singleton.py::TestStaleDataElimination::test_refresh_clears_killed_process_from_running`
- `tests/unit/test_gpu_health.py::TestNoBackendReturnsEmpty::test_no_backend_returns_empty` — `nvidia-smi` mock raises `CalledProcessError`, code path doesn't catch it.
- `tests/unit/test_registry_extended.py::TestStartLocalAgent::test_start_local_agent_success` — UI auto-spawn behavior the spike flagged for removal.
- `tests/unit/test_regression.py::TestIssue13LocalAgentAutoStart::test_start_local_agent_success` — same.

Verify the count is still 4 with: `python -m pytest tests/unit/ -q | tail -3`

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

# Tests (522 expected, 4 pre-existing failures)
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
