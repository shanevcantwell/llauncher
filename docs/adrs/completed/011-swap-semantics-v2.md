# ADR-011: Swap Semantics v2

**Status:** Accepted  
**Date:** 2026-05-02  

## Context

ADR-002 ("Unified Swap-with-Eviction Semantics") consolidated three different swap implementations (UI, Agent API, MCP tool) into a single `state.start_with_eviction()` method with rollback and readiness polling. That work was sound for its premise, but several decisions since have changed the premise:

- **ADR-008** reframes `LauncherState` as a stateless facade. The "three callers each constructing their own state" problem ADR-002 addressed no longer exists in that shape; every caller now reaches the same tool layer.
- **ADR-010** establishes that swap is per-port, port-keyed, and a distinct verb from start. The model-keyed `/start-with-eviction/{model}` endpoint is gone. The verb's precondition is that the port is occupied.
- The scoping conversation surfaced two requirements ADR-002 doesn't address:
  1. **Harness-initiated self-swap contract.** When an LLM agent emits a tool call to swap its own brain, the harness orchestrates the swap. The harness keeps a stable transport to llauncher; the inference transport dies during the swap. Reconnection is the harness's responsibility.
  2. **Concurrent swap rejection.** When a swap is mid-flight on a port (old stopped, new not yet ready), a second swap arriving on that port should reject with a clear signal rather than queue or race.

This ADR redefines swap mechanics for the v2 architecture and supersedes ADR-002.

## Decision

### Single Entry Point: `swap(port, model)` Through the Tool Layer

There is one swap operation, exposed identically through:

- **MCP tool:** `swap_server(port, model)`
- **HTTP Agent:** `POST /swap/{port}` body `{model}`
- **CLI:** `llauncher server swap <port> <model>`
- **Streamlit UI:** the model-card "swap" action

All four reach the same tool-layer `swap` function, which calls into the stateless facade (per ADR-008). No per-caller logic; no `strict_rollback` parameter (the MCP-vs-UI strictness distinction from ADR-002 is gone — single shared mechanic).

### Five-Phase Mechanic

**Phase 1 — Pre-flight validation** (no state mutation):

- Model exists in `config.json` on the target node.
- Model file health passes (per ADR-005).
- VRAM headroom is sufficient if GPU info is available (per ADR-006).
- Port is occupied (swap's precondition; per ADR-010).
- The port's lockfile and live process are consistent (per ADR-008's reconciliation rules).
- No swap currently in progress on this port (see In-Flight Marker below).

If any check fails: `success=false, port_state=unchanged, action=rejected_preflight`. Old model untouched.

**Phase 2 — Take the in-flight marker.** Atomically create `{LAUNCHER_RUN_DIR}/{port}.swap` (open with `O_EXCL`). If creation fails because the file exists, return `rejected_in_progress` immediately. The marker contains caller, timestamp, llauncher pid, and from/to model names — enough for stale-marker reconciliation later.

**Phase 3 — Stop the old model.** SIGTERM, brief grace period, escalate to SIGKILL if needed. Remove the old lockfile. Audit-log `stopped` (commanded). If stop fails (process refuses to die): release the marker, return `success=false, port_state=unchanged, action=rejected_stop_failed`. Old model is still running; nothing else has changed.

**Phase 4 — Start the new model.** Launch `llama-server` with the argv sentinel and the new config. Write the new lockfile. Audit-log `started` (commanded).

- If the process never starts (binary missing, syscall failure, etc.): proceed to Rollback.
- If the process starts but doesn't pass readiness within timeout (default 120 s, configurable): terminate it, then proceed to Rollback.

**Phase 5 — Readiness poll.** `GET /health` on the new model's port until 200 OK or timeout. On success: release the marker, audit-log `swapped`, return `success=true, port_state=serving, action=swapped`.

### Rollback

Rollback restarts the previous model on the same port using a config snapshot taken at pre-flight (so a config change mid-swap doesn't poison rollback). It uses the same launch and readiness mechanic as Phase 4, with its own poll.

| Rollback outcome | port_state | action | success |
|------------------|------------|--------|---------|
| Old model restored, ready | restored | rolled_back | false |
| Rollback start failed | unavailable | failed | false |
| Rollback readiness timeout | unavailable | failed | false |

The in-flight marker is released in all cases. On `unavailable`, an audit entry `port_dead` is recorded with operator-facing message: *"Swap failed and rollback failed — manual intervention required."*

### Same-Model Swap

Swap to the model already running on the port returns `success=true, port_state=serving, action=already_running` immediately. No teardown, no relaunch (per ADR-010 — restart is a deliberately deferred separate verb). Pre-flight still runs for the new-model checks (it's the same model, so the checks pass trivially); the marker is taken and released for audit consistency.

### Harness-Initiated Self-Swap Contract

When an LLM agent on model A emits a tool call to swap port P (which A occupies) to model B, the call is processed by the agent's **harness** — never by A directly. LLMs emit tokens, not HTTP calls.

```
                 stable transport
   harness ──────────────────────────► llauncher
       │                                  │
       │     dies mid-swap                │
       │ ╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳                │
       └──────► A on P (stopped) ──── B starts on P
                                                │
       reconnect ◄──────────────────── ready ◄──┘
```

- The harness's transport to llauncher (HTTP Agent or MCP) stays open throughout. The swap response is delivered on **this** transport, not the inference transport.
- The harness's inference transport to A dies when A is stopped in Phase 3. The harness must detect this and not attempt to reuse it.
- After the swap returns `success=true, port_state=serving`, the harness opens a fresh inference transport to B on the same port.
- `port_state=restored` means A is running again — the harness can reconnect, but with a **fresh** inference session. KV cache, conversation history, and any in-flight state are gone. The harness should treat `restored` as a session reset, not a session continuation.
- `port_state=unavailable` means the harness has no inference target on P. Operator intervention is required; the harness should surface this rather than silently retry.

### In-Flight Marker

Per-port marker file at `{LAUNCHER_RUN_DIR}/{port}.swap`, written atomically at the start of Phase 2 and removed at the end of any terminal phase (success, rollback, or failure).

Contents:

```json
{
  "caller": "mcp",
  "started_at": "2026-05-02T14:30:00Z",
  "llauncher_pid": 4242,
  "from_model": "mistral-7b",
  "to_model": "llama-3-8b"
}
```

A swap arriving on a port whose marker file exists rejects immediately:

```json
{
  "success": false,
  "action": "rejected_in_progress",
  "port_state": "unchanged",
  "error": "swap_in_progress",
  "since": "2026-05-02T14:30:00Z",
  "in_flight_caller": "mcp"
}
```

If the llauncher process holding the marker dies externally, the marker becomes stale. Lazy reconciliation on next read (same pattern as lockfile staleness in ADR-008): if `llauncher_pid` is dead, the marker is stale; clean it up, audit-log `swap_aborted`. The port may be in any state at that moment (old stopped, new partial, neither); the lockfile + reconciliation rules from ADR-008 report it honestly.

### Response Shape

Aligns with ADR-010's `action`-bearing envelope:

```json
{
  "success": true,
  "action": "swapped",
  "port_state": "serving",
  "port": 8081,
  "model": "llama-3-8b",
  "previous_model": "mistral-7b",
  "pid": 12345,
  "startup_logs": ["..."]
}
```

`action` values for swap:

| action | Meaning | success |
|--------|---------|---------|
| `swapped` | Different model swapped in, ready | true |
| `already_running` | Same model was already there | true |
| `rolled_back` | New model failed, old model restored | false |
| `failed` | New model failed, rollback also failed; port is dead | false |
| `rejected_preflight` | Pre-flight check failed before any state change | false |
| `rejected_stop_failed` | Couldn't stop old model; old still running | false |
| `rejected_in_progress` | Swap already in flight on this port | false |
| `rejected_empty` | Port had no occupant; per ADR-010 swap requires occupied | false |

`port_state` values: `serving | restored | unchanged | unavailable` (semantics preserved from ADR-002).

### Caller Differences (Eliminated)

ADR-002 distinguished `strict_rollback=True` (MCP) from `strict_rollback=False` (UI / HTTP). With the unified mechanic above, this distinction is removed. All callers get the same behavior. Pre-flight always reads the persisted config; if the old model's config has been deleted, pre-flight catches it (the lockfile says model X is on the port but config X is missing — corruption case from ADR-008's reconciliation rules) and returns `rejected_preflight`. There is no "non-strict" mode that would proceed past a bad pre-flight.

## Consequences

**Positive:**

- One swap mechanic across all callers; no per-caller variants to keep in sync.
- Concurrency safety against double-swap via the in-flight marker.
- Self-swap contract is explicit; harness reconnect responsibility documented.
- Response carries enough information for an LLM to decide the next step (reconnect to new, reconnect to restored with fresh session, escalate on `unavailable`).
- Builds cleanly on ADR-008 (lockfile / process identity), ADR-009 (per-node sovereignty), and ADR-010 (verb space).

**Negative:**

- New per-port marker file adds another piece of filesystem state to keep clean. Mitigation: stale-marker reconciliation on read, same pattern as lockfile staleness.
- Rollback uses a config snapshot taken at pre-flight, not the live config at rollback time. If a user updated the config between pre-flight and rollback, rollback reflects the snapshot. Deterministic, but may surprise a user expecting "live" behavior.
- The `unavailable` state requires operator intervention. No automatic retry. Acceptable for single-user hobby scope; production-grade would warrant supervision.

**Open Questions:**

1. Default readiness timeout. ADR-002 used 120 s; sticking with 120 s, configurable per call. Revisit only if very large models on slower storage become routine.
2. `startup_logs` field cap. ADR-002 used the first 100 lines; preserving that.
3. Stale-marker cleanup is lazy (on next read). Acceptable for hobby scope; if footers / dashboards display "swap in progress" briefly when the marker is actually stale, it self-resolves on next reconciliation.

## Supersession

This ADR **supersedes ADR-002** in full. ADR-002's implementation plan (Tasks 1–8) is replaced by the work captured here plus its forthcoming companion Issues. Specifically:

- `state.start_with_eviction()` → tool-layer `swap(port, model)` reaching the stateless facade per ADR-008.
- `EvictionResult` dataclass → response shape above; rename to `SwapResult` for clarity.
- `_compat` tuple-return wrapper → unnecessary; v2 is a clean rewrite (per the "rewrite, not migration" framing from the v2 conversation).
- The `strict_rollback` parameter is removed.
- Test plan (ADR-002 Tasks 6 + 7) needs re-derivation against the new shape; track separately.

## Relationship to Other ADRs

- **Builds on ADR-008** (stateless facade): swap operates on facade-level operations (read lockfile, stop process, start process, write lockfile, audit-log) without owning state itself.
- **Builds on ADR-009** (symmetric topology): a swap on a remote node is dispatched via HTTP to that node's tool layer; the swap mechanic itself runs there, not at the caller.
- **Builds on ADR-010** (port ownership): the `/swap/{port}` endpoint shape and verb precondition (port occupied) are set there; the mechanics are set here.
- **References ADR-005** (model health): pre-flight model file validation.
- **References ADR-006** (GPU / VRAM monitoring): pre-flight VRAM headroom check.
- **Supersedes ADR-002** (unified swap-with-eviction).
