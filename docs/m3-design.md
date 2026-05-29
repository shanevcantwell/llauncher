# M3 Design — UI Migration + v1 Path Removal + Multi-Node Hardening

**Status:** Draft
**Date:** 2026-05-05
**Predecessor:** [v2-handoff.md](v2-handoff.md), [v2-implementation-roadmap.md](plans/v2-implementation-roadmap.md) §M3
**Successor:** [m4-design.md](m4-design.md)

## Goal

Restore daily-driver capability on v2 while finishing the topology work that was scaffolded in M2. By the end of M3:

- The Streamlit UI calls `llauncher.operations.*` directly. No UI code path goes through `LauncherState.start_server` / `stop_server` / `start_with_eviction_compat`.
- `state.py` is a read-side facade only. All v1 mutation methods are deleted, along with the nine tests skipped in slice 6.
- Multi-node dispatch goes through a single tool-layer entry point that short-circuits to local infra when the target is this node, and forwards via HTTP when it isn't.
- The pi-coding-agent + footer extension keep working through the migration. No client-visible regressions on `/status`, `/models`, or the verb endpoints.

## Reconciliation with the Original Roadmap

The original roadmap (2026-05-02) put **multi-node** in M3 and **UI** in M4. The post-M2 reality is:

- The remote dispatch wire (`RemoteNode`, `RemoteAggregator`) is already in place from M0 and was port-keyed in slice 4. What's *missing* is the self-loop short-circuit and the auth pass-through.
- The UI's v1 mutation calls are the largest blocker to deleting `state.py`'s mutation surface. Until they move, M3's "delete v1 paths" can't complete.

So M3 collapses both concerns: UI migration unblocks v1 deletion, and the remaining multi-node hardening rides along since it touches the same call sites. Full UI redesign moves to M4.

## Current State (Post-Slice 7)

| Surface | Status |
|---------|--------|
| HTTP Agent verbs | ✅ Port-keyed, ops-backed (slice 4) |
| MCP tools | ✅ Port-keyed, ops-backed (slice 5) |
| CLI (`llauncher server start/stop`) | ✅ Goes through ops (M1) |
| **Streamlit UI** | ✅ Now calls `ops.swap`, `ops.stop`, `ops.start` directly (slice 7 closes #47) |
| **Multi-node dispatch** | ⚠️ `RemoteAggregator` works for HTTP, but no self-loop short-circuit and no auth pass-through |
| **`state.py` mutation surface** | ❌ Still present; deletion deferred to slice 9 |
| **Skipped v1 tests** | ⚠️ Eleven tests still skipped (slice 6), awaiting v1-path deletion in slice 9 |

## Work Breakdown

### Slice 7 — UI migration (closes #47) ✅

**Status:** Complete. Three call sites in `ui/tabs/model_card.py` migrated from v1 state to v2 ops:

| Line | Current | Replace with |
|------|---------|--------------|
| `:144` | `state.start_with_eviction_compat(model_name, port, caller="ui")` | `ops.swap(model_name, port, caller="ui")` |
| `:255` | `state.stop_server(port, caller="ui")` | `ops.stop(port, caller="ui")` |
| `:302` | `state.start_server(model_name, caller="ui")` | `ops.start(model_name, port, caller="ui")` (port required — see below) |

The third site was the one slice 4 left a guarded TODO on. The UI now surfaces a port input via `_render_start_form()` rather than relying on the v1 `default_port` fallback.

**Result-envelope handling.** v1 returned `(success, message, process)`; v2 returns a `StartResult` / `StopResult` / `SwapResult` dataclass with `action`, `model`, `port`, `pid`, `message`. The UI renders the `action` discriminator (different toast colors for `started` vs `already_running` vs `rejected_occupied`).

**No fallback.** Per ADR-010, the UI may default a port from `DEFAULT_PORT` env, but `ops.start` itself takes no defaults — the UI synthesizes a port before calling.

### Slice 8 — Self-loop short-circuit (touches ADR-009)

The tool layer needs a single entry that picks local vs. remote based on the target node name. Pattern:

```python
# llauncher/operations/dispatch.py  (new)
def start(model: str, port: int, *, caller: str, target: str | None = None) -> StartResult:
    if _resolves_to_local(target):
        return _local.start(model, port, caller=caller)
    return _remote.swap_on_node(target, model, port, caller=caller)  # swap for parity with local eviction
```

**Key decision (Slice 7 discovery):** Remote dispatch must use `swap_on_node` instead of `start_on_node`. When the target port is occupied:
- `start_on_node` returns `rejected_occupied` → no UI feedback, just a toast
- `swap_on_node` triggers full eviction flow with rollback/readiness polling — same as local behavior

This parity was discovered during Slice 7; it should be documented in ADR-011 or v2-handoff.md.

`_resolves_to_local(target)` returns True when `target is None` or `target == LLAUNCHER_AGENT_NODE_NAME` (defaulting to `socket.gethostname()`). Per ADR-009 §"Self-Loop Dispatch."

The local branch keeps the current `ops.start` signature; the remote branch adapts the existing `RemoteAggregator.swap_on_node` call. Net result: callers (UI, CLI, MCP) take a `target` arg and the dispatch picks the transport.

**Decision to defer:** whether `target` lives on the verb signatures or in a thread-local context. Pin to **explicit verb argument** for now — it's the simplest correctness story and matches ADR-009's "tool-layer signature carries `target`" wording.

**Auth pass-through (ADR-003):** when dispatching remote, attach `X-Api-Key` from `LLAUNCHER_AGENT_TOKEN`. The HTTP Agent already validates it; `RemoteNode._headers` already produces the header. Verify the call chain end-to-end and add a unit test against a recorded transport.

### Slice 9 — `state.py` reduction

After slice 7, no caller mutates state. Delete:

- `LauncherState.start_server`
- `LauncherState.stop_server`
- `LauncherState._start_with_eviction_impl`
- `LauncherState.start_with_eviction` / `start_with_eviction_compat`
- `LauncherState.can_start` / `can_stop`
- `LauncherState.record_action`
- `EvictionResult` dataclass (now lives only as a removed legacy type)

Keep:

- `LauncherState.refresh()`, `refresh_running_servers()`, `refresh_models_from_disk()`
- `models`, `running` dicts (read-side state for `/status`, `/models`, `server_status`, `list_models`, `get_model_config`)
- `__post_init__` lazy refresh

Then delete the eleven slice-6-skipped tests. They reference removed methods; the `pytest.mark.skip` reasons all explicitly call this M3 milestone.

### Slice 10 — Loose ends

- **#45** — Add the `RemoteAggregator` falsy-registry regression test that the slice-1 fix forgot. Trivial; lives in `tests/unit/test_remote.py`.
- **NodeRegistry auto-spawn removal.** `NodeRegistry.start_local_agent` is the auto-spawn path that the spike §6 flagged. Delete it now that the UI no longer triggers it (verify post-slice-7 that nothing calls it). Delete the two regression tests skipped in slice 6.
- **`DEFAULT_PORT=8080` collides with `blacklisted_ports={8080}`.** Handoff §"Institutional Knowledge" #6. One-line fix: change `DEFAULT_PORT` default to `8081` (or whatever the harness footer expects). Tracked as a side-quest issue if not already filed.

## Touch Points

| Module | Change |
|--------|--------|
| `llauncher/ui/tabs/model_card.py` | Replace three v1 call sites with `ops.*`; route port from form |
| `llauncher/operations/__init__.py` | Optionally re-export new `dispatch` module |
| `llauncher/operations/dispatch.py` | **New.** Local/remote dispatch keyed on `target` |
| `llauncher/state.py` | Delete v1 mutation methods (slice 9) |
| `llauncher/remote/registry.py` | Delete `start_local_agent` (slice 10) |
| `llauncher/core/settings.py` | Adjust `DEFAULT_PORT` default if not already done |
| `tests/unit/test_state.py` | Delete v1-path tests (slice 9) |
| `tests/integration/test_state_integration.py` | Delete v1-path tests (slice 9) |
| `tests/integration/test_ui.py` | Replace UI tests against the v2 ops form |
| `tests/unit/test_remote.py` | Add #45 regression test (slice 10) |

## Test Strategy

- **Slice 7:** mock `ops.start/stop/swap` in UI tests; assert the right verb is called with the right args. No real model launches needed at this layer.
- **Slice 8:** unit test `_resolves_to_local()` directly; integration test the dispatch with a mocked `RemoteAggregator` to confirm the local path bypasses HTTP and the remote path attaches headers.
- **Slice 9:** the proof of correctness is "tests still pass without the deleted methods, and the eleven slice-6 skips are gone." Net delta should leave the suite at ~600 passed, 0 skipped (modulo unrelated `live` tests).
- **Slice 10:** smaller targeted tests; nothing structural.

## Exit Criteria

- [ ] No grep hit for `state.start_server`, `state.stop_server`, `start_with_eviction` in `llauncher/` or `tests/` (excluding doc references).
- [ ] All 11 v1-skipped tests deleted; 0 skips on `pytest tests/`.
- [ ] `ops.start/stop/swap/delete_model` accept `target: str | None`; UI/CLI/MCP all carry it through.
- [ ] `LLAUNCHER_AGENT_TOKEN` set → remote calls include `X-Api-Key`; unset → no header. Verified by transport-level test.
- [ ] `state.py` is < 200 lines; only read-side methods remain.
- [ ] Manual smoke: UI starts/stops/swaps a real model on the local node; the harness footer continues to render correctly.

## Estimate

**~3–4 sessions.** Slice 7 is the heavy lift; slices 8–10 each fit in a session.

## Open Questions

1. Does the UI need a "node selector" widget in M3, or does it stay implicitly local until M4? **Recommendation:** implicit local in M3 (`target=None` everywhere); the selector is M4's job.
2. Should `delete_model` also take `target`? Per ADR-009 it has to (config sovereignty is per-node). Yes — design dispatch with all four verbs symmetric.
3. After deletion, does `state.py` get renamed to something more accurate (`read_state.py`? `live_state.py`)? **Defer to M5/M6**; keep the import path stable through M4.

## References

- ADR-008 §"Stateless Facade" — the deletion target's rationale
- ADR-009 §"Self-Loop Dispatch" — slice 8's contract
- ADR-010 — port-keyed verb signatures the UI now adopts
- v2-orientation-spike §6 — UI auto-spawn deprecation note
- Issue #47 — UI migration (slice 7)
- Issue #45 — RemoteAggregator regression test (slice 10)
