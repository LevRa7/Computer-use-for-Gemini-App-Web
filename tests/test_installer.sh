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
# The first case asserts the BUILT-IN default domain, so the machine's own domain
# file has to be kept out of the way: resolve_domain() prefers it (it is what makes
# an installed node remember its gateway), and a developer box that already has one
# would otherwise fail this test for a reason that has nothing to do with the URL.
echo "[Test 6] Testing canonical URL generation..."
URL_LINE=$(MESH_DOMAIN_FILE=/nonexistent-domain-file MESH_PUBLIC_URL= AGY_PUBLIC_BASE_URL= \
    bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --token=tok123 --lang=en \
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

# Test 7: the dry run reports the architecture every download depends on
# The uv archive is published per CPU and the registration payload carries the
# value, so an empty or missing row is a real regression, not a cosmetic one.
echo "[Test 7] Testing the architecture row..."
ARCH_LINE=$(bash "$INSTALLER" --dry-run --mode=standalone --lang=en | grep '^\[DRY-RUN\] Architecture:')
[ -n "$ARCH_LINE" ] || {
    echo "[FAIL] the dry run must report the architecture"
    exit 1
}
ARCH_VALUE="${ARCH_LINE#*: }"
[ -n "$ARCH_VALUE" ] || {
    echo "[FAIL] the architecture row is empty: $ARCH_LINE"
    exit 1
}
echo "[PASS] architecture reported as '$ARCH_VALUE'"

# Test 8: a machine without Python stops instead of writing a unit that cannot work
# This used to fall through with PYTHON_BIN=/usr/bin/python3 whether or not that
# file existed, so the node never started and nothing said why.
echo "[Test 8] Testing fail-closed dependency handling..."
grep -q 'Could not obtain a working Python 3' "$INSTALLER" || {
    echo "[FAIL] install.sh must stop when no Python can be obtained"
    exit 1
}
grep -q 'Could not install websockets' "$INSTALLER" || {
    echo "[FAIL] install.sh must stop when websockets cannot be installed"
    exit 1
}
ENSURE_BODY=$(awk '/^ensure_dependencies\(\) \{/{f=1} f{print} f&&/^\}$/{exit}' "$INSTALLER")
echo "$ENSURE_BODY" | grep -q 'exit 1' || {
    echo "[FAIL] ensure_dependencies must exit non-zero rather than continue"
    exit 1
}
echo "[PASS] the dependency step fails closed"

# Test 9: the autostart surfaces pin the interpreter that was actually resolved
# The venv and uv fallbacks both live outside PATH, so "$(which python3)" silently
# pointed launchd at an interpreter without websockets.
echo "[Test 9] Testing that autostart pins the resolved interpreter..."
if grep -q 'which python3' "$INSTALLER"; then
    echo "[FAIL] the autostart must not re-resolve python3 with 'which'"
    exit 1
fi
PINNED=$(grep -c '<string>\$PYTHON_BIN</string>' "$INSTALLER")
[ "$PINNED" -ge 2 ] || {
    echo "[FAIL] both launchd plists must pin \$PYTHON_BIN (found $PINNED)"
    exit 1
}
grep -q 'ExecStart=\$PYTHON_BIN' "$INSTALLER" || {
    echo "[FAIL] the systemd units must pin \$PYTHON_BIN"
    exit 1
}
echo "[PASS] autostart pins the resolved interpreter"

# Test 10: no bare python3 is invoked after ensure_dependencies
# Only the region after the LAST ensure_dependencies is checked: earlier,
# module_default_domain() is allowed a guarded best-effort call with a sed fallback.
echo "[Test 10] Testing for bare interpreter calls after ensure_dependencies..."
LAST_ENS=$(grep -n '^[[:space:]]*ensure_dependencies[[:space:]]*$' "$INSTALLER" | tail -1 | cut -d: -f1)
[ -n "$LAST_ENS" ] || {
    echo "[FAIL] no ensure_dependencies call found"
    exit 1
}
BARE=$(tail -n +"$LAST_ENS" "$INSTALLER" \
    | grep -nE '(^|[^-A-Za-z0-9_"])(python3|python)([[:space:]]|$)' \
    | grep -vE '^[0-9]+:[[:space:]]*#' \
    | grep -vE 'command -v|PYTHON_BIN|python_works|python_has_websockets|resolve_python|python3-pip|python3-venv' || true)
[ -z "$BARE" ] || {
    echo "[FAIL] install.sh still invokes a bare interpreter after ensure_dependencies:"
    echo "$BARE"
    exit 1
}
echo "[PASS] no bare interpreter calls remain"

# Test 11: Termux (Android) is detected and takes its own branch
# A phone is the one target that is not "just Linux": no /etc, no root, no sudo, no
# systemd. The branch is exercised in dry-run mode, the only way to reach it from a
# non-Android machine.
echo "[Test 11] Testing the Termux/Android dry run..."
TMP_HOME="$(mktemp -d 2>/dev/null || echo /tmp/mesh-termux-home)"
TERMUX_OUT=$(TERMUX_VERSION=0.118.0 PREFIX=/data/data/com.termux/files/usr HOME="$TMP_HOME" \
    MESH_DOMAIN_FILE= MESH_PUBLIC_URL=https://mesh.example.com \
    bash "$INSTALLER" --dry-run --mode=tunnel --user=pixel7pro --lang=en)
echo "$TERMUX_OUT" | grep -q 'Platform: Termux (Android)' || {
    echo "[FAIL] a simulated Termux must be detected"
    exit 1
}
echo "$TERMUX_OUT" | grep -q 'Android phone/tablet (Termux)' || {
    echo "[FAIL] the device row must name Android/Termux"
    exit 1
}
# /etc does not exist for a phone app: the domain file has to be per-user, which is
# also what core/domain.py resolves there.
TERMUX_DOMAIN_LINE=$(echo "$TERMUX_OUT" | grep '^\[DRY-RUN\] Domain file:')
case "$TERMUX_DOMAIN_LINE" in
    *.config/antigravity-mesh/domain.env) ;;
    *) echo "[FAIL] a phone must use the per-user domain file: $TERMUX_DOMAIN_LINE"; exit 1 ;;
esac
case "$TERMUX_DOMAIN_LINE" in
    */etc/antigravity-mesh/*) echo "[FAIL] a phone cannot write to /etc: $TERMUX_DOMAIN_LINE"; exit 1 ;;
esac
echo "$TERMUX_OUT" | grep -q 'Systemd service' && {
    echo "[FAIL] no systemd unit is installed on Android"
    exit 1
}
rm -rf "$TMP_HOME"
echo "[PASS] Termux dry run passed"

# Test 12: the Termux branch installs with pkg and supervises with runit
# run_privileged() is a no-op on a phone (no root, no sudo): wrapping the Termux
# packages in it would install nothing and still let the installer continue.
echo "[Test 12] Testing the Termux package and autostart path..."
grep -q 'pkg install -y python python-pip' "$INSTALLER" || {
    echo "[FAIL] the Termux branch must install python via pkg"
    exit 1
}
if grep -q 'run_privileged pkg' "$INSTALLER"; then
    echo "[FAIL] pkg must never go through run_privileged (no sudo on Android)"
    exit 1
fi
grep -q 'configure_termux_node "agy-agent" "core.agent"' "$INSTALLER" || {
    echo "[FAIL] the tunnel node must use the Termux autostart helper"
    exit 1
}
grep -q 'configure_termux_node "agy-standalone" "core.server"' "$INSTALLER" || {
    echo "[FAIL] the standalone server must use the Termux autostart helper"
    exit 1
}
grep -q 'termux-wake-lock' "$INSTALLER" || {
    echo "[FAIL] the boot script must take the wake lock"
    exit 1
}
grep -q '\.termux/boot' "$INSTALLER" || {
    echo "[FAIL] a Termux:Boot entry must be written"
    exit 1
}
TERMUX_BODY=$(awk '/^configure_termux_node\(\) \{/{f=1} f{print} f&&/^\}$/{exit}' "$INSTALLER")
[ -n "$TERMUX_BODY" ] || {
    echo "[FAIL] configure_termux_node not found"
    exit 1
}
# Comments are stripped first: the function explains in prose that there is no
# systemctl on Android, and only real command lines matter here.
TERMUX_CODE=$(echo "$TERMUX_BODY" | grep -vE '^[[:space:]]*#')
echo "$TERMUX_CODE" | grep -q 'systemctl' && {
    echo "[FAIL] the Termux autostart must not use systemctl"
    exit 1
}
echo "$TERMUX_CODE" | grep -q 'sv up' || {
    echo "[FAIL] the Termux autostart must start the runit service"
    exit 1
}
echo "[PASS] Termux package and autostart path verified"

# Test 13: without Termux nothing changes
# The Termux branch must not leak into an ordinary Linux/macOS install.
echo "[Test 13] Testing that a normal host is untouched..."
LINUX_OUT=$(env -u TERMUX_VERSION -u PREFIX \
    MESH_DOMAIN_FILE=/nonexistent-domain-file MESH_PUBLIC_URL= \
    bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --lang=en)
echo "$LINUX_OUT" | grep -q 'Systemd service and dependencies check: OK' || {
    echo "[FAIL] a non-Termux host must still be told about the systemd unit"
    exit 1
}
echo "$LINUX_OUT" | grep -q 'Platform: Termux' && {
    echo "[FAIL] the Termux branch leaked into a normal install"
    exit 1
}
echo "[PASS] a normal host is untouched"

# Test 14: a gateway this device cannot resolve is reported as such
# The phone case: a gateway on a private network (Tailscale, a VPN, a DNS override
# on the operator's laptop) resolves there and nowhere else. Before this guard the
# installer silently downloaded nothing and then blamed the gateway with
# "Failed to obtain authentication token" - the wrong cause, discovered much later.
echo "[Test 14] Testing the gateway DNS preflight..."
grep -q 'require_gateway_dns() {' "$INSTALLER" || {
    echo "[FAIL] install.sh must check that the gateway name resolves"
    exit 1
}
DNS_CALLS=$(grep -c 'require_gateway_dns || exit 1' "$INSTALLER")
[ "$DNS_CALLS" -ge 2 ] || {
    echo "[FAIL] the preflight must guard both the bootstrap and the registration (found $DNS_CALLS)"
    exit 1
}
grep -q 'Could not download the node code' "$INSTALLER" || {
    echo "[FAIL] a bootstrap that downloaded nothing must stop the installer"
    exit 1
}
# Only a DNS failure may stop the run: an older gateway without /health still
# serves the tunnel, so a 404 or a timeout must not be fatal.
grep -qE '"\$rc" -eq 6' "$INSTALLER" || {
    echo "[FAIL] the preflight must key on curl's exit 6 (could not resolve host)"
    exit 1
}
echo "[PASS] the gateway DNS preflight is in place"

# Test 15: a WSL node is reported as WSL, not as the laptop it happens to run on
# WSL2 forwards the host's battery into /sys/class/power_supply, so the battery test
# won and a Debian node under Windows reported itself as "Laptop" - measured on a
# real WSL2 install, where /sys/class/power_supply/BAT1 exists.
echo "[Test 15] Testing that WSL outranks the battery check..."
WSL_LINE=$(grep -n 'microsoft" /proc/version' "$INSTALLER" | head -1 | cut -d: -f1)
BAT_LINE=$(grep -n 'power_supply/BAT\*' "$INSTALLER" | head -1 | cut -d: -f1)
if [ -z "$WSL_LINE" ] || [ -z "$BAT_LINE" ]; then
    echo "[FAIL] could not locate the WSL and battery detection checks"
    exit 1
fi
[ "$WSL_LINE" -lt "$BAT_LINE" ] || {
    echo "[FAIL] the WSL check (line $WSL_LINE) must come before the battery check (line $BAT_LINE)"
    exit 1
}
echo "[PASS] WSL is detected before the battery (lines $WSL_LINE < $BAT_LINE)"

# Test 16: the resolved domain is written down, not only used for this run
# The node builds its public share links from core/domain.py, which reads
# MESH_PUBLIC_URL from the environment and then from the domain file - and never
# the legacy MESH_GATEWAY that agent.env carries. A node installed with only
# agent.env therefore dials the right gateway while minting links on the built-in
# default, which is exactly what a real Termux node did (it handed out
# smart-server.online links while its tunnel went to the configured gateway).
echo "[Test 16] Testing that the installer persists the domain..."
grep -q '^write_domain_file() {' "$INSTALLER" || {
    echo "[FAIL] install.sh must persist the resolved domain in the domain file"
    exit 1
}
CALLS=$(grep -c 'write_domain_file "\$GATEWAY"' "$INSTALLER" || true)
[ "$CALLS" -eq 2 ] || {
    echo "[FAIL] both install branches must write the domain down (found $CALLS call sites)"
    exit 1
}
# The tunnel branch writes it next to agent.env, before the agent is started.
CONFIG_SAVE=$(grep -n '^chmod 600 "\$CONFIG_FILE"$' "$INSTALLER" | head -1 | cut -d: -f1)
TUNNEL_WRITE=$(grep -n '^write_domain_file "\$GATEWAY"$' "$INSTALLER" | tail -1 | cut -d: -f1)
[ -n "$CONFIG_SAVE" ] && [ -n "$TUNNEL_WRITE" ] && [ "$CONFIG_SAVE" -lt "$TUNNEL_WRITE" ] || {
    echo "[FAIL] the domain must be written after agent.env (config=$CONFIG_SAVE write=$TUNNEL_WRITE)"
    exit 1
}

# A dry run reports what would be written and changes nothing.
DRY_DIR="$(mktemp -d)"
DRY_DOMAIN_FILE="$DRY_DIR/domain.env"
DRY_OUT=$(MESH_DOMAIN_FILE="$DRY_DOMAIN_FILE" MESH_PUBLIC_URL= AGY_PUBLIC_BASE_URL= \
    bash "$INSTALLER" --dry-run --mode=tunnel --user=node-one --lang=en --domain=mesh.example.com)
echo "$DRY_OUT" | grep -q "^\[DRY-RUN\] Domain file: $DRY_DOMAIN_FILE\$" || {
    echo "[FAIL] the dry run must name the domain file it would write"
    exit 1
}
echo "$DRY_OUT" | grep -q '^\[DRY-RUN\] Domain file value: MESH_PUBLIC_URL=https://mesh.example.com$' || {
    echo "[FAIL] the dry run must name the value it would write"
    exit 1
}
[ ! -e "$DRY_DOMAIN_FILE" ] || {
    echo "[FAIL] a dry run must not write the domain file"
    exit 1
}
rm -rf "$DRY_DIR"

# The writer itself, exercised from its own source rather than grepped: the same
# three functions the installer runs are extracted from install.sh and called here.
extract_fn() {
    awk -v fn="$1" 'index($0, fn "() {") == 1 {f=1} f {print} f && /^\}$/{exit}' "$INSTALLER"
}
for fn in normalise_host strip_bom domain_file_value module_default_domain write_domain_file; do
    extract_fn "$fn" | grep -q . || {
        echo "[FAIL] could not extract $fn from install.sh"
        exit 1
    }
done
FN_DIR="$(mktemp -d)"
DEFAULT_DOMAIN_DIR="$FN_DIR/.config/antigravity-mesh"
DEFAULT_DOMAIN_FILE="$DEFAULT_DOMAIN_DIR/domain.env"
DOMAIN_PLACEHOLDER="__MESH_DOMAIN__"
IS_TERMUX=false
LANG_CHOICE=en
# module_default_domain() reads core/domain.py from the checkout, which is how the
# writer recognises the built-in default it must refuse to record.
SCRIPT_DIR="$REPO_DIR"
# A temp directory needs no privilege, so the direct write is the path taken; the
# stub keeps a real sudo out of the test.
run_privileged() { "$@"; }
eval "$(extract_fn normalise_host)"
eval "$(extract_fn strip_bom)"
eval "$(extract_fn domain_file_value)"
eval "$(extract_fn module_default_domain)"
eval "$(extract_fn write_domain_file)"

write_domain_file "racknerd-5a24bf9.merino-carob.ts.net" >/dev/null
[ "$(cat "$DEFAULT_DOMAIN_FILE")" = "MESH_PUBLIC_URL=https://racknerd-5a24bf9.merino-carob.ts.net" ] || {
    echo "[FAIL] the domain was not written: $(cat "$DEFAULT_DOMAIN_FILE" 2>/dev/null)"
    exit 1
}
# The same domain again is a no-op: a marker appended by hand has to survive.
echo '# keep' >> "$DEFAULT_DOMAIN_FILE"
write_domain_file "racknerd-5a24bf9.merino-carob.ts.net" >/dev/null
grep -q '^# keep$' "$DEFAULT_DOMAIN_FILE" || {
    echo "[FAIL] an unchanged domain must not rewrite the file"
    exit 1
}
# An explicit domain outranks what the file said, and is reported as a change.
write_domain_file "mesh.example.test" >/dev/null
[ "$(cat "$DEFAULT_DOMAIN_FILE")" = "MESH_PUBLIC_URL=https://mesh.example.test" ] || {
    echo "[FAIL] an explicit domain must replace the old one: $(cat "$DEFAULT_DOMAIN_FILE")"
    exit 1
}
# A scheme-carrying value is normalised, never pasted in twice.
write_domain_file "https://mesh.example.test/" >/dev/null
[ "$(cat "$DEFAULT_DOMAIN_FILE")" = "MESH_PUBLIC_URL=https://mesh.example.test" ] || {
    echo "[FAIL] the value must be normalised: $(cat "$DEFAULT_DOMAIN_FILE")"
    exit 1
}
# The placeholder and an empty value are never written.
rm -f "$DEFAULT_DOMAIN_FILE"
write_domain_file "$DOMAIN_PLACEHOLDER" >/dev/null
write_domain_file "" >/dev/null
[ ! -e "$DEFAULT_DOMAIN_FILE" ] || {
    echo "[FAIL] a placeholder or empty domain must not create the file"
    exit 1
}
# The project's built-in default is a fallback, not a configuration: recording it
# would pin "nothing is configured" and make agent.env's legacy MESH_GATEWAY inert.
DEFAULT_HOST=$(sed -n 's/^DEFAULT_PUBLIC_BASE_URL[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' \
    "$REPO_DIR/core/domain.py" | head -1)
DEFAULT_HOST="${DEFAULT_HOST#*//}"
[ -n "$DEFAULT_HOST" ] || {
    echo "[FAIL] could not read the built-in default from core/domain.py"
    exit 1
}
write_domain_file "$DEFAULT_HOST" >/dev/null
[ ! -e "$DEFAULT_DOMAIN_FILE" ] || {
    echo "[FAIL] the built-in default must never be recorded in the domain file"
    exit 1
}
rm -rf "$FN_DIR"
echo "[PASS] the domain file is written, normalised and never rewritten in place"

echo "=== All Installer Tests Passed Successfully ==="
