# Build llama.cpp's llama-server with CUDA, verifying the two flags whose
# failure mode is SILENT rather than an error. See issue #397 for the full
# writeup; the short version:
#
#   - CMAKE_BUILD_TYPE=Release: with a single-config generator (Ninja, used
#     here to avoid MSBuild version checks), `cmake --build --config
#     Release` is IGNORED and the build defaults to Debug (unoptimized,
#     assertions live). This was a real, months-long, undetected defect.
#     We set it explicitly and read it back from CMakeCache.txt after
#     configuring -- fail loud if it did not stick.
#   - GGML_CUDA_FA_ALL_QUANTS=ON (default here): with it OFF, the CUDA
#     flash-attention kernels reject several quantized KV cache types and
#     the scheduler silently relocates the whole FLASH_ATTN_EXT op to the
#     CPU backend -- prompt processing collapses 20-40x while token
#     generation looks normal. No error, no log line. See llama.cpp
#     issue #27109. Costs extra compile time; that is the tradeoff.
#
# Prerequisites:
#   - CUDA toolkit (nvcc on PATH).
#   - Ninja (choco install ninja / scoop install ninja) -- used as the
#     CMake generator instead of the default MSVC/MSBuild generator so a
#     newer MSVC than the CUDA toolkit officially supports doesn't get
#     rejected by MSBuild's own version gate before cmake even gets a say.
#   - A "Developer PowerShell for VS" / "x64 Native Tools" prompt, so cl.exe
#     is on PATH for nvcc to shell out to.
#
# Usage:
#   .\scripts\windows\build-llama-server.ps1 -CudaArch 86
#   .\scripts\windows\build-llama-server.ps1 -CudaArch 89 -SrcDir C:\src\llama.cpp -Jobs 16
#   .\scripts\windows\build-llama-server.ps1 -CudaArch 86 -NoFaAllQuants
#   .\scripts\windows\build-llama-server.ps1 -CudaArch 86 -AllowUnsupportedCompiler
#   .\scripts\windows\build-llama-server.ps1 -CudaArch 86 -WithUi

[CmdletBinding()]
param(
    # llama.cpp source checkout. Defaults to a sibling of this repo
    # checkout; override for any other layout. No operator-specific
    # default is baked in.
    [string]$SrcDir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'llama.cpp'),

    # CMAKE_CUDA_ARCHITECTURES, e.g. 86 (Ampere), 89 (Ada), 90 (Hopper).
    # Required -- there is no universally-safe default GPU architecture.
    [Parameter(Mandatory = $true)]
    [string]$CudaArch,

    [int]$Jobs = [Environment]::ProcessorCount,

    # Opt-out of the recommended default (see rationale above). NOT
    # recommended -- only for a fast iteration build where flash-attention
    # KV-cache-quant correctness/perf doesn't matter yet.
    [switch]$NoFaAllQuants,

    # Build llama.cpp's bundled web UI. Off by default: llauncher drives
    # llama-server over HTTP and never uses the bundled UI, and that stage
    # pulls in an npm/Node toolchain + network fetch this build otherwise
    # avoids entirely.
    [switch]$WithUi,

    # Pass -allow-unsupported-compiler to nvcc for the case where the
    # installed MSVC is newer than the CUDA toolkit officially supports.
    # This SUPPRESSES nvcc's own version check -- it is not a fix, and the
    # build can still fail, or worse, produce silently bad codegen. Try a
    # matching MSVC/CUDA pair first; use this only when you understand
    # that tradeoff.
    [switch]$AllowUnsupportedCompiler
)

$ErrorActionPreference = 'Stop'

function Say  ($msg) { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Info ($msg) { Write-Host "[..]  $msg" -ForegroundColor Yellow }
function Die  ($msg) { Write-Host "[!!]  $msg" -ForegroundColor Red; exit 1 }

$FaAllQuants = if ($NoFaAllQuants) { 'OFF' } else { 'ON' }
$BuildUi     = if ($WithUi) { 'ON' } else { 'OFF' }

# --- Resolve + validate source dir --------------------------------------
if (-not (Test-Path $SrcDir)) {
    Die "llama.cpp source dir not found at '$SrcDir'. Pass -SrcDir <path> to a llama.cpp checkout."
}
$SrcDir = (Resolve-Path $SrcDir).Path
if (-not (Test-Path (Join-Path $SrcDir 'CMakeLists.txt'))) {
    Die "'$SrcDir' does not look like a llama.cpp checkout (no CMakeLists.txt)."
}

# --- Preflight: CUDA toolkit + Ninja -------------------------------------
if (-not (Get-Command nvcc.exe -ErrorAction SilentlyContinue)) {
    Die "nvcc.exe not found on PATH -- CUDA toolkit is required. Install it (adds nvcc to PATH via the installer, or add it manually), open a fresh shell, and re-run."
}
if (-not (Get-Command ninja.exe -ErrorAction SilentlyContinue)) {
    Die "ninja.exe not found on PATH. Install it (choco install ninja / scoop install ninja) and re-run. Ninja is used as the generator to avoid MSBuild's own version gate on a newer MSVC than the CUDA toolkit supports."
}
if (-not (Get-Command cmake.exe -ErrorAction SilentlyContinue)) {
    Die "cmake.exe not found on PATH."
}
Info "CUDA toolkit: $((& nvcc --version | Select-Object -Last 1))"

$BuildDir = Join-Path $SrcDir 'build'

# --- Wipe build dir (cross-CUDA-version cache poisoning) -----------------
# Only after confirming BuildDir really is <SrcDir>\build for the checkout
# the caller pointed us at -- never blind-delete a bare/short path.
if (Test-Path $BuildDir) {
    $expected = Join-Path $SrcDir 'build'
    if ((Resolve-Path $BuildDir).Path -ne (Resolve-Path $expected -ErrorAction SilentlyContinue).Path) {
        Die "Refusing to wipe unexpected build dir: $BuildDir"
    }
    Info "Removing stale build dir $BuildDir (avoids cross-CUDA-version cache poisoning)..."
    Remove-Item -Recurse -Force $BuildDir
}

# --- Configure -------------------------------------------------------------
$cmakeArgs = @(
    '-S', $SrcDir,
    '-B', $BuildDir,
    '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=Release',
    '-DGGML_CUDA=ON',
    "-DCMAKE_CUDA_ARCHITECTURES=$CudaArch",
    "-DGGML_CUDA_FA_ALL_QUANTS=$FaAllQuants",
    "-DLLAMA_BUILD_UI=$BuildUi"
)
if ($AllowUnsupportedCompiler) {
    Info "AllowUnsupportedCompiler set -- suppressing nvcc's MSVC-version check. This is not a fix; the build can still fail or produce bad codegen."
    $cmakeArgs += '-DCMAKE_CUDA_FLAGS=-allow-unsupported-compiler'
}

Info "Configuring (Release, GGML_CUDA=ON, GGML_CUDA_FA_ALL_QUANTS=$FaAllQuants, LLAMA_BUILD_UI=$BuildUi, arch=$CudaArch)..."
& cmake.exe @cmakeArgs
if ($LASTEXITCODE -ne 0) {
    Die "cmake configure failed (exit $LASTEXITCODE). See output above."
}

# --- Verify the configure actually stuck (read back, not assumed) --------
$cache = Join-Path $BuildDir 'CMakeCache.txt'
if (-not (Test-Path $cache)) {
    Die "Configure reported success but $cache is missing -- cannot verify."
}
$cacheLines = Get-Content $cache

function Get-CacheValue($name) {
    $line = $cacheLines | Where-Object { $_ -match "^$name`:" } | Select-Object -Last 1
    if (-not $line) { return $null }
    return ($line -split '=', 2)[1]
}

$buildType = Get-CacheValue 'CMAKE_BUILD_TYPE'
if ($buildType -ne 'Release') {
    Die "CMAKE_BUILD_TYPE verification FAILED: CMakeCache.txt has '$buildType', expected 'Release'. A single-config generator silently defaulting to Debug is exactly the failure this script exists to catch -- do not proceed with this build."
}
Say "Verified CMAKE_BUILD_TYPE=Release in $cache."

$faQuants = Get-CacheValue 'GGML_CUDA_FA_ALL_QUANTS'
if ($faQuants -ne $FaAllQuants) {
    Die "GGML_CUDA_FA_ALL_QUANTS verification FAILED: CMakeCache.txt has '$faQuants', expected '$FaAllQuants'."
}
Say "Verified GGML_CUDA_FA_ALL_QUANTS=$FaAllQuants in $cache."

# --- Build -----------------------------------------------------------------
Info "Building llama-server with $Jobs job(s) (this can take a while, especially with FaAllQuants=ON)..."
& cmake.exe --build $BuildDir --config Release -j $Jobs --target llama-server
if ($LASTEXITCODE -ne 0) {
    Die "Build FAILED (exit $LASTEXITCODE). See compiler output above."
}

# --- Locate the binary and report LLAMA_SERVER_PATH -----------------------
$bin = Get-ChildItem -Path $BuildDir -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $bin) {
    Die "Build reported success but no llama-server.exe was found under $BuildDir."
}

Say "Build complete."
Write-Host ""
Write-Host "Set this in your environment / agent.env (docs/operations/run-as-a-service.md):"
Write-Host "  LLAMA_SERVER_PATH=$($bin.FullName)"
