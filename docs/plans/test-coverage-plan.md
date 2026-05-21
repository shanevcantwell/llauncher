# Test Coverage Plan — Phase 1 (Baseline + Plan)

**Status:** active — Phases A/B/C landed (PRs #72/#73/#74); Phase D in flight on `phase-d-coverage-floor` (PR #90, non-UI coverage 95%, `--cov-fail-under=93`). UI track deferred to #69.
**Scope:** raise non-UI coverage and install a CI floor; coordinate with security plan's Phase C assertions.
**Last touched:** 2026-05-20 (PR #90 open).
**Companion dossier:** `docs/handoffs/2026-05-test-coverage-and-security.md`.

---

Generated against `main` (post #55 / ADR-015 orphan list).
Baseline run: 776 passed, 9 skipped, 2 warnings, 14.34 s.
Full coverage output: `/tmp/llauncher-cov-baseline.txt`.

## Baseline

**Total: 79% line coverage** (3685 stmts, 785 missed).

Top modules ranked by uncovered line count:

| Module | Stmts | Miss | Cover | Key missing ranges |
|---|---:|---:|---:|---|
| `ui/tabs/forms.py` | 151 | 144 | 5% | 15-115, 169-212, 222-350, 403-451 |
| `core/gpu.py` | 260 | 130 | 50% | 121-188, 230-235, 287-365, 373-432 |
| `ui/tabs/nodes.py` | 117 | 111 | 5% | 16-27, 37-143, 152-243 |
| `ui/tabs/model_card.py` | 146 | 81 | 45% | 92-99, 117-123, 143-190, 268-373 |
| `remote/node.py` | 220 | 54 | 75% | 212-214, 292-336, 346-369, 398-401 |
| `ui/tabs/model_registry.py` | 50 | 46 | 8% | 26-93, 109-116 |
| `cli.py` | 213 | 43 | 80% | 56-78, 130-135, 233-245, 382-394, 427-429 |
| `state.py` | 233 | 35 | 85% | 285-308, 410-434, 563-598 |
| `core/process.py` | 255 | 15 | 94% | 60, 243-244, 322-325, 482, 533-536 |
| `log_rotation.py` | 48 | 12 | 75% | 74-76, 102-108, 127-141 |

Notable smaller modules below 90%: `core/settings.py` 79%, `agent/server.py` 93%, `models/config.py` 89%, `remote/state.py` 89%, `core/marker.py` 87%, `core/lockfile.py` 94%.

## Closed Issues Lacking Regression Tests

Recent ADR/audit work (#54, #55, #52, #53, #60, #61, #62, #65, #59, #57) all have either explicit regression tests or strong feature-test coverage. The gaps are concentrated in older UI bug reports and a few enhancement-flavored items.

| Issue | Type | Title | Suggested test target |
|---|---|---|---|
| #1 | bug | Dashboard layout should match Manager tab single-column list | `tests/unit/test_dashboard.py` — snapshot dashboard column structure |
| #2 | bug | Manager tab missing Start/Stop controls for models | `tests/unit/test_models_tab.py` — assert Start/Stop affordances per row |
| #19 | bug | DuplicateElementKey error in dashboard refresh button | `tests/unit/test_dashboard.py` — unique keys with N models |
| #35 | bug | CLI tools `nodeFg is not defined` — model swap broken | `tests/unit/test_cli.py` — exercise `llaunch swap` with mocked HTTP |
| #25 | enh | Dashboard UX — sorting, status cleanup, inline toggle with eviction | `tests/unit/test_dashboard.py` — sort key + toggle semantics |
| #17 | enh | Consolidate Running Servers into Models section | `tests/unit/test_models_tab.py` — running indicator merged in models list |
| #12 | ux | Log refresh button shouldn't consume separate column | `tests/unit/test_model_card.py` (new) — layout assertion |
| #9 | enh | Per-model log refresh button | `tests/unit/test_model_card.py` — refresh action wired |
| #8 | enh | Filesystem browser for model/mmproj path | `tests/unit/test_model_card.py` — browser component rendered |
| #41 | misc | CLI naming `llauncher` vs `llaunch` | `tests/unit/test_cli.py` — both entrypoints resolve |
| #40 | refactor | Port-keyed start/swap, drop model-keyed eviction | `tests/integration/test_swap.py` — port-keyed semantics |
| #47 | refactor | Migrate UI from v1 `state.start_server` to v2 `operations.start` | UI tab tests — call sites use v2 ops |
| #30 | refactor | MCP lazy singleton + per-call refresh | `test_phase1_lazy_singleton.py` exists — verify covers original symptom |

Most regression-shaped historical bugs without explicit tests: **#1, #2, #19, #35** (four closed bug-labeled issues lacking obvious coverage).

## Phases

### Phase A — Recent feature regressions (ADR-013/014/015 + #65/#62)

Confirm and harden coverage for the most recent feature surface. Existing tests cover the unit-level happy paths; gaps are in cross-module wiring and error branches.

Files to extend:
- `tests/unit/test_log_rotation.py` — cover 74-76, 102-108, 127-141 (rotation failure modes: missing dir, permission error, oversized initial file).
- `tests/unit/test_orphan.py` — cover `operations/orphan.py:73-74, 119` (cleanup failure path, edge in list verb).
- `tests/unit/test_marker.py` — cover `core/marker.py:116-123, 188-193` (stale-marker reconciliation paths added with cancel/orphan).
- `tests/unit/test_agent_lifespan.py` — add test that lifespan shutdown still reaps when one lockfile read raises.
- `tests/unit/test_remote.py` — extend self-loop tests to cover `remote/node.py:292-336, 346-369` (non-self-loop branches refactored alongside #62).

### Phase B — Long-tail unit gaps in highest-uncovered non-UI modules

Target modules where logic is testable without GUI rendering. UI scope (`ui/tabs/forms.py` 130+ missed, `ui/tabs/nodes.py`, etc.) is **deferred** — out of scope for Phase B. Phase B focuses on `core/gpu.py` (130 missed) and other non-UI gaps. UI work is tracked in #69 (Streamlit `AppTest` harness).

Files to add/extend:
- `tests/unit/test_gpu.py` (new) or extend `test_gpu_health.py` — currently 50%. Cover NVML enumeration fallbacks (121-188), VRAM-budget arithmetic (287-329), accelerator-detection branches (333-365, 373-432). Mock `pynvml`/subprocess.
- `tests/unit/test_remote_node_paths.py` (new) — push `remote/node.py` from 75% toward 90% by covering heartbeat / error-translation branches at 292-336.
- `tests/unit/test_state.py` — cover state.py:285-308, 410-434, 563-598 (error-path / dehydration branches).
- `tests/unit/test_cli.py` — cover flag-parsing / output-formatting branches at 56-78, 233-245, 382-394.
- `tests/unit/test_core_settings_auth.py` — cover `core/settings.py:23-30` (auth/env edge).

### Phase C — Real-use-case MCP integration automation (NEW HARNESS)

Goal: drive llauncher exactly as an MCP client would, exercising **start → swap → cancel → orphan-list → stop** as a continuous flow plus failure variants. User-requested phase; produces high-value regression coverage the unit suite cannot, because it crosses MCP → operations → state → agent boundaries.

Harness shape: **in-process tool dispatch.** Tests import the MCP tool functions directly and call them via the same registration table the server uses — no stdio subprocess layer for now. (Revisit if wire-protocol drift becomes a concern; a future stdio smoke test could be added.)

llama-server policy: **both, gated.** The stub binary is used by default in CI; real-binary tests are gated behind `@pytest.mark.integration_real` and are intended for manual runs on this workstation.

- (a) **Stub binary** — a tiny shell script satisfying port-bind + health endpoint + graceful SIGTERM. Default for CI; configured via a test-only `models.toml` entry. Avoids requiring a real GGUF.
- (b) **Real-binary path** — marker-gated (`@pytest.mark.integration_real`), uses `~/.local/bin/llama-server` with a small GGUF. Not run in CI; manual workstation verification only.

Other dependencies:
- A temp `LLAUNCHER_HOME` per test (fixture already exists in `tests/conftest.py`).
- The agent's FastAPI app mounted in-process via `httpx.ASGITransport` so MCP → HTTP calls don't bind real ports.

Proposed test files (new, under `tests/integration/mcp/`):
- `conftest.py` — fixtures: `mcp_dispatch`, `stub_llama_server`, `agent_asgi_client`, `clean_lockfile_dir`.
- `test_e2e_start_stop.py` — `start_server` then `stop_server`; assert lockfile + marker + audit transitions.
- `test_e2e_swap.py` — start A, `swap_server` to B; assert eviction, marker continuity, audit log entries.
- `test_e2e_cancel.py` — start A with delay; `cancel_server` mid-flight; assert ADR-014 marker terminal state + no orphan.
- `test_e2e_orphan_lifecycle.py` — induce orphan (kill agent mid-start), then `list_orphans` and reconcile; assert ADR-015 list/cleanup verbs.
- `test_e2e_failure_modes.py` — preflight failure, port already bound, swap-to-same-port no-op, cancel after completion.
- `test_mcp_stdio_smoke.py` — minimal subprocess spawn of `llauncher-mcp`, single `list_models` round-trip (wire protocol check).

### Phase D — Edge cases / error paths surfaced by coverage diff

**Status: landed (2026-05-20).** `tests/unit/test_phase_d_coverage.py` adds 32 tests targeting:
- `core/gpu.py`: `_try_NVIDIA`/`_try_ROCM`/`_try_MPS` exception-handler branches, secondary `nvidia-smi --query-gpu=driver_version` error paths, `_query_MPS` body with canned `system_profiler` output, `GPUDevice.to_dict`.
- `state.py`: `start_server` validation-error early return, `_start_with_eviction_impl` strict-rollback "no old config" / "old path missing" branches, invalid-port range checks.
- `cli.py`: table-render branches of `model info` / `server status` / `node list` / `_color` text inference.
- `core/settings.py`: directory-detection branch for `LLAMA_SERVER_PATH` via `importlib.reload`.

Non-UI coverage rose **92% → 95%** (78 lines newly covered, 935 passing).

Residual Phase D candidates remaining (deferred — not blocking the floor):
- `state.py:563-598` (readiness-error rollback path inside swap) — substantial setup, candidate for a follow-up sweep.
- `core/process.py:60, 243-244, 325, 406-407, 482, 533-536` — terminal process states.
- `core/lockfile.py:104-111`.

UI tabs (`forms.py`, `nodes.py`, `model_registry.py`, `model_card.py`) remain deferred to #69's Streamlit `AppTest` harness.

## CI Coverage Floor

**Status: landed (2026-05-20).** `pytest.ini` `addopts` enforces `--cov-fail-under=93` against the non-UI scope (`llauncher/ui/*` and `llauncher/agent/__main__.py` are omitted in `pyproject.toml` `[tool.coverage.run]`).

Rationale: an aggregate floor against the full source mixes two populations — non-UI (95%) and UI (5–8% pending #69) — so it is either vacuous or blocks UI work. The non-UI floor at 93% gives ~2pt headroom over the post-Phase D measurement, ratchets up when more lines land, and re-baselines against combined measurement when #69 closes.
