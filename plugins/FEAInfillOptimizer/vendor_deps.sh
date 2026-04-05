#!/usr/bin/env bash
# vendor_deps.sh — Download and vendor FEA plugin dependencies
#
# Downloads gmsh (Python API + native library) into _vendor/ so the
# plugin works in Cura's frozen Python environment without pip install.
#
# Usage:
#   cd plugins/FEAInfillOptimizer
#   ./vendor_deps.sh              # vendor for current platform
#   ./vendor_deps.sh --clean      # remove vendor dir first
#
# Requirements: Python 3.12 + pip (for downloading wheels)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${SCRIPT_DIR}/_vendor"
PYTHON_VERSION="3.12"

# Parse arguments
CLEAN=false
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=true ;;
        --help|-h) echo "Usage: $0 [--clean]"; exit 0 ;;
    esac
done

if [ "$CLEAN" = true ] && [ -d "$VENDOR_DIR" ]; then
    echo "Cleaning existing vendor directory..."
    rm -rf "$VENDOR_DIR"
fi

mkdir -p "$VENDOR_DIR"

echo "=== FEA Plugin Dependency Vendoring ==="

# Find Python 3.12
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

echo "Python: $PYTHON ($($PYTHON --version))"

# Download the gmsh wheel to a temp dir
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "Downloading gmsh wheel..."
"$PYTHON" -m pip download gmsh --dest "$TMPDIR" --only-binary :all: --no-deps 2>&1 | tail -3

WHEEL=$(ls "$TMPDIR"/gmsh-*.whl 2>/dev/null | head -1)
if [ -z "$WHEEL" ]; then
    echo "ERROR: Failed to download gmsh wheel"
    exit 1
fi

echo "Wheel: $(basename "$WHEEL")"

# Extract only what we need from the wheel
echo "Extracting..."
"$PYTHON" -c "
import zipfile, os, shutil

vendor = '${VENDOR_DIR}'
wheel = '${WHEEL}'

with zipfile.ZipFile(wheel) as z:
    # Extract gmsh.py (the Python API)
    z.extract('gmsh.py', vendor)
    print('  ✓ gmsh.py')

    # Extract the native library (in .data/data/lib/)
    for name in z.namelist():
        if name.endswith('.dylib') or name.endswith('.so') or name.endswith('.dll'):
            # Put the lib directly in _vendor/lib/ where gmsh.py looks for it
            target_dir = os.path.join(vendor, 'lib')
            os.makedirs(target_dir, exist_ok=True)
            basename = os.path.basename(name)
            target = os.path.join(target_dir, basename)
            with z.open(name) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(target, 0o755)
            size_mb = os.path.getsize(target) / 1024 / 1024
            print(f'  ✓ lib/{basename} ({size_mb:.1f}MB)')

    # Extract dist-info for version tracking
    for name in z.namelist():
        if '.dist-info/' in name and ('METADATA' in name or 'RECORD' in name):
            z.extract(name, vendor)
"

# Verify
echo ""
echo "Verifying..."
PYTHONPATH="$VENDOR_DIR" "$PYTHON" -c "
import gmsh
print('  ✓ gmsh', gmsh.__version__ if hasattr(gmsh, '__version__') else '(imported OK)')
gmsh.initialize()
gmsh.finalize()
print('  ✓ gmsh native library loads correctly')
" 2>/dev/null || {
    echo "  ✗ gmsh verification failed"
    # Try with LD path
    echo "  Retrying with explicit library path..."
    PYTHONPATH="$VENDOR_DIR" DYLD_LIBRARY_PATH="$VENDOR_DIR/lib" "$PYTHON" -c "
import gmsh
gmsh.initialize()
gmsh.finalize()
print('  ✓ gmsh works with DYLD_LIBRARY_PATH')
" 2>/dev/null || echo "  ✗ Still failed — check native library compatibility"
}

echo ""
echo "Vendor directory:"
du -sh "$VENDOR_DIR"
echo ""
echo "Contents:"
find "$VENDOR_DIR" -maxdepth 2 -type f \( -name "*.py" -o -name "*.dylib" -o -name "*.so" -o -name "*.dll" \) | while read f; do
    size=$(du -sh "$f" | cut -f1)
    echo "  $size  ${f#$VENDOR_DIR/}"
done

echo ""
echo "=== Done ==="
echo ""
echo "Commit: git add plugins/FEAInfillOptimizer/_vendor/"
echo "Note: _vendor/ contains platform-specific native libraries."
echo "For cross-platform, run on each target and merge."
