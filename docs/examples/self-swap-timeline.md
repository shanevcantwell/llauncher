# Self-Swap: Prose Timeline of a Canonical Swap

**Companion to:** [ADR-016 — Canonical Self-Swap](../adrs/016-canonical-self-swap.md).
**Executable proof:** [`tests/integration/test_self_swap.py`](../../tests/integration/test_self_swap.py).

This is the long-form walkthrough of the worked example summarized in ADR-016's T0–T4 table. Read it once when you are writing a harness on top of llauncher; refer back to ADR-016 §3 for the response-shape contract.

## Setup

The scenario is one agent harness — a long-lived process the operator launched — that has two roles bundled together:

1. **It is a chat / completion client.** It sends inference requests over HTTP to `http://localhost:8081/...` whenever it needs the model to generate text.
2. **It is an MCP client.** It opened a stdio session to a child process running `python -m llauncher.mcp_server` and uses that session to administer llauncher (list models, start/stop servers, swap, cancel).

The "self-swap" property is what makes #2 useful on the same port as #1: the harness can call `swap_server(port=8081, model_name="beta")` to replace the model serving its own HTTP traffic, and the MCP session it just used to issue the call is unaffected.

Concretely, at T = T0−ε the process tree looks like:

```
harness (PID 10001)
├── llauncher MCP child (PID 10002, stdio attached to 10001)
└── llama-server "alpha" (PID 10100, listening on :8081, spawned earlier by MCP child)
```

The harness has the MCP child's stdin/stdout. The harness has HTTP connectivity to PID 10100. PIDs 10002 and 10100 are unrelated processes — PID 10100 was spawned by the MCP child as a fully-detached child, and the MCP child does *not* hold its file descriptors past the spawn.

## T0 — Harness sends `swap_server` over MCP

```
harness                MCP child (10002)          llama-server alpha (10100)
   │                         │                              │
   │  JSON-RPC frame:        │                              │
   │  tools/call             │                              │
   │  swap_server            │                              │
   │  {port: 8081,           │                              │
   │   model_name: "beta"}   │                              │
   ├────────── stdin ────────►                              │
   │                         │ (still serving HTTP traffic) │
```

At T0 the harness writes one JSON-RPC `tools/call` frame to the MCP child's stdin. From the harness's perspective, this is one `await` on the MCP client SDK. The MCP child's stdio read loop receives the frame, looks up `swap_server` in its dispatch table (`llauncher.mcp_server.server._dispatch_tool` at line 81), and calls `servers_tools.swap_server(arguments)`. That handler in turn calls `llauncher.operations.swap.swap("beta", 8081, caller="mcp")`.

PID 10100 is unaware of this. It is still answering HTTP requests on :8081. If the harness had a streaming completion in flight against alpha, it is still receiving tokens.

Nothing has changed except that one function call is now in flight inside the MCP child.

## T0 → T1 — Pre-flight, marker, stop-old

Inside `operations.swap.swap()`:

- **Phase 1 (pre-flight)** validates that beta exists in the config, that the lockfile for port 8081 is live and points at a `llama-server` matching `alpha`'s PID, and runs optional model-health and VRAM checks. Wall time: typically <50 ms (it's all file reads and a `psutil` check).
- **Phase 2 (marker)** atomically creates a `swap.in_progress` file in the run dir, claiming exclusive right to mutate this port. Wall time: one `O_EXCL` syscall.
- **Phase 3 (stop-old)** calls `proc.stop_server_by_port(8081)`. That sends SIGTERM to PID 10100, waits up to the grace period for it to exit, and (if needed) escalates to SIGKILL. The lockfile is removed.

The harness's open HTTP connection to PID 10100 *does* get torn down here — that's unavoidable, because PID 10100 is being killed. If the harness had a streaming completion in flight, it sees its HTTP stream end. The MCP session, on the other hand, is still entirely alive — it is owned by PID 10002, which has not received any signal.

T1 is the moment Phase 3 returns successfully:

```
harness (10001)
├── MCP child (10002)              -- still alive, still attached to harness's stdio
└── (no llama-server on :8081)     -- PID 10100 is gone, lockfile cleared, port released
```

Wall time T0 → T1: dominated by the SIGTERM grace window, typically 1–5 s.

## T1 → T2 — Spawn new

Phase 4 of `operations.swap.swap()` calls `_launch_and_await_ready(beta_config, 8081, ...)`, which:

1. Calls `proc.start_server(beta_config, 8081)` — `subprocess.Popen` spawns a new `llama-server`. New PID, say 10150. It opens its log file (`~/.llauncher/logs/beta-8081.log`), starts loading weights, and starts listening on :8081.
2. Writes a new lockfile for :8081 with `model="beta", pid=10150`, using `O_EXCL`. If something raced us into the lockfile, we terminate the just-spawned process and bail; in the self-swap happy path nothing races us because the marker file blocks any other in-flight swap on the same port.

T2 is the moment after the lockfile write but before the readiness poll completes. The process tree is:

```
harness (10001)
├── MCP child (10002)             -- still alive
└── llama-server beta (10150)     -- spawned, loading weights, not yet ready
```

Wall time T1 → T2: a few hundred ms — process spawn is fast; loading weights is what takes long, and that happens during T2 → T3.

## T2 → T3 — Readiness poll

`_launch_and_await_ready` calls `proc.wait_for_server_ready(8081, timeout=120, cancel_check=lambda: mk.is_cancelled(8081))`. That function:

- Polls every `check_interval` seconds (default 1 s).
- On each tick:
  - Checks the cancel marker (ADR-014). If `cancelled=True`, returns `(False, logs)` immediately and the swap rolls back.
  - Tries a TCP `connect_ex` against `127.0.0.1:8081`. If it succeeds, the port is listening.
  - If listening, tails the log file and looks for one of `listening`, `server started`, `ready to serve`, `rest api listening`. If any matches, returns `(True, logs)`.

Wall time T2 → T3: the load time of the GGUF, typically 0.5–10 s for small models, longer for big ones. The cancel checkpoint is what makes the harness's recovery branch viable — if the load hangs (corrupt GGUF, disk stall), the harness can issue `cancel_server(port=8081)` over the *same MCP session* (which is still alive!) and the next poll tick will observe the cancel within ~1 s, terminate PID 10150, and restart alpha.

The MCP child (PID 10002) is *blocked* on this `await` for the duration of T2 → T3. Other clients of the same MCP child would see their tool calls queued behind it — this is a property of the swap operation, not of the swap-and-MCP architecture. The swap's worst-case latency is bounded by `DEFAULT_READINESS_TIMEOUT_S` (120 s).

T3 is when readiness returns `(True, logs)`. The process tree is unchanged from T2 except PID 10150 is now serving traffic.

## T3 → T4 — Response back to harness

`operations.swap.swap()` records an audit entry (`SWAPPED / SUCCESS`), constructs a `SwapResult`:

```python
SwapResult(
    success=True,
    action="swapped",
    port_state="serving",
    port=8081,
    model="beta",
    previous_model="alpha",
    pid=10150,
    message="Swapped alpha → beta on port 8081",
    startup_logs=[...],
    cancel_ignored_post_commit=False,
)
```

…and returns it. `servers_tools.swap_server` calls `.to_dict()` on it and returns to `_dispatch_tool`. `call_tool_handler` wraps the dict in a `TextContent(type="text", text=json.dumps(result))` and the MCP server framework writes one JSON-RPC response frame to stdout.

The harness reads the frame off its end of the MCP child's stdout. Its `await` on the MCP SDK returns the dict. It branches on `success`, `action`, and `port_state` (per [ADR-016 §3](../adrs/016-canonical-self-swap.md#3-response-shape-contract--swapresult-fields-the-harness-observes)):

- `success=True` and `action="swapped"` → swap completed, beta is now serving.
- `port_state="serving"` → the port has a healthy model; safe to send inference requests.
- `previous_model="alpha"` → for the harness's own footer/logging.

The harness then sends its first completion request to `http://localhost:8081/completion`. That hits PID 10150 (the new beta process). The HTTP socket from before T1 is long gone; this is a fresh TCP connection. The response is a beta-model completion.

T4 is the moment that first post-swap completion request returns. End-to-end T0 → T4 is dominated by Phase 3 (stop-old grace, 1–5 s) plus Phase 5 (readiness, 0.5–10 s) — typically 2–15 s total for a small model on a fast disk.

## What stays alive across T0 → T4

Looking at the entire timeline, every party that exists at T0 still exists at T4 *except* the old `llama-server` process (PID 10100):

| Party                       | T0 | T1 | T2 | T3 | T4 |
|-----------------------------|----|----|----|----|----|
| Harness (10001)             | ✓  | ✓  | ✓  | ✓  | ✓  |
| MCP child (10002)           | ✓  | ✓  | ✓  | ✓  | ✓  |
| stdio JSON-RPC session      | ✓  | ✓  | ✓  | ✓  | ✓  |
| Lockfile for :8081          | ✓ (alpha) | ✗ | ✓ (beta) | ✓ (beta) | ✓ (beta) |
| `llama-server` alpha (10100)| ✓  | ✗  | ✗  | ✗  | ✗  |
| `llama-server` beta (10150) | ✗  | ✗  | ✓  | ✓  | ✓  |
| Listening socket on :8081   | ✓ (alpha) | ✗ | ✓ (beta) | ✓ (beta) | ✓ (beta) |
| Harness's HTTP conn to :8081| ✓ (to alpha) | ✗ | — | — | ✓ (fresh, to beta) |

The MCP session — the second column — never has an ✗. That column is the property ADR-016 calls "the MCP control channel survives the swap." The lockfile column has a gap because we deliberately remove it after stopping old (so we don't end up with two lockfiles pointing at the same port during the spawn race window).

## Recovery branch — readiness hang and `cancel_server`

The timeline above is the happy path. The interesting failure mode is **the readiness poll hangs at T2 → T3** (beta's weights are corrupt; the model loader is in an infinite mmap retry; the disk is dying). The `swap_server` call in the harness is blocked inside that 120 s `await`.

Because the MCP session is *still alive* (the survival property does not depend on the swap completing), the harness — *or* the operator, *or* a second harness using the same MCP child — can issue a separate tool call:

```
cancel_server(port=8081)
```

That call is dispatched by the same MCP child, takes the same `_dispatch_tool` path, but does *not* block — it just atomically sets `cancelled=True` on the in-flight marker file and returns within milliseconds.

The next tick of the readiness poll inside the original `swap_server` call sees `mk.is_cancelled(8081) == True`, terminates PID 10150, removes its lockfile, and falls into the rollback branch. Rollback respawns alpha on :8081 and returns a `SwapResult` with `success=False, action="cancelled", port_state="restored", model="alpha", previous_model="alpha"`.

Worst-case wall time from the harness issuing `cancel_server` to seeing the original `swap_server` return: one `check_interval` (default 1 s) plus the time to respawn alpha (~5–10 s). The harness is *never* stuck — at every wall-clock moment between T0 and T4 it has a working MCP session and a deterministic way to abandon a hung swap.

See [ADR-014](../adrs/014-cancellation.md) for the cancellation mechanism and `tests/integration/test_mcp_flows.py::test_cancel_post_commit_during_swap_is_advisory` for the executable proof of the cancel path.

## Why this works — structural reasons

Three independent properties make the MCP-survival behavior structural, not incidental. A new contributor's first instinct is sometimes to fold these layers together (e.g., embed `llama.cpp` in the MCP child to avoid the second process). That refactor would break this contract; the ADR-016 decision is explicit about why we don't do it.

1. **Process separation.** The MCP child and every `llama-server` are different OS processes. `proc.stop_server_by_port(8081)` operates on PIDs claimed in lockfiles, which by construction are `llama-server` PIDs. The MCP child's PID is never in a lockfile. (See `operations/swap.py:380`.)
2. **Transport separation.** MCP is JSON-RPC over the MCP child's stdio. Inference is HTTP. The swap mutates the latter only.
3. **Lifecycle separation.** The MCP child's lifecycle is owned by whoever spawned it (the harness). The inference process's lifecycle is owned by llauncher's lockfile registry. They share no in-memory state and communicate only through the file-system seam ADR-008 ratified.

A change that violates any of these requires a new ADR that explicitly supersedes ADR-016.
