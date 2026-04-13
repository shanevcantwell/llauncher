# Test Coverage Plan

## Progress

| Date | Coverage | Tests Added | Notes |
|------|----------|-------------|-------|
| 2026-04-13 (baseline) | 58% | 224 tests | Initial state |
| 2026-04-13 (after Phase 1-2) | 60% | +34 tests | Config validators, RemoteNode error cases |

## Baseline (Original)

**Starting Coverage: 58%** (1882 statements, 797 missing)

### Uncovered Modules by Gap Size

| Module | Coverage | Missing Lines | Priority |
|--------|----------|---------------|----------|
| `llauncher/ui/tabs/dashboard.py` | 1% | 284 | Low (UI - requires streamlit) |
| `llauncher/ui/app.py` | 0% | 98 | Low (UI - requires streamlit) |
| `llauncher/ui/tabs/nodes.py` | 0% | 145 | Low (UI - requires streamlit) |
| `llauncher/ui/tabs/manager.py` | 0% | 5 | Low (UI - requires streamlit) |
| `llauncher/ui/tabs/running.py` | 0% | 5 | Low (UI - requires streamlit) |
| `llauncher/agent/server.py` | 20% | 77 | Medium |
| `llauncher/agent/__main__.py` | 0% | 3 | Low (entry point) |
| `llauncher/remote/node.py` | 65% | 47 | High |
| `llauncher/remote/state.py` | 63% | 22 | High |
| `llauncher/core/discovery.py` | 73% | 34 | High |
| `llauncher/mcp/server.py` | 69% | 19 | Medium |

## Closed Issues Without Regression Tests

| Issue | Title | Test Needed |
|-------|-------|-------------|
| #18 | UI crash when editing models with old-format extra_args config | Test ModelConfig migration for extra_args |
| #13 | Local agent auto-start not working in ensure_local_agent() | Test ensure_local_agent() with stopped agent |
| #11 | UX: Log refresh button column width | N/A (UI layout) |
| #10 | (not listed) | - |
| #9 | Add refresh button for individual model logs | N/A (feature, no bug) |
| #8 | Add filesystem browser | N/A (feature) |
| #7 | Remove unused multi-GPU fields | Test ModelConfig rejects n_gpu, gpu_device_ids |
| #6 | Missing llama-server config fields | Test all fields roundtrip through ModelConfig |
| #5 | Start button fails - config.port renamed | Test start_server uses default_port |
| #4 | Revisit launch-*.sh scripts | Test discovery of all config fields |
| #3 | Port number coupled to model config | Test port allocation is separate from config |
| #2 | Manager tab missing Start/Stop controls | N/A (UI feature) |
| #1 | Dashboard tab layout | N/A (UI layout) |

## Phased Implementation Plan

### Phase 1: Core Config & Model Tests (High Priority)
**Goal:** Add regression tests for recent bug fixes (#18, #7, #6, #5)

**Files to test:**
- `llauncher/models/config.py` (90% → 95%+)

**Test cases:**
1. ModelConfig.extra_args migration (old string format → new format)
2. ModelConfig rejects deprecated fields (n_gpu, gpu_device_ids) with clear error
3. All ModelConfig fields roundtrip correctly through to_dict/from_dict
4. ModelConfig default_port used correctly in command building

**New test file:** `tests/unit/test_config_migration.py`

---

### Phase 2: Remote Node & State Tests (High Priority)
**Goal:** Improve coverage of remote operations (65% → 85%+)

**Files to test:**
- `llauncher/remote/node.py` (65% → 85%+)
- `llauncher/remote/state.py` (63% → 85%+)

**Missing lines to cover:**
- `node.py`: 107-109, 128-131, 146-149, 165-167, 185-192, 203-215, 227-238
- `state.py`: 36, 73-86, 122-126, 144-148, 156-157

**Test cases:**
1. RemoteNode API calls with error responses
2. RemoteNode timeout handling
3. RemoteAggregator multi-node state merging
4. RemoteAggregator.start_on_node() success/failure paths
5. RemoteAggregator.stop_on_node() success/failure paths
6. RemoteAggregator.get_logs_on_node() empty/missing logs

**Existing test file:** `tests/unit/test_remote.py` (extend)

---

### Phase 3: Core Discovery Tests (High Priority)
**Goal:** Improve discovery coverage (73% → 90%+)

**File to test:**
- `llauncher/core/discovery.py` (73% → 90%+)

**Missing lines to cover:**
- 28, 60-62, 67, 72, 168, 192, 195-196, 208-211, 221, 224-225, 247-271

**Test cases:**
1. ModelDiscovery with empty directories
2. ModelDiscovery with symlinks
3. ModelDiscovery filtering (GGUF only)
4. _is_valid_gguf() with corrupt files
5. _parse_gguf_metadata() with missing metadata

**Existing test file:** `tests/unit/test_discovery.py` (extend)

---

### Phase 4: Agent & MCP Tests (Medium Priority)
**Goal:** Improve agent and MCP coverage

**Files to test:**
- `llauncher/agent/server.py` (20% → 60%+)
- `llauncher/mcp/server.py` (69% → 85%+)

**Test cases:**
1. Agent server startup/shutdown
2. Agent request routing
3. MCP tool registration
4. MCP error handling

**Existing test files:**
- `tests/unit/test_agent.py` (extend)
- `tests/unit/mcp/test_server.py` (extend)

---

### Phase 5: UI Syntax Tests (Low Priority)
**Goal:** Add non-runtime UI tests

**Strategy:** Since streamlit isn't available in CI, focus on:
1. AST-based syntax validation (already done)
2. Import validation with mocked streamlit
3. Logic extraction tests (already in test_ui_rendering.py)

---

## Open Questions

1. **UI tests:** Should we add streamlit to dev dependencies to enable full UI testing, or continue with syntax/logic-only tests?

2. **Integration tests:** The current integration tests (test_state.py, test_swap.py) are comprehensive. Should we add more integration tests for remote operations?

3. **Agent tests:** The agent/server.py module is only 20% covered. Is this intentional (hard to test without running server), or should we add more unit tests?

4. **Mock strategy:** For remote tests, should we use `respx` for HTTP mocking or `unittest.mock` for simpler patching?
