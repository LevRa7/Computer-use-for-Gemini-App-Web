#!/usr/bin/env bash
# ==============================================================================
#  deploy_gateway.sh - standardised deployment to the SHARED gateway host
#
#  One gateway serves every node (see README -> "One shared domain"), so this
#  script is the only supported way to publish it: the deployed gateway, the
#  served installers and the node bootstrap payload can never drift apart again.
#
#  It is idempotent and verified:
#    1. reachability + directory checks,
#    2. upload to a staging dir with an sha256 manifest checked on the host,
#    3. timestamped backup of everything it replaces,
#    4. atomic install with the correct owner (service user for the app dir),
#    5. service restart and a public health check naming the shared domain.
#
#  USAGE
#    MESH_GATEWAY_SSH=root@<gateway-ip> ./deploy_gateway.sh
#    MESH_GATEWAY_SSH=root@<gateway-ip> MESH_GATEWAY_SSH_PASS_FILE=~/.ssh/gw-pass \
#        ./deploy_gateway.sh
#    ./deploy_gateway.sh --dry-run
#
#  CONFIGURATION (environment only - never hardcode hosts or secrets)
#    MESH_GATEWAY_SSH            required ssh target, e.g. root@203.0.113.10
#    MESH_GATEWAY_SSH_KEY        optional private key path
#    MESH_GATEWAY_SSH_PASS_FILE  optional password file for sshpass
#    MESH_GATEWAY_SSH_OPTS       optional extra ssh flags (e.g. "-F /dev/null"
#                                inside a sandbox that cannot read /etc/ssh)
#    MESH_GATEWAY_APP_DIR        default /opt/antigravity-mesh
#    MESH_GATEWAY_WWW_DIR        default /var/www/antigravity-mesh
#    MESH_GATEWAY_SERVICE        default agy-gateway.service
#    MESH_GATEWAY_BACKUP_DIR     default /root/backups/antigravity-mesh
#    MESH_PUBLIC_URL             default https://smart-server.online
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; RESET="\033[0m"
fi
info() { printf "${BOLD}%s${RESET}\n" "$*"; }
ok()   { printf "${GREEN}✓ %s${RESET}\n" "$*"; }
warn() { printf "${YELLOW}! %s${RESET}\n" "$*"; }
die()  { printf "${RED}✗ %s${RESET}\n" "$*" >&2; exit 1; }

DRY_RUN=false
WITH_NODE_CODE=true
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --no-node-code) WITH_NODE_CODE=false ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^#\{1,2\} \{0,1\}//'; exit 0 ;;
        *) die "Unknown parameter: $1 (try --help)" ;;
    esac
    shift
done

APP_DIR="${MESH_GATEWAY_APP_DIR:-/opt/antigravity-mesh}"
WWW_DIR="${MESH_GATEWAY_WWW_DIR:-/var/www/antigravity-mesh}"
SERVICE="${MESH_GATEWAY_SERVICE:-agy-gateway.service}"
BACKUP_ROOT="${MESH_GATEWAY_BACKUP_DIR:-/root/backups/antigravity-mesh}"
PUBLIC_URL="${MESH_PUBLIC_URL:-https://smart-server.online}"

for d in "$APP_DIR" "$WWW_DIR" "$BACKUP_ROOT"; do
    case "$d" in /*) ;; *) die "Path must be absolute: $d" ;; esac
done

# ------------------------------------------------------------------------------
# What gets deployed
#   app dir  : the gateway process itself
#   www dir  : installers and node bootstrap payload served to new nodes
# ------------------------------------------------------------------------------
APP_FILES=(gateway.py)
WWW_FILES=(install.sh install.ps1)
NODE_FILES=()
if [ "$WITH_NODE_CODE" = true ]; then
    for f in core/*.py skills/*.md; do
        [ -f "$f" ] && NODE_FILES+=("$f")
    done
fi

for f in "${APP_FILES[@]}" "${WWW_FILES[@]}" ${NODE_FILES[@]+"${NODE_FILES[@]}"}; do
    [ -f "$f" ] || die "Missing local file: $f"
done

# ------------------------------------------------------------------------------
# SSH transport
# ------------------------------------------------------------------------------
SSH_TARGET="${MESH_GATEWAY_SSH:-}"
[ -n "$SSH_TARGET" ] || die "MESH_GATEWAY_SSH is not set (e.g. root@<gateway-ip>); try --help."

SSH_BIN=(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
if [ -n "${MESH_GATEWAY_SSH_OPTS:-}" ]; then
    # shellcheck disable=SC2206  # intentional word splitting of extra flags
    SSH_BIN+=(${MESH_GATEWAY_SSH_OPTS})
fi
[ -n "${MESH_GATEWAY_SSH_KEY:-}" ] && SSH_BIN+=(-i "$MESH_GATEWAY_SSH_KEY")
if [ -n "${MESH_GATEWAY_SSH_PASS_FILE:-}" ]; then
    command -v sshpass >/dev/null 2>&1 || die "sshpass is required for MESH_GATEWAY_SSH_PASS_FILE"
    [ -f "$MESH_GATEWAY_SSH_PASS_FILE" ] || die "Password file not found: $MESH_GATEWAY_SSH_PASS_FILE"
    SSH_BASE=(sshpass -f "$MESH_GATEWAY_SSH_PASS_FILE" "${SSH_BIN[@]}")
else
    SSH_BASE=("${SSH_BIN[@]}" -o BatchMode=yes)
fi

rsh()  { "${SSH_BASE[@]}" "$SSH_TARGET" "$@"; }
rput() { "${SSH_BASE[@]}" "$SSH_TARGET" "cat > '$2'" < "$1"; }

TS="$(date +%Y%m%d-%H%M%S)"
STAGE="/tmp/mesh-deploy-$TS"

info "Deploying Antigravity Mesh gateway"
echo "  target     : $SSH_TARGET"
echo "  app dir    : $APP_DIR"
echo "  www dir    : $WWW_DIR"
echo "  service    : $SERVICE"
echo "  public url : $PUBLIC_URL"
echo "  node code  : $WITH_NODE_CODE (${#NODE_FILES[@]} files)"
echo "  dry run    : $DRY_RUN"

# local path -> remote path
PAIRS=()
for f in "${APP_FILES[@]}"; do PAIRS+=("$f|$STAGE/$(basename "$f")"); done
for f in "${WWW_FILES[@]}"; do PAIRS+=("$f|$STAGE/$(basename "$f")"); done
for f in ${NODE_FILES[@]+"${NODE_FILES[@]}"}; do PAIRS+=("$f|$STAGE/$f"); done

if [ "$DRY_RUN" = true ]; then
    for pair in "${PAIRS[@]}"; do
        local_f="${pair%%|*}"; remote_f="${pair##*|}"
        case "$local_f" in
            gateway.py) dest="$APP_DIR/$(basename "$local_f")" ;;
            *)          dest="$WWW_DIR/$local_f" ;;
        esac
        echo "  would deploy: $local_f -> $dest"
    done
    echo "  would back up into: $BACKUP_ROOT/$TS"
    echo "  would restart     : $SERVICE"
    ok "dry run complete"
    exit 0
fi

# 1) reachability + layout -----------------------------------------------------
rsh "set -e
systemctl cat $SERVICE >/dev/null 2>&1 || { echo 'service $SERVICE not found' >&2; exit 1; }
[ -d $APP_DIR ] || { echo 'missing $APP_DIR' >&2; exit 1; }
[ -d $WWW_DIR ] || { echo 'missing $WWW_DIR' >&2; exit 1; }
mkdir -p $STAGE/core $STAGE/skills"
ok "gateway reachable, directories present"

# 2) upload + on-host sha256 manifest verification -----------------------------
manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT
for pair in "${PAIRS[@]}"; do
    local_f="${pair%%|*}"; remote_f="${pair##*|}"
    rput "$local_f" "$remote_f"
    printf '%s  %s\n' "$(sha256sum "$local_f" | cut -d' ' -f1)" "$remote_f" >> "$manifest"
done
rput "$manifest" "$STAGE/manifest.sha256"
rsh "sha256sum -c $STAGE/manifest.sha256 >/dev/null" \
    || die "sha256 verification failed on the gateway (nothing was installed)"
ok "uploaded and verified ${#PAIRS[@]} files"

# 3) backup --------------------------------------------------------------------
rsh "set -e
mkdir -p $BACKUP_ROOT/$TS
[ -f $APP_DIR/gateway.py ] && cp -a $APP_DIR/gateway.py $BACKUP_ROOT/$TS/gateway.py || true
for n in install.sh install.ps1 gateway.py; do
    [ -f $WWW_DIR/\$n ] && cp -a $WWW_DIR/\$n $BACKUP_ROOT/$TS/\$n.www || true
done
[ -d $WWW_DIR/core ] && cp -a $WWW_DIR/core $BACKUP_ROOT/$TS/core || true
[ -d $WWW_DIR/skills ] && cp -a $WWW_DIR/skills $BACKUP_ROOT/$TS/skills || true
[ -f /etc/antigravity-mesh/gateway.env ] && cp -a /etc/antigravity-mesh/gateway.env $BACKUP_ROOT/$TS/ || true
true"
ok "backup written to $BACKUP_ROOT/$TS"

# 4) install -------------------------------------------------------------------
SVC_USER="$(rsh "systemctl show -p User --value $SERVICE | tr -d '[:space:]'")"
[ -n "$SVC_USER" ] || SVC_USER=root
rsh "set -e
install -o $SVC_USER -g $SVC_USER -m 644 $STAGE/gateway.py $APP_DIR/gateway.py
install -o root -g root -m 755 $STAGE/install.sh $WWW_DIR/install.sh
install -o root -g root -m 644 $STAGE/install.ps1 $WWW_DIR/install.ps1"
if [ "$WITH_NODE_CODE" = true ]; then
    rsh "set -e
for f in $STAGE/core/*.py; do install -o root -g root -m 644 \"\$f\" $WWW_DIR/core/; done
for f in $STAGE/skills/*.md; do install -o root -g root -m 644 \"\$f\" $WWW_DIR/skills/; done"
fi
ok "files installed (app owner: $SVC_USER)"

# 5) restart + public health ---------------------------------------------------
rsh "systemctl restart $SERVICE; sleep 3; systemctl is-active $SERVICE"
health="$(curl -fsS -m 15 "$PUBLIC_URL/")" || die "public health check failed: $PUBLIC_URL/"
echo "$health" | grep -q '"status":"healthy"' || die "gateway reported unhealthy: $health"
ok "gateway healthy: $(echo "$health" | head -c 140)"

rsh "rm -rf $STAGE" >/dev/null 2>&1 || true
ok "deploy complete"
warn "rollback: cp -a $BACKUP_ROOT/$TS/gateway.py $APP_DIR/ && systemctl restart $SERVICE"
