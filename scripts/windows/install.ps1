# Install / refresh the llauncher-agent Windows service via NSSM.
#
# Prerequisites:
#   - NSSM (Non-Sucking Service Manager), resolved via a fallback chain
#     (issue #352): $env:NSSM override -> PATH -> choco bin shim -> choco
#     lib payload -> scoop shim. PATH is not required if nssm is findable
#     via one of the other locations (e.g. a Windows Update dropped the
#     choco bin dir from PATH but nssm.exe is still sitting right there).
#     Download from https://nssm.cc/ or `choco install nssm` / `scoop
#     install nssm`.
#   - Project venv created via `scripts\run.bat install`.
#   - This script must run elevated (right-click "Run as Administrator").
#
# Idempotent: re-running picks up env-file edits and a new venv path.
# Will NOT overwrite an existing env file (your token survives), but pre-#139
# legacy LAUNCHER_AGENT_* key names ARE migrated in place to LLAUNCHER_AGENT_*
# (values preserved; issue #281).
#
# Single live source (issue #284): %USERPROFILE%\.llauncher\agent.env is
# read DIRECTLY by both the agent service and the UI at startup -- there is
# no installer-time snapshot and no agent.token mirror file any more. This
# script's job on every run is: seed agent.env ONCE if absent, migrate a
# stale agent.token into it if found, inject LAUNCHER_STATE_DIR into the
# NSSM service env (see the LocalSystem note below), and pass through
# interpreter-level vars via NSSM AppEnvironmentExtra. Editing
# agent.env.example after first install does nothing -- it is a
# seed-once template, not live config.
#
# LocalSystem wrinkle: NSSM defaults new services to the LocalSystem
# account, whose Path.home() does NOT resolve to the installing operator's
# profile. Without help, the service process would resolve a DIFFERENT
# agent.env than the one the operator's UI reads. This script injects
# LAUNCHER_STATE_DIR=<installing user's %USERPROFILE%\.llauncher> into
# AppEnvironmentExtra so the service converges on the same file.
#
# Usage:
#   .\scripts\windows\install.ps1
#   .\scripts\windows\install.ps1 -NoStart
#   .\scripts\windows\install.ps1 -Uninstall

[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$ServiceName = 'llauncher-agent'
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir  = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$VenvExe     = Join-Path $ProjectDir '.venv\Scripts\llauncher-agent.exe'
$VenvPython  = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$EnvDir      = Join-Path $env:USERPROFILE '.llauncher'
$EnvFile     = Join-Path $EnvDir 'agent.env'
$TokenFile   = Join-Path $EnvDir 'agent.token'
$LogDir      = Join-Path $env:USERPROFILE '.llauncher\logs'
$EnvExample  = Join-Path $ScriptDir 'agent.env.example'

function Say  ($msg) { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Info ($msg) { Write-Host "[..]  $msg" -ForegroundColor Yellow }
function Warn ($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Red }
function Die  ($msg) { Write-Host "[!!]  $msg" -ForegroundColor Red; exit 1 }

# --- Elevation check --------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "This script must be run from an elevated (Administrator) PowerShell."
}

# --- Locate nssm --------------------------------------------------------
# PATH is not the only place nssm legitimately lives (issue #352): a
# Windows Update can silently drop C:\ProgramData\chocolatey\bin from the
# system PATH without touching choco's own install -- nssm.exe (and choco
# itself) keep working fine from an elevated prompt that still has the
# entry, but a fresh/other shell's `Get-Command nssm.exe` comes back empty
# and the pre-#352 installer misdiagnosed that as "nssm not installed",
# sending operators to reinstall tooling that was never missing (the bogus
# precondition behind PR #345). Resolve through a fallback chain instead,
# first match wins, and probe every candidate so a genuine absence can name
# everywhere it looked:
#   1. $env:NSSM override (explicit operator escape hatch, pre-#352)
#   2. nssm.exe on PATH (Get-Command)
#   3. the choco shim: C:\ProgramData\chocolatey\bin\nssm.exe
#   4. the newest nssm.exe under choco's package payload:
#      C:\ProgramData\chocolatey\lib\nssm\tools\**\nssm.exe
#   5. scoop shim: %USERPROFILE%\scoop\shims\nssm.exe
$nssmCandidates = [System.Collections.Generic.List[string]]::new()
$nssm = $null
$nssmSource = $null

if ($env:NSSM) {
    $nssmCandidates.Add($env:NSSM)
    if (Test-Path $env:NSSM) {
        $nssm = $env:NSSM; $nssmSource = '$env:NSSM override'
    } else {
        # Loud, not silent (#352 review): an operator who set $env:NSSM
        # expected it to be used. Falling through to the next candidate
        # without a word would leave them wondering why their override was
        # ignored -- name the invalid path before continuing the chain.
        Warn "`$env:NSSM is set to '$($env:NSSM)' but that path does not exist -- falling through to the next candidate."
    }
}

if (-not $nssm) {
    $onPath = (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source
    if ($onPath) {
        $nssmCandidates.Add($onPath)
        $nssm = $onPath
        $nssmSource = 'PATH'
    } else {
        $nssmCandidates.Add('nssm.exe (via PATH / Get-Command)')
    }
}

$chocoBinNssm = 'C:\ProgramData\chocolatey\bin\nssm.exe'
if (-not $nssm) {
    $nssmCandidates.Add($chocoBinNssm)
    if (Test-Path $chocoBinNssm) { $nssm = $chocoBinNssm; $nssmSource = 'chocolatey bin shim' }
}

$chocoLibNssmGlob = 'C:\ProgramData\chocolatey\lib\nssm\tools\**\nssm.exe'
if (-not $nssm) {
    $nssmCandidates.Add($chocoLibNssmGlob)
    $chocoLibRoot = 'C:\ProgramData\chocolatey\lib\nssm\tools'
    if (Test-Path $chocoLibRoot) {
        # The choco nssm package ships both win32 and win64 payloads with
        # identical (or near-identical, within filesystem timestamp
        # resolution) LastWriteTime -- Sort-Object -Descending alone leaves
        # that tie's winner up to PowerShell 5.1's unstable sort, which can
        # silently pick the 32-bit binary on one run and the 64-bit binary
        # on the next. Prefer win64 deterministically as the primary sort
        # key, falling back to LastWriteTime to break any remaining tie.
        $newestLibNssm = Get-ChildItem -Path $chocoLibRoot -Filter 'nssm.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object -Property @(
                @{ Expression = { if ($_.FullName -match 'win64') { 0 } else { 1 } }; Descending = $false },
                @{ Expression = 'LastWriteTime'; Descending = $true }
            ) |
            Select-Object -First 1
        if ($newestLibNssm) { $nssm = $newestLibNssm.FullName; $nssmSource = 'chocolatey lib payload' }
    }
}

$scoopShimNssm = Join-Path $env:USERPROFILE 'scoop\shims\nssm.exe'
if (-not $nssm) {
    $nssmCandidates.Add($scoopShimNssm)
    if (Test-Path $scoopShimNssm) { $nssm = $scoopShimNssm; $nssmSource = 'scoop shim' }
}

if (-not $nssm -or -not (Test-Path $nssm)) {
    $tried = ($nssmCandidates | ForEach-Object { "  - $_" }) -join "`n"
    Die @"
nssm.exe not found. Probed, in order (first match wins):
$tried

If nssm IS installed but off PATH (e.g. a Windows Update dropped
C:\ProgramData\chocolatey\bin from PATH -- issue #352), either restore that
PATH entry or set `$env:NSSM = '<path>\nssm.exe'` and re-run. Only install
fresh tooling if none of the above actually resolves:
  choco install nssm
  scoop install nssm
  https://nssm.cc/download (then add to PATH, or set `$env:NSSM`)
"@
}
Say "Using NSSM at $nssm (resolved via $nssmSource)"

# --- Uninstall path ---------------------------------------------------
if ($Uninstall) {
    if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
        Info "Stopping $ServiceName..."
        & $nssm stop $ServiceName confirm | Out-Null
        Info "Removing $ServiceName..."
        & $nssm remove $ServiceName confirm | Out-Null
        Say "Service removed."
    } else {
        Info "Service $ServiceName is not installed."
    }
    Info "Env file left at $EnvFile (delete manually if desired)."
    exit 0
}

# --- Preflight --------------------------------------------------------
if (-not (Test-Path $VenvExe)) {
    Die "Did not find $VenvExe.  Run 'scripts\run.bat install' first."
}

# --- Interpreter floor (issue #334) ------------------------------------
# pyproject.toml declares `requires-python = ">=3.11"`, but this installer
# only checked that the venv EXISTED, not that it was built on a floor-
# clearing interpreter -- a <3.11 venv installed silently and failed later
# at import time (trust-and-degrade instead of fail-loud; PARSE-AT-THE-DOOR
# applied to prerequisites). Check the venv's own python.exe before wiring
# the service to it.
$RequiredMajor = 3
$RequiredMinor = 11
$pyVersionOutput = & $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($LASTEXITCODE -ne 0 -or -not $pyVersionOutput) {
    Die "Could not determine the $VenvPython interpreter version."
}
$pyVersionParts = $pyVersionOutput.Trim().Split('.')
$foundMajor = [int]$pyVersionParts[0]
$foundMinor = [int]$pyVersionParts[1]
if ($foundMajor -lt $RequiredMajor -or ($foundMajor -eq $RequiredMajor -and $foundMinor -lt $RequiredMinor)) {
    Die @"
$VenvPython is $($pyVersionOutput.Trim()), but llauncher requires >=$RequiredMajor.$RequiredMinor (pyproject.toml requires-python).
Delete .venv, install a Python >=$RequiredMajor.$RequiredMinor interpreter, and re-run 'scripts\run.bat install'.
"@
}

# --- Env file (token + config) ----------------------------------------
if (-not (Test-Path $EnvDir))  { New-Item -ItemType Directory -Path $EnvDir  | Out-Null }
if (-not (Test-Path $LogDir))  { New-Item -ItemType Directory -Path $LogDir  | Out-Null }

# --- Migrate a pre-#284 agent.token mirror BEFORE any fresh seed ------
# Ordering matters: if agent.env is absent but a stale agent.token mirror
# still holds the operator's real, already-in-use token (e.g. agent.env
# was deleted/never synced while the mirror survived), the seed-from-
# template step below must NOT overwrite it with a newly generated
# random token -- that would silently orphan the live credential the
# mirror was carrying. So: if the mirror exists and carries a value,
# seed agent.env from THAT value instead of generating a fresh one, and
# skip the generate-new-token seed entirely.
$migratedMirrorToken = $null
if (Test-Path $TokenFile) {
    $mirrorToken = (Get-Content $TokenFile -Raw).Trim()
    if ($mirrorToken) {
        $migratedMirrorToken = $mirrorToken
    }
}

if (-not (Test-Path $EnvFile)) {
    if ($migratedMirrorToken) {
        Info "Seeding $EnvFile from the template using the live token found in the stale agent.token mirror..."
        # IMPORTANT: write WITHOUT a UTF-8 BOM -- Windows PowerShell 5.1's
        # `Set-Content -Encoding utf8` prepends EF BB BF, which would
        # corrupt the first key name (issue #127).
        $seedLines = @((Get-Content $EnvExample) `
            -replace 'replace-me-with-a-random-token', $migratedMirrorToken)
        [System.IO.File]::WriteAllLines(
            $EnvFile, $seedLines, (New-Object System.Text.UTF8Encoding($false)))
        Say "Wrote $EnvFile, carrying forward the token from $TokenFile (not overwritten with a fresh one)."
    } else {
        Info "Seeding $EnvFile from the template (one-time; see agent.env.example header)..."
        $token = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(32))"
        # IMPORTANT: write WITHOUT a UTF-8 BOM -- Windows PowerShell 5.1's
        # `Set-Content -Encoding utf8` prepends EF BB BF, which would
        # corrupt the first key name (issue #127).
        $seedLines = @((Get-Content $EnvExample) `
            -replace 'replace-me-with-a-random-token', $token.Trim())
        [System.IO.File]::WriteAllLines(
            $EnvFile, $seedLines, (New-Object System.Text.UTF8Encoding($false)))
        Say "Wrote $EnvFile with a generated 32-byte token."
    }
    Info "Edit it to set LLAUNCHER_AGENT_NODE_NAME / HOST / PORT as needed."
} else {
    # Loud on skip (issue #284): edits to the template are never read again
    # after this first-install seed -- agent.env is the only file either
    # process consults from here on.
    Info "agent.env already exists at $EnvFile -- skipping template seed."
    Info "  Edits to ${EnvExample} are never read after first install;"
    Info "  edit $EnvFile directly (the live source) and re-run this script."

    # --- Migrate pre-#139 legacy keys (issue #281), deduped (issue #285)
    # Commit 9f098d9 (#138/#139) renamed LAUNCHER_AGENT_* to
    # LLAUNCHER_AGENT_*, but env files written from the pre-rename
    # template still carry the single-L keys -- which nothing reads any
    # more, so the agent silently auto-generates its own token under the
    # SERVICE account's profile and the UI 403s on every authed endpoint.
    # PARSE-AT-THE-DOOR: rewrite the key prefix in place, once,
    # deterministically, preserving each value byte-for-byte.
    #
    # Issue #285: a blanket prefix rewrite created a DUPLICATE when a legacy
    # line's migrated key already existed as a canonical line -- the
    # installer half of the "403s keep coming back" recurrence (paired with
    # #293's runtime half). The migration now DROPS a legacy line whose
    # migrated key already exists (the canonical line wins), loudly. Logic
    # is extracted to MigrateEnvKeys.ps1 (mirrors migrate_env_keys.sh) so
    # both installers resolve duplicates identically and the logic is
    # unit-testable without install.ps1's ACL/NSSM steps.
    . (Join-Path $ScriptDir 'MigrateEnvKeys.ps1')
    $migration = Invoke-EnvKeyMigration -Lines @(Get-Content $EnvFile)
    if ($migration.Migrated.Count -gt 0 -or $migration.Dropped.Count -gt 0) {
        # IMPORTANT: write WITHOUT a UTF-8 BOM -- Windows PowerShell 5.1's
        # `Set-Content -Encoding utf8` prepends EF BB BF, which would
        # corrupt the first key name.
        [System.IO.File]::WriteAllLines(
            $EnvFile, $migration.Lines, (New-Object System.Text.UTF8Encoding($false)))
    }
    if ($migration.Migrated.Count -gt 0) {
        Say ("Migrated pre-#139 legacy keys in ${EnvFile}: " + ($migration.Migrated -join ', '))
    }
    if ($migration.Dropped.Count -gt 0) {
        Say ("Dropped $($migration.Dropped.Count) pre-#139 legacy line(s) in ${EnvFile} whose migrated key already existed (canonical line wins; issue #285): " + ($migration.Dropped -join ', '))
    }
}

# Lock the env file: remove inheritance, grant current user only.
$me = "$env:USERDOMAIN\$env:USERNAME"
function Set-OwnerOnlyAcl($path) {
    $acl = Get-Acl $path
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $me, 'FullControl', 'Allow')
    # Strip any inherited rules left over from the protection flip
    $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
    $acl.AddAccessRule($rule)
    Set-Acl -Path $path -AclObject $acl
}
Set-OwnerOnlyAcl $EnvFile
Say "Locked ACL on $EnvFile (current user only)."

# --- Retire the agent.token mirror (issue #284) ------------------------
# agent.env is now the single live source, parsed directly by both the
# service and the UI (llauncher.core.agent_token.resolve_agent_token) -- no
# installer-maintained mirror file. A stale agent.token from a pre-#284
# install is migrated in place, once, at the door (PARSE-AT-THE-DOOR):
# if agent.env has NO usable token line, the mirror's value is moved into
# agent.env before the mirror is deleted, so a live credential already in
# use is never silently discarded. If agent.env already has a token, the
# mirror is simply retired (deleted) since it is no longer read by anything.
if (Test-Path $TokenFile) {
    # -Last 1 (not -First 1): matches systemd's EnvironmentFile= parser
    # semantics ("last wins") and llauncher.core.agent_token.parse_env_file
    # -- a duplicate-key agent.env must resolve identically across the
    # installer's own check and the runtime the installer is validating
    # against (issue #285).
    $tokenLineExisting = (Get-Content $EnvFile) | Where-Object { $_ -match '^LLAUNCHER_AGENT_TOKEN=' } | Select-Object -Last 1
    $tokenValueExisting = if ($tokenLineExisting) { ($tokenLineExisting -replace '^LLAUNCHER_AGENT_TOKEN=', '').Trim() } else { '' }
    if (-not $tokenValueExisting) {
        $mirrorToken = (Get-Content $TokenFile -Raw).Trim()
        if ($mirrorToken) {
            # IMPORTANT: append WITHOUT a UTF-8 BOM -- Windows PowerShell
            # 5.1's `Add-Content -Encoding utf8` prepends EF BB BF, which
            # would corrupt the first key name (issue #127).
            $appendedLines = @((Get-Content $EnvFile) + "LLAUNCHER_AGENT_TOKEN=$mirrorToken")
            [System.IO.File]::WriteAllLines(
                $EnvFile, $appendedLines, (New-Object System.Text.UTF8Encoding($false)))
            Say "Migrated live token from $TokenFile into $EnvFile (agent.env has no token line)."
        }
    }
    Remove-Item -Path $TokenFile -Force
    Info "Retired by #284; live source is $EnvFile. Removed stale mirror $TokenFile."
}

# --- Fail loud if agent.env still has no usable token (issue #281/#284) -
# -Last 1: same last-wins rationale as above (issue #285) -- this check
# must agree with what the agent process will actually resolve.
$tokenLine = (Get-Content $EnvFile) | Where-Object { $_ -match '^LLAUNCHER_AGENT_TOKEN=' } | Select-Object -Last 1
$tokenValue = if ($tokenLine) { ($tokenLine -replace '^LLAUNCHER_AGENT_TOKEN=', '').Trim() } else { '' }
if (-not $tokenValue) {
    # Fail loud, never trust-and-degrade (issue #281): without a usable
    # token line in the live source the service comes up "green" but the
    # agent silently auto-generates its own token under the SERVICE
    # account's profile, and the UI gets 403 on every authed endpoint.
    Die @"
No usable LLAUNCHER_AGENT_TOKEN line found in ${EnvFile} (missing or empty value).
Refusing to install the service without a usable token in the live source. Fix one of:
  - Add a line to ${EnvFile}:  LLAUNCHER_AGENT_TOKEN=<token>
    (generate one:  python -c "import secrets; print(secrets.token_urlsafe(32))")
  - Or delete ${EnvFile} and re-run this script to regenerate it from the template.
"@
}

# --- Parse env file into NSSM AppEnvironmentExtra format --------------
# NSSM accepts multiple "KEY=VALUE" arguments after AppEnvironmentExtra.
$envPairs = @()
foreach ($line in Get-Content $EnvFile) {
    $trim = $line.Trim()
    if (-not $trim -or $trim.StartsWith('#')) { continue }
    $envPairs += $trim
}

# --- LocalSystem wrinkle (issue #284) -----------------------------------
# NSSM defaults new services to the LocalSystem account, whose Path.home()
# does NOT resolve to the installing operator's %USERPROFILE%. Without
# this, the service process would resolve a DIFFERENT (or nonexistent)
# agent.env than $EnvFile above, silently diverging from what the
# operator's UI reads. Inject the resolved state dir explicitly so both
# processes converge on the same live file. This does NOT re-introduce a
# token mirror -- it is a pointer to the single live source, not a copy of
# its contents.
$envPairs += "LAUNCHER_STATE_DIR=$EnvDir"
Say "Injecting LAUNCHER_STATE_DIR=$EnvDir into the service environment (LocalSystem wrinkle, #284)."

# --- Unbuffered stdout/stderr (issue #128) ------------------------------
# NSSM captures agent stdout/stderr to AppStdout/AppStderr files (below),
# but a redirected (non-TTY) Python stream is block-buffered by default,
# so runtime log lines sit in an in-process buffer indefinitely instead of
# reaching those files. PYTHONUNBUFFERED must be set BEFORE the
# interpreter starts (NSSM AppEnvironmentExtra, not agent.env, which
# load_dotenv() only reads after Python is already running) -- unconditional
# because there is no scenario where an operator wants the pre-#128
# buffering bug back.
$envPairs += "PYTHONUNBUFFERED=1"
Say "Injecting PYTHONUNBUFFERED=1 into the service environment (unbuffered agent logging, #128)."

# --- Install or refresh service ---------------------------------------
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Info "Service exists - stopping for refresh..."
    & $nssm stop $ServiceName confirm | Out-Null
} else {
    Info "Registering service $ServiceName..."
    & $nssm install $ServiceName $VenvExe | Out-Null
    Say "Service registered."
}

# (Re-)apply configuration. These are all idempotent.
# Application MUST be re-applied here, not only in the fresh-install
# branch above: on a refresh of an existing service, `nssm install` is
# never called, so a write-once Application would leave the service
# executing whichever clone's venv it was first installed from -- even
# after AppDirectory below is repointed at a different clone (the bug
# behind this fix; re-running from a different checkout silently ran
# stale code).
& $nssm set $ServiceName Application $VenvExe | Out-Null
& $nssm set $ServiceName AppDirectory $ProjectDir | Out-Null
& $nssm set $ServiceName DisplayName "llauncher remote management agent" | Out-Null
& $nssm set $ServiceName Description "Local llauncher agent (see https://github.com/shanevcantwell/llauncher)." | Out-Null
& $nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $nssm set $ServiceName AppStdout (Join-Path $LogDir 'agent.out.log') | Out-Null
& $nssm set $ServiceName AppStderr (Join-Path $LogDir 'agent.err.log') | Out-Null
& $nssm set $ServiceName AppRotateFiles 1 | Out-Null
& $nssm set $ServiceName AppRotateBytes 10485760 | Out-Null  # 10 MB
& $nssm set $ServiceName AppExit Default Restart | Out-Null
& $nssm set $ServiceName AppRestartDelay 5000 | Out-Null      # 5 s
# Graceful: send Ctrl-Break (which uvicorn treats as SIGINT), wait 30 s,
# then escalate. Console-control method = 1.
& $nssm set $ServiceName AppStopMethodConsole 30000 | Out-Null
# Pass env vars as a single multi-line value (each line "KEY=VALUE").
& $nssm set $ServiceName AppEnvironmentExtra ($envPairs -join "`r`n") | Out-Null
Say "Service configuration applied."

if (-not $NoStart) {
    Info "Starting $ServiceName..."
    & $nssm start $ServiceName | Out-Null
    Start-Sleep -Seconds 2
    $status = (Get-Service $ServiceName).Status
    if ($status -eq 'Running') {
        Say "Service is running."
    } else {
        Write-Host "[!!]  Service status: $status" -ForegroundColor Red
        Write-Host "      Check $LogDir\agent.err.log" -ForegroundColor Red
        exit 1
    }
}

Write-Host @"

Next steps:
  Get-Service $ServiceName
  Get-Content "$LogDir\agent.out.log" -Tail 20 -Wait

To refresh after editing ${EnvFile}:
  .\scripts\windows\install.ps1

To uninstall:
  .\scripts\windows\install.ps1 -Uninstall
"@
