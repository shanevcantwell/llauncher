# M4 Design — UI Redesign for Symmetric Topology

**Status:** Draft
**Date:** 2026-05-05
**Predecessor:** [m3-design.md](m3-design.md)
**Successor:** [m5-design.md](m5-design.md)

## Goal

Bring the Streamlit UI into alignment with ADR-LLNCH-009 (symmetric hub/spoke topology) and ADR-LLNCH-010 (port at the call site). After M3 the UI is functional but topologically naïve — it implicitly assumes "this node" everywhere and inherits a v1-shaped tab layout. M4 is the redesign that:

- Surfaces **node selection** as a first-class concept on every page that mutates or reads server state.
- Drops the auto-spawn-local-agent behavior in favor of explicit "agent must be running already" UX.
- Reorganizes the tabs around the v2 verbs (start / swap / stop / delete-model) rather than the v1 lifecycle.
- Stays a Streamlit app — no framework swap. The redesign is a content reorganization plus a thin adapter to `ops.*` with `target=` plumbing.

## Why a Redesign and Not a Refactor

After M3, every UI mutation already calls `ops.*(target=None)`. A spot-fix to add a node dropdown in front of each verb would work for one or two pages but would propagate inconsistencies across the seven existing tab files. The orientation spike §6 also flagged the UI's auto-spawn behavior as fighting ADR-LLNCH-009 — that's a UX decision that wants a single coherent treatment, not a per-tab patch.

The redesign budget is small (one milestone) because the underlying ops surface is already correct.

## Current Tabs (Pre-M4)

| File | Purpose | M4 fate |
|------|---------|---------|
| `ui/tabs/dashboard.py` | Cross-node running-server view | **Keep**, port to per-node aggregation |
| `ui/tabs/model_card.py` | Per-model detail + start/stop/swap | **Keep**, restructure around verbs |
| `ui/tabs/forms.py` | Add/edit model config form | **Keep**, add target selector |
| `ui/tabs/model_registry.py` | Browse all configs | **Keep**, scope to selected node |
| `ui/tabs/manager.py` | Bulk model management | **Merge** into dashboard or registry |
| `ui/tabs/nodes.py` | Manage `nodes.json` peer list | **Keep**, drop auto-spawn affordances |
| `ui/tabs/running.py` | Running-server list | **Merge** into dashboard |

## Work Breakdown

### Slice 11 — Node selector widget + session state

A reusable component, not a tab:

```python
# llauncher/ui/components/node_selector.py  (new)
def render_node_selector(label: str = "Node") -> str:
    """Returns the selected target node name. 'local' is the default."""
    nodes = ["local", *NodeRegistry().list_node_names()]
    return st.selectbox(label, nodes, key="ui.target_node")
```

The selector writes to `st.session_state["ui.target_node"]`. Every verb-bearing page reads from there. Persistence across reruns is the standard Streamlit pattern.

**Consequence:** the `target` arg in `ops.*` is no longer always `None` from the UI; it's whatever the user picked. The CLI and MCP keep their explicit-arg style (no session state).

### Slice 12 — Drop auto-spawn

Remove the "agent not running? auto-launch via subprocess" code path that slice 10 already excised from `NodeRegistry`. Replace with an explicit UX:

- On UI startup, ping the local agent.
- If no response: render a "The llauncher agent is not running on this node. Start it with `llauncher agent start` and refresh." banner. Do **not** subprocess.Popen anything.
- If user wants the auto-launch ergonomic, the CLI provides `llauncher agent start --background` (a separate slice, M3 or M5).

This matches ADR-LLNCH-009's "every node runs the same software, role is determined by invocation" framing — the UI is just a viewer of an agent that already exists.

### Slice 13 — Tab restructure

Reorganize around what users *do* rather than what objects exist:

- **Dashboard.** Cross-node aggregate of running servers. Replaces both `dashboard.py` and `running.py`. Polls each node's `/status`. Connect-fails-loudly per ADR-LLNCH-009.
- **Models.** Browse + edit configs scoped to the selected node. Merges `forms.py` + `model_registry.py`. The "Model Card" detail (per-model start/swap/stop/delete) is a sub-route here, not a separate top-level tab.
- **Nodes.** Manage `nodes.json`. Drops the "auto-spawn local agent" button. Adds an "Add peer by URL" form that calls `RemoteNode(...).ping()` to validate before persisting.
- **Audit.** New tab. Tail of `~/.llauncher/audit.jsonl` for the selected node. Read-only. Filter by action type. Useful for debugging swap rollbacks.

### Slice 14 — Verb result rendering

Adopt the ADR-LLNCH-010 envelope as a first-class UI primitive. Different `action` values render with different visual weight:

| `action` | Toast color | Notes |
|----------|-------------|-------|
| `started`, `stopped`, `swapped`, `deleted` | success (green) | Mutated state |
| `already_running`, `already_empty`, `not_found` | info (blue) | No-op idempotent paths |
| `rejected_occupied`, `rejected_empty`, `rejected_in_use`, `rejected_preflight`, `rejected_in_progress` | warning (yellow) | Caller-side error; show why and what to do |
| `rolled_back` | warning + diagnostic (yellow + expander) | Show `restored_model`, `previous_model`, `startup_logs` tail |
| `failed`, `error`, `rejected_stop_failed` | error (red) | Manual intervention guidance |

Centralize this in `ui/utils.py` as `render_op_result(result_dict)` so every page renders the same way.

## Touch Points

| Module | Change |
|--------|--------|
| `llauncher/ui/components/node_selector.py` | **New** — slice 11 |
| `llauncher/ui/utils.py` | Add `render_op_result()` (slice 14) |
| `llauncher/ui/app.py` | Restructure tab routing (slice 13) |
| `llauncher/ui/tabs/dashboard.py` | Cross-node aggregation; absorb `running.py` |
| `llauncher/ui/tabs/manager.py` | **Delete**; merge into dashboard/models |
| `llauncher/ui/tabs/running.py` | **Delete** |
| `llauncher/ui/tabs/forms.py` + `model_registry.py` | Merge into a single "Models" tab |
| `llauncher/ui/tabs/nodes.py` | Drop auto-spawn affordances; add ping-then-save |
| `llauncher/ui/tabs/audit.py` | **New** — slice 13 |

## Test Strategy

The Streamlit UI doesn't lend itself to unit-test depth. Strategy:

- **Component-level tests** for `node_selector.py` and `render_op_result()` using `streamlit.testing.v1.AppTest` (Streamlit's official test harness).
- **Integration tests** with a mocked `ops` module — confirm the right verb is called with the session-state target.
- **Manual smoke checklist** in `tests/manual/m4-smoke.md` for the cross-node flows that aren't worth automating.

## Exit Criteria

- [ ] No tab references a hard-coded "local" target; every verb-bearing page reads from `st.session_state["ui.target_node"]`.
- [ ] `start_local_agent` no longer exists in the codebase. UI shows a banner when the agent is down.
- [ ] Audit tab tails the selected node's `audit.jsonl`.
- [ ] `render_op_result` is the single rendering path for every verb result; no per-tab toast logic.
- [ ] `manager.py` and `running.py` deleted; their content lives in dashboard/models.
- [ ] Manual smoke pass: add a peer via UI, swap a model on it, observe the result on the dashboard, see the audit entry.

## Estimate

**~2–3 sessions.** Slice 11 + 14 are small adapter work. Slice 13 is the bulk (tab restructure with content-preserving migrations).

## Open Questions

1. **Streamlit vs. something else?** Out of scope. Streamlit is fine for single-user hobby UI; a framework swap would dwarf this milestone.
2. **Should the audit tab support filtering by action type?** Yes, simple `st.multiselect`.
3. **Reset/clear running session state on node change?** Yes — selecting a different node should not preserve a prior node's "currently editing model X" state. Implement as an `on_change` callback that clears stale keys.
4. **Live updates on dashboard?** Manual refresh button is sufficient for hobby scope. Auto-refresh with `st.fragment` is a stretch goal if budget allows.

## References

- ADR-LLNCH-009 — symmetric topology, `nodes.json` per-node ownership, connect-fails-loudly
- ADR-LLNCH-010 — verb envelope (`success`, `action`, `port`, `model`, ...)
- v2-orientation-spike §6 — UI auto-spawn deprecation
- Issue #45 — falsy-registry guard (already in M3, but the UI is the consumer)
