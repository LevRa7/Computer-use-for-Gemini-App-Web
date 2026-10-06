#!/usr/bin/env bash
# ==============================================================================
#  Antigravity Mesh - render the nginx configuration for the public domain.
#
#  The domain comes from the one place ops/mesh-domain.sh resolves it from:
#  MESH_PUBLIC_URL, then the domain file, then core/domain.py's single default.
#  Nothing here holds a domain literal.
#
#  Usage:
#     ops/nginx/render-domain.sh                 # render into ./rendered/
#     ops/nginx/render-domain.sh --out DIR       # render into DIR
#     ops/nginx/render-domain.sh --apply         # install into /etc/nginx
#                                                # (backup + nginx -t + reload)
#     ops/nginx/render-domain.sh --check         # fail if the rendered vhost
#                                                # differs from the live one
#  Env:
#     MESH_DOMAIN_FILE       path to the domain file
#     MESH_CERT_NAME         certbot lineage name (default: the domain)
#     MESH_NGINX_DIR         nginx config root for --apply (default /etc/nginx)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/mesh-domain.vhost.template"
SNIPPET="$SCRIPT_DIR/antigravity-mesh-mcp.conf"

# shellcheck source=../mesh-domain.sh
. "$REPO_DIR/ops/mesh-domain.sh"

BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; RESET="\033[0m"
fi
info() { printf "${BOLD}%s${RESET}\n" "$*"; }
ok()   { printf "${GREEN}✓ %s${RESET}\n" "$*"; }
die()  { printf "${RED}✗ %s${RESET}\n" "$*" >&2; exit 1; }

OUT_DIR=""
APPLY=false
CHECK=false
while [ $# -gt 0 ]; do
    case "$1" in
        --out)   OUT_DIR="${2:-}"; [ -n "$OUT_DIR" ] || die "--out needs a directory"; shift 2 ;;
        --apply) APPLY=true; shift ;;
        --check) CHECK=true; shift ;;
        -h|--help) sed -n '2,26p' "$0" | sed 's/^#\{1,2\} \{0,1\}//'; exit 0 ;;
        *) die "Unknown parameter: $1 (try --help)" ;;
    esac
done

mesh_resolve_domain "$REPO_DIR" \
    || die "Cannot determine the public domain: export MESH_PUBLIC_URL, write MESH_PUBLIC_URL into ${MESH_DOMAIN_FILE:-/etc/antigravity-mesh/domain.env}, or run from a checkout that has core/domain.py"

CERT_NAME="${MESH_CERT_NAME:-$MESH_DOMAIN_HOST}"
NGINX_DIR="${MESH_NGINX_DIR:-/etc/nginx}"

info "Rendering the Mesh nginx configuration"
echo "  domain      : $MESH_DOMAIN_URL"
echo "  server_name : $MESH_DOMAIN_HOST"
echo "  cert name   : $CERT_NAME"
echo "  source      : $MESH_DOMAIN_SOURCE"

render() {
    local dst="$1"
    mkdir -p "$(dirname "$dst")"
    sed -e "s|__MESH_DOMAIN__|$MESH_DOMAIN_HOST|g" \
        -e "s|__MESH_CERT_NAME__|$CERT_NAME|g" \
        "$TEMPLATE" > "$dst"
    grep -q '__MESH_' "$dst" && die "unsubstituted placeholder left in $dst"
    return 0
}

if [ "$CHECK" = true ]; then
    live_vhost="$NGINX_DIR/sites-available/$MESH_DOMAIN_HOST"
    [ -f "$live_vhost" ] || die "no live vhost at $live_vhost to compare against"
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    render "$tmp"
    if diff -u "$live_vhost" "$tmp" >/dev/null; then
        ok "the live vhost matches the rendered template"
        exit 0
    fi
    diff -u "$live_vhost" "$tmp" || true
    die "the live vhost differs from the rendered template (see the diff above)"
fi

if [ "$APPLY" = true ]; then
    command -v nginx >/dev/null 2>&1 || die "nginx is not installed here; use --out to render elsewhere"
    TS="$(date +%Y%m%d-%H%M%S)"
    backup_dir="/root/backups/antigravity-mesh/nginx-$TS"
    mkdir -p "$backup_dir"
    vhost_target="$NGINX_DIR/sites-available/$MESH_DOMAIN_HOST"
    snippet_target="$NGINX_DIR/snippets/antigravity-mesh-mcp.conf"

    [ -f "$vhost_target" ] && cp -a "$vhost_target" "$backup_dir/" || true
    [ -f "$snippet_target" ] && cp -a "$snippet_target" "$backup_dir/" || true

    render "$vhost_target"
    install -m 644 "$SNIPPET" "$snippet_target"
    ln -sfn "$vhost_target" "$NGINX_DIR/sites-enabled/$MESH_DOMAIN_HOST"

    if ! nginx -t 2>&1; then
        warn_rollback="cp -a $backup_dir/. $NGINX_DIR/ && nginx -t && systemctl reload nginx"
        printf "${RED}✗ nginx rejected the rendered configuration${RESET}\n" >&2
        echo "  the previous files are in $backup_dir" >&2
        echo "  rollback: $warn_rollback" >&2
        exit 1
    fi
    systemctl reload nginx
    ok "applied; backup in $backup_dir"
    echo "  rollback: cp -a $backup_dir/. $NGINX_DIR/ && nginx -t && systemctl reload nginx"
    exit 0
fi

# Default: render next to the caller, so nothing on the host changes.
OUT_DIR="${OUT_DIR:-$PWD/rendered}"
render "$OUT_DIR/$MESH_DOMAIN_HOST"
install -m 644 "$SNIPPET" "$OUT_DIR/antigravity-mesh-mcp.conf"
ok "rendered into $OUT_DIR"
echo "  vhost   : $OUT_DIR/$MESH_DOMAIN_HOST"
echo "  snippet : $OUT_DIR/antigravity-mesh-mcp.conf"
echo "  apply   : $0 --apply"
