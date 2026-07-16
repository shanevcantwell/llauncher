# Pre-#139 legacy-key migration + dedupe for the Windows installer,
# extracted so it is unit-testable in isolation (Pester / a pwsh harness)
# without driving install.ps1's ACL/venv steps. Dot-sourced by
# scripts/windows/install.ps1; defines one function and runs nothing at
# dot-source time.
#
# Issue #285 (installer half of the "403s keep coming back" recurrence,
# paired with #293's runtime half): the pre-#139 rename migrates
# LAUNCHER_AGENT_* -> LLAUNCHER_AGENT_* IN PLACE, but with no dedupe a
# legacy line whose migrated key already exists as a canonical line becomes
# a DUPLICATE. PARSE-AT-THE-DOOR: migrate in place once, and a legacy line
# colliding with an existing canonical key is DROPPED (never rewritten into
# a second line), loudly. This mirrors migrate_env_keys.sh exactly so both
# installers resolve duplicates identically (issue #285).
#
# Issue #298 (follow-up from #296 review): the collision set must also
# catch a collision produced WITHIN the same migration pass — two legacy
# same-key lines (e.g. two ``LAUNCHER_AGENT_TOKEN=`` lines) with NO
# pre-existing canonical line. The original snapshot-once $canonicalKeys
# only knew about canonical lines already present, so both legacy lines
# migrated and the pass itself produced two canonical lines. The fix grows
# $canonicalKeys as each legacy line migrates (in file order), so a second
# same-key legacy line — whether colliding with a pre-existing canonical
# line or with the first line of a same-pass pair — is dropped identically.

function Invoke-EnvKeyMigration {
    <#
    .SYNOPSIS
        Migrate + dedupe pre-#139 legacy LAUNCHER_AGENT_* keys in a set of
        env-file lines.
    .DESCRIPTION
        Pure over its input: takes the file's lines, returns an ordered
        hashtable-like PSCustomObject with:
          - Lines    : the rewritten line array (write these back)
          - Migrated : "OLD -> NEW" strings for legacy lines migrated
          - Dropped  : "OLD -> NEW" strings for legacy lines dropped because
                       their migrated key already existed canonically
        A legacy line whose migrated key already appears as a canonical
        (LLAUNCHER_AGENT_) line is DROPPED; the canonical line wins. Comment
        lines and non-matching lines pass through untouched, order preserved.
    .PARAMETER Lines
        The env file's lines (e.g. @(Get-Content $EnvFile)).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]]$Lines
    )

    # Canonical keys already present (post-rename form) win any collision.
    $canonicalKeys = New-Object System.Collections.Generic.HashSet[string]
    foreach ($line in $Lines) {
        if ($line -match '^\s*(LLAUNCHER_AGENT_[A-Za-z0-9_]*)\s*=') {
            [void]$canonicalKeys.Add($Matches[1])
        }
    }

    $out = New-Object System.Collections.Generic.List[string]
    $migrated = @()
    $dropped = @()
    foreach ($line in $Lines) {
        # Single-L legacy line? (LLAUNCHER already matched the canonical set;
        # the negative lookahead keeps a canonical LLAUNCHER_ line from
        # matching this legacy branch.)
        if ($line -match '^(\s*)LAUNCHER_AGENT_([A-Za-z0-9_]*)\s*=') {
            $oldKey = ($line -split '=', 2)[0].Trim()
            $newKey = 'L' + $oldKey  # LAUNCHER_ -> LLAUNCHER_
            if ($canonicalKeys.Contains($newKey)) {
                # Migrated key already exists canonically -> DROP this line.
                $dropped += "$oldKey -> $newKey"
                continue
            }
            $newLine = $line -replace '^(\s*)LAUNCHER_AGENT_', '${1}LLAUNCHER_AGENT_'
            $out.Add($newLine)
            $migrated += "$oldKey -> $newKey"
            # Grow the canonical set immediately (#298) so a LATER legacy
            # line with this same key — a same-pass collision, no
            # pre-existing canonical line required — is dropped too.
            [void]$canonicalKeys.Add($newKey)
        }
        else {
            $out.Add($line)
        }
    }

    return [PSCustomObject]@{
        Lines    = $out.ToArray()
        Migrated = $migrated
        Dropped  = $dropped
    }
}
