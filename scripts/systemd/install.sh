#!/bin/bash
# Install / refresh the llauncher-agent systemd unit.
#
# Two install scopes:
#   --user   (default) — per-user unit under ~/.config/systemd/user, state
#                        under $HOME. Unchanged from the original installer.
#   --system           — system unit at /etc/systemd/system run as the
#                        dedicated `llauncher` account (group `inference`),
#                        with state under /var/lib/llauncher. Requires root
#                        and pre-existing host provisioning. See ADR-018.
#
# Idempotent: safe to re-run after a `git pull` to pick up unit-file
# changes. Will NOT overwrite an existing env file (so your token and
# host config survive reinstalls).
#
# Usage:
#   ./scripts/systemd/install.sh                    # user: install+enable+start
#   ./scripts/systemd/install.sh --no-start         # user: render+enable only
#   ./scripts/systemd/install.sh --uninstall        # user: remove unit
#   sudo ./scripts/systemd/install.sh --system      # system: install+enable+start
#   sudo ./scripts/systemd/install.sh --system --no-start
#   sudo ./scripts/systemd/install.sh --system --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_BIN="$PROJECT_DIR/.venv/bin"

UNIT_NAME="llauncher-agent.service"
ENV_EXAMPLE="$SCRIPT_DIR/llauncher-agent.env.example"

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}ℹ${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }

# --- Argument parsing (flags compose: --system + --no-start/--uninstall) ---
MODE="user"
START_AFTER_INSTALL=1
DO_UNINSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --system)    MODE="system" ;;
        --user)      MODE="user" ;;
        --no-start)  START_AFTER_INSTALL=0 ;;
        --uninstall) DO_UNINSTALL=1 ;;
        *)           err "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# --- Mode-dependent locations -----------------------------------------
# UI service posture: see ADR-022 (decided: per-operator systemd --user; implementation pending). UI is currently hand-launched.
# The UI process (Streamlit) is separate from the systemd service and does
# NOT inherit LLAUNCHER_AGENT_TOKEN from the unit's environment, so it can
# only authenticate against the local agent by reading the mirrored token
# from TOKEN_FILE. See issue #131 + the Windows counterpart in
# scripts/windows/install.ps1.
if [ "$MODE" = "system" ]; then
    UNIT_DIR="/etc/systemd/system"
    STATE_DIR="/var/lib/llauncher"
    ENV_DIR="$STATE_DIR"
    ENV_FILE="$STATE_DIR/agent.env"
    LLAUNCHER_DIR="$STATE_DIR"
    TOKEN_FILE="$STATE_DIR/agent.token"
    TEMPLATE="$SCRIPT_DIR/llauncher-agent.service.system.in"
    SYSTEMCTL=(systemctl)
    JOURNALCTL=(journalctl)
    # Group-readable so the operator UI and a non-admin agent user can read
    # the token without copying it. Group `inference` is provisioned by the
    # host script (see preflight).
    FILE_MODE=0640
    FILE_GROUP=inference
else
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    ENV_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llauncher"
    ENV_FILE="$ENV_DIR/agent.env"
    LLAUNCHER_DIR="$HOME/.llauncher"
    TOKEN_FILE="$LLAUNCHER_DIR/agent.token"
    TEMPLATE="$SCRIPT_DIR/llauncher-agent.service.in"
    SYSTEMCTL=(systemctl --user)
    JOURNALCTL=(journalctl --user)
    FILE_MODE=0600
    FILE_GROUP=""  # no chgrp in user mode; defined so $FILE_GROUP is set under `set -u`
fi
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

uninstall() {
    if [ "$MODE" = "system" ] && [ "$EUID" -ne 0 ]; then
        err "--system --uninstall must run as root (removes /etc/systemd/system unit)."
        exit 1
    fi
    info "Stopping and disabling $UNIT_NAME..."
    "${SYSTEMCTL[@]}" disable --now "$UNIT_NAME" 2>/dev/null || true
    if [ -f "$UNIT_PATH" ]; then
        rm -f "$UNIT_PATH"
        say "Removed $UNIT_PATH"
    fi
    "${SYSTEMCTL[@]}" daemon-reload
    info "Env file left in place at $ENV_FILE (delete manually if desired)."
    info "Token file left in place at $TOKEN_FILE (delete manually if desired)."
    say "Uninstalled."
    exit 0
}

[ "$DO_UNINSTALL" -eq 1 ] && uninstall

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

# --- System-mode preflight --------------------------------------------
# This installer renders the unit + writes config only. It does NOT create
# the user/group/state-dir/ACLs/polkit — that is the host script's job.
# Require that provisioning to already exist and fail loudly otherwise.
if [ "$MODE" = "system" ]; then
    if [ "$EUID" -ne 0 ]; then
        err "--system mode must run as root (writes /etc/systemd/system and /var/lib/llauncher)."
        exit 1
    fi
    provisioning_missing=0
    if ! id llauncher >/dev/null 2>&1; then
        err "Missing system user 'llauncher'."; provisioning_missing=1
    fi
    if ! getent group inference >/dev/null 2>&1; then
        err "Missing group 'inference'."; provisioning_missing=1
    fi
    if [ ! -d "$STATE_DIR" ]; then
        err "Missing state dir $STATE_DIR."; provisioning_missing=1
    fi
    if [ "$provisioning_missing" -ne 0 ]; then
        err "Host provisioning incomplete. Run harness-tools"
        err "  claude/host-config/setup-inference-lane.sh"
        err "first to create the llauncher user, inference group, and $STATE_DIR."
        exit 1
    fi
fi

# --- Env file ----------------------------------------------------------
# In system mode STATE_DIR is provisioned (owner/perms/ACLs) by the host
# script, so do NOT create or chmod it here; just write files into it.
if [ "$MODE" != "system" ]; then
    mkdir -p "$ENV_DIR"
    chmod 700 "$ENV_DIR"
fi

if [ ! -f "$ENV_FILE" ]; then
    info "Generating $ENV_FILE with a fresh token..."
    TOKEN="$("$VENV_BIN/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    # Seed from the example, then substitute the placeholder token.
    sed "s|replace-me-with-a-random-token|$TOKEN|" "$ENV_EXAMPLE" > "$ENV_FILE"
    chmod "$FILE_MODE" "$ENV_FILE"
    [ "$MODE" = "system" ] && chgrp "$FILE_GROUP" "$ENV_FILE"
    say "Wrote $ENV_FILE (mode $FILE_MODE) with a generated 32-byte token."
    info "Edit it to set LLAUNCHER_AGENT_NODE_NAME / HOST / PORT as needed."
else
    chmod "$FILE_MODE" "$ENV_FILE"  # repair perms if they drifted
    [ "$MODE" = "system" ] && chgrp "$FILE_GROUP" "$ENV_FILE"
    say "Env file already exists at $ENV_FILE — leaving it untouched."
fi

# --- Token mirror (issue #131) -----------------------------------------
# Mirror LLAUNCHER_AGENT_TOKEN from the env file into TOKEN_FILE so the UI
# process — which does NOT share the systemd service env — can authenticate
# against the local agent. In user mode this is ~/.llauncher/agent.token; in
# system mode it is /var/lib/llauncher/agent.token, group-readable to
# `inference` so the operator UI / non-admin agent user can read it without
# copying. Symmetric with the Windows install.ps1 mirror block.
#
# `tail -n1` (not `head -n1`) matches systemd's EnvironmentFile parser
# semantics ("last wins"); the value the agent actually runs with is the
# last LLAUNCHER_AGENT_TOKEN= line, so the mirror must reflect that.
# `tr -d '[:space:]'` defends against trailing whitespace or stray CRs
# from hand-edited env files.
if [ "$MODE" != "system" ]; then
    mkdir -p "$LLAUNCHER_DIR"
    chmod 700 "$LLAUNCHER_DIR"
fi
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
    chmod "$FILE_MODE" "$TOKEN_FILE"
    [ "$MODE" = "system" ] && chgrp "$FILE_GROUP" "$TOKEN_FILE"
    say "Mirrored token to $TOKEN_FILE (mode $FILE_MODE) so the UI can authenticate."
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

"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" enable "$UNIT_NAME" >/dev/null
say "Enabled $UNIT_NAME"

if [ "$START_AFTER_INSTALL" -eq 1 ]; then
    "${SYSTEMCTL[@]}" restart "$UNIT_NAME"
    sleep 1
    if "${SYSTEMCTL[@]}" is-active --quiet "$UNIT_NAME"; then
        say "Service is active."
    else
        err "Service failed to start. Last 20 log lines:"
        "${JOURNALCTL[@]}" -u "$UNIT_NAME" -n 20 --no-pager || true
        exit 1
    fi
fi

if [ "$MODE" = "system" ]; then
cat <<EOF

Next steps:
  systemctl status llauncher-agent
  journalctl -u llauncher-agent -f

The unit is enabled for multi-user.target and will start at boot.

To uninstall:
  sudo $SCRIPT_DIR/install.sh --system --uninstall
EOF
else
cat <<EOF

Next steps:
  systemctl --user status llauncher-agent
  journalctl --user -u llauncher-agent -f

For autostart at boot without an active login session, run once:
  sudo loginctl enable-linger "\$USER"

To uninstall:
  $SCRIPT_DIR/install.sh --uninstall
EOF
fi
