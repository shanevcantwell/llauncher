# ADR-008: LauncherState as Stateless Facade

**Status:** Accepted  
**Date:** 2026-05-01  

## Context

The current architecture (per `PRODUCT_REQUIREMENTS.md` §1.2 and §3.1) treats `LauncherState` as a stateful object that owns:

- `models: dict[str, ModelConfig]` — loaded from `config.json`
- `running: dict[int, RunningServer]` — derived from a `psutil` process scan
- `audit: list[AuditEntry]` — in-memory action log
- `rules: ChangeRules` — access control

with a `refresh()` method that reloads everything. Four independent `LauncherState` instances exist simultaneously, each with its own refresh discipline:

| Caller | Refresh Behavior |
|--------|------------------|
| Agent HTTP server | Module singleton; refreshes on every request |
| MCP server | Lazy-loaded singleton; **does not refresh on read operations** |
| Streamlit UI | Per-session state with manual refresh before display |
| Ad-hoc temp instance | Function-scoped; instantiated for one-off checks |

Observed problems:

1. **Stale-state bugs.** MCP read tools never refresh, so `list_models` and `server_status` return data from the moment the process started.
2. **Temp instance anti-pattern.** Code paths construct a fresh `LauncherState()` purely to call a single check (e.g., port availability), incurring a full process-table scan per check.
3. **Audit log loss.** `refresh()` resets the audit list (per §3.1's `# Reset audit on full refresh? (specify behavior)` TODO), and the audit log is never persisted to disk. §2.3 calls it "action logging for governance and debugging" but no governance-grade durability exists.
4. **Conflation of concerns.** Config CRUD, running-process introspection, audit recording, and access-control rules are bundled into one class with a shared lifecycle. Each has its own natural source of truth.

## Decision

### Option Chosen: Reframe LauncherState as a Stateless Service Facade

`LauncherState` (or its successor; rename out of scope here) owns no data. It is a thin, instantiable service object that exposes operations against external sources of truth:

| Concern | Source of Truth | Access Discipline |
|---------|-----------------|-------------------|
| Model configs | `~/.llauncher/config.json` | Read-through; atomic write on mutation |
| Running servers | Lockfile dir + OS process table | Read-through; lazy reconciliation |
| Audit log | Append-only file at a configurable path | Append on each action; never reset |
| Rules | Settings / env | Read-through |

`refresh()` is **removed**. Nothing is cached at the facade layer; every call queries the underlying sources fresh. Caching, where worthwhile, is pushed into the source-specific layer (e.g., the existing `GPUHealthCollector` 5 s TTL, model-health 60 s TTL).

### Process Identity: Lockfile + Argv Sentinel

To distinguish llauncher-managed `llama-server` processes from any other on the host:

- **Lockfile** at `~/.llauncher/run/{port}.lock` written when a server starts, containing `{pid, model, started_at, llauncher_pid}`. Removed on clean stop.
- **Argv sentinel** — a marker flag set at start time (e.g. `--alias <model>`) so live processes can be cross-checked against their lockfile claim.

### Reconciliation Rules (lazy, on read)

| Lockfile | Pid alive? | Argv match? | Verdict |
|----------|------------|-------------|---------|
| Present | Yes | Yes | Ours, healthy |
| Present | No | — | Stale; clean up the lockfile, audit-log `observed_stopped` |
| Present | Yes | No | Corruption; log warning, refuse to act on this port |
| Absent | (process matches argv) | Yes | Orphan; leave alone, audit-log `observed_orphan` |
| Absent | — | No | Not ours; ignore |

Lockfile format is **internal to llauncher** — the harness footer and other external consumers go through the HTTP Agent (which composes lockfile + pid-alive checks per request) and never read the file directly. The format may change without affecting external contracts.

### Audit Log: Commanded vs. Observed

The audit log distinguishes actions llauncher performed from state changes it discovered:

- **Commanded:** `started`, `stopped`, `swapped`, `model_added`, `model_updated`, `model_removed`.
- **Observed:** `observed_stopped` (process found dead during reconciliation), `observed_orphan` (process matching argv with no lockfile).

Persisted as JSON Lines at a configurable path (default `~/.llauncher/audit.jsonl`). Append-only; never truncated by llauncher itself.

### Volume-Mountable Paths

Both the lockfile dir and the audit log path are configurable via env:

- `LAUNCHER_RUN_DIR` (default `~/.llauncher/run`)
- `LAUNCHER_AUDIT_PATH` (default `~/.llauncher/audit.jsonl`)

so that container deployments can mount them as volumes, enabling in-container agents to introspect the state of llauncher running on the host.

## Consequences

**Positive:**

- Eliminates the "four independent instances" problem entirely — facade objects are cheap and stateless; consumers construct as many as they want.
- MCP read-staleness disappears — there is no cached state to be stale.
- The temp-instance anti-pattern stops being an anti-pattern.
- Audit log gains real durability and a useful debugging signal (commanded vs. observed).
- Each source of truth has a single, named owner; no cross-cutting reset semantics.
- In-container agents can introspect the host's llauncher state through volume-mounted run/ and audit log.

**Negative:**

- Every read touches its underlying source. Process-table scans are not free; a hot path scanning per call would degrade.
  - *Mitigation:* the harness footer (highest-frequency consumer) goes through the HTTP Agent, which performs a single scan per request. CLI and MCP read operations are low-frequency.
- Audit log file grows unbounded.
  - *Mitigation:* rotation / retention is a separate concern (tracked as an Issue / future ADR).
- Lockfile + argv adds two mechanisms instead of one; both must be kept in sync at start time.
  - *Mitigation:* a single `start_server()` code path writes both; failure to write the lockfile aborts the start.

**Open Questions:**

1. **Orphan policy.** Default is "leave alone, audit-log `observed_orphan`." Is there a use case for adopting orphans (claiming an unmanaged `llama-server` matching our argv pattern)? Probably not — adopting silently is creepy. Defer to a deliberate `claim` operation if ever needed.
2. **Audit log rotation / retention** — out of scope here; track separately.
3. **CLI rename** (`llauncher` → `llaunch`) affects the argv sentinel choice; pinned in a separate rename Issue, not here.

## Supersession

Supersedes the `LauncherState` description in `PRODUCT_REQUIREMENTS.md` §1.2 and §3.1. §11.3's "anti-patterns to avoid in rewrite" #1 and #3 dissolve under this decision; #2 (redundant scans inside a single request) is downstream of the per-request scan strategy in the HTTP Agent and remains a separate concern.
