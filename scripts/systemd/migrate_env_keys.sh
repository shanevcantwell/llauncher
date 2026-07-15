# shellcheck shell=bash
# Pre-#139 legacy-key migration + dedupe, extracted so it is unit-testable
# in isolation (tests/unit/test_install_sh_dedupe.py) without driving the
# full systemd installer's venv/systemctl preflight. Sourced by
# scripts/systemd/install.sh; defines one function and runs nothing at
# source time.
#
# Issue #285 (installer half of the "403s keep coming back" recurrence,
# paired with #293's runtime half): the pre-#139 rename migrates
# LAUNCHER_AGENT_* → LLAUNCHER_AGENT_* IN PLACE, but with no dedupe a
# legacy line whose migrated key already exists as a canonical line becomes
# a DUPLICATE. For LLAUNCHER_AGENT_TOKEN that duplicate is a split-brain
# waiting to happen (the runtime, install.ps1, and install.sh all resolve
# last-wins as of d5f83b9, but two token lines is still a footgun the
# operator can trip by hand-editing). PARSE-AT-THE-DOOR: migrate in place
# once, and a legacy line colliding with an existing canonical key is
# DROPPED (never rewritten into a second line), loudly.

# migrate_and_dedupe_env_keys <env_file>
#
# Rewrites <env_file> in place:
#   - Each legacy `LAUNCHER_AGENT_<K>=...` line whose migrated key
#     `LLAUNCHER_AGENT_<K>` does NOT already appear (as a canonical line)
#     in the file is migrated in place, value preserved byte-for-byte.
#   - A legacy line whose migrated key ALREADY appears canonically is
#     DROPPED (the existing canonical line wins), with a loud message on
#     stderr naming the dropped line.
#
# Emits a `say`/`info`-style summary via the caller's `say` function when
# it exists (install.sh defines it); falls back to stderr otherwise so the
# function is usable standalone in tests.
#
# Comment lines (`#…`) never match the key-anchored pattern and pass
# through untouched. Line order is preserved for surviving lines, so the
# runtime's last-wins EnvironmentFile semantics are unaffected.
migrate_and_dedupe_env_keys() {
    local env_file="$1"

    # Canonical keys already present (post-rename form) — these win any
    # collision with a migrated legacy line.
    local canonical_keys
    canonical_keys="$(grep -E '^[[:space:]]*LLAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=' "$env_file" 2>/dev/null \
        | sed -E 's/^[[:space:]]*(LLAUNCHER_AGENT_[A-Za-z0-9_]*).*/\1/' \
        | sort -u || true)"

    local legacy_keys
    legacy_keys="$(grep -E '^[[:space:]]*LAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=' "$env_file" 2>/dev/null \
        | sed -E 's/^[[:space:]]*(LAUNCHER_AGENT_[A-Za-z0-9_]*).*/\1/' \
        | sort -u || true)"

    [ -z "$legacy_keys" ] && return 0

    local migrated=() dropped=() legacy_key migrated_key
    while IFS= read -r legacy_key; do
        [ -z "$legacy_key" ] && continue
        migrated_key="L$legacy_key"  # LAUNCHER_ -> LLAUNCHER_
        if printf '%s\n' "$canonical_keys" | grep -qxF "$migrated_key"; then
            dropped+=("$legacy_key -> $migrated_key")
        else
            migrated+=("$legacy_key -> $migrated_key")
        fi
    done <<EOF
$legacy_keys
EOF

    # Rewrite the file in a single awk pass so the read and the write never
    # race and the temp swap is atomic. For each legacy line: drop if its
    # migrated key is in the collision set, else migrate the prefix.
    local tmp drop_keys=""
    if [ "${#dropped[@]}" -gt 0 ]; then
        drop_keys="$(printf '%s\n' "${dropped[@]}" | sed -E 's/ -> .*//')"
    fi
    tmp="$(mktemp "${env_file}.migrate.XXXXXX")"
    DROP_KEYS="$drop_keys" \
        awk '
        BEGIN {
            n = split(ENVIRON["DROP_KEYS"], arr, "\n")
            for (i = 1; i <= n; i++) if (arr[i] != "") drop[arr[i]] = 1
        }
        {
            line = $0
            if (line ~ /^[[:space:]]*LAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=/) {
                k = line
                sub(/^[[:space:]]*/, "", k)
                sub(/[[:space:]]*=.*/, "", k)
                if (k in drop) next        # collides with canonical -> drop
                lead = line; sub(/[^[:space:]].*/, "", lead)
                rest = line; sub(/^[[:space:]]*LAUNCHER_AGENT_/, "", rest)
                print lead "LLAUNCHER_AGENT_" rest
                next
            }
            print line
        }
        ' "$env_file" > "$tmp"
    cat "$tmp" > "$env_file"  # preserve inode/perms of $env_file
    rm -f "$tmp"

    if [ "${#migrated[@]}" -gt 0 ]; then
        _env_migrate_say "Migrated pre-#139 legacy keys in $env_file: $(_join_comma "${migrated[@]}")"
    fi
    if [ "${#dropped[@]}" -gt 0 ]; then
        _env_migrate_say "Dropped ${#dropped[@]} pre-#139 legacy line(s) in $env_file whose migrated key already existed (canonical line wins; issue #285): $(_join_comma "${dropped[@]}")"
    fi
}

# Join array elements with ", ".
_join_comma() {
    local out="" x
    for x in "$@"; do
        if [ -z "$out" ]; then out="$x"; else out="$out, $x"; fi
    done
    printf '%s' "$out"
}

# Use install.sh's `say` when sourced by it; otherwise stderr (standalone/test).
_env_migrate_say() {
    if command -v say >/dev/null 2>&1; then
        say "$1"
    else
        printf '%s\n' "$1" >&2
    fi
}
