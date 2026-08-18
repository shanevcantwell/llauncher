#!/bin/bash
# Build llama.cpp's llama-server with CUDA, verifying the two flags whose
# failure mode is SILENT rather than an error. See issue #397 for the full
# writeup; the short version:
#
#   - CMAKE_BUILD_TYPE=Release: with a single-config generator (make, the
#     default here), `cmake --build --config Release` is IGNORED and the
#     build defaults to Debug (unoptimized, assertions live). This was a
#     real, months-long, undetected defect. We set it explicitly and read
#     it back from CMakeCache.txt after configuring -- fail loud if it did
#     not stick.
#   - GGML_CUDA_FA_ALL_QUANTS=ON (default here): with it OFF, the CUDA
#     flash-attention kernels reject several quantized KV cache types and
#     the scheduler silently relocates the whole FLASH_ATTN_EXT op to the
#     CPU backend -- prompt processing collapses 20-40x while token
#     generation looks normal. No error, no log line. See llama.cpp
#     issue #27109. Costs extra compile time; that is the tradeoff.
#
# Usage:
#   ./scripts/build-llama-server.sh [options]
#
# Options (all overridable by env var too; flag wins):
#   --src DIR            llama.cpp source checkout (env LLAMA_CPP_SRC_DIR,
#                         default: ./llama.cpp)
#   --cuda-arch ARCH      CMAKE_CUDA_ARCHITECTURES, e.g. 86 (Ampere),
#                         89 (Ada), 90 (Hopper) (env CUDA_ARCH, required)
#   --jobs N              parallel build jobs (env BUILD_JOBS,
#                         default: nproc)
#   --no-fa-all-quants     build with GGML_CUDA_FA_ALL_QUANTS=OFF (opt-out;
#                         see rationale above -- NOT recommended)
#   --with-ui              build llama.cpp's bundled web UI (LLAMA_BUILD_UI=ON).
#                         Off by default: llauncher drives llama-server over
#                         HTTP and never uses the bundled UI, and that stage
#                         pulls in an npm/Node toolchain + network fetch that
#                         this build otherwise avoids.
#   -h, --help              show this help and exit
#
# Fails loud (non-zero exit, clear message) on: missing CUDA toolkit,
# configure failure, build failure, or either verification in the
# rationale above not matching what was requested.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors (matches scripts/run.sh / scripts/systemd/install.sh)
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
say()  { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}ℹ${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }
die()  { err "$1"; exit 1; }

# --- Defaults / env overrides ------------------------------------------
SRC_DIR="${LLAMA_CPP_SRC_DIR:-$SCRIPT_DIR/../llama.cpp}"
CUDA_ARCH="${CUDA_ARCH:-}"
JOBS="${BUILD_JOBS:-$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)}"
FA_ALL_QUANTS="ON"
BUILD_UI="OFF"

# --- Argument parsing ----------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --src)              SRC_DIR="$2"; shift 2 ;;
        --cuda-arch)         CUDA_ARCH="$2"; shift 2 ;;
        --jobs)              JOBS="$2"; shift 2 ;;
        --no-fa-all-quants)  FA_ALL_QUANTS="OFF"; shift ;;
        --with-ui)           BUILD_UI="ON"; shift ;;
        -h|--help)
            sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "Unknown argument: $1 (see --help)" ;;
    esac
done

SRC_DIR="$(cd "$SRC_DIR" 2>/dev/null && pwd || true)"
[ -n "$SRC_DIR" ] && [ -d "$SRC_DIR" ] || die "llama.cpp source dir not found. Pass --src DIR or set LLAMA_CPP_SRC_DIR."
[ -f "$SRC_DIR/CMakeLists.txt" ] || die "$SRC_DIR does not look like a llama.cpp checkout (no CMakeLists.txt)."
[ -n "$CUDA_ARCH" ] || die "CUDA architecture not set. Pass --cuda-arch (e.g. 86 for Ampere, 89 Ada, 90 Hopper) or set \$CUDA_ARCH."

# --- Preflight: CUDA toolkit ---------------------------------------------
command -v nvcc >/dev/null 2>&1 || die "nvcc not found on PATH -- CUDA toolkit is required. Install it and ensure nvcc is on PATH, then re-run."
command -v cmake >/dev/null 2>&1 || die "cmake not found on PATH."
info "CUDA toolkit: $(nvcc --version | tail -1)"

BUILD_DIR="$SRC_DIR/build"

# --- Wipe build dir (cross-CUDA-version cache poisoning) -----------------
# Only after confirming BUILD_DIR really is <src>/build under the checkout
# the caller pointed us at -- never rm -rf a bare/relative path.
if [ -d "$BUILD_DIR" ]; then
    case "$BUILD_DIR" in
        "$SRC_DIR"/build) ;;
        *) die "Refusing to wipe unexpected build dir: $BUILD_DIR" ;;
    esac
    info "Removing stale build dir $BUILD_DIR (avoids cross-CUDA-version cache poisoning)..."
    rm -rf "$BUILD_DIR"
fi

# --- Configure -------------------------------------------------------------
info "Configuring (Release, GGML_CUDA=ON, GGML_CUDA_FA_ALL_QUANTS=$FA_ALL_QUANTS, LLAMA_BUILD_UI=$BUILD_UI, arch=$CUDA_ARCH)..."
if ! cmake -S "$SRC_DIR" -B "$BUILD_DIR" \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" \
        -DGGML_CUDA_FA_ALL_QUANTS="$FA_ALL_QUANTS" \
        -DLLAMA_BUILD_UI="$BUILD_UI"; then
    die "cmake configure failed. See output above."
fi

# --- Verify the configure actually stuck (read back, not assumed) --------
CACHE="$BUILD_DIR/CMakeCache.txt"
[ -f "$CACHE" ] || die "Configure reported success but $CACHE is missing -- cannot verify."

cache_get() { grep -E "^$1:" "$CACHE" | tail -1 | cut -d= -f2-; }

build_type="$(cache_get CMAKE_BUILD_TYPE)"
if [ "$build_type" != "Release" ]; then
    die "CMAKE_BUILD_TYPE verification FAILED: CMakeCache.txt has '$build_type', expected 'Release'. A single-config generator silently defaulting to Debug is exactly the failure this script exists to catch -- do not proceed with this build."
fi
say "Verified CMAKE_BUILD_TYPE=Release in $CACHE."

fa_quants="$(cache_get GGML_CUDA_FA_ALL_QUANTS)"
if [ "$fa_quants" != "$FA_ALL_QUANTS" ]; then
    die "GGML_CUDA_FA_ALL_QUANTS verification FAILED: CMakeCache.txt has '$fa_quants', expected '$FA_ALL_QUANTS'."
fi
say "Verified GGML_CUDA_FA_ALL_QUANTS=$FA_ALL_QUANTS in $CACHE."

# --- Build -----------------------------------------------------------------
info "Building llama-server with $JOBS job(s) (this can take a while, especially with FA_ALL_QUANTS=ON)..."
if ! cmake --build "$BUILD_DIR" --config Release -j"$JOBS" --target llama-server; then
    die "Build FAILED. See compiler output above."
fi

# --- Locate the binary and report LLAMA_SERVER_PATH -----------------------
BIN="$BUILD_DIR/bin/llama-server"
[ -x "$BIN" ] || BIN="$(find "$BUILD_DIR" -maxdepth 4 -type f -name llama-server -perm -u+x 2>/dev/null | head -1)"
[ -n "$BIN" ] && [ -x "$BIN" ] || die "Build reported success but no llama-server binary was found under $BUILD_DIR."

say "Build complete."
echo ""
echo "Set this in your environment / agent.env (docs/operations/run-as-a-service.md):"
echo "  LLAMA_SERVER_PATH=$BIN"
