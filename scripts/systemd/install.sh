#!/bin/bash
# Install / refresh the llauncher-agent systemd unit.
#
# Two install scopes:
#   --user   (default) — per-user unit under ~/.config/systemd/user, state
#                        under $HOME. ExecStart resolves through
#                        /usr/local/bin/llauncher-agent into the PINNED,
#                        root-owned /opt/llauncher/venv (#357 ratified
#                        Option A, issue #360) — never this checkout's
#                        .venv. See docs/operations/run-as-a-service.md,
#                        "Composing the pinned runtime venv".
#   --system           — system unit at /etc/systemd/system run as the
#                        dedicated `llauncher` account (group `inference`),
#                        with state under /var/lib/llauncher. Requires root
#                        and pre-existing host provisioning. Still recomposes
#                        this checkout's dev-tree .venv (ADR-023 Phase A,
#                        issue #227) — unchanged by #360. See ADR-018.
#
# Idempotent: safe to re-run after a `git pull` to pick up unit-file
# changes. Will NOT overwrite an existing env file (so your token and
# host config survive reinstalls), but pre-#139 legacy LAUNCHER_AGENT_*
# key names ARE migrated in place to LLAUNCHER_AGENT_* (values
# preserved; issue #281).
#
# Single live source (issue #284): agent.env is read DIRECTLY by both the
# agent service (systemd EnvironmentFile=) and the UI
# (llauncher.core.agent_token.resolve_agent_token) at their own startup —
# there is no agent.token mirror file any more. A stale agent.token from a
# pre-#284 install is migrated into agent.env once, at the door, then
# deleted; the installer announces this loudly. Editing
# agent.env.example after first install does nothing — it is a seed-once
# template, not live config.
#
# Usage:
#   ./scripts/systemd/install.sh                    # user: install+enable+start
#   ./scripts/systemd/install.sh --no-start         # user: render+enable only
#   ./scripts/systemd/install.sh --uninstall        # user: remove unit
#   sudo ./scripts/systemd/install.sh --system      # system: install+enable+start
#   sudo ./scripts/systemd/install.sh --system --no-start
#   sudo ./scripts/systemd/install.sh --system --uninstall
#
# Post-start verification (issue #420): a restart is only declared a success
# once the agent's auth-exempt GET /health answers 200 — `is-active` alone
# only proves the unit didn't crash within a fixed sleep, not that the HTTP
# server bound its port (same defect class as #308, #400/#413). Poll
# bounds/interval are tunable via READINESS_TIMEOUT_SECS /
# READINESS_POLL_INTERVAL (see scripts/systemd/lib-readiness.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_BIN="$PROJECT_DIR/.venv/bin"

# shellcheck source=scripts/systemd/lib-readiness.sh
. "$SCRIPT_DIR/lib-readiness.sh"
# shellcheck source=scripts/systemd/check_python_floor.sh
. "$SCRIPT_DIR/check_python_floor.sh"

UNIT_NAME="llauncher-agent.service"
# Root oneshot that guarantees the agent venv (system mode only; ADR-023/#227).
ENSURE_UNIT_NAME="llauncher-agent-ensure-venv.service"
ENV_EXAMPLE="$SCRIPT_DIR/agent.env.example"

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
# NOT inherit LLAUNCHER_AGENT_TOKEN from the unit's environment, so it
# authenticates against the local agent by parsing ENV_FILE directly
# (llauncher.core.agent_token.resolve_agent_token; issue #284 — single
# live source, no mirror). TOKEN_FILE below is retained only as the
# pre-#284 mirror path this installer migrates from and then deletes. See
# the Windows counterpart in scripts/windows/install.ps1.
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
    # agent.env (and therefore its token line) directly, without copying.
    # Group `inference` is provisioned by the host script (see preflight).
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
    # Tear down the system-scope ensure unit alongside the agent (ADR-023/#227).
    if [ "$MODE" = "system" ]; then
        "${SYSTEMCTL[@]}" disable --now "$ENSURE_UNIT_NAME" 2>/dev/null || true
        if [ -f "$UNIT_DIR/$ENSURE_UNIT_NAME" ]; then
            rm -f "$UNIT_DIR/$ENSURE_UNIT_NAME"
            say "Removed $UNIT_DIR/$ENSURE_UNIT_NAME"
        fi
    fi
    "${SYSTEMCTL[@]}" daemon-reload
    info "Env file left in place at $ENV_FILE (delete manually if desired)."
    if [ -f "$TOKEN_FILE" ]; then
        info "Stale mirror file also left in place at $TOKEN_FILE (retired by #284;"
        info "  live source is $ENV_FILE — delete $TOKEN_FILE manually if desired)."
    fi
    say "Uninstalled."
    exit 0
}

[ "$DO_UNINSTALL" -eq 1 ] && uninstall

# --- Interpreter floor (issue #334) -------------------------------------
# pyproject.toml declares `requires-python = ">=3.11"`. Neither venv this
# installer depends on (dev-tree .venv in --system mode, /opt/llauncher/venv
# in --user mode) is built by this script — but a <3.11 interpreter on PATH
# would still be silently fed into whichever recompose ritual (run.sh setup
# / install-cli.sh) the preflight below points the operator at, failing much
# later at import time. Fail loud here, before naming that ritual.
check_python_floor python3 3 11

# --- Preflight ---------------------------------------------------------
# #357 ratified Option A (issue #360): the --user agent unit's ExecStart no
# longer resolves into this dev checkout's .venv — it resolves through
# /usr/local/bin/llauncher-agent into the PINNED, root-owned
# /opt/llauncher/venv (same indirection ADR-023 already uses for the UI
# unit). Fail loud here, at install time, rather than silently falling back
# to a repo venv (which the unit template no longer even points at).
#
# --system mode is UNCHANGED by #360: it recomposes @PROJECT_DIR@/.venv via
# the root ensure-venv oneshot (ADR-023 Phase A, issue #227) and still needs
# that dev-tree venv populated first.
if [ "$MODE" = "system" ]; then
    if [ ! -x "$VENV_BIN/llauncher-agent" ]; then
        err "Did not find $VENV_BIN/llauncher-agent."
        err "Run './scripts/run.sh setup' first to recompose the venv (ADR-023)."
        exit 1
    fi
else
    PINNED_VENV="/opt/llauncher/venv"
    if [ ! -x "$PINNED_VENV/bin/llauncher-agent" ]; then
        err "Did not find $PINNED_VENV/bin/llauncher-agent."
        err "The --user agent unit's ExecStart resolves through /usr/local/bin/llauncher-agent"
        err "into the pinned $PINNED_VENV (ADR-023, issue #360) — there is no fallback to a"
        err "repo venv. Compose it (root, one-time or to recompose):"
        err "  sudo bash $SCRIPT_DIR/install-cli.sh"
        err "See docs/operations/run-as-a-service.md, \"Composing the pinned runtime venv\"."
        exit 1
    fi
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

# --- Migrate a pre-#284 agent.token mirror BEFORE any fresh seed -------
# Ordering matters: if $ENV_FILE is absent but a stale agent.token mirror
# still holds the operator's real, already-in-use token (e.g. agent.env
# was deleted/never synced while the mirror survived), the seed-from-
# template step below must NOT overwrite it with a newly generated
# random token — that would silently orphan the live credential the
# mirror was carrying. So: if the mirror exists and carries a value,
# seed $ENV_FILE from THAT value instead of generating a fresh one.
MIGRATED_MIRROR_TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
    MIGRATED_MIRROR_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE" || true)"
fi

if [ ! -f "$ENV_FILE" ]; then
    if [ -n "$MIGRATED_MIRROR_TOKEN" ]; then
        info "Seeding $ENV_FILE from the template using the live token found in the stale agent.token mirror..."
        sed "s|replace-me-with-a-random-token|$MIGRATED_MIRROR_TOKEN|" "$ENV_EXAMPLE" > "$ENV_FILE"
        say "Wrote $ENV_FILE, carrying forward the token from $TOKEN_FILE (not overwritten with a fresh one)."
    else
        info "Seeding $ENV_FILE from the template (one-time; see agent.env.example header)..."
        # Plain python3 (issue #360): token generation must not depend on
        # either venv existing at install time — the dev .venv is no longer
        # a --user-mode precondition, and the pinned /opt venv may not have
        # been composed yet when this seeds the FIRST env file.
        TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        # Seed from the example, then substitute the placeholder token.
        sed "s|replace-me-with-a-random-token|$TOKEN|" "$ENV_EXAMPLE" > "$ENV_FILE"
        say "Wrote $ENV_FILE (mode $FILE_MODE) with a generated 32-byte token."
    fi
    chmod "$FILE_MODE" "$ENV_FILE"
    [ "$MODE" = "system" ] && chgrp "$FILE_GROUP" "$ENV_FILE"
    info "Edit it to set LLAUNCHER_AGENT_NODE_NAME / HOST / PORT as needed."
else
    chmod "$FILE_MODE" "$ENV_FILE"  # repair perms if they drifted
    [ "$MODE" = "system" ] && chgrp "$FILE_GROUP" "$ENV_FILE"
    # Loud on skip (issue #284): edits to the template are never read again
    # after this first-install seed — $ENV_FILE is the only file either
    # process consults from here on.
    info "agent.env already exists at $ENV_FILE — skipping template seed."
    info "  Edits to $ENV_EXAMPLE are never read after first install;"
    info "  edit $ENV_FILE directly (the live source) and re-run this installer."

    # --- Migrate pre-#139 legacy keys (issue #281), deduped (issue #285) --
    # Commit 9f098d9 (#138/#139) renamed LAUNCHER_AGENT_* → LLAUNCHER_AGENT_*,
    # but env files written from the pre-rename template still carry the
    # single-L keys — which nothing reads any more, so the agent silently
    # auto-generates its own token and the UI 403s on every authed endpoint.
    # PARSE-AT-THE-DOOR: rewrite the key prefix in place, once,
    # deterministically, preserving each value byte-for-byte.
    #
    # Issue #285: a blanket prefix rewrite created a DUPLICATE when a legacy
    # line's migrated key already existed as a canonical line — the
    # installer half of the "403s keep coming back" recurrence (paired with
    # #293's runtime half). The migration now DROPS a legacy line whose
    # migrated key already exists (the canonical line wins), loudly. Comment
    # lines start with '#' and never match the key-anchored pattern; line
    # order of surviving lines is preserved, so systemd's last-wins
    # EnvironmentFile semantics (and the `tail -n1` reads below) are
    # unaffected. Logic is extracted to migrate_env_keys.sh so it is
    # unit-testable without the venv/systemctl preflight above.
    # shellcheck source=scripts/systemd/migrate_env_keys.sh
    . "$SCRIPT_DIR/migrate_env_keys.sh"
    migrate_and_dedupe_env_keys "$ENV_FILE"
fi

# --- Retire the agent.token mirror (issue #284) -------------------------
# agent.env is now the single live source, read DIRECTLY by both the
# systemd service (EnvironmentFile=) and the UI
# (llauncher.core.agent_token.resolve_agent_token) at their own startup —
# no installer-maintained mirror file. A stale agent.token from a
# pre-#284 install is migrated in place, once, at the door
# (PARSE-AT-THE-DOOR): if $ENV_FILE has NO usable token line, the
# mirror's value is appended to $ENV_FILE before the mirror is deleted, so
# a live credential already in use is never silently discarded. If
# $ENV_FILE already has a token, the mirror is simply retired (deleted)
# since nothing reads it any more.
#
# `tail -n1` (not `head -n1`) matches systemd's EnvironmentFile parser
# semantics ("last wins"); the value the agent actually runs with is the
# last LLAUNCHER_AGENT_TOKEN= line. `tr -d '[:space:]'` defends against
# trailing whitespace or stray CRs from hand-edited env files. `|| true`
# keeps a grep miss (no matching line) from tripping `set -e` via the
# failing command-substitution.
if [ "$MODE" != "system" ]; then
    mkdir -p "$LLAUNCHER_DIR"
    chmod 700 "$LLAUNCHER_DIR"
fi
if [ -f "$TOKEN_FILE" ]; then
    EXISTING_TOKEN_VALUE="$(grep -E '^LLAUNCHER_AGENT_TOKEN=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
    if [ -z "$EXISTING_TOKEN_VALUE" ]; then
        MIRROR_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE" || true)"
        if [ -n "$MIRROR_TOKEN" ]; then
            printf 'LLAUNCHER_AGENT_TOKEN=%s\n' "$MIRROR_TOKEN" >> "$ENV_FILE"
            say "Migrated live token from $TOKEN_FILE into $ENV_FILE ($ENV_FILE had no token line)."
        fi
    fi
    rm -f "$TOKEN_FILE"
    info "Retired by #284; live source is $ENV_FILE. Removed stale mirror $TOKEN_FILE."
fi

# --- Fail loud if the env file still has no usable token (issue #281/#284) -
# Legacy single-L (LAUNCHER, not LLAUNCHER; #138/#139) key names have
# already been migrated in place above, so reaching this branch means the
# env file genuinely has no usable token — never a shape we
# trust-and-degrade on (issue #281).
TOKEN_VALUE="$(grep -E '^LLAUNCHER_AGENT_TOKEN=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
if [ -z "$TOKEN_VALUE" ]; then
    err "No usable LLAUNCHER_AGENT_TOKEN line found in $ENV_FILE (missing or empty value)."
    err "Refusing to install the service without a usable token in the live source — the"
    err "agent would silently auto-generate its own token and the UI could never authenticate."
    err "Fix one of:"
    err "  - Add a line to $ENV_FILE:  LLAUNCHER_AGENT_TOKEN=<token>"
    err "    (generate one:  python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    err "  - Or delete $ENV_FILE and re-run this installer to regenerate it from the template."
    exit 1
fi

# --- Unit file ---------------------------------------------------------
# @VENV_BIN@ only appears in the --system template (llauncher-agent.service.system.in,
# ADR-023 Phase A dev-tree recompose, unchanged by #360). The --user template
# (llauncher-agent.service.in) dropped the placeholder — its ExecStart is a
# fixed /usr/local/bin/llauncher-agent, so this substitution is a harmless
# no-op there.
mkdir -p "$UNIT_DIR"
sed \
    -e "s|@VENV_BIN@|$VENV_BIN|g" \
    -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    "$TEMPLATE" > "$UNIT_PATH"
say "Rendered unit to $UNIT_PATH"

# --- Ensure-venv unit (system scope only; ADR-023 / #227) --------------
# The agent system unit Requires=/After= this root oneshot so the agent never
# starts against a missing/broken venv. It is system-scoped because recompose
# must run where the dev-tree .venv is writable (root), not as User=llauncher.
# User mode's ExecStart now resolves through /usr/local/bin/llauncher-agent
# into the PINNED /opt/llauncher/venv (issue #360) — it neither owns nor
# recomposes a venv itself, and gets a fail-loud ExecStartPre backstop in
# the unit template instead (mirrors the UI unit, ADR-023 Phase B). So it
# gets no ensure unit here either.
if [ "$MODE" = "system" ]; then
    ENSURE_TEMPLATE="$SCRIPT_DIR/llauncher-agent-ensure-venv.service.in"
    ENSURE_UNIT_PATH="$UNIT_DIR/$ENSURE_UNIT_NAME"
    sed \
        -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
        "$ENSURE_TEMPLATE" > "$ENSURE_UNIT_PATH"
    say "Rendered ensure unit to $ENSURE_UNIT_PATH"
fi

"${SYSTEMCTL[@]}" daemon-reload
if [ "$MODE" = "system" ]; then
    "${SYSTEMCTL[@]}" enable "$ENSURE_UNIT_NAME" >/dev/null
    say "Enabled $ENSURE_UNIT_NAME"
fi
"${SYSTEMCTL[@]}" enable "$UNIT_NAME" >/dev/null
say "Enabled $UNIT_NAME"

if [ "$START_AFTER_INSTALL" -eq 1 ]; then
    "${SYSTEMCTL[@]}" restart "$UNIT_NAME"
    sleep 1
    if ! "${SYSTEMCTL[@]}" is-active --quiet "$UNIT_NAME"; then
        err "Service failed to start. Last 20 log lines:"
        "${JOURNALCTL[@]}" -u "$UNIT_NAME" -n 20 --no-pager || true
        exit 1
    fi
    say "Unit is active — polling for a live /health readback..."

    # Derive host:port from the SAME live agent.env this installer manages
    # (ENV_FILE, resolved above per $MODE), not a re-guessed default. A
    # 0.0.0.0 (or empty) bind host means "listens on all interfaces" — poll
    # loopback in that case, since 0.0.0.0 is not itself a connectable
    # address. Defaults mirror llauncher/agent/config.py::AgentConfig.
    # Strip surrounding single/double quotes (trailing sed) so a hand-edited
    # quoted value (e.g. LLAUNCHER_AGENT_HOST='"0.0.0.0"') still hits the
    # loopback fallback below rather than being polled verbatim.
    HEALTH_HOST="$(grep -E '^LLAUNCHER_AGENT_HOST=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' | sed 's/^["'"'"']//;s/["'"'"']$//' || true)"
    HEALTH_PORT="$(grep -E '^LLAUNCHER_AGENT_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' | sed 's/^["'"'"']//;s/["'"'"']$//' || true)"
    if [ -z "$HEALTH_HOST" ] || [ "$HEALTH_HOST" = "0.0.0.0" ]; then
        HEALTH_HOST="127.0.0.1"
    fi
    [ -n "$HEALTH_PORT" ] || HEALTH_PORT="8765"

    if ! verify_http_readiness "http://${HEALTH_HOST}:${HEALTH_PORT}/health" JOURNALCTL "$UNIT_NAME"; then
        exit 1
    fi
    say "Service is active and serving (/health confirmed)."
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
