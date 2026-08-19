# shellcheck shell=bash
# lib-readiness.sh — bounded post-start readiness poll, shared by
# install.sh (#420) and install-ui.sh (#421).
#
# Defect class (#308, #400/#413, this pair): `systemctl restart` -> `sleep
# N` -> `is-active --quiet` confirms only that the unit didn't crash in the
# sleep window, not that the process bound its port and actually serves.
# Mirrors the wait_for_server_ready idiom already used elsewhere in this
# repo (llauncher/core/process.py) and the bounded-poll shape of
# scripts/systemd/ensure-server.sh (merged same day, #424): poll, don't
# sleep-and-hope; on timeout, surface the actual last readback plus a
# journal tail — never a bare "failed".
#
# Sourced-only: defines functions, runs nothing at source time (matches
# migrate_env_keys.sh's convention). Callers must already have `info`/`err`
# (or equivalent) defined; this file writes poll progress via `info` when
# present, else falls back to stderr directly so it stays usable standalone
# (e.g. under a test harness that sources it without the full installer).
#
# Environment (readiness poll tuning, same names/shape as ensure-server.sh):
#   READINESS_TIMEOUT_SECS   seconds to wait for a 200 (default 30).
#   READINESS_POLL_INTERVAL  seconds between poll attempts (default 1).

# _readiness_log <msg>
# Uses the caller's `info` if defined, else stderr — keeps this file usable
# when sourced outside install.sh/install-ui.sh's own helper definitions.
_readiness_log() {
    if declare -F info >/dev/null 2>&1; then
        info "$1"
    else
        echo "$1" >&2
    fi
}

# wait_for_http_ready <url> [timeout_secs] [poll_interval_secs]
#
# Bounded-poll GET <url> until it returns HTTP 200, or the timeout elapses.
# Uses `curl -fsS` with a short per-attempt timeout so one hung attempt
# cannot itself blow the overall budget. Prints the last observed HTTP
# status (or "unreachable" if curl never connected) to stdout on EITHER
# path, so the caller always has a concrete readback to report — success
# path included, for parity with ensure-server.sh's wire-verify idiom.
#
# Returns 0 on a 200 within the budget, 1 on timeout.
wait_for_http_ready() {
    local url="$1"
    local timeout_secs="${2:-${READINESS_TIMEOUT_SECS:-30}}"
    local poll_interval="${3:-${READINESS_POLL_INTERVAL:-1}}"

    local elapsed=0
    local last_status="unreachable"
    while [ "$elapsed" -lt "$timeout_secs" ]; do
        last_status="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo "unreachable")"
        if [ "$last_status" = "200" ]; then
            echo "$last_status"
            return 0
        fi
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    echo "$last_status"
    return 1
}

# dump_journal_tail <journalctl_scope_array_name> <unit_name> [lines]
#
# Prints the last N lines (default 20) of the unit's journal, using the
# caller's already-resolved --user/--system JOURNALCTL array (so scope
# stays correct no matter which installer called in). Takes the array by
# NAME (nameref) rather than by value so callers don't have to re-quote a
# multi-element array through a string.
dump_journal_tail() {
    # `local -n` (nameref) requires bash >= 4.3; host floor is 5.2 (this
    # comment is the enforcement surface for now).
    local -n _journalctl_scope="$1"
    local unit_name="$2"
    local lines="${3:-20}"
    "${_journalctl_scope[@]}" -u "$unit_name" -n "$lines" --no-pager || true
}

# verify_http_readiness <url> <journalctl_scope_array_name> <unit_name> [timeout_secs] [poll_interval_secs]
#
# Full success/failure ritual shared by both installers:
#   1. bounded-poll <url> for a 200
#   2. on success: log the readback, return 0
#   3. on timeout: log the last readback, dump the journal tail, return 1
#      (caller still owns `exit 1` — this function never exits the shell,
#      so it stays usable from a test harness that sources it).
verify_http_readiness() {
    local url="$1"
    local journalctl_scope_name="$2"
    local unit_name="$3"
    local timeout_secs="${4:-${READINESS_TIMEOUT_SECS:-30}}"
    local poll_interval="${5:-${READINESS_POLL_INTERVAL:-1}}"

    _readiness_log "Waiting up to ${timeout_secs}s for $url to answer 200..."
    local readback
    if readback="$(wait_for_http_ready "$url" "$timeout_secs" "$poll_interval")"; then
        _readiness_log "Readiness confirmed: $url -> HTTP $readback."
        return 0
    fi

    if declare -F err >/dev/null 2>&1; then
        err "$unit_name did not become ready within ${timeout_secs}s (last readback from $url: HTTP $readback)."
        err "Last 20 log lines:"
    else
        echo "$unit_name did not become ready within ${timeout_secs}s (last readback from $url: HTTP $readback)." >&2
        echo "Last 20 log lines:" >&2
    fi
    dump_journal_tail "$journalctl_scope_name" "$unit_name" 20
    return 1
}
