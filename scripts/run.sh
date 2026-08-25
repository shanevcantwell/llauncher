#!/bin/bash
# llauncher - Linux/Mac runner script
# Usage: ./run.sh [mcp|ui|agent|discover|setup|install]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Ensure + activate the repo-local virtual environment. Only the commands
# that actually run code need this — `install` (disabled) and the help text
# must not bootstrap a ~498 MB venv as a side effect (issue #154).
ensure_venv() {
    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        print_info "Virtual environment not found. Creating one..."
        cd "$PROJECT_DIR"
        python3 -m venv .venv
        print_status "Virtual environment created"
    fi
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.venv/bin/activate"
}

case "${1:-}" in
    setup)
        # Recompose the agent venv from pyproject.toml (ADR-LLNCH-023, issue #227).
        #
        # This is the single, named recompose command the system ensure unit
        # (llauncher-agent-ensure-venv.service) calls, and the live successor
        # to the disabled `install` pointer (issue #154). It REUSES the one
        # venv-creation path — ensure_venv (added by #219, which only CREATES
        # the venv) — and adds the POPULATE step on top, rather than
        # parallel-implementing a second venv builder.
        #
        # OQ3 (lazy re-heal): existence-check on the entry point. If the
        # venv already resolves `llauncher-agent`, this is a no-op — we
        # recompose only when missing/broken, never eagerly every boot.
        # OQ2 (minimal): rebuild from pyproject.toml `>=` floors via an
        # editable install; no lockfile (deferred to ADR-LLNCH-023 Phase C).
        if [ -x "$PROJECT_DIR/.venv/bin/llauncher-agent" ]; then
            print_status "Agent venv already populated (llauncher-agent present) — nothing to recompose."
            exit 0
        fi
        ensure_venv  # creates + activates .venv (the #219 path); do not duplicate it
        print_info "Recomposing agent venv from $PROJECT_DIR/pyproject.toml (editable install)..."
        # Fail-loud: a nonzero pip recompose must abort with an actionable
        # message so the ensure unit enters `failed` and the agent (which
        # Requires= it) never starts against a half-built venv.
        if ! pip install -e "$PROJECT_DIR"; then
            print_error "Recompose FAILED: 'pip install -e $PROJECT_DIR' returned nonzero."
            print_error "The agent venv at $PROJECT_DIR/.venv is NOT usable. Resolve the error"
            print_error "above (network down? broken pyproject.toml?) and re-run: ./scripts/run.sh setup"
            exit 1
        fi
        if [ ! -x "$PROJECT_DIR/.venv/bin/llauncher-agent" ]; then
            print_error "Recompose INCOMPLETE: the llauncher-agent entry point is still missing"
            print_error "after 'pip install -e'. Refusing to report success (fail-loud, ADR-LLNCH-023)."
            exit 1
        fi
        print_status "Agent venv recomposed: $PROJECT_DIR/.venv/bin/llauncher-agent present."
        ;;
    install)
        # Disabled: this installed into the repo-local .venv, disconnected
        # from the operator's global commands — the "complete" banner implied
        # a global readiness it never delivered. See issue #154.
        print_error "run.sh install is disabled: it installed into a repo-local .venv,"
        print_error "disconnected from your global commands. For a global install"
        print_error "(puts llauncher / llauncher-ui on your PATH):"
        echo "    pip install --user -e .   # from this repo, with no venv active"
        exit 1
        ;;
    mcp)
        ensure_venv
        print_info "Starting MCP server..."
        python -m llauncher.mcp.server
        ;;
    ui)
        ensure_venv
        print_info "Starting Streamlit UI..."
        # Bind to loopback by default. The dashboard has no built-in auth;
        # see README "Streamlit UI" + docs/plans/security-hardening-plan.md
        # §2.8 (C12). Override with LAUNCHER_UI_HOST only behind a gateway
        # (Tailscale / SSH tunnel / reverse proxy with auth).
        streamlit run "$PROJECT_DIR/llauncher/ui/app.py" \
            --server.address "${LAUNCHER_UI_HOST:-127.0.0.1}"
        ;;
    agent)
        ensure_venv
        print_info "Starting remote management agent..."
        print_info "Agent will listen on 0.0.0.0:8765"
        print_info "Set LLAUNCHER_AGENT_PORT and LLAUNCHER_AGENT_NODE_NAME to customize"
        llauncher-agent
        ;;
    stop)
        # Do NOT call ensure_venv here: `stop` must not bootstrap a ~500MB
        # venv as a side effect on a machine that installed globally
        # (`pip install --user -e .`) with no local .venv (issue #229).
        # If a repo-local venv exists, activate it so the agent already
        # running from it is reachable; otherwise rely on PATH.
        if [ -d "$PROJECT_DIR/.venv" ]; then
            # shellcheck disable=SC1091
            source "$PROJECT_DIR/.venv/bin/activate"
        fi
        print_info "Stopping remote management agent..."
        llauncher-agent --stop
        ;;
    discover)
        ensure_venv
        print_info "Discovering launch scripts..."
        python -m llauncher discover
        ;;
    *)
        echo "llauncher - MCP-first launcher for llama.cpp servers"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  mcp       Start MCP server (for LLM clients)"
        echo "  agent     Start remote management agent (foreground)"
        echo "  ui        Start Streamlit UI (requires agent; start it first)"
        echo "  stop      Stop running agent"
        echo "  discover  List discovered launch scripts"
        echo "  setup     Recompose the agent .venv from pyproject.toml (editable install)"
        echo ""
        echo "Environment variables for agent:"
        echo "  LLAUNCHER_AGENT_HOST     Host to bind to (default: 0.0.0.0)"
        echo "  LLAUNCHER_AGENT_PORT     Port to listen on (default: 8765)"
        echo "  LLAUNCHER_AGENT_NODE_NAME Friendly name for this node"
        echo ""
        echo "First time setup (puts llauncher / llauncher-ui on your PATH):"
        echo "  pip install --user -e .   # from this repo, with no venv active"
        ;;
esac
