#!/usr/bin/env bash
# ==============================================================================
#  Antigravity Mesh - resolve the public domain, once, for every shell script.
#
#  Sourced by deploy_gateway.sh and ops/nginx/render-domain.sh. The order below
#  is the same order core/domain.py implements for the runtime; keeping the shell
#  side in one file means "where does the domain come from" has a single answer
#  for scripts too.
#
#    1. MESH_PUBLIC_URL in the environment;
#    2. MESH_PUBLIC_URL (or the AGY_PUBLIC_BASE_URL alias) in the domain file -
#       /etc/antigravity-mesh/domain.env by default, MESH_DOMAIN_FILE overrides;
#    3. core/domain.py's DEFAULT_PUBLIC_BASE_URL, read from the checkout.
#
#  On success exports:
#    MESH_DOMAIN_URL   https://mesh.example.com   (no trailing slash)
#    MESH_DOMAIN_HOST  mesh.example.com
#    MESH_DOMAIN_SOURCE  human-readable origin, for logs
#    MESH_DOMAIN_FILE_RESOLVED  the domain file path that was consulted
#  and returns 0. Returns 1 when nothing yields a domain.
# ==============================================================================

mesh_resolve_domain() {
    local repo_dir="${1:-.}"
    local value="" candidate_python="" source=""

    MESH_DOMAIN_FILE_RESOLVED="${MESH_DOMAIN_FILE:-/etc/antigravity-mesh/domain.env}"

    if [ -n "${MESH_PUBLIC_URL:-}" ]; then
        value="$MESH_PUBLIC_URL"
        source="MESH_PUBLIC_URL environment variable"
    fi

    local domain_file="$MESH_DOMAIN_FILE_RESOLVED"
    if [ -z "$value" ] && [ -f "$domain_file" ]; then
        # Windows PowerShell writes a UTF-8 BOM, and it would sit in front of the
        # first key, so 'MESH_PUBLIC_URL=' would never match. Strip it first.
        local domain_text
        if [ "$(head -c 3 "$domain_file" 2>/dev/null | od -An -tx1 | tr -d ' \n')" = "efbbbf" ]; then
            domain_text="$(tail -c +4 "$domain_file")"
        else
            domain_text="$(cat "$domain_file")"
        fi
        value="$(printf '%s\n' "$domain_text" | sed -n 's/^[[:space:]]*MESH_PUBLIC_URL[[:space:]]*=[[:space:]]*//p' | head -n 1)"
        if [ -z "$value" ]; then
            value="$(printf '%s\n' "$domain_text" | sed -n 's/^[[:space:]]*AGY_PUBLIC_BASE_URL[[:space:]]*=[[:space:]]*//p' | head -n 1)"
        fi
        value="$(printf '%s' "$value" | tr -d '"' | tr -d "'" | tr -d '\r')"
        [ -n "$value" ] && source="$domain_file"
    fi

    if [ -z "$value" ]; then
        # Path candidates, in order. The Microsoft Store "python.exe" (and its
        # python3.exe twin) is an App Execution Alias: it is on PATH, it prints
        # nothing usable, and it stops working after a Store repair - the same trap
        # the Windows installer refuses to pin into an autostart entry. Skipping it
        # by path is what lets the real interpreter behind it be found, or makes the
        # resolver refuse honestly instead of trusting a stub.
        local resolved_python=""
        for candidate_python in python3 python py; do
            command -v "$candidate_python" >/dev/null 2>&1 || continue
            resolved_python="$(command -v "$candidate_python")"
            case "$resolved_python" in
                *[Ww]indows[Aa]pps*) continue ;;
            esac
            value="$("$resolved_python" -c "import sys; sys.path.insert(0, r'$repo_dir'); from core import domain; print(domain.DEFAULT_PUBLIC_BASE_URL)" 2>/dev/null || true)"
            [ -n "$value" ] && { source="$repo_dir/core/domain.py default"; break; }
        done
    fi

    if [ -z "$value" ]; then
        MESH_DOMAIN_URL=""; MESH_DOMAIN_HOST=""; MESH_DOMAIN_SOURCE=""
        export MESH_DOMAIN_URL MESH_DOMAIN_HOST MESH_DOMAIN_SOURCE MESH_DOMAIN_FILE_RESOLVED
        return 1
    fi

    case "$value" in
        http://*|https://*) ;;
        *) value="https://$value" ;;
    esac
    MESH_DOMAIN_URL="${value%/}"
    MESH_DOMAIN_HOST="$(printf '%s' "$MESH_DOMAIN_URL" | sed -e 's|^[A-Za-z][A-Za-z0-9+.-]*://||' -e 's|/.*$||')"
    MESH_DOMAIN_SOURCE="$source"
    export MESH_DOMAIN_URL MESH_DOMAIN_HOST MESH_DOMAIN_SOURCE MESH_DOMAIN_FILE_RESOLVED
    return 0
}
