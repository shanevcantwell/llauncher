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
        print_info "Installing llauncher and dependencies..."
        pip install -e ".[ui]" --quiet
        print_status "Installation complete"
        echo ""
        echo "Commands available:"
        echo "  ./run.sh mcp     - Start MCP server"
        echo "  ./run.sh ui      - Start Streamlit UI (auto-starts agent)"
        echo "  ./run.sh agent   - Start remote management agent"
        echo "  ./run.sh stop    - Stop running agent"
        echo "  ./run.sh discover - List discovered models"
        ;;
    mcp)
        print_info "Starting MCP server..."
        python -m llauncher.mcp.server
        ;;
    ui)
        print_info "Starting Streamlit UI..."
        # The UI auto-spawns llauncher-agent via start_new_session=True, which
        # detaches it from Streamlit's process group. Without a trap here, the
        # agent leaks past UI shutdown. Snapshot pre-existing agents so we
        # don't kill an unrelated `./run.sh agent` or `agent-bg`.
        pre_agents="$(pgrep -f '^.*llauncher-agent$' 2>/dev/null || true)"
        cleanup_ui_agents() {
            post_agents="$(pgrep -f '^.*llauncher-agent$' 2>/dev/null || true)"
            for pid in $post_agents; do
                # Skip agents that were already running before we started.
                if ! printf '%s\n' "$pre_agents" | grep -qx "$pid"; then
                    kill "$pid" 2>/dev/null && \
                        print_info "Stopped UI-spawned agent (pid $pid)"
                fi
            done
        }
        trap cleanup_ui_agents EXIT INT TERM
        streamlit run "$PROJECT_DIR/llauncher/ui/app.py"
        ;;
    agent)
        print_info "Starting remote management agent..."
        print_info "Agent will listen on 0.0.0.0:8765"
        print_info "Set LAUNCHER_AGENT_PORT and LAUNCHER_AGENT_NODE_NAME to customize"
        llauncher-agent
        ;;
    agent-bg)
        print_info "Starting remote management agent in background..."
        nohup llauncher-agent > "$PROJECT_DIR/agent.log" 2>&1 &
        echo $! > "$PROJECT_DIR/agent.pid"
        print_status "Agent started (PID: $!)"
        echo "Logs: $PROJECT_DIR/agent.log"
        echo "Stop with: kill \$(cat $PROJECT_DIR/agent.pid)"
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
        echo "  install   Install llauncher and dependencies"
        echo "  mcp       Start MCP server (for LLM clients)"
        echo "  ui        Start Streamlit UI (auto-starts agent)"
        echo "  agent     Start remote management agent (foreground)"
        echo "  agent-bg  Start remote management agent (background)"
        echo "  stop      Stop running agent"
        echo "  discover  List discovered launch scripts"
        echo ""
        echo "Environment variables for agent:"
        echo "  LAUNCHER_AGENT_HOST     Host to bind to (default: 0.0.0.0)"
        echo "  LAUNCHER_AGENT_PORT     Port to listen on (default: 8765)"
        echo "  LAUNCHER_AGENT_NODE_NAME Friendly name for this node"
        echo ""
        echo "First time setup:"
        echo "  $0 install"
        ;;
esac
