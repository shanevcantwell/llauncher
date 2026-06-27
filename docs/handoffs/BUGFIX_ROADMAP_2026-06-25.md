# Bug-Fix Roadmap — shanevcantwell, severity-first (2026-06-25)

**Scope:** 4 live bug-bearing repos — llauncher, harness-tools, thought-vault-integration
(tvi), semantic-kinematics-mcp (skm). semantic-chunker = legacy/note-only. Windows quartet
+ blocked-infra set = parked. `langgraph-agentic-scaffold` excluded by request.

**Inventory basis:** 56 repos scanned, 59 open bugs, 6 repos with active bug load. Liveness
validated against June-2026 git activity (additive check): thought-vault-integration,
llauncher, leap-null-falsify, harness-tools, design-docs, semantic-kinematics-mcp, pi-jail,
frontier-advisor touched in June 2026. Of the non-roadmap live repos: leap-null-falsify has
GitHub issues disabled (no backlog); design-docs/pi-jail/frontier-advisor carry only
non-bug issues.

**Parallelism model:** *Different repos are always concurrent* (separate git repos →
separate worktrees, zero conflict surface). *Within* a repo, items are grouped into
**lanes** by subsystem; one lane = one serial PR chain, lanes run concurrently. Lane
conflicts are the only serialization constraint.

---

## Phase 0 — Hygiene (do first, ~15 min, serial)

| Action | Repo | Disposition |
|---|---|---|
| Consolidate **#186/#187/#188** → 1 canonical nonpooled-embeddings bug, close 2 dupes | llauncher | git lane |
| Confirm-park blocked set: **#1/#2/#9/#11** (`blocked`) + Windows **#127/#130/#132** (`user:gate`) | harness, llauncher | note only |

## Phase 1 — Blocking-runnable + live-model hazards (top severity)

*Parallel across repos; tvi #10 gates the rest of tvi.*

| Bug | Repo | What | Disp. | Lane |
|---|---|---|---|---|
| **#10** P0 | tvi | missing runtime deps → repo non-runnable | fix | **tvi-gate** (must land first) |
| **#11/#12/#13** P1 | tvi | f-string / `_is_tool_only` / dead-code imports | fix | tvi-A/B/C (parallel *after* #10) |
| **#184** | llauncher | `--cache-reuse` detonates on resident Qwen3.6 | fix | **ll-args** |
| **#189** | llauncher | MTP+parallel violates `-np 1` | fix | **ll-args** (same lane as #184 — both touch arg-validation/process) |
| **#54** | harness | qwen3.6 record-skipping (live agent loops) | fix | **h-loop** |

## Phase 1.5 — Self-leverage (small; repays within session)

| Bug | Repo | What | Lane |
|---|---|---|---|
| **#29** | harness | allowlist prefix never matches git-lane shapes (dispatch friction) | **h-perm** |
| **#28** | harness | destructive-git hook false-positives on commit bodies | **h-hook** (parallel with #29 — different files) |

## Phase 2 — Security + silent-failure correctness

| Bug | Repo | What | Disp. | Lane |
|---|---|---|---|---|
| **#150** | llauncher | VRAM check silently passes on missing GPU data | `auto:fix` | **ll-preflight** |
| **#149** | llauncher | VRAM estimate quant/KV-blind (related to #150) | `auto:draft` | **ll-preflight** (same lane; draft→ratify) |
| **#88** | llauncher | `_skip_path_validation` class-flag leak | `auto:fix` | **ll-config** |
| **#181** | llauncher | UI eject silent no-op (argv vs lockfile) | fix | **ll-ui** |
| **#141** | llauncher | extension wrong API endpoints | `auto:fix` | **ll-ext** |
| **#186★** | llauncher | nonpooled embeddings 400 (post-consolidation) | fix | **ll-embed** (high, not currently-blocking — :8082 pooled is healthy) |
| **#44** | harness | dead `start_server` renderResult | fix | **h-llauncher-ext** |
| **#14** | harness | `swap_server` empty-ports → fall back to start | fix | **h-llauncher-ext** (same lane as #44 — same TS extension) |

## Phase 3 — Arch / medium correctness

| Bug | Repo | What | Disp. | Lane |
|---|---|---|---|---|
| **#171** | llauncher | remote→agent import regression | fix | **ll-arch** |
| **#156** | llauncher | `update_model_config` loses batch/parallel | fix | **ll-config** (chains after #88) |
| **#154** | llauncher | run.sh misleading install banner | `auto:fix` | **ll-install** |
| **#151** | llauncher | `LAUNCHER_*`→`LLAUNCHER_*` rename | `auto:fix` **breaking** | **ll-envrename** (run **last, alone** — broad sweep, conflicts with everything) |
| **#1/#9/#16** | skm | null-binding / MCP-contract bypass / non-self-describing checkpoint | fix | **skm-A/B/C** (parallel — distinct areas) |

## Phase 4 — Deferred (note only)

- tvi P2–P4 batch (#14–#22, #25, #26, #33) — cleanups/enhancements, not severity
- llauncher Windows quartet #127/#130/#132 — operator-parked (#132 security-tagged but `user:gate`)
- harness blocked set #1/#2/#9/#11 — infra-dependent
- semantic-chunker #3 — legacy repo

---

## Parallel-execution summary

**Max concurrent fan-out** (no two share a lane / repo file-surface):

- **Repo-level:** tvi ∥ llauncher ∥ harness ∥ skm always run together.
- **Phase 1 wave:** `tvi-gate(#10)` → then `{tvi-A,B,C}` ∥ `ll-args(#184,#189)` ∥
  `h-loop(#54)` → **up to 5 concurrent worktrees.**
- **Phase 2 wave:** `ll-preflight` ∥ `ll-config` ∥ `ll-ui` ∥ `ll-ext` ∥ `ll-embed` ∥
  `h-llauncher-ext` → **6 llauncher lanes safe to parallelize** *because they touch disjoint
  subsystems* (preflight / ModelConfig / UI / extension / embeddings).
- **Serialization hard-rules:**
  1. `tvi #10` before any other tvi fix.
  2. `#184`+`#189` share `ll-args` → serial within lane.
  3. `#44`+`#14` share the llauncher TS extension → serial within lane.
  4. `#88` before `#156` (both `ll-config`).
  5. **`#151` env-rename runs dead last, alone** — its sweep collides with every other llauncher lane.

**Budget read:** Token budget (77% weekly as of session start) is not the binding
constraint — wall-clock (<24h) is. The spine is Phases 0–1.5 (~7 fixes + 1 consolidation);
everything `auto:fix` can go branch→fix+tests→gates→PR→merge-on-green under the llauncher
autonomy contract. With repo-level + intra-repo lane parallelism, the practical ceiling is
~5–6 concurrent worktrees per wave, which is what turns the 24h wall from "spine only" into
"spine + most of Phases 2–3."
