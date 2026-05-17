# ADR-015: Orphan Policy — Annotation and Listing

**Status:** Accepted
**Date:** 2026-05-17
**Relationship to other ADRs:** ADR-008 (LauncherState as Stateless Facade) defines the on-disk reconciliation surface this ADR extends. ADR-010 (port at the call site) defines the verb-shape conventions used by the new `GET /orphans` endpoint and the `list_orphans` MCP tool. ADR-011 (swap semantics v2) defines the lockfile-vs-process matching used to decide managed-vs-unmanaged.

**Supersedes:** No prior ADR — pre-ADR behavior was "if it's not in our lockfile registry, it doesn't exist."

## Context

llauncher claims each model it launches by writing a per-port JSON lockfile in `LAUNCHER_RUN_DIR`. The agent shutdown path (#65) and the reconciliation guarantees of ADR-008 both treat the lockfile registry as the source of truth: anything in it is *managed* and must be reaped on graceful shutdown; anything not in it is invisible.

That invisibility is wrong for two recurring operator scenarios:

1. **Pre-existing llama-server.** The operator started a `llama-server` by hand (or with a sibling tool) before launching llauncher. It is bound to a port llauncher might want to use. Today `server start` rejects with `rejected_occupied` and the operator has no llauncher-side handle on the process.
2. **Crashed-agent residue.** llauncher crashed (SIGKILL, OOM-kill) without running its lifespan handler; the child `llama-server` is still alive, the lockfile may or may not still be on disk, and on next agent start there is no path to enumerate "what's running on this host that we used to own."

A future revision will add an `adopt` verb that writes a lockfile for a discovered orphan, claiming it as managed. That work is large — it has to decide what `model` field to write into the lockfile when only argv is available, whether to refuse on cmdline mismatches, and how to reconcile against the live config registry. **This ADR deliberately scopes only the annotation and listing half.** Adoption is left for ADR-015's deferred section, with the discovery surface in place so the operator can at least *see* the orphans today.

## Decision

### 1. Annotated process scan

A new function in `llauncher.core.process`:

```python
def find_all_llama_servers_annotated() -> list[tuple[psutil.Process, int | None, bool]]
```

Returns each live `llama-server` paired with the port extracted from `--port` in its argv (or `None` if no `--port`) and a `cmdline_unreadable: bool` flag for processes whose `cmdline()` raised `psutil.AccessDenied`. The port-extraction idiom mirrors `state.py:refresh_running_servers` so the two scans agree.

The original `find_all_llama_servers()` is **not modified**. Its callers (the v1 state path, footer cache) keep their existing behavior. Per the project's "annotated companion, not in-place edit" convention, the new scan is a sibling.

### 2. `OrphanInfo` dataclass and `list_orphans()`

```python
@dataclass(frozen=True)
class OrphanInfo:
    pid: int
    port: int | None
    cmdline_unreadable: bool = False
```

`operations.list_orphans()` returns `[OrphanInfo, ...]` for every process from the annotated scan whose `(port, pid)` does not match a *live* lockfile. The match rule is:

```
managed ≡ read_lockfile(port) is not None
        AND is_pid_alive(lockfile.pid)
        AND lockfile.pid == observed.pid
```

Any other state is an orphan: no lockfile, lockfile points at a dead pid, or lockfile points at a different live pid on the same port.

### 3. Audit emission cadence

`LauncherState` gains `orphans: list[OrphanInfo]` and `_observed_orphan_pids: set[int]`. `refresh_orphans()` runs at the end of `refresh()` and:

- For each currently-observed orphan pid not in `_observed_orphan_pids`: emit `audit_log.AuditAction.OBSERVED_ORPHAN` (which already exists, ADR-008) and add the pid to the set.
- For each pid in `_observed_orphan_pids` not in the current scan: drop from the set. A pid that later reappears re-emits exactly once.
- Repeated sightings of a pid already in the set: silent.

This dedupe lives in memory on the agent process; audit emission is once-per-sighting-pair-per-agent-lifetime in steady state. Restarting the agent re-emits every orphan, which is the correct behavior — restart resets the operator's view.

### 4. Permission-denied processes

A `llama-server` whose `cmdline()` raises `psutil.AccessDenied` is surfaced in the orphan list with `port=None, cmdline_unreadable=True`. The first sighting per pid logs a warning at WARNING level; subsequent sightings are silenced via `_warned_unreadable_pids: set[int]` (same prune-on-disappearance lifecycle as the audit-dedupe set). Such pids do **not** emit `observed_orphan` audit entries — we cannot honestly classify them as managed vs. unmanaged without seeing argv.

### 5. Surfaces

- **HTTP**: `GET /orphans` returns `{node, orphans: [...], total}`. `GET /status` gains `orphans` and `total_orphans` fields alongside its existing `running_servers` array.
- **MCP**: `list_orphans` tool with the same envelope.
- **CLI**: `llauncher orphan list` with `--json` matching the project's `server status` / `node list` rendering convention.

### 6. Out of scope

The following are deferred to a future revision (see §Deferred Work):

- `adopt_orphan` in any surface (CLI, HTTP, MCP).
- `POST /orphan/adopt/{port}` (not even a 501 stub).
- Any lockfile *write* on behalf of an orphan pid.
- Adding `is_managed` to `RunningServer` — the running roster and the orphan list are two adjacent surfaces, not a unified one with a discriminator field.
- Cmdline-model parsing (extracting `-m /path/to/model.gguf` from argv to populate a `model` field on `OrphanInfo`). The annotated scan extracts port only; the `model` name lives in the lockfile we did not write.

## Consequences

### Positive

- The operator can now `llauncher orphan list` and see every unmanaged `llama-server` on the host, with port and pid.
- Audit log gains a chronological record of when orphans first appeared, useful for post-mortem ("this pid was orphaned at T+12 minutes — that aligns with the OOM I saw in dmesg").
- The HTTP and MCP surfaces are in place for future agent integrations (a harness footer could surface "you have 2 orphan llama-servers we didn't launch") without further verb additions.
- The adopt question is deferred *without* leaving the discovery surface gated on it.

### Negative

- An orphan whose `cmdline` is unreadable shows up as `port=None`. There is no way to reconcile it with a lockfile, and the warning log is the only signal that something is there. Acceptable: in practice, llauncher and its targets run as the same user, so AccessDenied is rare.
- The audit log will pick up `observed_orphan` entries on every agent restart for every persistent orphan. The dedupe is intentionally per-agent-lifetime, not on-disk-persistent; we want a restart to give the operator a fresh view.
- A pid recycle (orphan pid X dies, OS reuses X for an unrelated process, that process then enters the scan as a llama-server somehow) would be misreported as "X re-appeared." Mitigation: pid recycle is rare on Linux within an agent's lifetime, and the audit entry includes port which would disambiguate.

### Open Questions

- **Should the orphan list include processes whose `--port` collides with a managed pid on a different port?** Currently no: we match `(observed.port, observed.pid)` against the lockfile for `observed.port`. A pid that lies about its port via argv is exotic enough that we'd rather wait for a real case.
- **Should `OBSERVED_ORPHAN` entries record the model path?** Not in this revision — `OrphanInfo.port` is enough for the operator to correlate with their own knowledge. The lockfile carries the model name for *managed* processes; we don't have an authoritative model name for orphans.

## Deferred Work

The following items are explicitly excluded from this ADR and tracked for a future revision (and likely a future ADR, given the design questions each raises):

### Adopt

A new verb `adopt_orphan(port, *, model: str | None = None)` that writes a lockfile claiming an existing orphan as managed. Open design questions:

- What goes in the `model` field of the lockfile when the operator does not specify? Parse argv for `-m`, then look up the path in the live config registry? Refuse if no config matches?
- What happens on `model` mismatch — argv says `/path/A.gguf`, operator-supplied `model_name` resolves to `/path/B.gguf`? Refuse? Trust the operator?
- Adopt-then-stop is a common dual: should `adopt` accept a `stop_after_adopt` shortcut, or is the verb pair sufficient?

These questions are real enough that pinning answers without a concrete operator request would be premature.

### `is_managed` on `RunningServer`

A unified roster discriminator across managed and orphan processes. Today they are two adjacent lists with no discriminator field because nothing in the codebase iterates them as one collection; adding the field would invite the assumption that they are uniform.

### Cmdline-model parsing

Extract `-m <path>` from argv during the annotated scan to populate a tentative `model` on `OrphanInfo`. Useful primarily as input to the adopt verb above; meaningless without it.

## Relationship to Other ADRs

- **Builds on ADR-008** (stateless facade): the managed-vs-unmanaged split is derived from the lockfile registry plus the live process table, the same two sources ADR-008 mandates for all state queries.
- **Builds on ADR-010** (port at the call site): `GET /orphans` and `list_orphans` are read endpoints; no port in the path because the verb returns all orphans. Future `adopt_orphan(port)` will follow ADR-010 with port at the call site.
- **Builds on ADR-011** (swap semantics v2): the managed-check predicate (`lockfile.pid == observed.pid AND alive`) reuses the same identity-match logic that swap uses to decide whether the port it just claimed is still ours.
