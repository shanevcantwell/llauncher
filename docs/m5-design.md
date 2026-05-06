# M5 Design — Tier 2 ADRs and Implementation

**Status:** Draft
**Date:** 2026-05-05
**Predecessor:** [m4-design.md](m4-design.md)
**Successor:** [m6-design.md](m6-design.md)

## Goal

Land the five Tier 2 items the v2 orientation spike (§5) deferred during the M1–M4 push. Each is "important but not load-bearing" — the system works without them, but each papers over a real gap that surfaces in production-like use:

1. **Footer contract** — pin the REST shape and cadence the harness footer relies on.
2. **Logs lifecycle** — rotation, retention, bounded tail.
3. **Cancellation** — interrupt an in-flight start or swap.
4. **Orphan policy** — what to do with `llama-server` processes that match a known config but have no lockfile.
5. **Canonical self-swap worked example** — operationalize ADR-011's claims as an integration test.

Each gets its own ADR (012–016) and a small implementation slice. M5 is paced one item per session.

## Why Tier 2 Now

The Tier 1 ADRs (008–011) reshaped the architecture; Tier 2 ADRs make it production-friendly. The decision to defer was correct — none of these were on the M1–M4 critical path, and shipping them prematurely would have meant rewriting them on top of moving foundations.

After M4 the foundations are stable. Tier 2 work doesn't risk bouncing into M1–M4 churn anymore.

## Item 1 — Footer Contract (ADR-012)

### Problem

The pi-coding-agent footer extension reads `~/.llauncher/nodes.json` directly and polls each node's `GET /status` per-token. The `/status` payload is ~1KB per node (full `ModelConfig.to_dict()`), and `/status` calls `state.refresh_running_servers()` on every request — a process-table scan per call, per node, per token. At footer cadence this is meaningful CPU and wire overhead.

The footer cares about three fields: `ctx_size`, `parallel`, and the active `model`. Everything else in the response is wasted.

### Decision Sketch

Add `GET /footer-context/{port}` returning a minimal `{model, ctx_size, parallel, port}` JSON object. Cache the payload for `LAUNCHER_FOOTER_CACHE_S` seconds (default 1s) per port — at footer redraw cadence (multiple per second) this collapses N redraws into one process-table scan.

The legacy `/status` stays for the dashboard and other introspection consumers; this is purely a footer-optimized side endpoint.

### Touch Points

- `llauncher/agent/routing.py` — new `/footer-context/{port}` endpoint.
- `llauncher/agent/footer_cache.py` — **new**, TTL-cached lookup against the lockfile + `ConfigStore`.
- `pi-footer-extension/footer-budget.ts` — switch URL; backward-compat fallback to `/status` if 404 (one release cycle).

### Slice Scope

ADR + endpoint + cache + TS migration. ~1 session.

## Item 2 — Logs Lifecycle (ADR-013)

### Problem

Logs are at `~/.llauncher/logs/{name}-{port}.log`, opened in `"w"` mode — **truncated on every start**. So the most useful debugging artifact (logs from the run before the crash) is destroyed by the restart. There's also no size cap; `_tail_file` reads the entire file into memory, which OOMs on a long-running server.

### Decision Sketch

- Open new logs in `"a"` mode (append) plus a startup banner line (`=== started at <iso> pid=<n> ===`).
- Roll on size: when a log file exceeds `LAUNCHER_LOG_MAX_BYTES` (default 50MB), rotate to `{name}-{port}.log.1`. Keep at most `LAUNCHER_LOG_KEEP` rotated files (default 3).
- `stream_logs` switches to a bounded tail: seek `min(size, lines * AVG_LINE_BYTES * 2)` from the end, then read forward.
- Add `LAUNCHER_LOG_DIR` env override (paired with the existing `LAUNCHER_RUN_DIR` and `LAUNCHER_AUDIT_PATH` per ADR-008).

### Touch Points

- `llauncher/core/process.py` — open mode + banner; bounded tail in `stream_logs`.
- `llauncher/core/log_rotation.py` — **new**, size-rotate-on-write helper.
- `llauncher/core/settings.py` — `LAUNCHER_LOG_DIR`, `LAUNCHER_LOG_MAX_BYTES`, `LAUNCHER_LOG_KEEP`.

### Slice Scope

ADR + rotation helper + bounded tail + env vars. ~1 session.

### Open: name-collision foot-gun

Spike §5 noted a name-sanitization collision risk (two configs sanitize to the same filename). Out of scope for ADR-013 (it's a config validation problem, not a logs problem) — file as a separate Issue and let ADR-013 reference it.

## Item 3 — Cancellation (ADR-014)

### Problem

`ops.start` and `ops.swap` block synchronously. Readiness polling sleeps in a `time.sleep(check_interval)` loop for up to 120s. If a client disconnects (LLM agent times out, UI tab closed, harness gives up), the swap continues to completion regardless. Worse, the next request finds the port busy and gets `rejected_in_progress`.

### Decision Sketch

The in-flight marker (ADR-011) is the cancel signal. A `cancel: true` flag written into `{port}.swap` is checked at every readiness poll iteration:

- If `cancel` observed during Phase 4 (start), the readiness loop breaks early. The new process is terminated. Phase 5 attempts to roll back to the previous model.
- If `cancel` observed during Phase 3 (stop), we're past the point of no return for the old process; cancel is rejected (already stopping).
- New endpoint `POST /cancel/{port}` writes the cancel flag and returns immediately.

This is cheaper than full `asyncio` task cancellation and stays compatible with the existing synchronous core. The trade-off — cancel is at most polling-interval-late — is acceptable for the 1–2 second poll cadence.

### Touch Points

- `llauncher/core/marker.py` — add `set_cancel(port)` and `is_cancelled(port)` helpers.
- `llauncher/operations/swap.py` — readiness loop checks `is_cancelled` each iteration.
- `llauncher/operations/start.py` — same, since start also has a readiness wait.
- `llauncher/agent/routing.py` — new `POST /cancel/{port}`.
- MCP tool: new `cancel_server(port)` mirroring the HTTP shape.

### Slice Scope

ADR + marker plumbing + endpoint + MCP tool + integration test. ~1.5 sessions.

## Item 4 — Orphan Policy (ADR-015)

### Problem

`find_all_llama_servers` picks up any process whose argv contains `"llama-server"`, including ones started by hand outside llauncher. Pre-lockfile, those were silently absorbed into `state.running` and treated as managed. Post-lockfile (M1), those are now distinguishable: lockfile-absent + argv-match = orphan.

We have no policy yet. Today the v2 read-side just doesn't show them. That's accidentally fine, but unstated.

### Decision Sketch

Default policy: **leave alone, audit-log `observed_orphan` once per scan**. Don't auto-stop, don't auto-claim. Surface them on the read-side as `running_servers` with a `managed: false` flag so the UI can render them in a muted style.

Provide an explicit `llauncher orphan adopt <port>` CLI verb (and matching MCP tool) that creates a lockfile claiming the orphan, after pid-alive verification. This is a manual escape hatch, not a default.

### Touch Points

- `llauncher/core/process.py` — `find_all_llama_servers` annotates each match with `is_managed: bool` based on lockfile presence.
- `llauncher/operations/orphan.py` — **new**, `list_orphans()`, `adopt_orphan(port)` verbs.
- `llauncher/cli.py` — `llauncher orphan list` / `llauncher orphan adopt`.
- MCP `list_orphans`, `adopt_orphan` tools.
- HTTP `GET /orphans`, `POST /orphan/adopt/{port}`.
- `LauncherState.refresh_running_servers` — separate `running` from `orphans` collections (don't mix in the same dict).

### Slice Scope

ADR + verb + endpoint + tool + UI surface (small badge in dashboard). ~1.5 sessions.

## Item 5 — Canonical Self-Swap Example (ADR-016)

### Problem

ADR-011 claims "swap leaves the inference channel intact for the harness's MCP transport." The mechanism is structurally true (separate stdio process for MCP, separate HTTP/HTTPS for inference), but it's not documented anywhere and not exercised by a test that proves end-to-end correctness.

### Decision Sketch

This one is mostly documentation and one big integration test:

- ADR-016 captures the worked example in prose: "the harness sends `swap_server(port=P, model_name=B)` over MCP; here's the timeline of what dies and what stays alive; here's the response shape; here's what the harness does on each `port_state` value."
- The integration test (`tests/integration/test_self_swap.py`) reproduces the worked example: spin up two real `llama-server` processes (small models, e.g., LFM2-350m), trigger a swap, verify the MCP response shape, verify the new model serves a completion. Marked `@pytest.mark.live` since it actually exercises real binaries.

### Touch Points

- `docs/adrs/016-canonical-self-swap.md` — **new**.
- `tests/integration/test_self_swap.py` — **new**.
- `docs/examples/self-swap-timeline.md` — **new**, the prose timeline.
- `pi-footer-extension/` — link from its README to the worked example.

### Slice Scope

ADR + integration test + prose. ~1 session.

## M5 Work Order

The five items are mostly independent. Suggested sequence by **most-impactful-first**:

1. **Logs lifecycle** (item 2) — biggest production risk (the OOM-on-long-log + log-loss-on-restart pair). Land this first.
2. **Footer contract** (item 1) — biggest perf win.
3. **Cancellation** (item 3) — the rarest UX improvement but the spike flagged it as load-bearing for advanced users.
4. **Orphan policy** (item 4) — least urgent; the do-nothing default already works.
5. **Self-swap example** (item 5) — pure documentation/test debt. Last because it benefits from the others having shaken out.

## Exit Criteria

- [ ] ADRs 012–016 all `Accepted`.
- [ ] Each item's implementation slice landed with tests.
- [ ] Logs survive restarts; rotation cap enforced; `stream_logs` is bounded.
- [ ] Footer extension migrated to `/footer-context/{port}` (one release cycle of fallback).
- [ ] `POST /cancel/{port}` works for in-flight start/swap; integration test passes.
- [ ] Orphans surfaced read-side; `adopt` verb works; UI shows muted orphan rows.
- [ ] Self-swap integration test green on the live runner.

## Estimate

**~5–7 sessions.** One per item, with cancellation and orphan-policy each running a hair longer.

## Open Questions

1. **Footer contract — should it support batch?** `GET /footer-context?ports=8081,8082`. Probably yes; the footer often watches more than one port. Decide in ADR-012.
2. **Log rotation — synchronous or background thread?** Synchronous on rollover threshold check is fine for hobby scope; a background thread is overkill.
3. **Cancellation — should the marker carry an opaque token?** I.e., `cancel_token: <uuid>` so a stale cancel doesn't interrupt the next swap. Probably yes; design in ADR-014.
4. **Orphan UI — should adopt require a confirmation modal?** Yes — adopting a stranger process is a foot-gun if mis-clicked.

## References

- v2-orientation-spike §5 — the source of all five items
- ADR-008 — lockfile + audit log (orphan + cancellation depend on this)
- ADR-011 — in-flight marker (cancellation lives here)
- ADR-001 — TypeScript footer extension (item 1's consumer)
