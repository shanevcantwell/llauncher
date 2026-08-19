# shellcheck shell=bash
# Interpreter-floor guard, extracted so it is unit-testable in isolation
# (tests/unit/test_check_python_floor.py) without driving a full installer.
# Sourced by scripts/systemd/install.sh and scripts/systemd/install-cli.sh;
# defines one function and runs nothing at source time.
#
# Issue #334: pyproject.toml declares `requires-python = ">=3.11"` but no
# installer checked it — a <3.11 interpreter installed silently and failed
# later at import time (trust-and-degrade instead of fail-loud;
# PARSE-AT-THE-DOOR applied to prerequisites). This is the shared check both
# bash installers call BEFORE creating/using a venv with the found
# interpreter.

# check_python_floor <python_bin> <required_major> <required_minor>
#
# Exits nonzero with a loud message naming the found version and the
# required floor if <python_bin> is missing or below the floor. Silent
# (returns 0) when the interpreter satisfies the floor.
check_python_floor() {
    local python_bin="$1"
    local required_major="$2"
    local required_minor="$3"

    if ! command -v "$python_bin" >/dev/null 2>&1; then
        _python_floor_err "$python_bin not found."
        exit 1
    fi

    local found
    found="$("$python_bin" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || {
        _python_floor_err "Could not determine the $python_bin interpreter version."
        exit 1
    }

    local found_major="${found%%.*}"
    # Take the second dotted field only. The producer emits a fixed 2-part
    # "%d.%d", but strip-shortest-prefix (${found#*.}) would carry a trailing
    # ".patch" into found_minor if that ever drifts to 3 parts, feeding a
    # non-integer to the `-lt` arithmetic test and hard-failing under set -e.
    local found_minor="${found#*.}"
    found_minor="${found_minor%%.*}"

    if [ "$found_major" -lt "$required_major" ] || { [ "$found_major" -eq "$required_major" ] && [ "$found_minor" -lt "$required_minor" ]; }; then
        _python_floor_err "$python_bin is $found, but llauncher requires >=${required_major}.${required_minor} (pyproject.toml requires-python)."
        _python_floor_err "Install a Python >=${required_major}.${required_minor} interpreter and re-run this installer."
        exit 1
    fi
}

# Use the caller's `err` when it defines one (install.sh); otherwise plain
# stderr (install-cli.sh has no `err` helper, and standalone/test callers).
_python_floor_err() {
    if command -v err >/dev/null 2>&1; then
        err "$1"
    else
        printf '%s\n' "$1" >&2
    fi
}
