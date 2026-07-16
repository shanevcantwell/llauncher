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
#
# Issue #298 (follow-up from #296 review): the collision set above must
# also catch a collision produced WITHIN the same migration pass — two
# legacy same-key lines (e.g. two ``LAUNCHER_AGENT_TOKEN=`` lines) with NO
# pre-existing canonical line. The original snapshot-once ``canonical_keys``
# only knew about canonical lines already in the file, so both legacy lines
# migrated and the pass itself produced two canonical lines. The fix walks
# legacy lines in file order and grows the canonical set as each one
# migrates, so the second occurrence of a same-pass collision is caught and
# dropped just like a pre-existing one.

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

    local legacy_keys
    legacy_keys="$(grep -E '^[[:space:]]*LAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=' "$env_file" 2>/dev/null \
        | sed -E 's/^[[:space:]]*(LAUNCHER_AGENT_[A-Za-z0-9_]*).*/\1/' \
        | sort -u || true)"

    [ -z "$legacy_keys" ] && return 0

    # Two-phase seed-then-grow, mirroring MigrateEnvKeys.ps1 exactly: the
    # file is read TWICE by one awk program. Pass 1 (NR == FNR) only seeds
    # `seen` with every pre-existing canonical key in the WHOLE file — so a
    # canonical line wins the collision regardless of whether it appears
    # before or after its legacy twin in file order (#285). Pass 2 rewrites:
    # each migrated legacy key is added to `seen` the instant it migrates,
    # so a LATER same-key legacy line — a same-pass collision, no
    # pre-existing canonical line required — is dropped identically (#298).
    # Emits two summary lines on fd 3/4 (migrated/dropped, "OLD -> NEW"
    # pairs) for the caller to report.
    local tmp
    tmp="$(mktemp "${env_file}.migrate.XXXXXX")"
    exec 3>"${tmp}.migrated" 4>"${tmp}.dropped"
    awk '
        NR == FNR {
            if ($0 ~ /^[[:space:]]*LLAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=/) {
                k = $0
                sub(/^[[:space:]]*/, "", k)
                sub(/[[:space:]]*=.*/, "", k)
                seen[k] = 1
            }
            next
        }
        /^[[:space:]]*LAUNCHER_AGENT_[A-Za-z0-9_]*[[:space:]]*=/ {
            k = $0
            sub(/^[[:space:]]*/, "", k)
            sub(/[[:space:]]*=.*/, "", k)
            newk = "L" k
            if (newk in seen) {
                print k " -> " newk > "/dev/fd/4"
                next
            }
            seen[newk] = 1
            print k " -> " newk > "/dev/fd/3"
            lead = $0; sub(/[^[:space:]].*/, "", lead)
            rest = $0; sub(/^[[:space:]]*LAUNCHER_AGENT_/, "", rest)
            print lead "LLAUNCHER_AGENT_" rest
            next
        }
        { print }
        ' "$env_file" "$env_file" > "$tmp"
    exec 3>&- 4>&-
    cat "$tmp" > "$env_file"  # preserve inode/perms of $env_file

    local migrated=() dropped=() line
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        migrated+=("$line")
    done < "${tmp}.migrated"
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        dropped+=("$line")
    done < "${tmp}.dropped"
    rm -f "$tmp" "${tmp}.migrated" "${tmp}.dropped"

    if [ "${#migrated[@]}" -gt 0 ]; then
        _env_migrate_say "Migrated pre-#139 legacy keys in $env_file: $(_join_comma "${migrated[@]}")"
    fi
    if [ "${#dropped[@]}" -gt 0 ]; then
        _env_migrate_say "Dropped ${#dropped[@]} pre-#139 legacy line(s) in $env_file whose migrated key already existed (canonical line wins, including a same-pass collision; issue #285/#298): $(_join_comma "${dropped[@]}")"
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
