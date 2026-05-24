# ADR-014: Cancellation of In-Flight Start/Swap

**Status:** Accepted
**Date:** 2026-05-16
**Relationship to other ADRs:** ADR-011 (swap semantics v2) defines the five-phase mechanic this ADR adds cancel checkpoints to. ADR-010 (port ownership at the call site) defines the port-keyed `POST /cancel/{port}` shape. ADR-008 (stateless facade, on-disk reconciliation) governs the marker file the cancel signal piggy-backs on. ADR-003 (agent API authentication) governs auth for the new endpoint, with no exception.

**Supersedes:** No prior ADR — there was no documented cancel facility. The pre-ADR behavior was "wait it out or kill the agent."

## Context

`operations.swap()` and `operations.start()` are synchronous tool-layer calls that can spend most of their wall time inside Phase 5's readiness poll (default 120 s) waiting for a freshly spawned `llama-server` to listen and load weights. Once initiated, the only way to abandon the operation was to send the agent SIGTERM (or, pre-#65, SIGKILL) and accept whatever child state that left behind.

Three concrete cases motivate a cancel verb:

1. **Wrong-model swap.** A harness emits a swap to model B, then immediately discovers B is the wrong choice (user changed their mind, A's last reply made B obviously unfit). Today the harness must wait the full readiness window before issuing the correct swap.
2. **Stuck readiness poll.** A model whose binary is present but whose first inference hangs (corrupt weights, mmap stall on a slow disk) will hold the poll until timeout. Cancel lets the operator abandon the attempt without waiting.
3. **Pre-flight cycle hot-loop.** A misconfigured config (path typo on a network mount with a long stat timeout) can make even pre-flight slow. Cancel before commit lets the operator unwind without leaving partial state.

The design space is small but the wrong choice is expensive:

- **Polling granularity.** Mid-phase polling (cancel checks woven through `proc.start_server`, the lockfile write, the SIGTERM grace loop) buys a few seconds of latency at the cost of a much larger reviewable surface and a much harder reasoning load about where cancellation can interrupt. The user-pinned answer is **phase boundaries only**: this gives a worst-case cancel latency bounded by the longest phase (the readiness poll's `check_interval`, default 1 s), with all the simplicity of "every checkpoint is one `if`."
- **Mechanism.** A condition variable, a thread, or an asyncio Event would each force coupling between the cancel signaller and the cancellable code path. The marker module already provides atomic file-backed state per port, with reconciliation rules ADR-011 ratified. **Adding a boolean to the marker JSON** reuses the existing rewrite path; the signaller writes one field, the poller reads one field.
- **Post-commit window.** The sliver between spawn-success and lockfile-write is sub-millisecond in practice and unrecoverable: we've already paid the spawn cost and the new process is alive. Treating a cancel that arrives in this window as a rollback would require killing a healthy child for nothing. The user-pinned answer is **cancel is a no-op after spawn-success**: the operation completes, with an advisory note in the result indicating the cancel arrived too late.

This ADR pins those decisions in code.

## Decision

### 1. Cancel flag in the marker file

`SwapMarker` gains a `cancelled: bool` field, default `False`. The default makes pre-ADR markers (written by `take_marker` before this change) read as not-cancelled. The marker module gains two new functions:

- `request_cancel(port) -> bool` — atomic read-modify-write that sets `cancelled=True` on the existing marker. Returns `True` if a marker existed (cancel signal delivered), `False` if no marker existed (no in-flight op to cancel — successful no-op from the caller's view).
- `is_cancelled(port) -> bool` — predicate that returns `False` if no marker file exists or if the marker exists but has not been flagged.

`take_marker` initializes `cancelled=False`. `release_marker` is unchanged — removing the marker file implicitly clears the cancel state for the next op on that port.

### 2. Checkpoints at phase boundaries only

In `operations.swap`, cancel is checked:

- Before each pre-flight call (model-health, VRAM).
- After Phase 3 stop-old completes, before Phase 4 launch.
- Once per readiness-poll tick during Phase 5 (re-using the existing 1 s `check_interval` — no new thread, no async task). The poll function gains an optional `cancel_check: Callable[[], bool]` keyword.

In `operations.start`, cancel is checked:

- Before the pre-flight model-health call.
- Once per readiness-poll tick during the spawn-wait (same hook as swap).

**Why phase boundaries only.** Mid-phase polling would require threading cancel logic through `proc.start_server`, the lockfile-write race recovery, and the SIGTERM grace loop. Each is a tight critical section whose correctness was reviewed in isolation; weaving cancel through them would force re-review with a larger surface. The user-visible cost of phase-boundary polling is bounded by the readiness `check_interval` (default 1 s) — invisible relative to the operations themselves.

### 3. Cancel before commit → rollback path (reused)

When `is_cancelled(port)` is true at a pre-commit checkpoint, the operation takes the existing rollback path (same as pre-flight failure or readiness timeout). The result envelope gains a new action variant:

- `cancelled` — the operation was abandoned per a cancel request, with no surviving state change. For `swap`, this is structurally identical to `rolled_back` from the port-state side (the previous model is restored if Phase 3 already stopped it). For `start`, it means the port is empty again.

The audit log records `AuditResult.CANCELLED` against the `STARTED` or `SWAPPED` action, so the audit reflects "we tried, the caller cancelled, we cleaned up."

### 4. Cancel after spawn-success → no-op with advisory

If the cancel signal lands in the sliver between spawn-success and lockfile-write, or after the readiness poll has already returned `ready=True`, the operation completes normally. The result envelope sets `cancel_ignored_post_commit=True` so the caller can distinguish "we cancelled in time" from "we cancelled too late and your op succeeded anyway."

Rationale: the spawn cost has already been paid; the new process is alive and (within microseconds) about to be claimed by a lockfile. Killing it would require an extra teardown that the caller can perform deterministically with a follow-up `stop` if they actually want the port empty. We don't second-guess that on their behalf.

### 5. HTTP endpoint: `POST /cancel/{port}`

Port-keyed per ADR-010. Body is empty. Response shape:

```json
{
  "cancelled": true,
  "marker_existed": true,
  "port": 8081
}
```

- `marker_existed: false` — no in-flight op on this port. Returns **HTTP 200** (success — there is nothing to cancel; the caller's intent is satisfied). The caller may choose to treat this as a no-op or as evidence the op already finished.
- `marker_existed: true` — cancel flag was set on the in-flight op. Returns **HTTP 200**. The actual abandonment happens at the next phase-boundary checkpoint inside the running op; this endpoint does not block on it.

The endpoint is registered on the same router as `/swap` and subject to the same `AuthenticationMiddleware`.

### 6. MCP tool: `cancel_server(port)`

Mirrors the HTTP shape. Returns the same envelope. Tool prompt text follows ADR-010 §Tool Prompt Guidance: brief enough for an LLM to choose it correctly, naming the relevant ADR.

### 7. CLI: `llauncher server cancel <port>`

New verb under the existing `server` group. Match the rich-table + `--json` output convention used by `server start/stop/status`.

## Consequences

### Positive

- A previously unrecoverable wait (stuck readiness, wrong-model commit) is now a 1 s round-trip plus the time to the next checkpoint.
- Implementation reuses the marker module's atomic-rewrite path and the existing rollback. No new concurrency primitives, no threads, no asyncio.
- The cancel-vs-no-op-vs-too-late distinction is explicit in the response envelope. Callers can react deterministically.
- ADR-016 (canonical self-swap test) can now express the "cancel a stuck swap" path.

### Negative

- Worst-case cancel latency is the readiness poll's `check_interval` (1 s). At the default this is invisible; configurable lower if a real consumer asks.
- Adding `cancelled` to the marker JSON couples the cancel mechanism to the marker schema. Future marker-format changes must preserve the field. Acceptable: the marker is internal per ADR-011 and we already control all readers.
- A `cancel` arriving in the post-commit window completes silently as a successful op (with the `cancel_ignored_post_commit` advisory). A caller relying on cancel as a hard kill switch will be surprised; the advisory exists to make this case observable.

### Open Questions

- **Should `is_cancelled` reconcile stale markers?** The existing `read_marker` reconciliation (ADR-011 §In-Flight Marker) clears a marker whose `llauncher_pid` is dead. For cancel we don't need that — if the agent is dead the op is already over. Skipping reconciliation here keeps `is_cancelled` cheap. Filed as a follow-up if a real consumer needs it.
- **Cancel during rollback.** A cancel that arrives while rollback is in flight is currently ignored — rollback finishes. Rationale: cancelling rollback would leave the port in `unavailable`, which is strictly worse than `restored`. Not exposed as an option until a use case demands it.
- **Per-caller cancel propagation across the harness boundary.** A harness that wants to surface cancel to a UI affordance will need to fan-out the request itself; the API does not push cancel notifications.

## Relationship to Other ADRs

- **Builds on ADR-011** (swap semantics v2): cancel slots into the five-phase mechanic at phase boundaries, reusing the rollback path and the marker file.
- **Builds on ADR-010** (port ownership): the `POST /cancel/{port}` shape and the MCP tool's port-keyed signature follow directly.
- **Builds on ADR-008** (stateless facade): cancel state lives on disk in the marker file, not in any in-memory state.
- **Enables ADR-016** (canonical self-swap test): the cancel verb is a precondition for testing the "cancel a stuck swap" path that ADR-016 will document.
