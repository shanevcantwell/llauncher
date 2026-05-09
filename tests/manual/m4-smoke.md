# M4 manual smoke checklist

After landing M4 Slice 13 (#50). Run this once on a real workstation before tagging M4 ✅ shipped end-to-end.

## Setup
- [ ] `llauncher-agent` running in a terminal
- [ ] `streamlit run llauncher/ui/app.py` opens cleanly

## Sidebar
- [ ] `node_selector` renders with `local` as the first option
- [ ] Stop the agent, refresh page → `show_agent_down_banner` renders, rest of UI gated
- [ ] Restart the agent, refresh page → UI returns to normal

## Models tab
- [ ] Add a new model (in the "Add New Model" expander) — saves cleanly
- [ ] Edit a model — save reflects in registry table
- [ ] Port picker shows on model card; typing a blacklisted port shows the inline error
- [ ] Typing a port held by an unmanaged process shows the in-use warning
- [ ] Typing a port held by another running model shows the eviction-warning copy
- [ ] Start button is disabled / no-op when port input is empty
- [ ] Start successfully on a free port → SUCCESS toast via `render_op_result`
- [ ] Trigger a swap (start a different model on an occupied port via the eviction dialog) → swap completes; verify both pre and post results

## Dashboard tab
- [ ] Running server appears in the dashboard table for the selected target
- [ ] Configured-but-not-running models render in the read-only section
- [ ] No add/edit/start verbs in this tab (those moved to Models)

## Nodes tab
- [ ] Existing peers list correctly
- [ ] Add a peer (real or fake) — ping result surfaces

## Audit tab
- [ ] Most recent action (the `started` from the Models tab smoke) appears at the top (newest-first)
- [ ] Action filter narrows rows
- [ ] Result filter narrows rows
- [ ] Limit input bumps to 500 — dataframe row count grows
- [ ] Move `~/.llauncher/audit.jsonl` aside → empty-state info panel renders without error
- [ ] Restore the file → entries return
- [ ] Switch to a remote target in the sidebar → caption renders explaining remote-audit deferral (#64)

## Cleanup
- [ ] Stop the running server from Models → audit log gains `stopped` entry → Dashboard reflects empty state
