#!/bin/bash
# llauncher - Linux/Mac runner script
# Usage: ./run.sh [mcp|ui|discover|install]

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
        echo "  ./run.sh ui      - Start Streamlit UI"
        echo "  ./run.sh discover - List discovered models"
        ;;
    mcp)
        print_info "Starting MCP server..."
        python -m llauncher.mcp.server
        ;;
    ui)
        print_info "Starting Streamlit UI..."
        streamlit run "$PROJECT_DIR/llauncher/ui/app.py"
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
        echo "  ui        Start Streamlit UI (dashboard)"
        echo "  discover  List discovered launch scripts"
        echo ""
        echo "First time setup:"
        echo "  $0 install"
        ;;
esac
