# Test Coverage Plan

## Baseline

Run `pytest --cov --cov-report=term-missing` gave 85% overall coverage.

See the coverage report for details on uncovered lines.

## Uncovered Modules Ranked by Gap Size

1. **llauncher/agent/server.py** (20% covered) - Missing lines: 42-67, 79-117, 142-159, 170-199, 203
2. **llauncher/ui/tabs/dashboard.py** (26% covered) - Missing lines: 34-51, 54-57, 72, 107-112, 115-117, 127-128, 149-152, 157-159, 163-164, 178-199, 204-205, 227-367, 476-522, 532-701
3. **llauncher/mcp/server.py** (69% covered) - Missing lines: 45, 51, 53, 55, 57, 61, 63, 67, 75-86, 95, 99
4. **llauncher/remote/registry.py** (57% covered) - Missing lines: 44-46, 148-153, 161-183, 191-217, 221
5. **llauncher/remote/state.py** (71% covered) - Missing lines: 56-67, 88-94, 104-117, 187-188
6. **llauncher/agent/routing.py** (82% covered) - Missing lines: 58-59, 167-187, 210-221
7. **llauncher/core/discovery.py** (73% covered) - Missing lines: 28, 60-62, 67, 72, 168, 192, 195-196, 208-211, 221, 224-225, 247-271
8. **llauncher/mcp/tools/config.py** (89% covered) - Missing lines: 129, 133, 135, 137, 139, 141, 146-147
9. **llauncher/models/config.py** (90% covered) - Missing lines: 62-64, 67, 105, 145, 172, 174, 176, 182
10. **llauncher/agent/config.py** (78% covered) - Missing lines: 22-24
11. **llauncher/core/config.py** (93% covered) - Missing lines: 33-35, 105
12. **llauncher/core/process.py** (97% covered) - Missing lines: 41, 46, 257, 270-273
13. **llauncher/core/settings.py** (90% covered) - Missing line: 29
14. **llauncher/remote/node.py** (91% covered) - Missing lines: 129-131, 189-192, 212-215, 235
15. **llauncher/state.py** (93% covered) - Missing lines: 139, 167, 206-207, 241-243, 301
16. **tests/integration/test_swap.py** (38% covered) - Missing lines: 55-92, 105, 135, 139, 151-158, 177-240, 244
17. **tests/unit/test_ui_syntax.py** (89% covered) - Missing lines: 40-41, 47, 72-74

## Closed Issues Lacking Corresponding Test Cases

We examined the last 50 closed issues (see below). For each, we note if there is likely a missing regression test.

| Issue | Title | Area | Likely Missing Test? |
|-------|-------|------|----------------------|
| #18 | Bug: UI crash when editing models with old-format extra_args config | UI/models | Yes - need test for editing models with old config |
| #17 | REF-UI-001: Consolidate Running Servers into Models section | UI/refactor | Yes - test UI after refactor |
| #13 | BUG-UI-001: Local agent auto-start not working in ensure_local_agent() | UI/agent | Yes - test ensure_local_agent |
| #12 | UX-UI-007: Log refresh button should not consume separate column width | UI | Yes - test UI layout |
| #11 | BUG-UI-006: top_k and min_p missing from UI forms causes NameError | UI/forms | Yes - test UI forms with top_k/min_p |
| #9 | ENHANCEMENT-UI-005: Add refresh button for individual model logs | UI/logs | Yes - test refresh button |
| #8 | ENHANCEMENT-UI-004: Add filesystem browser for model/mmproj path selection | UI/filesystem | Yes - test filesystem browser |
| #7 | BUG-CONFIG-003: Remove unused multi-GPU fields | config | Yes - test config without multi-GPU fields |
| #6 | BUG-CONFIG-002: Missing llama-server config fields | config | Yes - test llama-server config fields |
| #5 | BUG-CORE-001: Start button fails - config.port renamed to config.default_port | core/config | Yes - test start button with port rename |
| #4 | BUG-DISCOVERY-004: Revisit launch-*.sh scripts for additional config fields | discovery/scripts | Yes - test launch scripts |
| #3 | BUG-DESIGN-003: Port number coupled to model configuration profile | design/config | Yes - test port configuration |
| #2 | BUG-UI-002: Manager tab missing Start/Stop controls for models | UI/manager | Yes - test Start/Stop controls |
| #1 | BUG-UI-001: Dashboard tab layout should match Manager tab's single-column list | UI/layout | Yes - test dashboard layout |

Note: This is not exhaustive; we recommend reviewing each closed issue to determine if a regression test is needed.

## Phased Groupings

We group the work into logical phases to allow incremental progress.

### Phase 1: Core Agent and MCP Server
Focus on low-coverage agent and MCP server modules.
- llauncher/agent/server.py
- llauncher/mcp/server.py
- llauncher/agent/routing.py
- llauncher/agent/config.py

### Phase 2: Remote and Registry
Focus on remote modules.
- llauncher/remote/registry.py
- llauncher/remote/state.py
- llauncher/remote/node.py

### Phase 3: Core and Models
Focus on core configuration and models.
- llauncher/core/discovery.py
- llauncher/core/config.py
- llauncher/core/process.py
- llauncher/core/settings.py
- llauncher/models/config.py

### Phase 4: UI Components
Focus on UI tabs and components.
- llauncher/ui/tabs/dashboard.py
- llauncher/ui/utils.py (already high coverage, but check)
- llauncher/ui/tabs/ (other tabs if any)

### Phase 5: Test Suite Improvements
Improve existing tests that have low coverage.
- tests/integration/test_swap.py
- tests/unit/test_ui_syntax.py

### Phase 6: Regression Tests for Closed Issues
Add regression tests for each closed issue identified above.

## Open Questions

- What is the expected behavior of the agent server under various configurations? (llauncher/agent/server.py)
- How should the dashboard handle real-time updates and data streaming? (llauncher/ui/tabs/dashboard.py)
- Are there any integration points between the MCP server and the remote modules that need testing?
- What is the correct way to mock the discovery process for unit tests?
- How should we test the UI components without requiring a full GUI? (We may need to use mocking or headless testing.)