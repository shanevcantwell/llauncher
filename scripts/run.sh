#!/bin/bash
# llauncher - Linux/Mac runner script
# Usage: ./run.sh [mcp|ui|agent|discover|install]

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

# Check if virtual environment exists
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    print_info "Virtual environment not found. Creating one..."
    cd "$PROJECT_DIR"
    python3 -m venv .venv
    print_status "Virtual environment created"
fi

# Activate virtual environment
source "$PROJECT_DIR/.venv/bin/activate"

case "${1:-}" in
    install)
        # Disabled: this installed into the repo-local .venv (activated above),
        # disconnected from the operator's global commands — the "complete"
        # banner implied a global readiness it never delivered. See issue #154.
        print_error "run.sh install is disabled: it installed into a repo-local .venv,"
        print_error "disconnected from your global commands. For a global install:"
        echo "    pip install --user -e \".[ui]\"   # from this repo, with no venv active"
        exit 1
        ;;
    mcp)
        print_info "Starting MCP server..."
        python -m llauncher.mcp.server
        ;;
    ui)
        print_info "Starting Streamlit UI..."
        # Bind to loopback by default. The dashboard has no built-in auth;
        # see README "Streamlit UI" + docs/plans/security-hardening-plan.md
        # §2.8 (C12). Override with LAUNCHER_UI_HOST only behind a gateway
        # (Tailscale / SSH tunnel / reverse proxy with auth).
        streamlit run "$PROJECT_DIR/llauncher/ui/app.py" \
            --server.address "${LAUNCHER_UI_HOST:-127.0.0.1}"
        ;;
    agent)
        print_info "Starting remote management agent..."
        print_info "Agent will listen on 0.0.0.0:8765"
        print_info "Set LLAUNCHER_AGENT_PORT and LLAUNCHER_AGENT_NODE_NAME to customize"
        llauncher-agent
        ;;
    stop)
        print_info "Stopping remote management agent..."
        llauncher-agent --stop
        ;;
    discover)
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
        echo ""
        echo "Environment variables for agent:"
        echo "  LLAUNCHER_AGENT_HOST     Host to bind to (default: 0.0.0.0)"
        echo "  LLAUNCHER_AGENT_PORT     Port to listen on (default: 8765)"
        echo "  LLAUNCHER_AGENT_NODE_NAME Friendly name for this node"
        echo ""
        echo "First time setup:"
        echo "  $0 install"
        ;;
esac
