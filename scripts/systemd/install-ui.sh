#!/usr/bin/env bash
# install-ui.sh — install / refresh the llauncher UI as a systemd --user unit.
#
# Per-operator, unprivileged, session-managed. Renders
# llauncher-ui.service.user.in into ~/.config/systemd/user/ and runs
# `systemctl --user enable --now`. See ADR-LLNCH-022 (narrows ADR-LLNCH-018's UI posture):
# the agent is a system service; the UI is a per-operator `systemd --user` unit.
#
# Run as your OWN operator account — NOT root. The token-read path works in
# place via your `inference`-group membership (host provisioning); the
# /usr/local/bin/llauncher-ui symlink is placed by install-cli.sh (root).
# Neither is this installer's job. The pinned venv itself
# (/opt/llauncher/venv, ADR-LLNCH-023, issue #360) is a hard preflight — this
# installer FAILS LOUD if it is absent (no repo-venv fallback exists). The
# symlink and group-membership preconditions remain soft warnings.
#
# LAUNCHER_STATE_DIR comes from the template (fixed at /var/lib/llauncher); it
# is intentionally NOT overridable from this installer's caller environment.
#
# Idempotent: safe to re-run after a `git pull` to pick up unit changes.
#
# Post-start verification (issue #421): `enable --now` alone only asks
# systemd to start the unit — it says nothing about whether Streamlit ever
# bound its port. After start, this installer polls the UI's own
# GET /_stcore/health (Streamlit's built-in readiness route — 200 once the
# runtime is ready to accept browser connections, 503 otherwise; stable
# across the streamlit>=1.30.0 range this repo pins) at the port resolved
# the same way llauncher.ui.launch does (LLAUNCHER_UI_PORT, default 8501).
# Poll bounds/interval are tunable via READINESS_TIMEOUT_SECS /
# READINESS_POLL_INTERVAL (see scripts/systemd/lib-readiness.sh, shared
# with install.sh's #420 fix — not duplicated).
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
JOURNALCTL=(journalctl --user)

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}ℹ${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }

# shellcheck source=scripts/systemd/lib-readiness.sh
. "$SCRIPT_DIR/lib-readiness.sh"

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
    err "This is a 'systemctl --user' unit owned by your login session (ADR-LLNCH-022)."
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

# --- Preflight: the pinned venv is a hard requirement (issue #360) -----
# #357 ratified Option A: the systemd deployment runs from a unique, PINNED
# venv (/opt/llauncher/venv) independent of any operator's or clone's
# working-tree state. There is no repo-venv fallback to degrade to — if the
# pin was never composed, fail loud here and point at the ritual, rather
# than rendering a unit doomed to fail at systemd's ExecStartPre backstop.
PINNED_VENV="/opt/llauncher/venv"
if [ ! -x "$PINNED_VENV/bin/llauncher-ui" ]; then
    err "$PINNED_VENV/bin/llauncher-ui not found — the pinned runtime venv has"
    err "not been composed on this host (ADR-LLNCH-023, issue #360). There is no"
    err "fallback to a repo venv. Compose it (root, one-time or to recompose):"
    err "  sudo bash $SCRIPT_DIR/install-cli.sh"
    err "See docs/operations/run-as-a-service.md, \"Composing the pinned runtime venv\"."
    exit 1
fi

# --- Preflight (warn, do NOT block) ------------------------------------
# Host-provisioning conditions, out of this installer's scope (ADR-LLNCH-022
# §installer-vs-host-provisioning). Warn loudly so the cause of a later
# 403/token failure is legible, but proceed — the unit can be rendered and
# enabled now; it will become functional once provisioning is in place.
if [ ! -x "$UI_BIN" ]; then
    info "[user:gate] $UI_BIN is absent even though the pinned venv exists."
    info "  Re-run the CLI installer as root to re-place the symlink:"
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
say "Enabled — polling for a live readback before declaring success..."

if ! systemctl --user is-active --quiet "$UNIT_NAME"; then
    err "$UNIT_NAME failed to start. Last 20 log lines:"
    "${JOURNALCTL[@]}" -u "$UNIT_NAME" -n 20 --no-pager || true
    exit 1
fi

# Same port resolution as llauncher.ui.launch::resolve_ui_port (defaults to
# Streamlit's 8501; LLAUNCHER_UI_PORT overrides). The unit template sets no
# LLAUNCHER_UI_HOST/PORT of its own (llauncher-ui.service.user.in), so this
# reads the same env this shell is running in — the value the operator would
# have to export before invoking this installer for a non-default port to
# take effect at all.
UI_PORT="${LLAUNCHER_UI_PORT:-8501}"

if ! verify_http_readiness "http://127.0.0.1:${UI_PORT}/_stcore/health" JOURNALCTL "$UNIT_NAME"; then
    exit 1
fi
say "$UNIT_NAME is active and serving (/_stcore/health confirmed on port $UI_PORT)."

cat <<EOF

Next steps:
  systemctl --user status llauncher-ui
  journalctl --user -u llauncher-ui -f

Optional — autostart at boot / survive logout (run once, your call):
  loginctl enable-linger "$USER"

To uninstall:
  $SCRIPT_DIR/install-ui.sh --uninstall
EOF
