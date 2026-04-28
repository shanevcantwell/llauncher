# Llauncher Session Findings — Unimplemented Features & Identified Gaps

**Source:** 56 session files across `~/.pi/agent/sessions/` (Apr 20–26, 2026), containing 20+ feature-intent keyword hits per file.  
**Method:** Grep for TODO/todo/"need to"/"should do"/"would be nice"/enhancement/missing/bug/incomplete/workaround/not supported/add support/"could add"/future work/"next step"/idea across all llauncher-related sessions → deduplicated by topic → cross-referenced against codebase.

---

## Already Implemented ✅ (FILTERED OUT)

| Topic | Where to Verify | Session Evidence |
|-------|-----------------|------------------|
| **Swap/Eviction with rollback** (ADR-002) | `state.py:EvictionResult`, `_start_with_eviction_impl()` (~537 lines, 5-phase decision tree + 3 rollback blocks) | Multiple sessions show this was actively built Apr 25. Todo #1–#5 all completed. Commit push done. |
| **MCP server with tools** (12+ tools across models/servers/config) | `mcp_server/tools/models.py`, `servers.py`, `config.py` | Built and verified; Phase 1 testing complete |
| **Agent HTTP API on port 8765** | `agent/server.py`, `agent/routing.py` (start, stop, swap, status, logs, models endpoints) | Consistently tested across sessions |
| **Remote node aggregation** | `remote/node.py`, `registry.py`, `state.py` | `RemoteState` with multi-node discovery and health reporting built |
| **Streamlit UI** | `ui/app.py` + tabs (dashboard, model_card, forms, manager, nodes, running) | Full 6-tab UI exists and tested |
| **ModelConfig + LauncherState + ConfigStore** | `models/config.py`, `state.py`, `core/config.py` | Core data classes verified |
| **Process management** (`start_server`/`stop_server_by_pid`/`wait_for_server_ready`) | `core/process.py` | Extensively tested (28+ tests in test_state.py) |

---

## Unimplemented Topics Ranked by Impact 🔴🟠🟡

### 1. 🔴 CRITICAL: Authentication on Agent HTTP API
**Impact:** Any network caller can start/stop models, consume GPU resources, and read logs with zero authentication barrier. Binding to `0.0.0.0` without auth = full infrastructure control for any attacker.

**What was discussed:** Session 2026-04-25T14:51:22 (deep code review) identified **Finding C1**: "Zero authentication on the agent API." Review doc already written at `docs/reviews/2026-04-25-enhancement-no-auth-agent-api.md` with design sketches for middleware + FastAPI-native alternatives. Proposed solution: `LAUNCHER_AGENT_TOKEN` env var auth middleware.

**Session IDs:**
- `019dc51f-b4a8-71cf-bb4e-8e1bd7ed1438` (Apr 25, deep review findings C1)
- `019dbacc-1545-721d-9b62-57f08f6b7ed2` (Apr 23, general robustness discussion)

**How it should work:** Environment variable `LAUNCHER_AGENT_TOKEN` defines a shared secret. Agent API requires `Authorization: Bearer <token>` header on all mutating endpoints. Read-only `/status`, `/models`, health checks remain public. FastAPI middleware or dependency injection for token verification.

---

### 2. 🟠 HIGH: Subagent Dialogue Buffer / Iterative Debate Protocol (ADR-003)
**Impact:** Enables true multi-agent iterative workflows within llauncher's ecosystem, not just one-shot queries. Would allow subagents to debate/refine architectural decisions before implementation.

**What was discussed:** Session `019dbacc-1545-721d-9b62-57f08f6b7ed2` (Apr 23) presents "ADR 003: Ephemeral Dialogue Buffer for Multi-Agent Interaction" — an append-only structured buffer where subagents and main session write turns, with a "conductor" deciding next speaker. Described as moving from "one-way consultation to iterative debate protocol."

**Session IDs:**
- `019dbacc-1545-721d-9b62-57f08f6b7ed2` (Apr 23)
- `019dc9b4-c56b-7092-9570-64aaf3c1f9c5` (Apr 26, fast-summary extension as partial implementation of lightweight LLM routing)

**Why it matters:** The review found ~4000 words of token-cost repetition in sessions. A structured buffer with selective context injection would dramatically reduce wasted tokens and improve multi-agent quality. Partially realized by the `fast-summary.ts` Pi extension but not integrated into llauncher core.

---

### 3. 🟠 HIGH: CLI Subcommand Interface (`llauncher start/stop/status`)
**Impact:** Currently, external tooling must use HTTP API or MCP tools. A native CLI would improve ergonomics for ops scripts, Docker entrypoints, and direct terminal usage — especially for operators without a browser (SSH-only environments).

**What was discussed:** Session `019dbacc-1545-721d-9b62-57f08f6b7ed2` noted: "Scripts are verb-named `.sh` files... No Makefiles, no task runners." The user's repo conventions emphasize simple CLI patterns. The existing `__main__.py` only supports `llauncher mcp | llauncher ui` — no process-level commands (start/stop/status for individual models). Also discussed as "workaround" in sessions where Docker-compose is the only entry point.

**Session IDs:**
- `019dbb3e-3b45-716a-a3da-b5143a0a528a` (Apr 23, repo conventions survey)
- `019dc0d8-6bdb-71af-9632-ff9a730eb721` (Apr 24, Docker/npm issues requiring CLI workarounds)

**How it should work:** Extend `argparse` subcommands: `llauncher server start <model>` / `stop <port>` / `status [--json]`. Mirror the core/process.py APIs. Follow user's "verb-named scripts" pattern — simple, no Makefiles.

---

### 4. 🟠 HIGH: Model Cache / Download Management in UI
**Impact:** No UI mechanism to discover whether models exist on disk or need downloading. Operators must manually check filesystem paths. With GPU memory constraints, knowing model file sizes and disk availability before starting is critical.

**What was discussed:** The review (session `019dc51f`) found: "Missing model existence validation in `start_with_eviction()` pre-flight" — the code validates model names but doesn't check that `model_path` actually exists on disk or is accessible. No download progress tracking, no model health dashboard.

**Session IDs:**
- `019dc58e-03e1-72f8-bf27-8061bf898b49` (Apr 25, footer/llauncher integration)
- `019dbef0-bd55-76e8-a249-dd496689b427` (Apr 24, Docker issues with model files)

**How it should work:** UI tab showing "Model Registry" with file existence checks, disk usage per model, and a `Download/Update` action. API endpoint `GET /models/health` returning file status for each configured model.

---

### 5. 🟡 MEDIUM: GPU Resource Monitoring Dashboard
**Impact:** No visibility into VRAM consumption, GPU temperature, or utilization metrics in the UI or agent API. Operators start models blind without knowing remaining VRAM capacity per port/model combination.

**What was discussed:** Sessions consistently showed the footer extension trying to derive context window info from llauncher's `/status` (session `019dc8ad-7075-723a-8640-2c64fd3d96bd`, Apr 26). The user pushed for "llauncher_ctx_size / parallel" but noted the need for actual GPU metrics. Also discussed: SearXNG research into llama-server `-np` flag (parallel slots vs KV cache pages — session `019dc634-8895-7719-bc9e-3cd92df392ef`, Apr 25).

**Session IDs:**
- `019dc8ad-7075-723a-8640-2c64fd3d96bd` (Apr 26, context meter debugging)
- `019dc634-8895-7719-bc9e-3cd92df392ef` (Apr 25, -np flag research, SearXNG schema issues)

**How it should work:** Add NVIDIA/SMI parsing to `/status` endpoint. Display VRAM per model, total vs available VRAM. Support `nvidia-smi`, `rocm-smi`, and Apple MPS backends. Track across multiple GPU nodes in remote registry.

---

### 6. 🟡 MEDIUM: SearXNG Integration Extension (External)
**Impact:** While not core llauncher, sessions show this was actively developed as a Pi extension for web search within the agent loop. Multiple schema bugs found with `use_default_settings: false` requiring all top-level sections to exist.

**What was discussed:** Session `019dc634-8895-7719-bc9e-3cd92df392ef` spent significant effort debugging SearXNG's schema validation — missing required nested objects (brand, ui, preferences, outgoing) causing JSON parse errors. Final solution: all top-level keys must exist even if empty `{}`.

**Session IDs:**
- `019dc634-8895-7719-bc9e-3cd92df392ef` (Apr 25, SearXNG schema fixes)
- `019dbb3e-3b45-716a-a3da-b5143a0a528a` (Apr 23, repo conventions — noting search tooling gap)

**Why captured:** This was discussed as a pattern for "llauncher-adjacent" extensions. The session shows an important lesson: external HTTP services need their own schema validation layer separate from llauncher core. Could inform future llauncher extension architecture.

---

### 7. 🟡 MEDIUM: Enhancement Repeat Penalty Configuration
**Impact:** The file `enhancement_repeat_penalty.md` exists in the repo root, indicating this is an active area of investigation but not yet implemented. Sessions show repeated discussions about model behavior tuning without concrete implementation decisions captured in ADR form.

**What was discussed:** Multiple sessions reference `llauncher/enhancement_repeat_penalty.md` during codebase surveys and review sessions. The file tracks ideas for controlling repeat penalties at the llama-server level through configuration, but no implementation has been made.

**Session IDs:**
- `019dc8f7-ebce-73bf-952b-0403f1d40c2f` (Apr 26)
- `019dbacc-1545-721d-9b62-57f08f6b7ed2` (Apr 23)

---

### 8. 🟢 LOW: Version Consistency Across Entry Points
**Impact:** Four different version strings across `pyproject.toml`, `__init__.py`, `__main__.py`, and FastAPI app — already partially fixed in session `019dc996-f9ae-749c-8106-d76febee22ac` (Apr 26) where version was standardized to a single source (`__init__.__version__`).

**What was discussed:** Session explicitly found and fixed: pyproject=0.1.0 vs `__init__`=0.1.1 vs `__main__`=hardcoded 0.1.0 vs FastAPI=hardcoded 0.1.0 → bumped all to `0.2.0a0` with single source of truth import.

**Session IDs:**
- `019dc996-f9ae-749c-8106-d76febee22ac` (Apr 26) — **PARTIALLY RESOLVED** (single source established but not yet committed/pushed as final version)

---

### 9. 🟢 LOW: FastAPI / OpenAPI Documentation
**Impact:** Agent API lacks generated API docs (`/docs`, `/openapi.json`). No `docs()` or `redoc` routes configured in agent/server.py, making it hard for developers to discover available endpoints and parameters.

**What was discussed:** Review Finding W12: "OpenAPI/docs missing from FastAPI app" — noted during the deep code review (session `019dc51f-b4a8-71cf-bb4e-8e1bd7ed1438`). Low urgency but would improve developer experience significantly.

**Session IDs:**
- `019dc51f-b4a8-71cf-bb4e-8e1bd7ed1438` (Apr 25, W12 in review findings)

---

### 10. 🟢 LOW: Rollback Abstraction (`_rollback_to_old_model`)
**Impact:** Three nearly-identical rollback blocks (~20 lines each) in `_start_with_eviction_impl()` — session `019dc51f` identified as Finding C2: duplicated rollback logic. Refactoring would reduce code complexity and prevent regression when rollback semantics change.

**What was discussed:** Review explicitly recommended extracting `_rollback_to_old_model(port, old_config)` helper method to centralize all three rollback paths (phase 3 start failure, phase 4 readiness timeout, phase 4 exception).

**Session IDs:**
- `019dc51f-b4a8-71cf-bb4e-8e1bd7ed1438` (Apr 25, C2 finding)

---

## Summary Table

| Priority | Topic | Sessions | Docs Written? | Suggested ADR # |
|----------|-------|----------|---------------|-----------------|
| 🔴 CRITICAL | Auth on Agent API | 019dc51f, 019dbacc | ✅ `docs/reviews/2026-04-25-enhancement-no-auth-agent-api.md` | ADR-003 |
| 🟠 HIGH | Subagent Dialogue Buffer (ADR-003 concept) | 019dbacc, 019dc9b4 | ❌ | ADR-004 |
| 🟠 HIGH | CLI Subcommand Interface | 019dbb3e, 019dc0d8 | ❌ | ADR-005 |
| 🟠 HIGH | Model Cache/Download Management | 019dc58e, 019dc0d8 | ❌ | ADR-006 |
| 🟡 MEDIUM | GPU Resource Monitoring | 019dc8ad, 019dc634 | ❌ | ADR-007 |
| 🟡 MEDIUM | SearXNG Extension Pattern Lessons | 019dc634, 019dbb3e | ❌ | (extension RFC) |
| 🟡 MEDIUM | Enhancement Repeat Penalty | 019dc8f7, 019dbacc | ✅ `enhancement_repeat_penalty.md` exists | ADR-008 |
| 🟢 LOW | Version Consistency | 019dc996 | ✅ Fixed in-progress | N/A (resolved) |
| 🟢 LOW | OpenAPI/docs for FastAPI | 019dc51f | ❌ | N/A (simple fix) |
| 🟢 LOW | Rollback Abstraction | 019dc51f | ❌ | N/A (refactor, no ADR needed) |

---

## Notes for ADR Authoring

1. **ADR-003 (Auth):** Design docs already exist. The review doc contains middleware sketches + FastAPI-native alternatives with acceptance criteria. Pick the simpler approach to avoid over-engineering.
2. **ADR-004 (Dialogue Buffer):** This is experimental/strategic. Sessions show it's aspirational — a "conductor" pattern for iterative debate. Don't implement until ADR proves value; capture as proposal first.
3. **ADR-005 (CLI):** User has strong preferences about simplicity: "verb-named `.sh` files, no Makefiles." Follow this convention. Mirror existing core/process.py APIs as subcommands.
4. **Session `019dc8f7` and `019dc8ad`:** These show a detailed debugging session of the Pi footer extension trying to bridge llauncher context window data into the TUI — important for understanding how llauncher's `/status` API is consumed by external tools.
