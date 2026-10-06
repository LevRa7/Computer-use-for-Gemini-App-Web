#!/usr/bin/env bash
# ==============================================================================
#  Antigravity Mesh - Universal Turnkey Installer (v0.1.2)
#  Bilingual: English (Default) & Russian, Device Detection, SSH & Autostart
# ==============================================================================
set -e

# Defaults
MODE=""
TLS="none"
PORT="8096"
DOMAIN=""
GATEWAY=""
USERNAME=""
TOKEN=""
DRY_RUN=false
QUICK=false
SSH_TARGET=""
SSH_PORT="22"
LANG_CHOICE=""
EXPLICIT_LANG=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
[ -z "$SCRIPT_DIR" ] && SCRIPT_DIR="."

# ------------------------------------------------------------------------------
#  PUBLIC DOMAIN - resolved from the one source of truth
#
#  The domain is declared exactly once, in core/domain.py. Everything below asks
#  that same chain, in the same order, and this is the ONLY place in this script
#  that picks a hostname:
#
#    1. an explicit --domain= / --gateway= on the command line;
#    2. MESH_PUBLIC_URL in the environment;
#    3. AGY_PUBLIC_BASE_URL in the environment (legacy alias, same meaning);
#    4. the domain file - MESH_DOMAIN_FILE when set, otherwise
#       /etc/antigravity-mesh/domain.env, or on Windows/Git Bash
#       %USERPROFILE%\.config\antigravity-mesh\domain.env;
#    5. $PUBLISHED_DOMAIN - the domain this copy was published with. The repository
#       carries the placeholder __MESH_DOMAIN__ here and deploy_gateway.sh rewrites
#       that line while publishing, so a served copy already knows its domain;
#    6. core/domain.py's DEFAULT_PUBLIC_BASE_URL, but only when that module sits
#       next to this script (a repository checkout, or a node that already
#       bootstrapped). A clone has no published value and refusing to install from
#       a clone would be wrong.
#
#  If none of them yields a real hostname the installer stops: registering a node
#  against a domain that does not resolve produces a node that can never connect
#  and an autostart entry that retries forever.
# ------------------------------------------------------------------------------
DOMAIN_PLACEHOLDER="__MESH_DOMAIN__"
# deploy_gateway.sh rewrites THIS line only (it is anchored on PUBLISHED_DOMAIN=),
# so the placeholder token above survives and still proves whether this copy was
# published. In the repository the two are equal, which means "not published".
PUBLISHED_DOMAIN="__MESH_DOMAIN__"

case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) DEFAULT_DOMAIN_DIR="${USERPROFILE:-$HOME}/.config/antigravity-mesh" ;;
    *)                    DEFAULT_DOMAIN_DIR="/etc/antigravity-mesh" ;;
esac
DEFAULT_DOMAIN_FILE="${MESH_DOMAIN_FILE:-$DEFAULT_DOMAIN_DIR/domain.env}"

# https://mesh.example.com/path/ -> mesh.example.com (the authority only)
normalise_host() {
    printf '%s' "$1" | sed -e 's|^[A-Za-z][A-Za-z0-9+.-]*://||' -e 's|/.*$||' \
        -e 's|^[[:space:]]*||' -e 's|[[:space:]]*$||'
}

# A KEY=VALUE file without its leading UTF-8 BOM (Windows PowerShell writes one).
strip_bom() {
    if [ "$(head -c 3 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" = "efbbbf" ]; then
        tail -c +4 "$1"
    else
        cat "$1"
    fi
}

# MESH_PUBLIC_URL outranks the legacy alias, whatever their order in the file.
domain_file_value() {
    local file="$1" value=""
    if [ ! -f "$file" ]; then
        return 0
    fi
    value="$(strip_bom "$file" | sed -n 's/^[[:space:]]*MESH_PUBLIC_URL[[:space:]]*=[[:space:]]*//p' | head -n 1)"
    if [ -z "$value" ]; then
        value="$(strip_bom "$file" | sed -n 's/^[[:space:]]*AGY_PUBLIC_BASE_URL[[:space:]]*=[[:space:]]*//p' | head -n 1)"
    fi
    printf '%s' "$value" | tr -d '"' | tr -d "'" | tr -d '\r' | sed -e 's|^[[:space:]]*||' -e 's|[[:space:]]*$||'
}

# The project default, taken from core/domain.py when that file is available here.
module_default_domain() {
    local module="$SCRIPT_DIR/core/domain.py" value=""
    if [ ! -f "$module" ]; then
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        value="$(cd "$SCRIPT_DIR" 2>/dev/null && python3 -c 'from core import domain; print(domain.DEFAULT_PUBLIC_BASE_URL)' 2>/dev/null)" || true
    fi
    if [ -z "$value" ]; then
        value="$(sed -n 's/^DEFAULT_PUBLIC_BASE_URL[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' "$module" | head -n 1)"
    fi
    printf '%s' "$value"
}

resolve_domain() {
    local explicit_value="${1:-}" candidate=""
    if [ -n "$explicit_value" ]; then
        printf '%s' "$(normalise_host "$explicit_value")"
        return 0
    fi
    for candidate in "${MESH_PUBLIC_URL:-}" "${AGY_PUBLIC_BASE_URL:-}"; do
        if [ -n "$candidate" ]; then
            printf '%s' "$(normalise_host "$candidate")"
            return 0
        fi
    done
    candidate="$(domain_file_value "$DEFAULT_DOMAIN_FILE")"
    if [ -n "$candidate" ]; then
        printf '%s' "$(normalise_host "$candidate")"
        return 0
    fi
    if [ -n "$PUBLISHED_DOMAIN" ] && [ "$PUBLISHED_DOMAIN" != "$DOMAIN_PLACEHOLDER" ]; then
        printf '%s' "$(normalise_host "$PUBLISHED_DOMAIN")"
        return 0
    fi
    candidate="$(module_default_domain)"
    if [ -n "$candidate" ]; then
        printf '%s' "$(normalise_host "$candidate")"
        return 0
    fi
    printf '%s' "$DOMAIN_PLACEHOLDER"
}

# Downloading the node code needs the domain, and so does every cloud-gateway
# mode. A standalone install that already has the code next to it needs neither.
domain_required() {
    if [ ! -f "$SCRIPT_DIR/core/agent.py" ]; then
        return 0
    fi
    if [ "$MODE" != "standalone" ]; then
        return 0
    fi
    return 1
}

CONFIG_DIR="$HOME/.config/antigravity-mesh"
CONFIG_FILE="$CONFIG_DIR/agent.env"

# Colors
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
BLUE="\033[0;34m"
MAGENTA="\033[0;35m"
RESET="\033[0m"

ensure_python() {
    if command -v python3 >/dev/null 2>&1; then
        return 0
    fi
    [ "$LANG_CHOICE" = "ru" ] && echo -e "\033[1;33m[!] Python 3 не найден. Автоматическая установка системных пакетов...\033[0m" || echo -e "\033[1;33m[!] Python 3 not found. Installing system packages...\033[0m"
    if command -v apt-get >/dev/null 2>&1; then
        [ "$(id -u)" -eq 0 ] && apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv curl || sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv curl
    elif command -v dnf >/dev/null 2>&1; then
        [ "$(id -u)" -eq 0 ] && dnf install -y -q python3 python3-pip curl || sudo dnf install -y -q python3 python3-pip curl
    elif command -v yum >/dev/null 2>&1; then
        [ "$(id -u)" -eq 0 ] && yum install -y -q python3 python3-pip curl || sudo yum install -y -q python3 python3-pip curl
    elif command -v pacman >/dev/null 2>&1; then
        [ "$(id -u)" -eq 0 ] && pacman -Sy --noconfirm python python-pip curl || sudo pacman -Sy --noconfirm python python-pip curl
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache python3 py3-pip curl
    elif [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        brew install python3
    fi
}

ensure_dependencies() {
    ensure_python
    PYTHON_BIN="$(command -v python3 || echo "/usr/bin/python3")"
    if ! "$PYTHON_BIN" -c "import websockets" 2>/dev/null; then
        [ "$LANG_CHOICE" = "ru" ] && echo "[1/4] Автономная установка зависимостей (websockets)..." || echo "[1/4] Installing dependencies (websockets)..."
        "$PYTHON_BIN" -m pip install websockets --break-system-packages 2>/dev/null || \
        "$PYTHON_BIN" -m pip install websockets 2>/dev/null || {
            mkdir -p "$CONFIG_DIR"
            local VENV_DIR="$CONFIG_DIR/venv"
            if ! "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null; then
                if command -v apt-get >/dev/null 2>&1; then
                    [ "$(id -u)" -eq 0 ] && apt-get install -y -qq python3-venv 2>/dev/null || sudo apt-get install -y -qq python3-venv 2>/dev/null || true
                    "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null || true
                fi
            fi
            if [ -f "$VENV_DIR/bin/pip" ]; then
                "$VENV_DIR/bin/pip" install --quiet websockets
                PYTHON_BIN="$VENV_DIR/bin/python3"
            fi
        }
    fi
    PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || echo "/usr/bin/python3")}"
}


# Parse CLI args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --lang=*)
            LANG_CHOICE="${1#*=}"
            EXPLICIT_LANG=true
            shift
            ;;
        -q|--quick)
            QUICK=true
            shift
            ;;
        --mode=*)
            MODE="${1#*=}"
            shift
            ;;
        --user=*)
            USERNAME="${1#*=}"
            shift
            ;;
        --token=*)
            TOKEN="${1#*=}"
            shift
            ;;
        --gateway=*)
            # Historical alias of --domain=: both are the explicit override.
            DOMAIN="${1#*=}"
            shift
            ;;
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        --ssh=*)
            SSH_ARG="${1#*=}"
            if [[ "$SSH_ARG" =~ ^([^:]+):([0-9]+)$ ]]; then
                SSH_TARGET="${BASH_REMATCH[1]}"
                SSH_PORT="${BASH_REMATCH[2]}"
            else
                SSH_TARGET="$SSH_ARG"
            fi
            shift
            ;;
        --tls=*)
            TLS="${1#*=}"
            shift
            ;;
        --domain=*)
            # The shared public domain that serves every node. This is NOT a
            # per-device subdomain: nodes are selected with ?user=<node>.
            DOMAIN="${1#*=}"
            GATEWAY="$DOMAIN"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--lang=en|ru] [-q|--quick] [--mode=tunnel|standalone|gateway] [--user=<node-name>] [--token=<token>] [--domain=<shared-domain>] [--port=<port>] [--ssh=user@host] [--dry-run]"
            echo ""
            echo "All nodes use ONE shared domain; the node is selected by ?user=<node-name>:"
            echo "  MCP URL: https://<shared-domain>/sse?user=<node-name>&token=<token>"
            echo ""
            echo "The shared domain is resolved from the one place that declares it, in this order:"
            echo "  --domain= / --gateway=, then MESH_PUBLIC_URL, then AGY_PUBLIC_BASE_URL,"
            echo "  then ${DEFAULT_DOMAIN_FILE}, then the value this copy was published with,"
            echo "  then core/domain.py's default when the repository is present."
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[ERROR] Unknown parameter: $1${RESET}"
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
#  Resolve the shared domain, then fetch the node code from it.
#
#  Order matters: the CLI arguments are parsed first (so --domain= counts), the
#  domain is resolved second, and only then is the bootstrap downloaded - from
#  the resolved domain, never from a name baked into this file.
# ------------------------------------------------------------------------------
GATEWAY="$(resolve_domain "$DOMAIN")"

if [ "$GATEWAY" = "$DOMAIN_PLACEHOLDER" ]; then
    if domain_required; then
        echo ""
        if [ "$LANG_CHOICE" = "ru" ]; then
            echo -e "${RED}${BOLD}[ОШИБКА] Домен шлюза не задан.${RESET}"
            echo -e "  В этой копии установщика остался плейсхолдер ${BOLD}${DOMAIN_PLACEHOLDER}${RESET}: она не была опубликована"
            echo -e "  скриптом deploy_gateway.sh, а домена нет ни в окружении, ни в файле ${BOLD}${DEFAULT_DOMAIN_FILE}${RESET}."
            echo -e "  Укажите домен одним из способов:"
            echo -e "    ${CYAN}./install.sh --domain=<общий-домен>${RESET}"
            echo -e "    ${CYAN}MESH_PUBLIC_URL=https://<общий-домен> ./install.sh${RESET}"
            echo -e "    ${CYAN}echo 'MESH_PUBLIC_URL=https://<общий-домен>' | sudo tee ${DEFAULT_DOMAIN_FILE}${RESET}"
            echo -e "  ${YELLOW}Установка остановлена: регистрация на несуществующем домене не выполняется, ничего не создано.${RESET}"
        else
            echo -e "${RED}${BOLD}[ERROR] No gateway domain configured.${RESET}"
            echo -e "  This copy of the installer still carries the ${BOLD}${DOMAIN_PLACEHOLDER}${RESET} placeholder: it was not"
            echo -e "  published by deploy_gateway.sh, and no domain was found in the environment or in ${BOLD}${DEFAULT_DOMAIN_FILE}${RESET}."
            echo -e "  Pass the domain in one of these ways:"
            echo -e "    ${CYAN}./install.sh --domain=<shared-domain>${RESET}"
            echo -e "    ${CYAN}MESH_PUBLIC_URL=https://<shared-domain> ./install.sh${RESET}"
            echo -e "    ${CYAN}echo 'MESH_PUBLIC_URL=https://<shared-domain>' | sudo tee ${DEFAULT_DOMAIN_FILE}${RESET}"
            echo -e "  ${YELLOW}Stopping: a node is never registered against a domain that does not exist, nothing was created.${RESET}"
        fi
        echo ""
        exit 1
    fi
    # Standalone install with the node code already next to this script: no domain
    # is needed, so this is a warning rather than a stop.
    GATEWAY=""
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${YELLOW}[!] Домен не задан - автономному режиму он не нужен, продолжаю.${RESET}"
    else
        echo -e "${YELLOW}[!] No domain configured - not needed for standalone mode, continuing.${RESET}"
    fi
fi

# Bootstrap when piped from curl or run outside the project: the node code is
# fetched from the SAME domain that was just resolved. core/domain.py travels with
# it, because the node asks that module for the domain as well.
if [ ! -f "$SCRIPT_DIR/core/agent.py" ]; then
    BOOTSTRAP_DIR="$HOME/.gemini-computer-use"
    mkdir -p "$BOOTSTRAP_DIR/core" "$BOOTSTRAP_DIR/skills"
    for f in core/agent.py core/server.py core/mcp_tools.py core/web_share.py core/domain.py core/vitals.py core/__init__.py skills/orchestrator.md; do
        bootstrap_tmp="$BOOTSTRAP_DIR/${f}.part"
        if curl -fsSL "https://${GATEWAY}/${f}" -o "$bootstrap_tmp" 2>/dev/null \
            || curl -fsSL "https://raw.githubusercontent.com/LevRa7/Computer-use-for-Gemini-App-Web/main/${f}" -o "$bootstrap_tmp" 2>/dev/null; then
            mv "$bootstrap_tmp" "$BOOTSTRAP_DIR/${f}"
        else
            rm -f "$bootstrap_tmp"
        fi
    done
    unset bootstrap_tmp
    SCRIPT_DIR="$BOOTSTRAP_DIR"
fi

# ------------------------------------------------------------------------------
#  SHARED-DOMAIN CONTRACT (canonical step)
#  Every node is reached through ONE public domain ($GATEWAY); the node is
#  selected by the ?user= query parameter. The installer never mints a
#  per-device subdomain: each extra hostname would need its own DNS record and
#  TLS SAN, and a name missing from the certificate fails the handshake with an
#  opaque "cannot connect to host" error in the Gemini client.
#     sse    https://<shared-domain>/sse?user=<node-name>&token=<token>
#     http   https://<shared-domain>/mcp?user=<node-name>&token=<token>
#     tunnel wss://<shared-domain>/ws/tunnel?user=<node-name>&token=<token>
#  $GATEWAY is read at call time so the interactive menu can still change it.
# ------------------------------------------------------------------------------
node_sse_url() { printf 'https://%s/sse?user=%s&token=%s' "$GATEWAY" "$1" "$2"; }

# Language prompt if interactive and not specified
if [ -z "$LANG_CHOICE" ]; then
    if [ "$QUICK" = false ] && [ -z "$MODE" ] && [ -t 0 ]; then
        echo -e "\n${BOLD}${CYAN}╔════════════════════════════════════════════════════════════════════════╗${RESET}"
        echo -e "${BOLD}${CYAN}║              🌐 LANGUAGE SELECTION / ВЫБОР ЯЗЫКА                       ║${RESET}"
        echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════════════════════════╝${RESET}"
        echo -e "  ${GREEN}1)${RESET} English (Default)"
        echo -e "  ${BLUE}2)${RESET} Русский"
        read -rp "Select / Выберите [1]: " LANG_INPUT
        if [ "$LANG_INPUT" = "2" ] || [ "$LANG_INPUT" = "ru" ] || [ "$LANG_INPUT" = "RU" ]; then
            LANG_CHOICE="ru"
        else
            LANG_CHOICE="en"
        fi
    else
        LANG_CHOICE="en"
    fi
fi

# Device detection
detect_device() {
    DETECTED_HOSTNAME=$(hostname -s 2>/dev/null || hostname)
    DETECTED_ARCH=$(uname -m)

    if [ "$(uname -s)" = "Darwin" ]; then
        DETECTED_OS="macOS $(sw_vers -productVersion 2>/dev/null || '')"
        if [ "$LANG_CHOICE" = "ru" ]; then
            DETECTED_TYPE="Apple Mac"
            sysctl -n hw.model 2>/dev/null | grep -qi "book" && DETECTED_TYPE="Apple MacBook (Ноутбук)"
        else
            DETECTED_TYPE="Apple Mac"
            sysctl -n hw.model 2>/dev/null | grep -qi "book" && DETECTED_TYPE="Apple MacBook (Laptop)"
        fi
    elif [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        DETECTED_OS="${PRETTY_NAME:-$NAME}"
    else
        DETECTED_OS=$(uname -s)
    fi

    if [ "$(uname -s)" != "Darwin" ]; then
        if [ "$LANG_CHOICE" = "ru" ]; then
            DETECTED_TYPE="Десктоп / Сервер"
            if [ -d /sys/class/power_supply ] && ls /sys/class/power_supply/BAT* 1>/dev/null 2>&1; then
                DETECTED_TYPE="Ноутбук (Laptop)"
            elif grep -q -i "microsoft" /proc/version 2>/dev/null; then
                DETECTED_TYPE="WSL (Windows Subsystem for Linux)"
            elif [ -f /.dockerenv ] || grep -q "docker\|containerd" /proc/1/cgroup 2>/dev/null; then
                DETECTED_TYPE="Контейнер (Docker/LXC)"
            elif command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt -q; then
                DETECTED_TYPE="Облачный сервер / VPS ($(systemd-detect-virt))"
            fi
        else
            DETECTED_TYPE="Desktop / Server"
            if [ -d /sys/class/power_supply ] && ls /sys/class/power_supply/BAT* 1>/dev/null 2>&1; then
                DETECTED_TYPE="Laptop"
            elif grep -q -i "microsoft" /proc/version 2>/dev/null; then
                DETECTED_TYPE="WSL (Windows Subsystem for Linux)"
            elif [ -f /.dockerenv ] || grep -q "docker\|containerd" /proc/1/cgroup 2>/dev/null; then
                DETECTED_TYPE="Container (Docker/LXC)"
            elif command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt -q; then
                DETECTED_TYPE="Cloud VPS ($(systemd-detect-virt))"
            fi
        fi
    fi
}

detect_device

print_device_info() {
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════════╗${RESET}"
        echo -e "${CYAN}║${RESET} ${BOLD}🔍 Обнаружено устройство:${RESET}"
        echo -e "${CYAN}║${RESET}   • Имя хоста   : ${GREEN}${DETECTED_HOSTNAME}${RESET}"
        echo -e "${CYAN}║${RESET}   • Тип         : ${YELLOW}${DETECTED_TYPE}${RESET}"
        echo -e "${CYAN}║${RESET}   • ОС          : ${DETECTED_OS}"
        echo -e "${CYAN}║${RESET}   • Архитектура : ${DETECTED_ARCH}"
        echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════════╝${RESET}"
    else
        echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════════╗${RESET}"
        echo -e "${CYAN}║${RESET} ${BOLD}🔍 Detected Device:${RESET}"
        echo -e "${CYAN}║${RESET}   • Hostname    : ${GREEN}${DETECTED_HOSTNAME}${RESET}"
        echo -e "${CYAN}║${RESET}   • Type        : ${YELLOW}${DETECTED_TYPE}${RESET}"
        echo -e "${CYAN}║${RESET}   • OS          : ${DETECTED_OS}"
        echo -e "${CYAN}║${RESET}   • Architecture: ${DETECTED_ARCH}"
        echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════════╝${RESET}"
    fi
}

print_mcp_box() {
    local title="$1"
    local url="$2"
    local copy_msg="$3"

    local max_len=${#title}
    [ ${#url} -gt $max_len ] && max_len=${#url}
    [ -n "$copy_msg" ] && [ ${#copy_msg} -gt $max_len ] && max_len=${#copy_msg}

    local width=$(( max_len + 4 ))
    [ $width -lt 84 ] && width=84

    local dashes=$(printf '%*s' "$width" '' | tr ' ' '-')
    local border="  +${dashes}+"
    local empty="  |$(printf '%*s' "$width" '')|"

    echo -e "${GREEN}${border}${RESET}"
    
    # Title
    local pad_title=$(( width - 2 - ${#title} ))
    printf "  ${GREEN}|${RESET} ${BOLD}%s${RESET}%*s ${GREEN}|${RESET}\n" "$title" "$pad_title" ""

    echo -e "${GREEN}${empty}${RESET}"

    # URL
    local pad_url=$(( width - 2 - ${#url} ))
    printf "  ${GREEN}|${RESET} ${BOLD}${YELLOW}%s${RESET}%*s ${GREEN}|${RESET}\n" "$url" "$pad_url" ""

    echo -e "${GREEN}${empty}${RESET}"

    # Copy message
    if [ -n "$copy_msg" ]; then
        local pad_copy=$(( width - 2 - ${#copy_msg} ))
        printf "  ${GREEN}|${RESET} ${BOLD}${GREEN}%s${RESET}%*s ${GREEN}|${RESET}\n" "$copy_msg" "$pad_copy" ""
    fi

    echo -e "${GREEN}${border}${RESET}"
}

# Dry-run
if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN] Simulating Antigravity Mesh installation..."
    echo "[DRY-RUN] Language: $LANG_CHOICE"
    echo "[DRY-RUN] Detected Host: $DETECTED_HOSTNAME ($DETECTED_TYPE)"
    echo "[DRY-RUN] Selected Mode: ${MODE:-standalone}"
    echo "[DRY-RUN] Port: $PORT"
    echo "[DRY-RUN] TLS: $TLS"
    if [ -n "$GATEWAY" ]; then echo "[DRY-RUN] Domain: $GATEWAY"; fi
    if [ -n "$USERNAME" ]; then
        echo "[DRY-RUN] User: $USERNAME"
        if [ -n "$GATEWAY" ]; then
            echo "[DRY-RUN] Canonical MCP URL: $(node_sse_url "$USERNAME" "${TOKEN:-<token>}")"
        fi
    fi
    if [ -n "$TOKEN" ]; then echo "[DRY-RUN] Token: set (standalone: stored in ${CONFIG_DIR}/standalone.env, chmod 600)"; fi
    echo "[DRY-RUN] Systemd service and dependencies check: OK"
    exit 0
fi

# SSH Remote install branch
if [ -n "$SSH_TARGET" ]; then
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${BOLD}${BLUE}=== Удаленная установка Antigravity Mesh через SSH ===${RESET}"
        echo -e "Целевой сервер: ${CYAN}${SSH_TARGET}${RESET} (порт: ${SSH_PORT})"
    else
        echo -e "${BOLD}${BLUE}=== Remote Antigravity Mesh SSH Installation ===${RESET}"
        echo -e "Target Server: ${CYAN}${SSH_TARGET}${RESET} (port: ${SSH_PORT})"
    fi

    SSH_BIN="ssh -o StrictHostKeyChecking=no"
    SCP_BIN="scp -o StrictHostKeyChecking=no"
    if [ -n "$SSHPASS" ] && command -v sshpass >/dev/null 2>&1; then
        SSH_BIN="sshpass -e $SSH_BIN"
        SCP_BIN="sshpass -e $SCP_BIN"
    fi

    $SSH_BIN -p "$SSH_PORT" "$SSH_TARGET" "mkdir -p ~/antigravity-mesh/core ~/antigravity-mesh/skills"
    $SCP_BIN -P "$SSH_PORT" -r "$SCRIPT_DIR/core"/* "$SSH_TARGET:~/antigravity-mesh/core/"
    $SCP_BIN -P "$SSH_PORT" "$SCRIPT_DIR/install.sh" "$SSH_TARGET:~/antigravity-mesh/"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        $SCP_BIN -P "$SSH_PORT" -r "$SCRIPT_DIR/skills"/* "$SSH_TARGET:~/antigravity-mesh/skills/" 2>/dev/null || true
    fi
    $SSH_BIN -p "$SSH_PORT" "$SSH_TARGET" "cd ~/antigravity-mesh && bash install.sh --quick --lang=$LANG_CHOICE"
    exit 0
fi

# Interactive Menu if no mode is specified and not in quick mode
if [ "$QUICK" = false ] && [ -z "$MODE" ] && [ -t 0 ]; then
    print_device_info
    echo ""
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${BOLD}Выберите режим установки:${RESET}"
        echo -e "  ${GREEN}1)${RESET} ⚡ ${BOLD}Быстрая настройка${RESET} (Имя узла как имя ПК + Общий домен + Автозапуск) [Рекомендуется]"
        echo -e "  ${BLUE}2)${RESET} 🖥️  ${BOLD}Локальный Standalone${RESET} (Только localhost:${PORT}, без облачного шлюза)"
        echo -e "  ${YELLOW}3)${RESET} ⚙️  ${BOLD}Кастомная настройка${RESET} (Ввести имя узла вручную, выбор общего домена)"
        echo -e "  ${CYAN}4)${RESET} 📡 ${BOLD}Удаленная установка на SSH-сервер${RESET}"
        echo -e "  ${RESET}0) Выход"
        echo ""
        read -rp "Ваш выбор [1]: " MENU_CHOICE
    else
        echo -e "${BOLD}Select Installation Mode:${RESET}"
        echo -e "  ${GREEN}1)${RESET} ⚡ ${BOLD}Quick Setup${RESET} (Node name from PC name + shared domain + Autostart) [Recommended]"
        echo -e "  ${BLUE}2)${RESET} 🖥️  ${BOLD}Local Standalone${RESET} (localhost:${PORT} only, no cloud gateway)"
        echo -e "  ${YELLOW}3)${RESET} ⚙️  ${BOLD}Custom Setup${RESET} (Custom node name, custom shared domain)"
        echo -e "  ${CYAN}4)${RESET} 📡 ${BOLD}Remote SSH Installation${RESET}"
        echo -e "  ${RESET}0) Exit"
        echo ""
        read -rp "Select [1]: " MENU_CHOICE
    fi
    MENU_CHOICE=${MENU_CHOICE:-1}

    case "$MENU_CHOICE" in
        1)
            MODE="tunnel"
            QUICK=true
            ;;
        2)
            MODE="standalone"
            ;;
        3)
            MODE="tunnel"
            echo ""
            if [ "$LANG_CHOICE" = "ru" ]; then
                read -rp "Введите имя узла [по умолчанию: ${DETECTED_HOSTNAME}]: " CUSTOM_SUB
                read -rp "Общий домен шлюза [по умолчанию: ${GATEWAY}]: " CUSTOM_GW
            else
                read -rp "Enter node name [default: ${DETECTED_HOSTNAME}]: " CUSTOM_SUB
                read -rp "Shared gateway domain [default: ${GATEWAY}]: " CUSTOM_GW
            fi
            if [ -n "$CUSTOM_SUB" ]; then USERNAME="$CUSTOM_SUB"; fi
            if [ -n "$CUSTOM_GW" ]; then GATEWAY="$CUSTOM_GW"; fi
            ;;
        4)
            echo ""
            if [ "$LANG_CHOICE" = "ru" ]; then
                read -rp "Введите SSH цель (например, user@<host-ip>): " REMOTE_TARGET
                read -rp "Порт SSH [22]: " REMOTE_PORT
            else
                read -rp "Enter SSH target (e.g., user@<host-ip>): " REMOTE_TARGET
                read -rp "SSH Port [22]: " REMOTE_PORT
            fi
            REMOTE_PORT=${REMOTE_PORT:-22}
            exec "$0" "--ssh=${REMOTE_TARGET}:${REMOTE_PORT}" "--lang=${LANG_CHOICE}"
            ;;
        0)
            echo "Cancelled."
            exit 0
            ;;
        *)
            MODE="tunnel"
            QUICK=true
            ;;
    esac
fi

if [ -z "$MODE" ]; then
    MODE="tunnel"
fi

print_device_info

# ==============================================================================
#  BRANCH: STANDALONE MODE
# ==============================================================================
if [ "$MODE" = "standalone" ]; then
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "\n${BOLD}${BLUE}=== Настройка локального Standalone FastMCP сервера ===${RESET}"
        echo "[1/3] Проверка окружения Python..."
    else
        echo -e "\n${BOLD}${BLUE}=== Setting up Local Standalone FastMCP Server ===${RESET}"
        echo "[1/3] Checking Python environment..."
    fi
    ensure_dependencies
    mkdir -p "$CONFIG_DIR"

    # Honour --token in standalone mode without embedding the secret in the
    # (world-readable) unit/plist: keep it in a 0600 env file for systemd
    # (EnvironmentFile) and use a 0600 plist environment block on macOS.
    # core.server reads MESH_TOKEN as the default for --token.
    STANDALONE_ENV_FILE="$CONFIG_DIR/standalone.env"
    ENV_FILE_LINE=""
    PLIST_ENV_BLOCK=""
    if [ -n "$TOKEN" ]; then
        printf 'MESH_TOKEN=%s\n' "$TOKEN" > "$STANDALONE_ENV_FILE"
        chmod 600 "$STANDALONE_ENV_FILE"
        ENV_FILE_LINE="EnvironmentFile=$STANDALONE_ENV_FILE"
        PLIST_TOKEN=$(printf '%s' "$TOKEN" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
        PLIST_ENV_BLOCK="    <key>EnvironmentVariables</key>
    <dict>
        <key>MESH_TOKEN</key>
        <string>${PLIST_TOKEN}</string>
    </dict>"
        if [ "$LANG_CHOICE" = "ru" ]; then
            echo -e "${GREEN}[✓] Токен авторизации включён (chmod 600).${RESET}"
        else
            echo -e "${GREEN}[OK] Authorization token enabled (chmod 600).${RESET}"
        fi
    else
        rm -f "$STANDALONE_ENV_FILE" 2>/dev/null || true
        if [ "$LANG_CHOICE" = "ru" ]; then
            echo -e "${YELLOW}[!] Токен не задан (--token): сервер доступен без авторизации на 127.0.0.1.${RESET}"
        else
            echo -e "${YELLOW}[!] No --token given: server is unauthenticated on 127.0.0.1.${RESET}"
        fi
    fi

    if [ "$LANG_CHOICE" = "ru" ]; then
        echo "[2/3] Настройка systemd автозапуска на ПК..."
    else
        echo "[2/3] Configuring autostart background service..."
    fi

    if [ "$(uname -s)" = "Darwin" ]; then
        LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
        mkdir -p "$LAUNCH_AGENTS"
        PLIST_FILE="$LAUNCH_AGENTS/com.antigravity.mesh.standalone.plist"
        cat << EOF > "$PLIST_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.antigravity.mesh.standalone</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>-m</string>
        <string>core.server</string>
        <string>--host=127.0.0.1</string>
        <string>--port=$PORT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
$PLIST_ENV_BLOCK
</dict>
</plist>
EOF
        if [ -n "$TOKEN" ]; then
            chmod 600 "$PLIST_FILE"
        fi
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        launchctl load -w "$PLIST_FILE" 2>/dev/null || true
    else
        USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
        mkdir -p "$USER_SYSTEMD_DIR"
        SERVICE_FILE="$USER_SYSTEMD_DIR/agy-standalone.service"
        cat << EOF > "$SERVICE_FILE"
[Unit]
Description=Antigravity Mesh Local Standalone MCP Server
After=network.target

[Service]
Type=simple
$ENV_FILE_LINE
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN -m core.server --host 127.0.0.1 --port=$PORT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable --now agy-standalone.service 2>/dev/null || {
            pkill -f "core.server" 2>/dev/null || true
            if [ -n "$TOKEN" ]; then
                nohup env MESH_TOKEN="$TOKEN" $PYTHON_BIN -m core.server --host 127.0.0.1 --port="$PORT" > "$CONFIG_DIR/standalone.log" 2>&1 &
            else
                nohup $PYTHON_BIN -m core.server --host 127.0.0.1 --port="$PORT" > "$CONFIG_DIR/standalone.log" 2>&1 &
            fi
        }
        loginctl enable-linger "$USER" 2>/dev/null || true
    fi

    if [ "$LANG_CHOICE" = "ru" ]; then
        echo "[3/3] Проверка локального эндпоинта..."
    else
        echo "[3/3] Verifying local endpoint..."
    fi
    HEALTH_URL="http://127.0.0.1:${PORT}/health"
    HEALTH_OK=false
    for _attempt in 1 2 3 4 5 6 7 8 9 10; do
        HTTP_CODE=$(curl -s -o /dev/null -m 2 -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || true)
        if [ "$HTTP_CODE" = "200" ]; then
            HEALTH_OK=true
            break
        fi
        sleep 1
    done
    if [ "$HEALTH_OK" != true ]; then
        if [ "$LANG_CHOICE" = "ru" ]; then
            echo -e "\033[1;31m[ОШИБКА] Standalone MCP-сервер не отвечает (HTTP ${HTTP_CODE:-нет ответа}): $HEALTH_URL\033[0m" >&2
            echo -e "  Проверьте статус: ${CYAN}systemctl --user status agy-standalone.service${RESET}" >&2
            echo -e "  Логи:             ${CYAN}journalctl --user -u agy-standalone.service -n 50 --no-pager${RESET}" >&2
        else
            echo -e "\033[1;31m[ERROR] Standalone MCP server is not responding (HTTP ${HTTP_CODE:-no response}): $HEALTH_URL\033[0m" >&2
            echo -e "  Check status: ${CYAN}systemctl --user status agy-standalone.service${RESET}" >&2
            echo -e "  Logs:         ${CYAN}journalctl --user -u agy-standalone.service -n 50 --no-pager${RESET}" >&2
        fi
        exit 1
    fi

    MCP_LOCAL_URL="http://localhost:${PORT}/sse"
    
    # Quick Copy to Clipboard
    COPIED=false
    B64_URL=$(printf "%s" "$MCP_LOCAL_URL" | base64 | tr -d '\r\n')
    printf "\033]52;c;%s\a" "$B64_URL" 2>/dev/null || true
    if command -v wl-copy >/dev/null 2>&1; then
        printf "%s" "$MCP_LOCAL_URL" | wl-copy 2>/dev/null && COPIED=true
    elif command -v xclip >/dev/null 2>&1; then
        printf "%s" "$MCP_LOCAL_URL" | xclip -selection clipboard 2>/dev/null && COPIED=true
    elif command -v xsel >/dev/null 2>&1; then
        printf "%s" "$MCP_LOCAL_URL" | xsel --clipboard --input 2>/dev/null && COPIED=true
    elif command -v pbcopy >/dev/null 2>&1; then
        printf "%s" "$MCP_LOCAL_URL" | pbcopy 2>/dev/null && COPIED=true
    elif command -v clip.exe >/dev/null 2>&1; then
        printf "%s" "$MCP_LOCAL_URL" | clip.exe 2>/dev/null && COPIED=true
    fi

    clear 2>/dev/null || printf "\033[2J\033[H" || true

    if [ "$LANG_CHOICE" = "ru" ]; then
        echo ""
        copy_msg=""
        if [ "$COPIED" = true ]; then
            copy_msg="[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)"
        else
            copy_msg="Скопируйте ссылку выше"
        fi
        print_mcp_box "ССЫЛКА ЛОКАЛЬНОГО MCP-СЕРВЕРА (СКОПИРУЙТЕ):" "$MCP_LOCAL_URL" "$copy_msg"
        echo ""
        echo -e "  ${BOLD}Куда вставлять ссылку в Google Gemini:${RESET}"
        echo -e "  1. Откройте в браузере: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo -e "  2. Нажмите ${BOLD}'Добавить приложение'${RESET} (Add app / Настройки MCP)"
        echo -e "  3. Вставьте скопированную ссылку в поле ${BOLD}'URL сервера'${RESET} (${BOLD}Ctrl+V${RESET}) и нажмите ${BOLD}Подключить${RESET}."
        echo ""
    else
        echo ""
        copy_msg=""
        if [ "$COPIED" = true ]; then
            copy_msg="[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)"
        else
            copy_msg="Copy the URL above"
        fi
        print_mcp_box "LOCAL MCP SERVER URL (COPY THIS):" "$MCP_LOCAL_URL" "$copy_msg"
        echo ""
        echo -e "  ${BOLD}Where to paste this URL in Google Gemini:${RESET}"
        echo -e "  1. Open in your browser: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo -e "  2. Click ${BOLD}'Add app'${RESET} (or navigate to MCP settings)"
        echo -e "  3. Paste the URL into the ${BOLD}'Server URL'${RESET} field (${BOLD}Ctrl+V${RESET}) and click ${BOLD}Connect${RESET}."
        echo ""
    fi
    exit 0
fi

# ==============================================================================
#  BRANCH: CLOUD GATEWAY + REVERSE TUNNEL
# ==============================================================================
if [ "$LANG_CHOICE" = "ru" ]; then
    echo -e "\n${BOLD}${MAGENTA}=== Настройка облачного туннеля (общий домен) ===${RESET}"
else
    echo -e "\n${BOLD}${MAGENTA}=== Configuring Cloud Gateway Tunnel (shared domain) ===${RESET}"
fi

if [ -z "$USERNAME" ]; then
    CLEAN_HOST=$(echo "$DETECTED_HOSTNAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
    USERNAME="$CLEAN_HOST"
fi
USERNAME=$(echo "$USERNAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$USERNAME" ] && USERNAME="mesh-node"

mkdir -p "$CONFIG_DIR"

if [ "$LANG_CHOICE" = "ru" ]; then
    echo "[1/4] Проверка Python зависимостей (websockets)..."
else
    echo "[1/4] Checking Python dependencies (websockets)..."
fi

ensure_dependencies

if [ "$LANG_CHOICE" = "ru" ]; then
    echo "[2/4] Регистрация узла '${USERNAME}' на общем домене (${GATEWAY})..."
else
    echo "[2/4] Registering node '${USERNAME}' on the shared domain (${GATEWAY})..."
fi

EXISTING_TOKEN=""
if [ -f "$CONFIG_FILE" ]; then
    CFG_USER=$(grep "MESH_USER" "$CONFIG_FILE" 2>/dev/null | cut -d '=' -f2 || true)
    CFG_TOKEN=$(grep "MESH_TOKEN" "$CONFIG_FILE" 2>/dev/null | cut -d '=' -f2 || true)
    if [ "$CFG_USER" = "$USERNAME" ] && [ -n "$CFG_TOKEN" ]; then
        EXISTING_TOKEN="$CFG_TOKEN"
    fi
fi

if [ -n "$TOKEN" ]; then
    REQ_TOKEN="$TOKEN"
elif [ -n "$EXISTING_TOKEN" ]; then
    REQ_TOKEN="$EXISTING_TOKEN"
else
    REQ_TOKEN=""
fi

DETECTED_MAC=""
if command -v python3 >/dev/null 2>&1; then
    DETECTED_MAC=$(python3 -c "import uuid; print(':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1]))" 2>/dev/null || true)
fi
if [ -z "$DETECTED_MAC" ] || [ "$DETECTED_MAC" = "00:00:00:00:00:00" ]; then
    for iface in /sys/class/net/*; do
        if [ -f "$iface/address" ] && [ "$(cat "$iface/type" 2>/dev/null)" = "1" ]; then
            addr=$(cat "$iface/address" 2>/dev/null)
            if [ -n "$addr" ] && [ "$addr" != "00:00:00:00:00:00" ]; then
                DETECTED_MAC="$addr"
                break
            fi
        fi
    done
fi

REG_BODY="{\"username\": \"${USERNAME}\", \"auto_suffix\": true, \"mac_address\": \"${DETECTED_MAC}\", \"os\": \"${DETECTED_OS}\""
if [ -n "$REQ_TOKEN" ]; then
    REG_BODY="${REG_BODY}, \"token\": \"${REQ_TOKEN}\""
fi
REG_BODY="${REG_BODY}}"

REG_RESP=$(curl -s -X POST "https://${GATEWAY}/api/register" \
    -H "Content-Type: application/json" \
    -d "$REG_BODY")

ASSIGNED_USER=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('username', ''))" "$REG_RESP" 2>/dev/null || true)
ASSIGNED_TOKEN=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('token', ''))" "$REG_RESP" 2>/dev/null || true)

if [ -z "$ASSIGNED_TOKEN" ]; then
    FALLBACK_USER="${USERNAME}-$(date +%s | tail -c 4)"
    REG_RESP=$(curl -s -X POST "https://${GATEWAY}/api/register" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"${FALLBACK_USER}\", \"auto_suffix\": true, \"mac_address\": \"${DETECTED_MAC}\", \"os\": \"${DETECTED_OS}\"}")
    ASSIGNED_USER=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('username', ''))" "$REG_RESP" 2>/dev/null || true)
    ASSIGNED_TOKEN=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('token', ''))" "$REG_RESP" 2>/dev/null || true)
fi

if [ -z "$ASSIGNED_TOKEN" ]; then
    echo -e "${RED}[ERROR] Failed to obtain authentication token from gateway.${RESET}"
    exit 1
fi

if [ "$ASSIGNED_USER" != "$USERNAME" ]; then
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${YELLOW}ℹ️  Имя '${USERNAME}' уже было занято. Автоматически назначено имя узла:${RESET} ${BOLD}${GREEN}${ASSIGNED_USER}${RESET}"
    else
        echo -e "${YELLOW}ℹ️  Name '${USERNAME}' was already taken. Automatically assigned node name:${RESET} ${BOLD}${GREEN}${ASSIGNED_USER}${RESET}"
    fi
fi

if [ "$LANG_CHOICE" = "ru" ]; then
    echo "[3/4] Сохранение конфигурации в ${CONFIG_FILE}..."
else
    echo "[3/4] Saving configuration to ${CONFIG_FILE}..."
fi

cat << EOF > "$CONFIG_FILE"
MESH_GATEWAY=${GATEWAY}
MESH_USER=${ASSIGNED_USER}
MESH_TOKEN=${ASSIGNED_TOKEN}
EOF
chmod 600 "$CONFIG_FILE"

if [ "$LANG_CHOICE" = "ru" ]; then
    echo "[4/4] Настройка и запуск службы автозапуска на ПК..."
else
    echo "[4/4] Configuring and starting background autostart service..."
fi

if [ "$(uname -s)" = "Darwin" ]; then
    LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$LAUNCH_AGENTS"
    PLIST_FILE="$LAUNCH_AGENTS/com.antigravity.mesh.agent.plist"
    cat << EOF > "$PLIST_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.antigravity.mesh.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>-m</string>
        <string>core.agent</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MESH_GATEWAY</key>
        <string>$GATEWAY</string>
        <key>MESH_USER</key>
        <string>$ASSIGNED_USER</string>
        <key>MESH_TOKEN</key>
        <string>$ASSIGNED_TOKEN</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load -w "$PLIST_FILE" 2>/dev/null || true
else
    USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$USER_SYSTEMD_DIR"
    cat << EOF > "$USER_SYSTEMD_DIR/agy-agent.service"
[Unit]
Description=Antigravity Mesh Reverse RPC Agent
After=network.target

[Service]
Type=simple
EnvironmentFile=$CONFIG_FILE
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN -m core.agent
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now agy-agent.service 2>/dev/null || {
        pkill -f "core.agent" 2>/dev/null || true
        nohup $PYTHON_BIN -m core.agent > "$CONFIG_DIR/agent.log" 2>&1 &
    }
    loginctl enable-linger "$USER" 2>/dev/null || true
fi

sleep 1

# Canonical shared-domain MCP URL (one domain for every node; ?user= selects it).
MCP_URL="$(node_sse_url "$ASSIGNED_USER" "$ASSIGNED_TOKEN")"

# Quick Copy to Clipboard
COPIED=false
B64_URL=$(printf "%s" "$MCP_URL" | base64 | tr -d '\r\n')
printf "\033]52;c;%s\a" "$B64_URL" 2>/dev/null || true
if command -v wl-copy >/dev/null 2>&1; then
    printf "%s" "$MCP_URL" | wl-copy 2>/dev/null && COPIED=true
elif command -v xclip >/dev/null 2>&1; then
    printf "%s" "$MCP_URL" | xclip -selection clipboard 2>/dev/null && COPIED=true
elif command -v xsel >/dev/null 2>&1; then
    printf "%s" "$MCP_URL" | xsel --clipboard --input 2>/dev/null && COPIED=true
elif command -v pbcopy >/dev/null 2>&1; then
    printf "%s" "$MCP_URL" | pbcopy 2>/dev/null && COPIED=true
elif command -v clip.exe >/dev/null 2>&1; then
    printf "%s" "$MCP_URL" | clip.exe 2>/dev/null && COPIED=true
fi

clear 2>/dev/null || printf "\033[2J\033[H" || true

if [ "$LANG_CHOICE" = "ru" ]; then
    echo ""
    copy_msg=""
    if [ "$COPIED" = true ]; then
        copy_msg="[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)"
    else
        copy_msg="Скопируйте ссылку выше"
    fi
    print_mcp_box "ССЫЛКА MCP-СЕРВЕРА ДЛЯ ПОДКЛЮЧЕНИЯ (СКОПИРУЙТЕ):" "$MCP_URL" "$copy_msg"
    echo ""
    echo -e "  ${BOLD}Куда вставлять ссылку в Google Gemini:${RESET}"
    echo -e "  1. Откройте в браузере: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo -e "  2. Нажмите ${BOLD}'Добавить приложение'${RESET} (Add app / Настройки MCP)"
    echo -e "  3. Вставьте скопированную ссылку в поле ${BOLD}'URL сервера'${RESET} (${BOLD}Ctrl+V${RESET}) и нажмите ${BOLD}Подключить${RESET}."
    echo ""
else
    echo ""
    copy_msg=""
    if [ "$COPIED" = true ]; then
        copy_msg="[OK] URL COPIED TO CLIPBOARD! (Paste with Ctrl+V)"
    else
        copy_msg="Copy the URL above"
    fi
    print_mcp_box "MCP SERVER CONNECTION URL (COPY THIS):" "$MCP_URL" "$copy_msg"
    echo ""
    echo -e "  ${BOLD}Where to paste this URL in Google Gemini:${RESET}"
    echo -e "  1. Open in your browser: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo -e "  2. Click ${BOLD}'Add app'${RESET} (or navigate to MCP settings)"
    echo -e "  3. Paste the URL into the ${BOLD}'Server URL'${RESET} field (${BOLD}Ctrl+V${RESET}) and click ${BOLD}Connect${RESET}."
    echo ""
fi
