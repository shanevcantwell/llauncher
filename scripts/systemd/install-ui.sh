#!/usr/bin/env bash
# install-ui.sh — install / refresh the llauncher UI as a systemd --user unit.
#
# Per-operator, unprivileged, session-managed. Renders
# llauncher-ui.service.user.in into ~/.config/systemd/user/ and runs
# `systemctl --user enable --now`. See ADR-022 (narrows ADR-018's UI posture):
# the agent is a system service; the UI is a per-operator `systemd --user` unit.
#
# Run as your OWN operator account — NOT root. The token-read path works in
# place via your `inference`-group membership (host provisioning); the
# /usr/local/bin/llauncher-ui symlink is placed by install-cli.sh (root).
# Neither is this installer's job — it only warns if they are missing.
#
# LAUNCHER_STATE_DIR comes from the template (fixed at /var/lib/llauncher); it
# is intentionally NOT overridable from this installer's caller environment.
#
# Idempotent: safe to re-run after a `git pull` to pick up unit changes.
#
# Usage:
#   ./scripts/systemd/install-ui.sh             # render + enable + start
#   ./scripts/systemd/install-ui.sh --uninstall # disable + remove unit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/llauncher-ui.service.user.in"
UI_BIN="/usr/local/bin/llauncher-ui"

UNIT_NAME="llauncher-ui.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}ℹ${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }

# --- Argument parsing --------------------------------------------------
DO_UNINSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) DO_UNINSTALL=1 ;;
        *)           err "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

if [ "$(id -u)" -eq 0 ]; then
    err "Run as your own operator account, NOT root."
    err "This is a 'systemctl --user' unit owned by your login session (ADR-022)."
    exit 1
fi

if ! command -v systemctl >/dev/null; then
    err "systemctl not found — this installer targets systemd hosts only."
    exit 1
fi

uninstall() {
    info "Stopping and disabling $UNIT_NAME..."
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    if [ -f "$UNIT_PATH" ]; then
        rm -f "$UNIT_PATH"
        say "Removed $UNIT_PATH"
    fi
    systemctl --user daemon-reload
    say "Uninstalled."
    exit 0
}

[ "$DO_UNINSTALL" -eq 1 ] && uninstall

# --- Preflight (warn, do NOT block) ------------------------------------
# Both conditions are operator/host provisioning, out of this installer's
# scope (ADR-022 §installer-vs-host-provisioning). Warn loudly so the cause
# of a later failure is legible, but proceed — the unit can be rendered and
# enabled now; it will become functional once provisioning is in place.
if [ ! -x "$UI_BIN" ]; then
    info "[user:gate] $UI_BIN is absent. The unit's ExecStart points there."
    info "  Run the CLI installer as root first:"
    info "    sudo bash $SCRIPT_DIR/install-cli.sh"
    info "  Until then the service will fail to start."
fi

if ! getent group inference >/dev/null 2>&1; then
    info "Group 'inference' does not exist on this host. The UI reads the system"
    info "  agent's live env file (/var/lib/llauncher/agent.env, 0640 root:inference) via"
    info "  group membership; token reads will fail until the group is provisioned"
    info "  (host provisioning, out of installer scope)."
elif ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx inference; then
    info "$USER is not a member of group 'inference'. The UI reads the system"
    info "  agent's live env file (/var/lib/llauncher/agent.env, 0640 root:inference) via"
    info "  group membership; token reads will fail until you are added"
    info "    sudo usermod -aG inference $USER   # host provisioning, then re-login"
fi

# --- Render ------------------------------------------------------------
# LAUNCHER_STATE_DIR is fixed in the template; this is a straight copy (no sed,
# no placeholders), so the caller's environment cannot override it.
mkdir -p "$UNIT_DIR"
cp "$TEMPLATE" "$UNIT_PATH"
say "Rendered unit to $UNIT_PATH"

# --- Activate ----------------------------------------------------------
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
say "Enabled and started $UNIT_NAME"

cat <<EOF

Next steps:
  systemctl --user status llauncher-ui
  journalctl --user -u llauncher-ui -f

Optional — autostart at boot / survive logout (run once, your call):
  loginctl enable-linger "$USER"

To uninstall:
  $SCRIPT_DIR/install-ui.sh --uninstall
EOF
