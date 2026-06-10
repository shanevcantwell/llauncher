# ADR-013: Per-Server Log Lifecycle — Append, Rotate, Bounded Tail

**Status:** Accepted
**Date:** 2026-05-08
**Relationship to other ADRs:** ADR-008 (configurable on-disk paths) extends here with `LAUNCHER_LOG_DIR`. ADR-005 (model health) is unaffected. The log-name sanitization collision risk flagged in `docs/m5-design.md` is filed separately ([#63](https://github.com/shanevcantwell/llauncher/issues/63)) and **out of scope** for this ADR.

**Supersedes:** No prior ADR — the previous behaviors (log files opened in `"w"` mode; `_tail_file` slurping the whole file via `f.readlines()`) were undocumented defaults rather than ratified decisions. Replacing them here for the record so a future reader does not assume those choices were intentional.

## Context

Each `llama-server` child process writes its stdout+stderr to `${LAUNCHER_LOG_DIR}/{name}-{port}.log`. Three problems with the pre-ADR behavior surfaced as M4 work approached:

1. **Truncation on every start.** `start_server` opened the log in `"w"` mode, destroying the previous run's output the moment the user hit "restart" — which is exactly when the previous run's tail was the most useful debugging artifact. The orientation spike (`docs/reviews/2026-05-02-v2-orientation-spike.md` §5) called this out as a Tier 2 deferred item.
2. **Unbounded growth.** With no rotation, a long-running server's log grew to the disk's limit. The single-user / single-host scope of this project doesn't make this an outage risk in practice, but it does make `_tail_file` — which used `f.readlines()` — slurp the entire file into memory on every call.
3. **No env override for the log directory.** `LAUNCHER_RUN_DIR` and `LAUNCHER_AUDIT_PATH` (ADR-008) are env-configurable for volume-mounted container deployments; logs are not. An in-container agent that wants to surface host-side logs has no way to point at the host's log directory.

This ADR addresses all three together because they share the same touch point (`core/process.py::start_server` and `_tail_file`) and have no useful intermediate state.

## Decision

### 1. Append mode + per-run banner

`start_server` opens the log file in `"a"` mode. Before handing the file descriptor to `subprocess.Popen`, it writes a single banner line:

```
=== started at 2026-05-08T14:33:21.847291 port=8081 ===
```

The banner makes the boundary between runs grep-friendly. ISO-8601 was chosen over a human-friendly format for sortability; PID is intentionally omitted because the banner is written *before* `Popen` returns, and writing it *after* would race with the child process's own first stdout line.

### 2. Size-based rotation, opportunistically applied

A new module `core/log_rotation.py` exposes:

```python
def rotate_if_needed(path: Path, *, max_bytes: int, keep: int) -> bool: ...
```

Called from `start_server` *before* the open, the helper checks the live log's size and, if it exceeds `max_bytes`, shifts the rotation chain up by one slot:

```
{name}-{port}.log     →  {name}-{port}.log.1
{name}-{port}.log.1   →  {name}-{port}.log.2
…
{name}-{port}.log.N   →  unlinked when N+1 > keep
```

Rotation is **deliberately** at process-start time, not on every write. We don't own the file descriptor at write time (the child does); a `logging.Handler`-style rotation would have to either fork-and-exec a helper or interrupt the child. Process-start rotation has neither problem and is sufficient for the cadence this project sees (a server is rarely restarted more often than once per minute).

Defaults: `LAUNCHER_LOG_MAX_BYTES = 50 * 1024 * 1024` (50 MiB), `LAUNCHER_LOG_KEEP = 3`. `max_bytes <= 0` disables rotation.

### 3. Bounded tail in `_tail_file`

The new implementation reads at most `lines × 160 × 2` bytes (the heuristic doubles a 160-byte average so callers asking for 100 lines get a 32 KiB window even if individual lines are unusually long). The window is taken from the *end* of the file via `seek(size - window)`. If the window started mid-file the first line is almost certainly truncated at an arbitrary byte boundary, so it's dropped before returning.

For the common 100-line tail of a multi-MB log, this changes the read budget from "whatever the file is" to "~32 KiB."

### 4. Configurable log directory

A new env var `LAUNCHER_LOG_DIR` joins the ADR-008 family:

| Env var | Default | Used for |
|---------|---------|----------|
| `LAUNCHER_RUN_DIR` | `~/.llauncher/run` | Lockfiles |
| `LAUNCHER_AUDIT_PATH` | `~/.llauncher/audit.jsonl` | Audit log |
| `LAUNCHER_LOG_DIR` | `~/.llauncher/logs` | Per-server logs (this ADR) |

`core/process.py` continues to expose a module-level `LOG_DIR` initialized from the setting, both for backward-compat with existing `patch("llauncher.core.process.LOG_DIR")` test calls and as a single canonical reference inside the module.

## Consequences

### Positive

- The previous run's tail survives a restart. Banner makes the boundary trivially grep-able.
- Bounded tail means `_tail_file` no longer scales with log size. UI log viewers won't OOM on long-lived servers.
- Container deployments can point logs at a volume-mounted directory without code changes.
- Rotation prevents disk fill in genuinely long-running setups (rare here, but cheap insurance).

### Negative

- Append mode means the log file grows across runs until the size cap fires. With small per-run output and a low `LAUNCHER_LOG_MAX_BYTES`, rotation can fire on what feels like a "fresh" file. Mitigated by the 50 MiB default — cadence-of-rotation is likely "weeks" for typical use.
- The `_tail_file` heuristic (160 bytes/line × 2) can produce fewer lines than requested if individual log lines are dramatically longer than that. Acceptable: returning a slightly-short tail is better than slurping a multi-GiB file. Callers that need exact line counts can read the rotated archives directly.
- Rotation is process-start-only: a server that runs for months without restart accumulates one giant live file regardless of `max_bytes`. This is a known acceptable limitation given the project's restart cadence (per ADR-009 hub-spoke topology, restarts are common via swap).

### Open Questions

- **Cross-tool log access.** Does the pi-coding-agent footer extension need rotated archives, or does it tail only the live file? Current behavior is the latter; if the answer changes we'll need a list-archives endpoint. Tracked informally; not a blocker.
- **Sanitization collision.** Two configs whose sanitized names collide (e.g. `model.a` and `model_a` both sanitize to `model_a`) would append into each other's logs after this ADR (since logs no longer truncate). Filed separately as [#63](https://github.com/shanevcantwell/llauncher/issues/63) — the sanitizer fix belongs in config validation, not log handling.

## Implementation Notes (2026-05-08)

Touch points (see `git log --grep "closes #52"` for the landing commit):

- `llauncher/core/log_rotation.py` — new. Rotation aborts on first rename/unlink failure rather than partially committing the chain shift.
- `llauncher/core/process.py::start_server` — rotation call + append open + banner write.
- `llauncher/core/process.py::_tail_file` — bounded-read rewrite.
- `llauncher/core/settings.py` — three new env vars.
- `tests/unit/test_log_rotation.py` — new (11 tests including a partial-rename-failure simulation).
- `tests/unit/test_process.py` — updated 1 (`test_normal_start` now patches `rotate_if_needed`), replaced 1 (`test_tail_file_unicode_error` → `test_tail_file_invalid_utf8_is_replaced`), added 8 new tests covering bounded tail and append-banner-rotate flow.

No callers of `LOG_DIR` or `_tail_file` outside `core/process.py` and tests; the module-level alias preserves the historical patch target.

## Amendment Notes

**2026-06-10:** The deferred gap — lossy log-filename sanitization
colliding distinct model names (#63, later re-reported as #146) — closed
by PR #160: `log_stem_for` is now the single injective name→filename
mint (sanitized stem + 8-hex SHA-256 of the exact canonical name).
Old-scheme files orphan and age out; no dual-scheme parsing. ADR moved
`accepted/` → `completed/`.
