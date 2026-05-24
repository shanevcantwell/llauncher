# Install / refresh the llauncher-agent Windows service via NSSM.
#
# Prerequisites:
#   - NSSM (Non-Sucking Service Manager) on PATH, or set $env:NSSM
#     to the absolute path of nssm.exe. Download from https://nssm.cc/
#     or `choco install nssm` / `scoop install nssm`.
#   - Project venv created via `scripts\run.bat install`.
#   - This script must run elevated (right-click "Run as Administrator").
#
# Idempotent: re-running picks up env-file edits and a new venv path.
# Will NOT overwrite an existing env file (your token survives).
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
$LogDir      = Join-Path $env:USERPROFILE '.llauncher\logs'
$EnvExample  = Join-Path $ScriptDir 'llauncher-agent.env.example'

function Say  ($msg) { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Info ($msg) { Write-Host "[..]  $msg" -ForegroundColor Yellow }
function Die  ($msg) { Write-Host "[!!]  $msg" -ForegroundColor Red; exit 1 }

# --- Elevation check --------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "This script must be run from an elevated (Administrator) PowerShell."
}

# --- Locate nssm ------------------------------------------------------
$nssm = if ($env:NSSM) { $env:NSSM } else { (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source }
if (-not $nssm -or -not (Test-Path $nssm)) {
    Die @"
nssm.exe not found. Install via one of:
  choco install nssm
  scoop install nssm
  https://nssm.cc/download (then add to PATH, or set `$env:NSSM = '<path>\nssm.exe'`)
"@
}
Say "Using NSSM at $nssm"

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

# --- Env file (token + config) ----------------------------------------
if (-not (Test-Path $EnvDir))  { New-Item -ItemType Directory -Path $EnvDir  | Out-Null }
if (-not (Test-Path $LogDir))  { New-Item -ItemType Directory -Path $LogDir  | Out-Null }

if (-not (Test-Path $EnvFile)) {
    Info "Generating $EnvFile with a fresh token..."
    $token = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content $EnvExample) `
        -replace 'replace-me-with-a-random-token', $token.Trim() `
        | Set-Content -Path $EnvFile -Encoding utf8
    Say "Wrote $EnvFile with a generated 32-byte token."
    Info "Edit it to set LAUNCHER_AGENT_NODE_NAME / HOST / PORT as needed."
} else {
    Say "Env file already exists at $EnvFile - leaving it untouched."
}

# Lock the env file: remove inheritance, grant current user only.
$acl = Get-Acl $EnvFile
$acl.SetAccessRuleProtection($true, $false)
$me = "$env:USERDOMAIN\$env:USERNAME"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $me, 'FullControl', 'Allow')
# Strip any inherited rules left over from the protection flip
$acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
$acl.AddAccessRule($rule)
Set-Acl -Path $EnvFile -AclObject $acl
Say "Locked ACL on $EnvFile (current user only)."

# --- Parse env file into NSSM AppEnvironmentExtra format --------------
# NSSM accepts multiple "KEY=VALUE" arguments after AppEnvironmentExtra.
$envPairs = @()
foreach ($line in Get-Content $EnvFile) {
    $trim = $line.Trim()
    if (-not $trim -or $trim.StartsWith('#')) { continue }
    $envPairs += $trim
}

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

To refresh after editing $EnvFile:
  .\scripts\windows\install.ps1

To uninstall:
  .\scripts\windows\install.ps1 -Uninstall
"@
