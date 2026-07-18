# M3 Divergence Investigation — What Happened to Slices 2–7

**Date:** 2026-05-07  
**Scope:** Reconstruct the timeline of M3 work across two repo copies and identify lost/uncommitted changes.

---

## Timeline Reconstruction

### The Two Repos

| Repo | Path | HEAD commit | Status |
|------|------|-------------|--------|
| **Main** (this container) | `~/github/shanevcantwell/llauncher/` | `85b7982` — chore: ignore runtime artifacts | Up-to-date with GitHub |
| **Inference-host** | `~/github/shanevcantwell/llauncher.inference-host/llauncher/` | `b256c2d` — docs: M3 Slice 7 completion + bug report | Has uncommitted changes; not pushed |

### Divergence Point

The inference-host repo is at `b256c2d`, which is the **M3 Slice 7 commit** on main. The main repo has only 2 commits ahead (`00f2f0a` audit findings, `85b7982` .gitignore). There is no git-level divergence — inference-host simply hasn't pulled since M3 slice 7 landed.

### What Actually Happened

An agent session ran on **inference-host** and made extensive changes to the working tree implementing M3 slices 2–7. These changes were:
- **Never committed** in the inference-host git repo (all show as `M` or `D` in `git status`)
- **Never pushed** to GitHub
- **Backed up** by copying the entire working tree to `llauncher.needsmerge/`

The main repo (this container) received only the M1 + M2 slice 1 commits that were pushed before the inference-host session started. The M3 work exists only as uncommitted changes on inference-host.

---

## Uncommitted Changes on Inference-Host

### Modified Files (27 files)

| File | Change Type | Description |
|------|-------------|-------------|
| `llauncher/operations.py` | **DELETED** | Replaced by split operations module |
| `llauncher/remote/node.py` | Modified | Port-keyed remote ops (`start_server(model, port)`, new `swap_server`) |
| `llauncher/remote/state.py` | Modified | Aggregator gains `swap_on_node()` method |
| `llauncher/agent/routing.py` | Modified | HTTP endpoint refactor (port-keyed routes) |
| `llauncher/mcp_server/server.py` | Modified | MCP server updates |
| `llauncher/mcp_server/tools/config.py` | Modified | Config tool updates |
| `llauncher/mcp_server/tools/servers.py` | Modified | Server tool updates (wired to v2 ops?) |
| `llauncher/models/config.py` | Modified | Model config changes |
| `llauncher/ui/tabs/model_card.py` | Modified | UI migration to v2 ops; remote swap parity |
| `docs/v2-handoff.md` | Modified | Updated handoff doc |
| `docs/plans/v2-implementation-roadmap.md` | Modified | Roadmap progress update |
| `scripts/run.sh` | Modified | Script updates |

### New Files (7 files)

| File | Description |
|------|-------------|
| `llauncher/operations/__init__.py` | Package init for split operations module |
| `llauncher/operations/start.py` | Start operation extracted from monolith |
| `llauncher/operations/stop.py` | Stop operation extracted from monolith |
| `llauncher/operations/swap.py` | Swap operation (ADR-LLNCH-011 five-phase) |
| `llauncher/operations/delete.py` | Delete model operation (Issue #37) |
| `llauncher/operations/preflight.py` | Pre-flight checks (health, VRAM) |
| `tests/unit/test_preflight.py` | Tests for preflight module |

### Modified Test Files (14 files)

All test files in `tests/unit/` and `tests/integration/` were updated to match the new operations structure.

---

## Key Structural Changes

### 1. Operations Module Split

**Before (main repo):** Single monolithic `llauncher/operations.py` (~600 lines) containing `start()`, `stop()`, `swap()`.

**After (inference-host uncommitted):** Split into `llauncher/operations/` package:
- `__init__.py` — re-exports public API for backward compatibility
- `start.py` — start operation with lockfile race handling
- `stop.py` — stop operation with stale lockfile reconciliation
- `swap.py` — five-phase swap mechanic (ADR-LLNCH-011)
- `delete.py` — model delete with active-lockfile check (Issue #37)
- `preflight.py` — model health + VRAM pre-flight checks

### 2. Port-Keyed Remote Operations

**Before:** `RemoteNode.start_server(model_name)` — model-keyed, no port parameter.

**After:** `RemoteNode.start_server(model_name, port)` — port-keyed per ADR-LLNCH-010. New `swap_server(model_name, port)` method added for remote swap dispatch.

### 3. Remote Swap Parity (M3 Slice 7)

The UI migration changed remote node start behavior from `aggregator.start_on_node()` to `aggregator.swap_on_node()`. This provides parity with local eviction semantics — remote starts on occupied ports now trigger full swap with rollback instead of returning 409. Documented in `docs/m3-slice7-remoteswap-bug-report.md`.

### 4. HTTP Endpoint Refactor

`agent/routing.py` was updated to port-keyed routes per ADR-LLNCH-010:
- `POST /start/{port}` body `{model}` (was `/start/{model_name}`)
- `POST /swap/{port}` body `{model}` (new endpoint)
- `POST /stop/{port}` (unchanged but verified)

---

## The needsmerge Directory

`~/github/shanevcantwell/llauncher.inference-host/llauncher.needsmerge/` is a **snapshot of the inference-host working tree** at some point during or after the M3 work. It contains:
- Full `.venv/` (runtime dependencies — not code)
- `__pycache__/` directories (compiled Python — not code)
- `htmlcov/` (coverage report HTML — not code)
- Agent runtime files (`agent.pid`, `agent.log`, etc.)
- **The actual source code** matching the inference-host uncommitted state

It is a backup, not a merge artifact. The name "needsmerge" reflects that these changes need to be merged into the main repo.

---

## Assessment

### What Was Accomplished on Inference-Host

| Milestone | Status on Inference-Host |
|-----------|--------------------------|
| M1 (Foundation) | ✅ Already committed before session |
| M2 Slice 1 (Swap mechanic) | ✅ Already committed before session |
| M2 Slice 2+ (Wire surfaces, VRAM/health preflight) | ⚠️ Uncommitted — operations split includes preflight module |
| M3 Slices 2–7 (UI migration, remote ops, port-keyed endpoints) | ⚠️ Uncommitted — all changes in working tree |

### What Was Lost

**Nothing is lost.** All work exists as uncommitted changes on inference-host. But:
- No commit history means no atomic rollback
- No tests were verified (tests exist but weren't run post-change)
- The split operations module may have import issues if `__init__.py` doesn't re-export correctly

### Risk Assessment

| Risk | Level | Details |
|------|-------|---------|
| Code quality | 🟡 Medium | Uncommitted changes were never reviewed or tested as a batch |
| Import compatibility | 🟠 High | Split operations module may break existing imports (`from llauncher.operations import swap`) |
| Test coverage | 🟡 Medium | Tests updated but not verified to pass against new structure |
| Data loss | 🟢 Low | needsmerge backup exists; inference-host working tree intact |

---

## Recommendations for Reconciliation

### Option A: Commit on Inference-Host, Then Pull (Recommended)

1. SSH into inference-host or access it directly
2. Run tests: `cd ~/github/shanevcantwell/llauncher.inference-host/llauncher && python -m pytest tests/unit/ -q`
3. If green: commit all changes as a batch, push to GitHub
4. Pull on main repo

**Pros:** Preserves commit history, atomic change set  
**Cons:** Requires access to inference-host; may need conflict resolution if main repo advanced further

### Option B: Cherry-Pick from needsmerge Snapshot

1. Copy the source files (not .venv/__pycache__/htmlcov/) from needsmerge into main repo
2. Run tests, fix any issues
3. Commit incrementally on main repo

**Pros:** Can be done entirely from this container  
**Cons:** Loses commit history; manual verification needed for each file

### Option C: Squash Merge via Git Worktree

1. On inference-host: `git add -A && git commit --squash`
2. Push to a temporary branch
3. Pull and merge on main repo

**Pros:** Clean single commit, preserves history  
**Cons:** Requires inference-host access; squash loses individual change granularity

---

## Immediate Action Items

1. **Decide reconciliation strategy** (A/B/C above) — requires user input
2. **Verify tests pass** on inference-host before any merge attempt
3. **Check operations/__init__.py** re-exports to ensure backward compatibility with existing imports
4. **Audit the split operations module** for correctness against ADRs 008–011 (the audit we just did was against the monolithic `operations.py`, not the split version)
