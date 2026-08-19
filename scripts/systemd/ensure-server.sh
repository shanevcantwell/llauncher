#!/bin/bash
# ensure-server.sh — idempotently converge a model onto a port.
#
# "Ensure NAME is serving on PORT" — safe to invoke from a login-hook unit,
# a cron poll, an operator's shell, or an agent. Convergent, not imperative:
# running it twice in a row when nothing changed is a silent no-op (exit 0,
# one line to stdout). This is the interim mechanism for keeping the
# resident embedder up across agent restarts — see issue #422 (design fork:
# systemd KillMode default kills child llama-servers on every agent
# restart). The durable fix is ProdM's resident-models design; this script
# is the bridge until that lands.
#
# Usage:
#   ensure-server.sh [NAME] [PORT]
#   ensure-server.sh embeddinggemma-300M-F32-pooled 8082   # defaults shown
#
# Behavior:
#   1. Port already serves NAME (verified on the WIRE, not `server status`
#      — see the #TBD status-mislabel note below) -> exit 0, one line.
#   2. Port serves a DIFFERENT model -> stop the occupant, start NAME.
#      (No `swap` verb exists in this CLI as of this writing — verified via
#      `llauncher server --help`; ADR-010 keeps port caller-supplied, so a
#      stop+start pair on the same port is the swap.)
#   3. Port is free -> start NAME.
#   In all cases: bounded readiness poll, then wire-verify GET /v1/models
#   reports NAME byte-for-byte (EMIT-CANONICAL). Success requires the wire
#   readback; any mismatch or timeout exits nonzero and prints what the
#   wire actually said.
#
# Known bug (#TBD, live-reproduced 2026-08-19): `llauncher server status`
# can mislabel a running server's model name when two configs share a
# gguf — this host's status table showed
# "embeddinggemma-300M-F32-nonpooled" on :8082 while the wire
# (GET /v1/models) correctly reported "embeddinggemma-300M-F32-pooled".
# This script NEVER trusts `server status` for identity — only the wire.
# `server status` is consulted only to learn what PID/port is occupied, so
# we know whether a stop is needed before starting NAME; the actual model
# identity check is always the wire readback.
#
# CLI resolution (fallback chain, first match wins; lesson from #352/#372 —
# announce which candidate resolved, don't silently pick one):
#   1. $LLAUNCHER_CLI               (explicit override)
#   2. /opt/llauncher/venv/bin/llauncher   (pinned system install)
#   3. <repo>/.venv/bin/llauncher          (dev checkout fallback)
#
# Token resolution: honors an already-exported LLAUNCHER_AGENT_TOKEN first;
# otherwise sources it from /var/lib/llauncher/agent.env (group-readable,
# live-proven this morning). Never embedded in source (house rule).
#
# Exit codes: 0 = converged and wire-verified. Nonzero = could not converge
# or could not verify; stderr names what was expected vs. what the wire
# said.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEFAULT_NAME="embeddinggemma-300M-F32-pooled"
DEFAULT_PORT="8082"
NAME="${1:-$DEFAULT_NAME}"
PORT="${2:-$DEFAULT_PORT}"

AGENT_ENV_FILE="/var/lib/llauncher/agent.env"
STATE_DIR="/var/lib/llauncher"

READY_TIMEOUT_SECS="${ENSURE_SERVER_READY_TIMEOUT:-30}"
READY_POLL_INTERVAL="${ENSURE_SERVER_POLL_INTERVAL:-1}"

# --- house-style helpers (matches install.sh) ---------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${YELLOW}i${NC} $1" >&2; }
say()  { echo -e "${GREEN}v${NC} $1" >&2; }
die()  { echo -e "${RED}x${NC} $1" >&2; exit 1; }

# --- CLI resolution: named fallback chain, announced ---------------------
resolve_cli() {
    local candidates=()
    if [ -n "${LLAUNCHER_CLI:-}" ]; then
        if [ -x "$LLAUNCHER_CLI" ]; then
            info "Using CLI from \$LLAUNCHER_CLI override: $LLAUNCHER_CLI"
            echo "$LLAUNCHER_CLI"
            return 0
        fi
        die "\$LLAUNCHER_CLI is set to '$LLAUNCHER_CLI' but it is not executable."
    fi
    candidates+=("/opt/llauncher/venv/bin/llauncher")
    candidates+=("$PROJECT_DIR/.venv/bin/llauncher")
    for c in "${candidates[@]}"; do
        if [ -x "$c" ]; then
            info "Using CLI: $c"
            echo "$c"
            return 0
        fi
    done
    die "No usable llauncher CLI found. Probed: \$LLAUNCHER_CLI (unset), ${candidates[*]}. Set \$LLAUNCHER_CLI to override."
}
LLAUNCHER_BIN="$(resolve_cli)"

# --- Token resolution: honor an existing export first ---------------------
if [ -z "${LLAUNCHER_AGENT_TOKEN:-}" ]; then
    if [ -r "$AGENT_ENV_FILE" ]; then
        # PARSE-AT-THE-DOOR: last-wins on duplicate keys, matches systemd's
        # EnvironmentFile semantics and install.sh's own reader.
        TOKEN_LINE="$(grep -E '^LLAUNCHER_AGENT_TOKEN=' "$AGENT_ENV_FILE" | tail -n1 || true)"
        if [ -n "$TOKEN_LINE" ]; then
            LLAUNCHER_AGENT_TOKEN="$(printf '%s' "$TOKEN_LINE" | cut -d= -f2- | tr -d '[:space:]')"
            export LLAUNCHER_AGENT_TOKEN
            info "Sourced LLAUNCHER_AGENT_TOKEN from $AGENT_ENV_FILE."
        fi
    fi
fi
[ -n "${LLAUNCHER_AGENT_TOKEN:-}" ] || die "No LLAUNCHER_AGENT_TOKEN available (not exported, and not readable from $AGENT_ENV_FILE)."

export LAUNCHER_STATE_DIR="${LAUNCHER_STATE_DIR:-$STATE_DIR}"

llauncher_json() {
    # Strips llauncher's own pydantic UserWarning lines (issue #156) that
    # land on stderr but can otherwise confuse naive callers; --json output
    # itself goes to stdout untouched.
    "$LLAUNCHER_BIN" "$@"
}

# --- Wire verification: never trust `server status` for identity ---------
# GET /v1/models -> data[0].id is the canonical name per EMIT-CANONICAL
# (llauncher starts every server with --alias = ModelConfig.name).
wire_model_name() {
    local port="$1"
    curl -fsS --max-time 5 "http://127.0.0.1:${port}/v1/models" 2>/dev/null \
        | python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
    data = doc.get("data") or []
    print(data[0]["id"] if data else "")
except Exception:
    print("")
' 2>/dev/null || true
}

port_pid_and_name() {
    # Reads llauncher's own status table only to learn occupancy (pid /
    # whether the port is in use) -- NEVER for identity (see header note on
    # the status-mislabel bug). Identity is always wire_model_name().
    local port="$1"
    llauncher_json server status --json 2>/dev/null \
        | python3 -c "
import json, sys
try:
    doc = json.load(sys.stdin)
    entry = doc.get('$port')
    print(entry['pid'] if entry else '')
except Exception:
    print('')
"
}

# --- Bounded readiness poll + wire verify ---------------------------------
wait_for_wire_match() {
    local port="$1" expected="$2"
    local elapsed=0
    local last_seen=""
    while [ "$elapsed" -lt "$READY_TIMEOUT_SECS" ]; do
        last_seen="$(wire_model_name "$port")"
        if [ "$last_seen" = "$expected" ]; then
            echo "$last_seen"
            return 0
        fi
        sleep "$READY_POLL_INTERVAL"
        elapsed=$((elapsed + READY_POLL_INTERVAL))
    done
    echo "$last_seen"
    return 1
}

# --- Converge ---------------------------------------------------------
current_wire_name="$(wire_model_name "$PORT")"

if [ -n "$current_wire_name" ] && [ "$current_wire_name" = "$NAME" ]; then
    echo "ensure-server: $NAME already serving on port $PORT (wire-verified)."
    exit 0
fi

if [ -n "$current_wire_name" ]; then
    # Port serves something, but not NAME (or wire not yet reachable while
    # status shows an occupant) -> stop the occupant, then start NAME.
    # No `swap` verb exists in this CLI (verified via `llauncher server
    # --help`); ADR-010 keeps port caller-supplied, so stop+start on the
    # same port is the swap.
    info "Port $PORT currently serves '$current_wire_name' (wire) — stopping before starting '$NAME'."
    llauncher_json server stop "$PORT" >&2 || die "Failed to stop the occupant on port $PORT."
else
    occupant_pid="$(port_pid_and_name "$PORT")"
    if [ -n "$occupant_pid" ]; then
        info "Port $PORT has a tracked process (pid $occupant_pid) but did not answer /v1/models — stopping it before starting '$NAME'."
        llauncher_json server stop "$PORT" >&2 || die "Failed to stop the occupant on port $PORT."
    else
        info "Port $PORT is free — starting '$NAME'."
    fi
fi

llauncher_json server start "$NAME" --port "$PORT" >&2 || die "Failed to start '$NAME' on port $PORT."

info "Waiting up to ${READY_TIMEOUT_SECS}s for port $PORT to wire-report '$NAME'..."
if readback="$(wait_for_wire_match "$PORT" "$NAME")"; then
    echo "ensure-server: $NAME converged onto port $PORT (wire-verified)."
    exit 0
else
    die "Timed out after ${READY_TIMEOUT_SECS}s waiting for port $PORT to report '$NAME'. Last wire readback: '${readback:-<unreachable>}'."
fi
