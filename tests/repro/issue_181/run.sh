#!/usr/bin/env bash
# Deterministic, GPU-free repro driver for llauncher issue #181.
#
# Runs each defect's repro module in sequence and prints REPRODUCED / NOT
# per defect. Exits non-zero if ANY defect fails to reproduce. Safe to run
# repeatedly: every module is hermetic (temp run/audit/log dirs, port 18181,
# fake llama-server stand-ins) and reaps everything it spawns.
#
# SAFETY: this script does NOT invoke broad pytest and NEVER touches real
# ports 8081/8082, the real llama.cpp binary, the GPU, or the running
# llauncher agent. It only runs the three repro modules below.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

# Prefer the project venv python if present; fall back to python3.
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

# Make the repro package importable (PYTHONPATH adds HERE for _repro_lib and
# REPO_ROOT for the real llauncher package).
export PYTHONPATH="$HERE:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

declare -a MODULES=(
  "A1:zombie blindness -> eject no-op:repro_a1_zombie_blindness.py"
  "B:model_path collision misattribution:repro_b_model_path_misattribution.py"
  "C:bogus uptime (start_time=now):repro_c_bogus_uptime.py"
)

FAIL=0
echo "=== llauncher issue #181 repro set (GPU-free, deterministic) ==="
for entry in "${MODULES[@]}"; do
  id="${entry%%:*}"
  rest="${entry#*:}"
  desc="${rest%%:*}"
  mod="${rest##*:}"
  echo
  echo "--- Defect $id: $desc ---"
  if "$PY" "$HERE/$mod"; then
    echo "Defect $id: REPRODUCED ✓"
  else
    echo "Defect $id: NOT-REPRODUCED ✗"
    FAIL=1
  fi
done

echo
echo "=== leak check (no fake llama-server / port 18181 may survive) ==="
LEAKS="$(pgrep -af 'llama-server' 2>/dev/null \
  | grep -v 'shell-snapshots' \
  | grep -v 'pgrep' \
  | grep -E -- '--port 18181|/fake/issue181' || true)"
if [[ -n "$LEAKS" ]]; then
  echo "LEAKED fake processes detected:"
  echo "$LEAKS"
  FAIL=1
else
  echo "no leaked fakes ✓"
fi

echo
if [[ "$FAIL" -eq 0 ]]; then
  echo "ALL DEFECTS REPRODUCED ✓ — no leaks"
else
  echo "ONE OR MORE DEFECTS DID NOT REPRODUCE (or a leak was found) ✗"
fi
exit "$FAIL"
