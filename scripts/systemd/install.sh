#!/bin/bash
# Install / refresh the llauncher-agent systemd *user* unit.
#
# Idempotent: safe to re-run after a `git pull` to pick up unit-file
# changes. Will NOT overwrite an existing env file (so your token and
# host config survive reinstalls).
#
# Usage:
#   ./scripts/systemd/install.sh           # install + enable + start
#   ./scripts/systemd/install.sh --no-start # render+enable only
#   ./scripts/systemd/install.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_BIN="$PROJECT_DIR/.venv/bin"

UNIT_NAME="llauncher-agent.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

ENV_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llauncher"
ENV_FILE="$ENV_DIR/agent.env"

# The UI process (Streamlit) is separate from the systemd service and
# does NOT inherit LLAUNCHER_AGENT_TOKEN from the unit's environment, so
# it can only authenticate against the local agent by reading the token
# from this file. See issue #131 + the Windows counterpart in
# scripts/windows/install.ps1.
LLAUNCHER_DIR="$HOME/.llauncher"
TOKEN_FILE="$LLAUNCHER_DIR/agent.token"

TEMPLATE="$SCRIPT_DIR/llauncher-agent.service.in"
ENV_EXAMPLE="$SCRIPT_DIR/llauncher-agent.env.example"

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}ℹ${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }

uninstall() {
    info "Stopping and disabling $UNIT_NAME..."
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    if [ -f "$UNIT_PATH" ]; then
        rm -f "$UNIT_PATH"
        say "Removed $UNIT_PATH"
    fi
    systemctl --user daemon-reload
    info "Env file left in place at $ENV_FILE (delete manually if desired)."
    info "Token file left in place at $TOKEN_FILE (delete manually if desired)."
    say "Uninstalled."
    exit 0
}

case "${1:-}" in
    --uninstall) uninstall ;;
    --no-start)  START_AFTER_INSTALL=0 ;;
    "")          START_AFTER_INSTALL=1 ;;
    *)           err "Unknown argument: $1"; exit 2 ;;
esac

# --- Preflight ---------------------------------------------------------
if [ ! -x "$VENV_BIN/llauncher-agent" ]; then
    err "Did not find $VENV_BIN/llauncher-agent."
    err "Run './scripts/run.sh install' first to create the venv."
    exit 1
fi

if ! command -v systemctl >/dev/null; then
    err "systemctl not found — this installer targets systemd hosts only."
    exit 1
fi

# --- Env file ----------------------------------------------------------
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

if [ ! -f "$ENV_FILE" ]; then
    info "Generating $ENV_FILE with a fresh token..."
    TOKEN="$("$VENV_BIN/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    # Seed from the example, then substitute the placeholder token.
    sed "s|replace-me-with-a-random-token|$TOKEN|" "$ENV_EXAMPLE" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    say "Wrote $ENV_FILE (mode 0600) with a generated 32-byte token."
    info "Edit it to set LLAUNCHER_AGENT_NODE_NAME / HOST / PORT as needed."
else
    chmod 600 "$ENV_FILE"  # repair perms if they drifted
    say "Env file already exists at $ENV_FILE — leaving it untouched."
fi

# --- Token mirror (issue #131) -----------------------------------------
# Mirror LLAUNCHER_AGENT_TOKEN from the env file into ~/.llauncher/agent.token
# so the UI process — which does NOT share the systemd service env — can
# authenticate against the local agent. Symmetric with the Windows
# install.ps1 mirror block.
#
# `tail -n1` (not `head -n1`) matches systemd's EnvironmentFile parser
# semantics ("last wins"); the value the agent actually runs with is the
# last LLAUNCHER_AGENT_TOKEN= line, so the mirror must reflect that.
# `tr -d '[:space:]'` defends against trailing whitespace or stray CRs
# from hand-edited env files.
mkdir -p "$LLAUNCHER_DIR"
chmod 700 "$LLAUNCHER_DIR"
# `|| true` keeps a grep miss (no matching line) from tripping `set -e`
# via the failing command-substitution — an empty TOKEN_VALUE then routes
# to the informative else-branch below instead of a silent exit 1. This
# is the failure mode when a pre-rename env file still uses the old
# single-L (LAUNCHER, not LLAUNCHER) token key (see #138/#139).
TOKEN_VALUE="$(grep -E '^LLAUNCHER_AGENT_TOKEN=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
if [ -n "$TOKEN_VALUE" ]; then
    # printf '%s' (no trailing newline) matches the byte-shape of
    # llauncher/agent/auth.py:_generate_and_persist_token after strip.
    printf '%s' "$TOKEN_VALUE" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    say "Mirrored token to $TOKEN_FILE (mode 0600) so the UI can authenticate."
else
    info "No LLAUNCHER_AGENT_TOKEN line found in $ENV_FILE; skipping token file mirror."
fi

# --- Unit file ---------------------------------------------------------
mkdir -p "$UNIT_DIR"
sed \
    -e "s|@VENV_BIN@|$VENV_BIN|g" \
    -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    "$TEMPLATE" > "$UNIT_PATH"
say "Rendered unit to $UNIT_PATH"

systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" >/dev/null
say "Enabled $UNIT_NAME"

if [ "$START_AFTER_INSTALL" -eq 1 ]; then
    systemctl --user restart "$UNIT_NAME"
    sleep 1
    if systemctl --user is-active --quiet "$UNIT_NAME"; then
        say "Service is active."
    else
        err "Service failed to start. Last 20 log lines:"
        journalctl --user -u "$UNIT_NAME" -n 20 --no-pager || true
        exit 1
    fi
fi

cat <<EOF

Next steps:
  systemctl --user status llauncher-agent
  journalctl --user -u llauncher-agent -f

For autostart at boot without an active login session, run once:
  sudo loginctl enable-linger "\$USER"

To uninstall:
  $SCRIPT_DIR/install.sh --uninstall
EOF
