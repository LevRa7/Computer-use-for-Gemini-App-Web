#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_DIR/install.sh"

echo "=== Running Installer Tests ==="

if [ ! -f "$INSTALLER" ]; then
    echo "[FAIL] install.sh not found"
    exit 1
fi

# Test 1: Help flag
echo "[Test 1] Testing --help flag..."
bash "$INSTALLER" --help | grep -q "Usage:"
echo "[PASS] --help works"

# Test 2: Dry-run standalone mode
echo "[Test 2] Testing --dry-run --mode=standalone --port=8096..."
OUTPUT=$(bash "$INSTALLER" --dry-run --mode=standalone --tls=none --port=8096)
echo "$OUTPUT" | grep -q "DRY-RUN"
echo "$OUTPUT" | grep -q "standalone"
echo "[PASS] dry-run standalone passed"

# Test 3: Dry-run gateway mode
echo "[Test 3] Testing --dry-run --mode=gateway..."
OUTPUT_GW=$(bash "$INSTALLER" --dry-run --mode=gateway --domain=smart-server.online)
echo "$OUTPUT_GW" | grep -q "gateway"
echo "[PASS] dry-run gateway passed"

echo "=== All Installer Tests Passed Successfully ==="
