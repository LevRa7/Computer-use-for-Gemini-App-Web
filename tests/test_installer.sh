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

# Test 4: Shared-domain URL contract
# Every node uses ONE domain and is selected by ?user=; the installer must never
# advertise a per-device subdomain (each extra hostname needs its own TLS SAN).
echo "[Test 4] Testing shared-domain URL contract..."
HELP_OUTPUT=$(bash "$INSTALLER" --help)
echo "$HELP_OUTPUT" | grep -q 'user=<node-name>' || {
    echo "[FAIL] --help must document the canonical ?user=<node-name> URL form"
    exit 1
}
echo "$HELP_OUTPUT" | grep -q 'shared-domain' || {
    echo "[FAIL] --help must document the shared gateway domain"
    exit 1
}
if echo "$HELP_OUTPUT" | grep -qE '<[a-z-]+>\.smart-server\.online'; then
    echo "[FAIL] --help still advertises a per-device subdomain"
    exit 1
fi
echo "[PASS] shared-domain contract documented"

# Test 5: Registration wording no longer sells subdomains
echo "[Test 5] Testing installer wording..."
bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --lang=en | grep -qi 'subdomain' && {
    echo "[FAIL] installer output still mentions subdomains"
    exit 1
}
echo "[PASS] installer wording standardised"

# Test 6: The canonical step builds the shared-domain URL with ?user=
echo "[Test 6] Testing canonical URL generation..."
URL_LINE=$(bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --token=tok123 --lang=en \
    | grep 'Canonical MCP URL')
echo "$URL_LINE" | grep -q 'https://smart-server.online/sse?user=node-one&token=tok123' || {
    echo "[FAIL] canonical URL is wrong: $URL_LINE"
    exit 1
}
CUSTOM_DOMAIN_URL=$(bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --token=tok123 \
    --domain=mesh.example.com --lang=en | grep 'Canonical MCP URL')
echo "$CUSTOM_DOMAIN_URL" | grep -q 'https://mesh.example.com/sse?user=node-one&token=tok123' || {
    echo "[FAIL] --domain must set the shared domain: $CUSTOM_DOMAIN_URL"
    exit 1
}
echo "[PASS] canonical URL generation passed"

echo "=== All Installer Tests Passed Successfully ==="
