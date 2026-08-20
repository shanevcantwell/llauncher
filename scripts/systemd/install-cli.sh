#!/usr/bin/env bash
# install-cli.sh — system install of the llauncher CLI from public origin/main.
# ----------------------------------------------------------------------------
# Installs `llauncher` (+ llauncher-agent, llauncher-mcp, llauncher-ui) onto
# the system PATH for ALL accounts, pulled from the PUBLIC GitHub repo —
# decoupled from any dev checkout and from any dev .venv. A dedicated venv
# lives at /opt/llauncher/venv (its own Python, NOT added to PATH); only the
# console-script entry points are symlinked into /usr/local/bin (already on
# every account's PATH). No venv bin directory is ever placed on PATH.
#
# This IS the compose ritual (#357 ratified Option A, issue #360): the
# systemd deployment (agent user unit + UI user unit) resolves ExecStart
# through these symlinks into this PINNED venv, independent of any operator's
# state or any clone's working-tree state. Recompose = re-run this script;
# that IS the deploy event. See docs/operations/run-as-a-service.md,
# "Composing the pinned runtime venv", for the full grant-window ritual this
# script is the composing step of.
#
# Usage (root):
#   sudo bash scripts/systemd/install-cli.sh                 # install/refresh from main
#   sudo REF=v0.4.0-alpha bash scripts/systemd/install-cli.sh  # pin a tag/branch/SHA
#   sudo bash scripts/systemd/install-cli.sh --uninstall
#
# Re-runnable: re-running refreshes the install to the current REF.
#
# Pairs with a system-wide state dir so the CLI sees live system state:
#   echo 'LAUNCHER_STATE_DIR=/var/lib/llauncher' | sudo tee /etc/profile.d/llauncher.sh
#   sudo usermod -aG inference <user>   # read access to /var/lib/llauncher
# ----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/systemd/venv_manifest.sh
. "$SCRIPT_DIR/venv_manifest.sh"
# shellcheck source=scripts/systemd/check_python_floor.sh
. "$SCRIPT_DIR/check_python_floor.sh"

REPO_URL="https://github.com/shanevcantwell/llauncher.git"
REF="${REF:-main}"
PREFIX="/opt/llauncher"
VENV="$PREFIX/venv"
BINDIR="/usr/local/bin"
MANIFEST="$PREFIX/venv-manifest.txt"
SCRIPTS=(llauncher llauncher-agent llauncher-mcp llauncher-ui)

[ "$(id -u)" -eq 0 ] || { echo "FATAL: run as root (sudo bash $0)"; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    for s in "${SCRIPTS[@]}"; do rm -f "$BINDIR/$s" && echo "removed $BINDIR/$s"; done
    rm -rf "$PREFIX" && echo "removed $PREFIX"
    echo "Uninstalled."
    exit 0
fi

command -v python3 >/dev/null || { echo "FATAL: python3 not found"; exit 1; }
command -v git     >/dev/null || { echo "FATAL: git not found (needed for the git+https install)"; exit 1; }

# Interpreter floor (issue #334): pyproject.toml declares
# `requires-python = ">=3.11"`, but a bare `command -v python3` above only
# proves SOME python3 exists, not that it clears the floor. A <3.11
# interpreter would build the venv below, then fail later at import time
# (trust-and-degrade instead of fail-loud). Check before `python3 -m venv`.
check_python_floor python3 3 11

echo "==> dedicated venv at $VENV (own Python; independent of any dev .venv; not on PATH)"
mkdir -p "$PREFIX"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip

echo "==> installing llauncher from public $REPO_URL @ $REF (non-editable)"
"$VENV/bin/pip" install --quiet --upgrade "llauncher @ git+$REPO_URL@$REF"

echo "==> recording the pin: $MANIFEST"
# The answerable-at-any-time pin (#360): exactly what this composition
# installed, so "what is /opt/llauncher/venv running" is never a guess.
# Recomposing (re-running this script) overwrites the manifest along with
# the venv — the manifest always describes the CURRENT /opt/llauncher/venv,
# never a stale prior composition. Logic lives in venv_manifest.sh so it is
# unit-testable without the network install above.
write_venv_manifest "$VENV/bin/pip" "$MANIFEST" "$REF"

echo "==> symlinking console scripts into $BINDIR (venv bin stays off PATH)"
for s in "${SCRIPTS[@]}"; do
    ln -sfn "$VENV/bin/$s" "$BINDIR/$s"
    echo "    $BINDIR/$s -> $VENV/bin/$s"
done

# world-readable/executable so every account can run the shared install
# (venv-manifest.txt is a plain file under $PREFIX, so it inherits the
# same read grant — the pin is answerable by any account, not just root).
chmod -R a+rX "$PREFIX"

echo "==> verify"
"$VENV/bin/python" -c "import llauncher; print('    installed llauncher', llauncher.__version__)"
for s in "${SCRIPTS[@]}"; do command -v "$s" >/dev/null && echo "    ok: $s resolves on PATH"; done
echo "Done — 'llauncher' is available to all accounts via $BINDIR (from public $REF), with no dev-.venv dependency."
