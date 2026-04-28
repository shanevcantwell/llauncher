## Worker C Brief: ADR-004 CLI Subcommand Interface

Working directory: /home/node/github/llauncher. Branch: main (up to date with origin/main).

### Code Implementation Tasks

#### Task 2.8 + 2.15: `llauncher/cli.py` (NEW) — Full Typer CLI app
```python
import typer
from rich.table import Table
app = typer.Typer(name="llauncher", help="CLI for managing llama.cpp server instances")
```

**Subcommand groups:**
- **model**: `list` (all configured models), `info <name>` (detailed single model)  
  → Uses ConfigStore from llauncher.core.config
- **server**: `start <model> [--port PORT]`, `stop <port>`, `status [--json]`  
  → Creates local LauncherState instance, delegates to its methods (mirrors agent API exactly)
- **node**: `add <name> --host HOST [--port PORT] [--api-key KEY]`, `list`, `remove <name>`, `status [all|--json]`  
  → Uses NodeRegistry from llauncher.remote.registry. Includes api_key when present on node for ping requests. After Phase 1, RemoteNode accepts api_key parameter.
- **config**: `path` (prints config file path), `validate <model>` (stateless validation)

**Output formatting:** Use rich.Table with color-coded status: green=running/online, red=stopped/offline/error, yellow=warning. Add --json flag to list/status commands for machine-readable output.

#### Task 2.9: `pyproject.toml` — Add CLI entry point
Change `[project.scripts]`:
```toml
[project.scripts]
llauncher = "llauncher.cli:app"
llauncher-agent = "llauncher.agent:main"  
llauncher-mcp = "llauncher.mcp_server.server:main"
```

### Test Tasks
| File | Tests to implement |
|------|-------------------|
| `tests/unit/test_cli.py` | help shows all groups, model list empty, server status local, start missing model error, start with explicit port, node add+list, node add with api_key persists, config path printed, node remove deletes, stop nonexistent port error (10-12 tests) |

Use typer.testing.CliRunner for all CLI tests. No subprocess invocation needed.

### Execution Order:
1. Install Typer if not already present: `pip3 install typr`  
   NOTE: Typer should already be installed as a transitive dependency of FastAPI (FastAPI depends on Starlette which may or may not have Typer). Check first with `python3 -c "import typer"`.
2. Create `llauncher/cli.py` with all subcommand groups and output formatting helpers
3. Update `pyproject.toml` to add the llauncher console script entry point  
   NOTE: After modifying pyproject.toml, reinstall in editable mode: `pip3 install -e . --no-deps` (to create the CLI binary without re-downloading deps)
4. Write all test files
5. Run CLI tests only: `python3 -m pytest tests/unit/test_cli.py -v --tb=short` (all must pass)
6. Run full baseline: `python3 -m pytest tests/unit/ -q` — verify no regressions
7. Verify CLI entry point works: `llauncher --help` should show all command groups

### Git Commit Message:
```
feat(cli): add subcommand interface via Typer

- Create llauncher/cli.py with model, server, node, and config command groups  
- Register CLI entry point in pyproject.toml as 'llauncher' console script
- Local state commands (model list/info, server start/stop/status) delegate to LauncherState + ConfigStore
- Remote commands (node add/list/remove/status) use NodeRegistry with httpx for pings
- Node registration supports --api-key parameter (ADR-003 integration point — uses phase 1 changes)
- Rich table-formatted output with color-coded status; --json flag for machine-readable mode
- All local operations mirror agent API behavior exactly — no divergence

Refs: ADR-004
```

Read the full implementation plan at /tmp/llauncher_implementation_plan.md for additional context. Follow existing llauncher coding patterns and style."
