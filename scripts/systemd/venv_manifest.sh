# shellcheck shell=bash
# Pinned-venv composition manifest, extracted so it is unit-testable in
# isolation (tests/unit/test_install_cli_manifest.py) without driving the
# full install-cli.sh network install (venv creation + `pip install` from
# git+https). Sourced by scripts/systemd/install-cli.sh; defines one
# function and runs nothing at source time.
#
# #357 ratified Option A (issue #360): the compose ritual records exactly
# what it installed to /opt/llauncher/venv-manifest.txt — "the pin,
# answerable at any time" (the issue's own words) rather than a guess.
# Recompose = re-run the ritual, which overwrites the manifest so it always
# describes the CURRENT pinned venv, never a stale prior composition.

write_venv_manifest() {
    # Args: $1 = path to the venv's pip binary, $2 = manifest destination
    # path, $3 = the git ref that was installed.
    local pip_bin="$1"
    local manifest_path="$2"
    local ref="$3"

    {
        echo "# llauncher /opt/llauncher/venv composition manifest"
        echo "# Written by scripts/systemd/install-cli.sh — do not hand-edit."
        echo "# ref: $ref"
        echo "# composed: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        "$pip_bin" freeze
    } > "$manifest_path"
}
