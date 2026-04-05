#!/usr/bin/env bash
# vendor_deps.sh — Download and vendor FEA plugin dependencies
#
# Downloads gmsh (Python API + native library) into _vendor/ for all
# supported platforms, creating a multi-platform vendor directory.
#
# Usage:
#   cd plugins/FEAInfillOptimizer
#   ./vendor_deps.sh                   # vendor for current platform only
#   ./vendor_deps.sh --all-platforms   # vendor for macOS, Windows (no Linux wheel available)
#   ./vendor_deps.sh --clean           # remove vendor dir first
#
# Requirements: Python 3.12 + pip

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${SCRIPT_DIR}/_vendor"
PYTHON_VERSION="3.12"

# gmsh platform wheels available on PyPI:
#   macOS ARM64:  gmsh-*-py2.py3-none-macosx_12_0_arm64.whl
#   macOS x86:    gmsh-*-py2.py3-none-macosx_10_15_x86_64.whl
#   Windows x64:  gmsh-*-py2.py3-none-win_amd64.whl
#   Linux:        NOT AVAILABLE as wheel — user must install gmsh system package

PLATFORMS_ALL=(
    "macosx_12_0_arm64"
    "macosx_10_15_x86_64"
    "win_amd64"
)

# Native library extensions per platform (for reference)
# macOS: .dylib, Windows: .dll, Linux: .so

# Parse arguments
CLEAN=false
ALL_PLATFORMS=false
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=true ;;
        --all-platforms) ALL_PLATFORMS=true ;;
        --help|-h)
            echo "Usage: $0 [--clean] [--all-platforms]"
            echo ""
            echo "  --clean           Remove existing _vendor/ first"
            echo "  --all-platforms   Vendor for macOS ARM64, macOS x86, Windows x64"
            echo "                    (default: current platform only)"
            exit 0
            ;;
    esac
done

if [ "$CLEAN" = true ] && [ -d "$VENDOR_DIR" ]; then
    echo "Cleaning existing vendor directory..."
    rm -rf "$VENDOR_DIR"
fi

mkdir -p "$VENDOR_DIR"

# Find Python
PYTHON=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        if [ "$ver" = "$PYTHON_VERSION" ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python ${PYTHON_VERSION} not found."
    exit 1
fi

echo "=== FEA Plugin Dependency Vendoring ==="
echo "Python: $PYTHON ($($PYTHON --version))"

# Determine platforms to vendor
if [ "$ALL_PLATFORMS" = true ]; then
    PLATFORMS=("${PLATFORMS_ALL[@]}")
else
    # Detect current platform
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)  PLATFORMS=("macosx_12_0_arm64") ;;
        Darwin-x86_64) PLATFORMS=("macosx_10_15_x86_64") ;;
        Linux-x86_64)  echo "WARNING: No gmsh wheel for Linux — install gmsh via system package manager"; PLATFORMS=() ;;
        MINGW*|MSYS*|CYGWIN*) PLATFORMS=("win_amd64") ;;
        *) echo "WARNING: Unknown platform $(uname -s)-$(uname -m)"; PLATFORMS=() ;;
    esac
fi

echo "Platforms: ${PLATFORMS[*]:-none}"
echo ""

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Always extract the Python API (platform-independent)
API_EXTRACTED="False"

for platform in "${PLATFORMS[@]}"; do
    echo "── Downloading gmsh for $platform ──"

    WHEEL_DIR="$TMPDIR/$platform"
    mkdir -p "$WHEEL_DIR"

    "$PYTHON" -m pip download gmsh --no-deps --only-binary :all: \
        --platform "$platform" --python-version "$PYTHON_VERSION" \
        --dest "$WHEEL_DIR" 2>&1 | grep -v "^\[notice\]"

    WHEEL=$(ls "$WHEEL_DIR"/gmsh-*.whl 2>/dev/null | head -1)
    if [ -z "$WHEEL" ]; then
        echo "  ✗ No wheel found for $platform"
        continue
    fi

    echo "  Wheel: $(basename "$WHEEL")"

    # Extract using Python
    "$PYTHON" -c "
import zipfile, os, shutil

vendor = '${VENDOR_DIR}'
wheel = '${WHEEL}'
platform = '${platform}'
api_extracted = ${API_EXTRACTED}

with zipfile.ZipFile(wheel) as z:
    # Extract gmsh.py once (it's the same across platforms)
    if not api_extracted:
        z.extract('gmsh.py', vendor)
        print('  ✓ gmsh.py')

    # Extract native libraries into platform-specific subdirectory
    # Structure: _vendor/lib/<platform>/libgmsh.*.dylib
    for name in z.namelist():
        basename = os.path.basename(name)
        if basename.endswith(('.dylib', '.so', '.dll')) and 'gmsh' in basename.lower():
            # Put in platform-specific subdir AND in lib/ for current platform
            plat_dir = os.path.join(vendor, 'lib', platform)
            os.makedirs(plat_dir, exist_ok=True)
            target = os.path.join(plat_dir, basename)
            with z.open(name) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(target, 0o755)
            size_mb = os.path.getsize(target) / 1024 / 1024
            print(f'  ✓ lib/{platform}/{basename} ({size_mb:.1f}MB)')
"

    API_EXTRACTED="True"
    echo ""
done

# Create a symlink or copy for the current platform's lib
echo "Setting up current platform library path..."
"$PYTHON" -c "
import os, sys, shutil, platform as plat

vendor = '${VENDOR_DIR}'
lib_dir = os.path.join(vendor, 'lib')

# Detect current platform
machine = plat.machine()
system = plat.system()
if system == 'Darwin' and machine == 'arm64':
    current = 'macosx_12_0_arm64'
elif system == 'Darwin':
    current = 'macosx_10_15_x86_64'
elif system == 'Windows':
    current = 'win_amd64'
else:
    current = None

if current:
    plat_dir = os.path.join(lib_dir, current)
    if os.path.isdir(plat_dir):
        # Copy platform libs directly to lib/ for gmsh.py to find
        for f in os.listdir(plat_dir):
            src = os.path.join(plat_dir, f)
            dst = os.path.join(lib_dir, f)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                print(f'  ✓ lib/{f} (active for current platform)')
    else:
        print(f'  ⚠ No vendored library for {current}')
"

echo ""

# Verify
echo "Verifying..."
PYTHONPATH="$VENDOR_DIR" "$PYTHON" -c "
import gmsh
ver = getattr(gmsh, '__version__', 'unknown')
print(f'  ✓ gmsh {ver}')
try:
    gmsh.initialize()
    gmsh.finalize()
    print('  ✓ Native library loads OK')
except Exception as e:
    print(f'  ✗ Native library failed: {e}')
" 2>/dev/null || echo "  ✗ Import failed"

echo ""
echo "Vendor directory:"
du -sh "$VENDOR_DIR"
echo ""
find "$VENDOR_DIR" -type f \( -name "*.py" -o -name "*.dylib" -o -name "*.so" -o -name "*.dll" \) | while read f; do
    size=$(du -sh "$f" | cut -f1)
    echo "  $size  ${f#$VENDOR_DIR/}"
done

echo ""
echo "=== Done ==="
