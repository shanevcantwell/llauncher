# Brief: Python Code Quality Remediation (Worker)

**Source Review:** plan-sleeptime-remediation-00-review-opus-4.7-complete-review.md, python-reviewer agent output  
**Related:** security-brief.md (overlapping auth fixes), silent-failure-hunter-brief.md (error-path overlaps)  
**Scope Limitation:** Only modify files under `/home/node/github/llauncher/`. No filesystem edits outside the codebase.

---

## Objective

Remediate all CRITICAL, HIGH, and select MEDIUM issues identified in the python-reviewer agent's audit of commits a4d0361..9c73c71. Focus on functional correctness, thread safety, and idiomatic Python/FastAPI/Typer usage.

## Coordination Notes with Other Briefs (Revised per Strategic-Planner Review)

| Concern | Other Brief That Owns It | This Brief's Role |
|---------|--------------------------|-------------------|
| Timing attack (`hmac.compare_digest`) | **Security brief** — owns this exclusively | Cleanup pass only: remove any remaining `!= self.expected_token` patterns missed by security fix |
| API key redaction + chmod 0o600 (C1) | **Security brief** — consolidated ownership per strategic-planner finding #6 | This brief does NOT touch `to_dict()` or `_save()`. These are exclusively in the security brief. |
| TTL Cache Lock addition | **Silent-failure-hunter brief** — owns complete cache fix (Lock + public invalidate) | Verify Lock exists and works; do not implement invalidate() yourself |

---

## Fixed Issue List

### HIGH (fix in this batch)

**H1 — Thread safety on `_TTLCache`**
- File: `util/cache.py` lines 10-54
- Action: Add `import threading`, create `self._lock = threading.Lock()` in `__init__()`. Wrap both `get()` check-and-delete and `set()` write operations inside `with self._lock:`.
- **Note:** The public `invalidate(key)` method is owned by the silent-failure-hunter brief. This brief verifies Lock existence as acceptance criteria.

**H2 — `shutil_which` reimplements stdlib incorrectly**
- File: `core/gpu.py` lines 338-346
- Action: Delete the entire `shutil_which` function. Replace all callers with direct `shutil.which()` calls. Remove any local import of `os` inside this function (already imported at module level).

**H3 — Bare except blocks across GPU code swallow errors**
- Files: `core/gpu.py` lines 141-142, 150-153, 161-163, 208-209, 276, 291; `model_health.py` lines 90-95; `routing.py` lines 177-183 and 409-415
- Action: Replace every bare `except Exception:` or bare `except:` with scoped catches (e.g., `except json.JSONDecodeError as e: logging.debug(...)`, `except PermissionError as e: logging.warning(...)`). Every catch must emit at minimum a `logging.debug()` of the exception message so operators can diagnose. For `_query_ROCM` specifically, restructure so that `out` is initialized before any try block to eliminate UnboundLocalError cascade.

**H4 — Dead loop + wrong indent in MPS parser**
- File: `core/gpu.py` lines 308-318
- Action: Rewrite `_query_MPS` to correctly iterate over `system_profiler` output lines, use the match groups properly inside the loop body (fixed indentation), and handle multi-GPU Apple systems.

**H5 — `_try_NVIDIA` simulate-flag logic inverted**
- File: `core/gpu.py` line 134
- Action: Replace `simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""` with explicit, readable logic:
  ```python
  simulated = bool(os.environ.get("LLAUNCHER_GPU_SIMULATE"))
  ```

**H6 — CLI shadows stdlib `json` module**
- File: `cli.py` lines 103, 119, 183, 248
- Action: Rename every `json: bool = typer.Option(...)` parameter to `as_json`. Update all references from `json` to `as_json` throughout each command function.

**H7 — Cache invalidation bypasses private attribute**
- File: `core/model_health.py` line 134 (`_health_cache._store.pop(...)`)
- Action: After silent-failure-hunter brief implements the public `invalidate()` method, change to `_health_cache.invalidate(model_path)`. This brief does NOT implement invalidate() — ownership is in silent-failure-hunter brief per strategic-planner finding #7.

### MEDIUM (fix in this batch, low-risk changes)

**M1 — Dead loop variable and broken regex cleanup** — already handled by H4.

**M2 — Unused parameters on `_collect_devices`**
- File: `core/gpu.py` line 112
- Action: Remove unused `simulate` and `num_simulated` parameters from the function signature.

**M3 — Import placement in model_health.py**
- File: `core/model_health.py` line 81 (deferred `from pathlib import Path`)
- Action: Move to module top with other imports.

**M4 — Unused `ctx` params across CLI commands**
- File: `cli.py` lines 117, 166, 182, 241, 261, 278, 338 (seven functions)
- Action: Remove the `ctx: typer.Context = None` parameter from each command function.

**M5 — `_to_float` AttributeError on numeric input**
- File: `core/gpu.py` line 389
- Action: Before calling `.strip()`, check `if not isinstance(v, str): return float(v)`. Wrap in `(ValueError, TypeError)` only (remove bare catch).

**M6 — CLI bypasses public API for `_nodes` access**
- File: `cli.py` lines 253, 284, 292, 306
- Action: Replace direct `registry._nodes` accesses with calls to `__iter__()`, `get_node()`, or `to_dict()` as appropriate.

### LOW (optional polish if time)

**L1 — Naive datetime in model_health.py line 93**
Change `datetime.fromtimestamp(stat.st_mtime)` → `datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)`. Add `from datetime import timezone` to imports.
- **Note:** Per strategic-planner finding #10, M1 (same issue) is now bundled into Phase B file edits and not a standalone task. Mark as "will be addressed during Phase B model_health.py edit" if working in Phase B context.

---

## Verification Requirements (Issue-Tag Checklist per Strategic-Planner Finding #11)

### Specific checks for each issue tag:
| Tag | Verification Command/Method |
|-----|----------------------------|
| H1 | `grep -n 'threading.Lock\|self._lock' util/cache.py` — Lock present; `with self._lock:` appears in get() and set() |
| H2 | `grep -n 'shutil_which' core/gpu.py` — zero results expected |
| H3 | `grep -c 'except Exception:' core/gpu.py core/model_health.py agent/routing.py` — must be 0 (each except block has scoped catches) |
| H4 | `grep -A5 'for line in out.stdout' core/gpu.py` — match group used inside loop body |
| H5 | `grep -n 'simulated_output=not os.environ.get' core/gpu.py` — zero results expected; replaced with explicit bool() |
| H6 | `grep -n ': bool = typer.Option.*json\|json: bool' cli.py` — zero results expected; renamed to as_json |
| H7 | After silent-failure-hunter implements invalidate(): `grep '_health_cache._store.pop' core/model_health.py` → zero results |
| M2 | `grep 'simulate,' core/gpu.py 112` — unused params removed from _collect_devices signature |
| M3 | `grep 'from pathlib import Path' core/model_health.py` — at module top, not inside function body |
| M4 | `grep 'ctx: typer.Context' cli.py` — zero results expected (7 ctx params removed) |
| M5 | `core/gpu.py 389` — `_to_float` checks isinstance(v, str) before .strip() call |
| M6 | `grep '\.\_nodes' cli.py` — zero direct _nodes accesses; uses public API instead |

### Post-change verification:
1. Run full test suite: `pytest tests/ -v` — all existing tests must pass with output showing each test name
2. No new bare `except:` (without scoped exception type) introduced anywhere in modified files
3. Verify H7 fix only applies AFTER silent-failure-hunter brief delivers invalidate() method
4. Confirm ADR-003 through 006 features still present: grep for `check_model_health`, `_TTLCache`, `GPUHealthCollector`, `app.add_typer`
