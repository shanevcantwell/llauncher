# Bug: Remote start behavior should match local eviction semantics - swap_on_node() gap in ADRs and plans

**Status:** Filed as bug to document design gap, not functional issue. The current implementation (Slice 7) uses `swap_on_node()` for remote starts which provides parity with local eviction but was implemented without formal design review.

## Summary

The UI migration to v2 operations (Slice 7) changed remote node start behavior from using `aggregator.start_on_node()` to `aggregator.swap_on_node()`. This provides parity with local eviction semantics but:

1. Was not documented in any ADR before implementation
2. Was not tracked as a bug or feature request
3. Represents an emergent solution discovered during UI migration

## Evidence of the Gap

### What Changed (Slice 7)
- **Before**: Remote nodes used `aggregator.start_on_node(node_name, model_name)` which returns 409 (`rejected_occupied`) when port is taken → no eviction dialog
- **After**: Remote nodes use `aggregator.swap_on_node(node_name, model_name, target_port)` which handles occupied ports gracefully via full swap with rollback/readiness polling

### Where It Was Introduced
Commit `7d2a64d` ("M2 slice 4 — port-keyed HTTP endpoints") introduced the `swap_on_node()` method as part of a broader refactor to add port-keyed remote operations. At that time:
- The UI still used `start_on_node()` for remote starts (see commit 7d2a64d's `llauncher/ui/tabs/model_card.py`)
- No documentation mentioned this would become the standard behavior

### Missing Design Artifacts
| Artifact | Status |
|----------|--------|
| ADR-LLNCH-010 | Defines `/swap/{port}` and `swap_server(port, model)` but doesn't discuss remote node dispatch semantics |
| ADR-LLNCH-011 | Establishes local swap semantics; "single entry point" refers to tool-layer, not aggregator methods |
| M3 design doc | Focuses on UI migration (`ops.start`/`stop`/`swap`) but doesn't describe remote eviction behavior |

### Why This Matters for the Roadmap

| Milestone | Goal | Gap |
|-----------|------|-----|
| M1 (complete) | v2 operations foundation | N/A |
| M2 (complete) | Port-keyed HTTP/MCP surface + single-node ops | `swap_on_node` added but remote behavior undefined |
| **M3** | UI rewrite + v1 path removal | **Missing: Remote node swap semantics documented and tested** |

The v2-handoff.md currently states:
> "RemoteNode + Aggregator: Updated to port-keyed shape. ... Aggregator gains `swap_on_node`, `delete_model_on_node`."

This was presented as a *consequence* of the port-keyed refactor, not an independently designed feature.

## How to Reproduce the Original (Pre-Slice 7) Behavior

1. Restore pre-slice-7 UI code:
   ```bash
   cd /home/node/github/shanevcantwell/llauncher
   git show 7d2a64d:llauncher/ui/tabs/model_card.py > llauncher/ui/tabs/model_card.py
   ```

2. Run UI and try to start a remote model on an occupied port:
   ```bash
   python -m llauncher.ui
   # Click "Start" on a remote model card where target port is in use
   ```
3. Observe: Returns `rejected_occupied` (409) with no eviction dialog

## How to Reproduce the Current (Slice 7) Behavior

1. Apply slice 7 changes:
   ```bash
   # The diff file contains the exact changes needed
   git apply /tmp/slice7_changes.diff
   ```

2. Run UI and try to start a remote model on an occupied port:
3. Observe: Triggers full swap behavior with rollback/readiness polling (parity with local eviction)

## Expected Behavior per ADR-LLNCH-011

ADR-LLNCH-011 §"Swap Semantics v2" states:

> "All four reach the same tool-layer swap function: local CLI, HTTP agent, MCP server tools, and Streamlit UI."

The issue is that **remote node dispatch** wasn't part of this "four paths" design. The ADRs describe:
- Local CLI → `ops.swap()`
- HTTP `/swap/{port}` → `ops.swap()`  
- MCP `swap_server(port, model)` → `ops.swap()`
- Streamlit UI (local) → `ops.swap()`

But **Streamlit UI (remote)** was never explicitly defined.

## How Slice 7 Fixed It

The current implementation uses `aggregator.swap_on_node()` which:
1. Calls the remote node's `swap_server(model_name, port)`
2. Delegates to local `ops.swap(port, model_name)` on that node
3. Uses full 5-phase swap with rollback/readiness polling (ADR-LLNCH-011)

This is actually **correct behavior** (provides parity with local eviction), but it should have been:
- Documented in an ADR amendment or design note before implementation
- Tested explicitly as a "remote node swap" scenario
- Tracked in a bug/issue

## Files to Modify (After Design Review)

### Apply the slice 7 diff:

```bash
# First, ensure you're at HEAD without slice 7 changes:
git status  # should show clean working tree after checkout above

# Then apply the saved diff:
cd /home/node/github/shanevcantwell/llauncher
git apply /tmp/slice7_changes.diff
```

### The underlying `aggregator.swap_on_node()` method already exists:

```python
# llauncher/remote/state.py (already in codebase, added in commit 7d2a64d)
def swap_on_node(
    self,
    node_name: str,
    model_name: str,
    port: int,
) -> dict | None:
    """Swap the model on ``port`` to ``model_name`` on a specific node."""
    node = self.registry.get_node(node_name)
    if node is None:
        return {"success": False, "error": f"Node '{node_name}' not found"}

    return node.swap_server(model_name, port)
```

## Assessment

This is **not a bug in functionality** — the current behavior (using `swap_on_node` for remote starts) is correct and provides parity with local eviction.

This **is a design gap**:
- The behavior was implemented without formal review
- No ADR, issue, or plan documents this decision
- M3Slice7 focused on UI migration but didn't address the "remote node swap" edge case

## Recommended Fix (Order of Priority)

1. **Document the remote swap semantics** as either:
   - An ADR amendment to ADR-LLNCH-011 (add "Remote Node Dispatch" section), OR
   - A new design note `docs/m3-slice7-remoteswap.md`

2. **Add integration test** that verifies:
   - Remote node starts on occupied port use swap behavior (not 409)
   - Rollback works if the remote server fails to start
   - Readiness polling succeeds/fails appropriately

3. **Update v2-handoff.md and m3-design.md** to clarify:
   - `swap_on_node()` was added in M2 slice 4 as a byproduct of port-keying
   - Its use for remote eviction parity was discovered during Slice 7 UI migration
   - This behavior is now intentional and should be preserved

## Related Artifacts

- **Commit**: `7d2a64d` — Introduced `swap_on_node()` method (M2 slice 4)
- **Issue #47** — UI migration to v2 ops ( Slice 7 )
- **ADR-LLNCH-010** — Port-keyed verbs
- **ADR-LLNCH-011** — Swap semantics v2

## Next Steps

1. Review this bug report and decide: document as design decision or file as ADR amendment?
2. If approved, apply slice 7 changes from `/tmp/slice7_changes.diff`
3. Create documentation in `docs/m3-slice7-remoteswap.md` or amend ADR-LLNCH-011
4. Add integration test for remote swap behavior
