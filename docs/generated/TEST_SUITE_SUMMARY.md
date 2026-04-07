# Test Suite Summary

Generated: 2026-04-07

## Coverage Overview

| Metric | Value |
|--------|-------|
| Total Tests | 133 |
| Overall Coverage | 70% |
| Test Files | 10 |

## Coverage by Module

| Module | Coverage | Tests |
|--------|----------|-------|
| `llauncher/__main__.py` | 96% | 9 |
| `llauncher/core/config.py` | 93% | 7 |
| `llauncher/core/discovery.py` | 73% | 10 |
| `llauncher/core/process.py` | 90% | 29 |
| `llauncher/mcp/server.py` | 69% | 6 |
| `llauncher/mcp/tools/config.py` | 91% | 12 |
| `llauncher/mcp/tools/models.py` | 100% | 7 |
| `llauncher/mcp/tools/servers.py` | 100% | 9 |
| `llauncher/models/config.py` | 88% | 2 |
| `llauncher/state.py` | 75% | 5 |
| `llauncher/ui/tabs/dashboard.py` | 27% | 26 |

## Test Files

### Unit Tests

| File | Tests | Description |
|------|-------|-------------|
| `tests/unit/test_main.py` | 9 | CLI entry point |
| `tests/unit/test_config.py` | 7 | Config store operations |
| `tests/unit/test_discovery.py` | 10 | Script discovery and parsing |
| `tests/unit/test_models.py` | 2 | Model config validation |
| `tests/unit/test_process.py` | 29 | Process management |
| `tests/unit/test_ui_rendering.py` | 18 | UI rendering logic |
| `tests/unit/mcp/test_server.py` | 6 | MCP server dispatch |
| `tests/unit/mcp/test_models_tools.py` | 7 | MCP model tools |
| `tests/unit/mcp/test_servers_tools.py` | 9 | MCP server tools |
| `tests/unit/mcp/test_config_tools.py` | 12 | MCP config tools |

### Integration Tests

| File | Tests | Description |
|------|-------|-------------|
| `tests/integration/test_state.py` | 5 | LauncherState operations |
| `tests/integration/test_ui.py` | 8 | UI integration |

## Test Coverage Highlights

### Fully Covered Modules (100%)
- `llauncher/mcp/tools/models.py` - Model listing and configuration
- `llauncher/mcp/tools/servers.py` - Server start/stop/status tools

### High Coverage (>90%)
- `llauncher/__main__.py` - CLI entry point (96%)
- `llauncher/core/config.py` - Configuration persistence (93%)
- `llauncher/core/process.py` - Process management (90%)
- `llauncher/mcp/tools/config.py` - Config CRUD tools (91%)
- `llauncher/models/config.py` - Model configuration (88%)

### Medium Coverage (70-89%)
- `llauncher/state.py` - Launcher state management (75%)
- `llauncher/core/discovery.py` - Script discovery (73%)
- `llauncher/mcp/server.py` - MCP server (69%)

### Low Coverage (<50%)
- `llauncher/ui/tabs/dashboard.py` - Dashboard UI (27%)
- `llauncher/ui/app.py` - Streamlit app (0%)
- `llauncher/ui/tabs/manager.py` - Manager tab (0%)
- `llauncher/ui/tabs/running.py` - Running tab (0%)

## Uncovered Areas

### UI Files (0-27% coverage)
The UI files have low coverage because Streamlit apps are difficult to test in isolation. The business logic has been tested via:
- `tests/unit/test_ui_rendering.py` - Tests form validation and display logic
- `tests/integration/test_ui.py` - Tests session state management and workflows

### Remaining Uncovered Lines
- **discovery.py**: Edge cases in script parsing and shard resolution
- **state.py**: Error handling paths in start/stop operations
- **mcp/server.py**: Async main entry points
- **process.py**: Process exception handling (NoSuchProcess, AccessDenied)

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=llauncher --cov-report=term-missing

# Run specific test file
pytest tests/unit/test_process.py -v

# Run with HTML coverage report
pytest --cov=llauncher --cov-report=html
```

## Test Categories

| Category | Count | Percentage |
|----------|-------|------------|
| CLI Tests | 9 | 7% |
| Config Tests | 7 | 5% |
| Discovery Tests | 10 | 8% |
| Model Tests | 2 | 2% |
| Process Tests | 29 | 22% |
| MCP Tests | 34 | 26% |
| UI Tests | 26 | 20% |
| Integration Tests | 16 | 12% |
