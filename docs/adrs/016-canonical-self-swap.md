# ADR-016: Canonical Self-Swap — Worked Example and Integration Test

**Status:** Accepted
**Date:** 2026-05-24
**Relationship to other ADRs:** ADR-011 (swap semantics v2) defines the five-phase mechanic this ADR exercises end-to-end. ADR-010 (port at the call site) defines the verb shape (`swap_server(port, model_name)`) the MCP tool exposes and that the worked example drives. ADR-014 (cancellation) supplies the recovery branch when readiness polling hangs. ADR-008 (stateless facade) and ADR-001 (pi-coding-agent TypeScript footer) are the two consumers whose contract this ADR pins.

**Supersedes:** No prior ADR. ADR-011 stated the self-swap property — "swap leaves the inference channel intact for the harness's MCP transport" — but neither the prose timeline nor the executable proof existed.

## Context

ADR-011 codified llauncher's swap as a five-phase mechanic with a structurally-clean separation between two transports:

- **The MCP control channel** is the agent's stdio child (`mcp_server.server.main_async`). The harness talks to that child over stdio JSON-RPC; nothing in the swap path touches it.
- **The inference channel** is a *separate* `llama-server` HTTP process, spawned and owned by `llauncher.operations.swap`. Swap reaps the old `llama-server` and spawns a new one on the same port. The MCP child is uninvolved.

The architectural claim that follows — *"the harness's MCP session survives a swap of the very model it is currently using"* — is structurally true given those two facts, but until this ADR it was undocumented and unproven:

1. **No prose record.** A new contributor or harness author had to read `operations/swap.py`, `mcp_server/server.py`, and `core/process.py` and reconstruct the timeline from code. The "what stays alive, what dies" story is exactly the story a harness author needs first.
2. **No executable proof.** `tests/integration/test_swap.py` (added pre-M5) exercises the swap operation at the lockfile and process-table level. It does not drive the swap *through* the MCP tool dispatch table, and does not assert that the MCP session is the same Python object across the operation. A regression that broke the MCP-survival property — e.g., refactoring `swap_server` to restart the whole agent — would not be caught by the existing test.
3. **No agreed response shape for the harness.** Tool-layer code passes around `SwapResult` (`llauncher/operations/swap.py:36`), but the harness sees a JSON dict. Which fields does the harness actually read? Without a pinned answer, every harness implementation makes its own choice and a non-breaking field rename in `SwapResult` could silently break a downstream.

This ADR fixes all three. It is a documentation-and-test ADR by intent: no production code changes, no new endpoint, no new verb. The deliverable is a canonical worked example plus its integration test plus its prose timeline, with the contract pinned on the response shape the harness consumes.

## Decision

### 1. Worked example: timeline T0 → T4

The canonical self-swap is: an agent harness, talking to the llauncher MCP child over stdio, calls `swap_server(port=P, model_name=B)` to replace model A (currently running on P) with model B. The harness *itself* is running on top of model A — the inference its next completion call will hit lives on P. The interesting property is that the call returns successfully and the harness's *very next* completion request, sent over HTTP to `http://localhost:P/...`, hits a freshly-loaded model B without any reconnect on the MCP side.

The five-phase swap (ADR-011) maps onto five wall-clock moments visible to the harness:

| Time | Event | What dies | What stays alive |
|------|-------|-----------|------------------|
| **T0** | Harness sends `swap_server` over MCP stdio | nothing | MCP child (stdio); old inference proc (HTTP); harness itself |
| **T1** | Pre-flight passes; marker file claimed; `proc.stop_server_by_port(P)` issues SIGTERM | old inference proc receives SIGTERM, exits cleanly (or escalates to SIGKILL after grace) | MCP child; harness; lockfile dir (marker remains until phase 5 returns) |
| **T2** | New `llama-server` process spawned with model B's config; new lockfile written (atomic O_EXCL) | — | MCP child; harness; new inference proc (not yet ready) |
| **T3** | Readiness poll succeeds (port accepts TCP + log banner matches); audit records `STARTED` and `SWAPPED` SUCCESS | — | MCP child; harness; new inference proc (ready) |
| **T4** | `SwapResult` returned across the `await` boundary in `_dispatch_tool`; MCP child writes one JSON-RPC frame back over stdio; harness sends its first completion request to `http://localhost:P/...` against model B | — (the harness's HTTP request hits the freshly bound socket) | MCP child; harness; new inference proc |

The mapping of these moments to functions:

- T0 → `llauncher.mcp_server.server._dispatch_tool("swap_server", ...)` is entered.
- T1 → `llauncher.operations.swap.swap()` Phase 3 (`proc.stop_server_by_port`) returns True.
- T2 → `_launch_and_await_ready` spawns the new process and writes the lockfile (lines 105–122 of `operations/swap.py`).
- T3 → `proc.wait_for_server_ready` returns `(True, logs)`; the success branch of `swap()` runs `al.record(AuditAction.SWAPPED, AuditResult.SUCCESS, ...)`.
- T4 → `servers_tools.swap_server` returns `result.to_dict()`; `_dispatch_tool` returns to `call_tool_handler` which writes the response frame; the harness reads the frame and proceeds.

The full prose walkthrough lives in `docs/examples/self-swap-timeline.md`; this ADR fixes the table as the contractual summary.

### 2. Why the MCP control channel survives the swap

Three independent facts that, together, make the survival property structural rather than incidental:

1. **The MCP child is a separate OS process from any `llama-server`.** The MCP server is the stdio-attached Python process spawned by the harness; `llama-server` processes are spawned by `llauncher.core.process.start_server` as fully-detached children of the agent process tree. The swap path's `proc.stop_server_by_port(port)` (`operations/swap.py:380`) operates only on the PID claimed by the lockfile on that port, which is — by construction — a `llama-server` PID, never the MCP child's PID.
2. **The transports are non-overlapping.** MCP is JSON-RPC over stdio between the harness and the MCP child. Inference is HTTP between the harness and the `llama-server` on port P. The swap mutates only the latter. There is no shared socket, no shared file descriptor, and no shared event loop.
3. **The lifecycles are distinct.** The agent's stdio lifecycle is owned by the harness (which spawned the MCP child); the inference lifecycle is owned by llauncher's lockfile registry. They communicate only through the file-system seam ADR-008 ratified (lockfile, marker, audit log) — none of which the MCP child needs to read or write to keep its stdio attached.

A future change that tried to make a single process handle both surfaces — e.g., an embedded `llama.cpp` in the MCP child — would violate this ADR's contract. Such a change must be its own ADR explicitly superseding this one.

### 3. Response shape contract — `SwapResult` fields the harness observes

The `SwapResult.to_dict()` envelope returned by `swap_server` includes more fields than the harness needs. The harness depends on the following subset; the others are introspection-only and may change without breaking the harness contract.

| Field | Type | Why the harness needs it |
|-------|------|--------------------------|
| `success` | `bool` | Branch on overall outcome. `True` ⇒ either `swapped` or `already_running`; `False` ⇒ port is in `restored`, `unchanged`, or `unavailable` state per `port_state`. |
| `action` | `str` | Discriminator for the success-vs-failure case. The harness specifically distinguishes `swapped` (new model is on the port) from `already_running` (same model, no-op) from `rolled_back`/`cancelled` (old model restored) from `failed` (port dead, needs human intervention) — these drive four different harness behaviors. |
| `port_state` | `str` | Authoritative answer to "what's on the port right now": `serving | restored | unchanged | unavailable`. The harness uses this to decide whether to retry inference or surface an error to the operator. |
| `model` | `str | null` | Name of the model currently on the port. The harness uses this to update its own "current model" footer state. |
| `previous_model` | `str | null` | Name of the model that *was* on the port before the swap. Used by the harness to log the transition and (in the `rolled_back` case) to know it's back to the old model. |
| `pid` | `int | null` | The new (or restored) `llama-server` PID. The harness rarely uses this directly but a debug-mode harness logs it so a post-mortem can correlate with the audit log. |

The following fields are **introspection-only** and not part of the harness-facing contract:

- `port` — echo of the request; the harness already knows.
- `message` — human-readable; the harness may pass it to the operator but must not parse it.
- `startup_logs` — failure-case debugging only; the harness surfaces it to the operator on `success=False` and otherwise ignores it.
- `cancel_ignored_post_commit` (ADR-014) — set when a cancel arrived after spawn-commit; the harness honors it as a "your cancel was too late" advisory but the swap completed successfully and the contract above still holds.

A field rename or removal in this contractual subset is a breaking change requiring a new ADR. A change in the introspection-only subset is not.

### 4. Recovery branch — readiness hang and the cancel path

The interesting failure mode for self-swap is *not* "swap fails fast" — that case is well-tested by `tests/integration/test_swap.py::test_swap_rollback_on_invalid_model`. The interesting case is **readiness hang**: the new `llama-server` spawned, the lockfile was written, but the model is taking 90 s to load (mmap stall on a slow disk, weights checksum, a corrupt GGUF that hangs in deserialization). The harness's `swap_server` call is now blocked inside `wait_for_server_ready` for up to `DEFAULT_READINESS_TIMEOUT_S` (120 s).

ADR-014 supplies the recovery: while the swap is hung in Phase 5, the harness (or any other client of the same MCP child) can issue `cancel_server(port=P)` over the same MCP transport. Because cancel is dispatched through the *same* MCP child but a *different* tool handler, and the readiness-poll loop has a phase-boundary cancel check on each `check_interval` tick (default 1 s), the cancel signal is observed within ~1 s by the running `swap`. The poll returns `(False, logs)`; `swap` falls into the rollback branch, restarts the previous model, and returns a `SwapResult` with `action="cancelled"` and `port_state="restored"`.

The MCP transport's survival across the *swap* is what this ADR makes canonical; the MCP transport's responsiveness *during* the swap is what ADR-014 makes canonical. Together they answer "the harness is never stuck — it always has a working control channel and always has a deterministic way out of a hung swap."

The integration test exercises the happy path. The cancel-during-hang path is covered by `tests/integration/test_mcp_flows.py::test_cancel_post_commit_during_swap_is_advisory` and `test_cancel_during_start_pre_commit`; this ADR does not duplicate it, only references it as the recovery branch.

### 5. Executable proof — `tests/integration/test_self_swap.py`

The integration test added by this ADR is the canonical proof of the worked example. It uses the in-process `mcp_dispatch` fixture (see `tests/integration/conftest.py`) — the same dispatch table the real MCP server uses — so a refactor that breaks tool routing will fail the test. It registers two small models on isolated paths, drives `swap_server` via the dispatch table, and asserts:

1. The `SwapResult` envelope conforms to the contract in §3 (every contracted field present, types match, values match the expected `swapped` outcome).
2. The MCP server's `_mcp_state` is the *same Python object* before and after the swap, exercising the "MCP child survived" property at the state layer — a refactor that reset or rebuilt `_mcp_state` mid-swap would fail this assertion.
3. Post-swap, the new `llama-server` is bound to the same port and a TCP connection to that port succeeds — exercising the "inference endpoint is up on the same port, with a different process behind it" property.
4. The lockfile's recorded PID after the swap differs from the lockfile's recorded PID before the swap — exercising the "old proc died, new proc lives" property.

The test is marked `@pytest.mark.integration` (matching `test_mcp_flows.py`). It uses the stub `llama-server` binary; the live-model variant runs only when `LLAUNCHER_INTEGRATION_REAL=1` and the real-binary fixtures are wired (see `conftest.py::_real_mode_available`), and is marked `@pytest.mark.live` per the existing convention.

## Consequences

### Positive

- A new harness author has a single document (`docs/examples/self-swap-timeline.md`) that answers "what happens when I call `swap_server` on the model I'm currently using" without reading source.
- A refactor that breaks the MCP-survival property fails the integration test instead of breaking a downstream silently.
- The harness-facing subset of `SwapResult` is pinned in §3. Internal refactors of `SwapResult` can proceed freely as long as that subset is preserved.
- The cancel recovery branch (ADR-014) has a documented home; future contributors reading the worked example see how the two ADRs compose.

### Negative

- The contract in §3 ossifies six field names. A future ADR that wants to rename `previous_model` (e.g., to `prior_model` for consistency with `prior_pid` if that were added) must explicitly amend this ADR.
- The integration test depends on the in-process `mcp_dispatch` fixture, which itself depends on the conftest harness's seam-patching contract. A change to the seam list in `tests/integration/conftest.py::mcp_env` could break this test for reasons unrelated to swap. Acceptable — the conftest is already the canonical seam for every MCP integration test.

### Open Questions

- **Should the contract include `startup_logs` for the failure case?** Today the harness surfaces `startup_logs` to the operator when `success=False`, but the contract above marks the field introspection-only on the grounds that a structured-error replacement would supersede it. If a future harness wants to programmatically grep `startup_logs` (e.g., to distinguish OOM from missing-file), we'd need to either pin the field or add a structured `error_kind` enum. Filed for a follow-up if a real consumer asks.
- **Should the worked example explicitly cover the multi-harness case?** Today the worked example assumes one harness. Two harnesses both holding stdio sessions to the same MCP child would both see their sessions survive the swap (the survival property is structural, not per-session) — but the second harness might also have an in-flight inference request that fails mid-flight when the old `llama-server` dies. Out of scope for this ADR; the worked example calls out single-harness explicitly.
- **Linking from the pi-coding-agent footer README.** The acceptance criteria mention this link. The pi-coding-agent / footer extension repo is not vendored into llauncher; the link should be added to that repo once the timeline doc is stable. Tracked in issue #117.

## Relationship to Other ADRs

- **Operationalizes ADR-011** (swap semantics v2): the five-phase mechanic is the substrate; this ADR pins the timeline and the harness-facing contract above it.
- **Builds on ADR-010** (port at the call site): the worked example uses the port-keyed `swap_server(port, model_name)` shape verbatim.
- **Composes with ADR-014** (cancellation): the recovery branch in §4 is the canonical use case for cancel.
- **Pins the consumer of ADR-008** (stateless facade): the MCP child and the inference process communicate only through the file-system seam ADR-008 ratified; that is exactly why the MCP control channel survives the swap.
- **Pins a contract for ADR-001** (pi-coding-agent footer): the response-shape contract in §3 is what the footer extension and any future TS-side consumer can rely on.
