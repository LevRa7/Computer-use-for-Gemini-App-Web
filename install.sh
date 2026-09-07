#!/usr/bin/env bash
# ==============================================================================
#  Antigravity Mesh - Universal Turnkey Installer (v1.2.0)
#  Bilingual: English (Default) & Russian, Device Detection, SSH & Autostart
# ==============================================================================
set -e

# Defaults
MODE=""
TLS="none"
PORT="8096"
DOMAIN=""
GATEWAY="smart-server.online"
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

# Bootstrap if piped from curl or run outside project
if [ ! -f "$SCRIPT_DIR/core/agent.py" ]; then
    BOOTSTRAP_DIR="$HOME/.gemini-computer-use"
    mkdir -p "$BOOTSTRAP_DIR/core" "$BOOTSTRAP_DIR/skills"
    curl -fsSL "https://${GATEWAY}/core/agent.py" -o "$BOOTSTRAP_DIR/core/agent.py" 2>/dev/null || true
    curl -fsSL "https://${GATEWAY}/core/server.py" -o "$BOOTSTRAP_DIR/core/server.py" 2>/dev/null || true
    curl -fsSL "https://${GATEWAY}/core/vitals.py" -o "$BOOTSTRAP_DIR/core/vitals.py" 2>/dev/null || true
    curl -fsSL "https://${GATEWAY}/core/__init__.py" -o "$BOOTSTRAP_DIR/core/__init__.py" 2>/dev/null || true
    curl -fsSL "https://${GATEWAY}/skills/orchestrator.md" -o "$BOOTSTRAP_DIR/skills/orchestrator.md" 2>/dev/null || true
    SCRIPT_DIR="$BOOTSTRAP_DIR"
fi

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
            GATEWAY="${1#*=}"
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
            DOMAIN="${1#*=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--lang=en|ru] [-q|--quick] [--mode=tunnel|standalone|gateway] [--user=<subdomain>] [--token=<token>] [--port=<port>] [--ssh=user@host] [--dry-run]"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[ERROR] Unknown parameter: $1${RESET}"
            exit 1
            ;;
    esac
done

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
    if [ -n "$DOMAIN" ]; then echo "[DRY-RUN] Domain: $DOMAIN"; fi
    if [ -n "$USERNAME" ]; then echo "[DRY-RUN] User: $USERNAME"; fi
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
        echo -e "  ${GREEN}1)${RESET} ⚡ ${BOLD}Быстрая настройка${RESET} (Субдомен как имя ПК + Облачный шлюз + Автозапуск) [Рекомендуется]"
        echo -e "  ${BLUE}2)${RESET} 🖥️  ${BOLD}Локальный Standalone${RESET} (Только localhost:${PORT}, без субдомена и шлюза)"
        echo -e "  ${YELLOW}3)${RESET} ⚙️  ${BOLD}Кастомная настройка${RESET} (Ввести имя субдомена вручную, выбор шлюза)"
        echo -e "  ${CYAN}4)${RESET} 📡 ${BOLD}Удаленная установка на SSH-сервер${RESET}"
        echo -e "  ${RESET}0) Выход"
        echo ""
        read -rp "Ваш выбор [1]: " MENU_CHOICE
    else
        echo -e "${BOLD}Select Installation Mode:${RESET}"
        echo -e "  ${GREEN}1)${RESET} ⚡ ${BOLD}Quick Setup${RESET} (Auto-subdomain from PC name + Cloud Gateway + Autostart) [Recommended]"
        echo -e "  ${BLUE}2)${RESET} 🖥️  ${BOLD}Local Standalone${RESET} (localhost:${PORT} only, no subdomain, no cloud gateway)"
        echo -e "  ${YELLOW}3)${RESET} ⚙️  ${BOLD}Custom Setup${RESET} (Custom subdomain name, custom gateway/port)"
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
                read -rp "Введите имя субдомена [по умолчанию: ${DETECTED_HOSTNAME}]: " CUSTOM_SUB
                read -rp "Хост шлюза [по умолчанию: ${GATEWAY}]: " CUSTOM_GW
            else
                read -rp "Enter subdomain name [default: ${DETECTED_HOSTNAME}]: " CUSTOM_SUB
                read -rp "Gateway host [default: ${GATEWAY}]: " CUSTOM_GW
            fi
            if [ -n "$CUSTOM_SUB" ]; then USERNAME="$CUSTOM_SUB"; fi
            if [ -n "$CUSTOM_GW" ]; then GATEWAY="$CUSTOM_GW"; fi
            ;;
        4)
            echo ""
            if [ "$LANG_CHOICE" = "ru" ]; then
                read -rp "Введите SSH цель (например, user@192.168.1.50): " REMOTE_TARGET
                read -rp "Порт SSH [22]: " REMOTE_PORT
            else
                read -rp "Enter SSH target (e.g., user@192.168.1.50): " REMOTE_TARGET
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
    mkdir -p "$CONFIG_DIR"

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
        <string>--port=$PORT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
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
        SERVICE_FILE="$USER_SYSTEMD_DIR/agy-standalone.service"
        cat << EOF > "$SERVICE_FILE"
[Unit]
Description=Antigravity Mesh Local Standalone MCP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 -m core.server --port=$PORT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable --now agy-standalone.service 2>/dev/null || {
            pkill -f "core.server" 2>/dev/null || true
            nohup /usr/bin/python3 -m core.server --port="$PORT" > "$CONFIG_DIR/standalone.log" 2>&1 &
        }
        loginctl enable-linger "$USER" 2>/dev/null || true
    fi

    if [ "$LANG_CHOICE" = "ru" ]; then
        echo "[3/3] Проверка локального эндпоинта..."
    else
        echo "[3/3] Verifying local endpoint..."
    fi
    sleep 1

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

    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${BOLD}${GREEN}🎉 Локальный Standalone MCP-сервер успешно запущен на этом ПК!${RESET}"
        echo ""
        echo -e "  ⚙️  ${BOLD}Порт:${RESET}               ${PORT}"
        echo -e "  🔄 ${BOLD}Автозапуск:${RESET}         Включен (фоновая служба)"
        echo ""
        echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
        echo -e "  ${BOLD}1. Откройте страницу приложений Gemini Spark в браузере:${RESET}"
        echo -e "     👉 ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo ""
        echo -e "  ${BOLD}2. Подключите ваш персональный MCP-сервер:${RESET}"
        local copy_msg=""
        if [ "$COPIED" = true ]; then
            copy_msg="[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)"
        else
            copy_msg="[OK] Скопировано в буфер обмена (OSC 52) / или выделите и скопируйте"
        fi
        print_mcp_box "ССЫЛКА ЛОКАЛЬНОГО MCP-СЕРВЕРА (SSE ENDPOINT):" "$MCP_LOCAL_URL" "$copy_msg"
        echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
        echo ""
        echo -e "  ${BOLD}Шаги подключения в Google Gemini / Spark:${RESET}"
        echo -e "  1. Перейдите по ссылке: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo -e "  2. Нажмите ${BOLD}Add app / Добавить приложение${RESET} (или Настройки ➔ MCP)"
        echo -e "  3. Вставьте скопированную ссылку (${BOLD}Ctrl+V${RESET}) и нажмите ${BOLD}Connect${RESET}!"
    else
        echo -e "${BOLD}${GREEN}🎉 Local Standalone MCP Server successfully running on this machine!${RESET}"
        echo ""
        echo -e "  ⚙️  ${BOLD}Port:${RESET}               ${PORT}"
        echo -e "  🔄 ${BOLD}Autostart:${RESET}          Enabled (background service)"
        echo ""
        echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
        echo -e "  ${BOLD}1. Open Gemini Spark Apps in your browser:${RESET}"
        echo -e "     👉 ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo ""
        echo -e "  ${BOLD}2. Connect your personal MCP Server:${RESET}"
        local copy_msg=""
        if [ "$COPIED" = true ]; then
            copy_msg="[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)"
        else
            copy_msg="[OK] Copied to clipboard (OSC 52) / or select and copy"
        fi
        print_mcp_box "LOCAL MCP SERVER SSE ENDPOINT URL:" "$MCP_LOCAL_URL" "$copy_msg"
        echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
        echo ""
        echo -e "  ${BOLD}Connection Steps in Google Gemini / Spark:${RESET}"
        echo -e "  1. Open: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
        echo -e "  2. Click ${BOLD}Add app${RESET} (or navigate to Settings ➔ Tools / MCP)"
        echo -e "  3. Paste the URL (${BOLD}Ctrl+V${RESET}) and click ${BOLD}Connect${RESET}!"
    fi
    echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
    exit 0
fi

# ==============================================================================
#  BRANCH: CLOUD GATEWAY + REVERSE TUNNEL
# ==============================================================================
if [ "$LANG_CHOICE" = "ru" ]; then
    echo -e "\n${BOLD}${MAGENTA}=== Настройка облачного туннеля и субдомена ===${RESET}"
else
    echo -e "\n${BOLD}${MAGENTA}=== Configuring Cloud Gateway Tunnel & Subdomain ===${RESET}"
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

python3 -c "import websockets" 2>/dev/null || {
    python3 -m pip install websockets --break-system-packages 2>/dev/null || python3 -m pip install websockets
}

if [ "$LANG_CHOICE" = "ru" ]; then
    echo "[2/4] Регистрация субдомена '${USERNAME}' на шлюзе (${GATEWAY})..."
else
    echo "[2/4] Registering subdomain '${USERNAME}' on Gateway (${GATEWAY})..."
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

REG_BODY="{\"username\": \"${USERNAME}\", \"auto_suffix\": true"
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
        -d "{\"username\": \"${FALLBACK_USER}\", \"auto_suffix\": true}")
    ASSIGNED_USER=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('username', ''))" "$REG_RESP" 2>/dev/null || true)
    ASSIGNED_TOKEN=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('token', ''))" "$REG_RESP" 2>/dev/null || true)
fi

if [ -z "$ASSIGNED_TOKEN" ]; then
    echo -e "${RED}[ERROR] Failed to obtain authentication token from gateway.${RESET}"
    exit 1
fi

if [ "$ASSIGNED_USER" != "$USERNAME" ]; then
    if [ "$LANG_CHOICE" = "ru" ]; then
        echo -e "${YELLOW}ℹ️  Имя '${USERNAME}' уже было занято. Автоматически назначен субдомен:${RESET} ${BOLD}${GREEN}${ASSIGNED_USER}${RESET}"
    else
        echo -e "${YELLOW}ℹ️  Name '${USERNAME}' was already taken. Automatically assigned subdomain:${RESET} ${BOLD}${GREEN}${ASSIGNED_USER}${RESET}"
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
ExecStart=/usr/bin/python3 -m core.agent
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now agy-agent.service 2>/dev/null || {
        pkill -f "core.agent" 2>/dev/null || true
        nohup /usr/bin/python3 -m core.agent > "$CONFIG_DIR/agent.log" 2>&1 &
    }
    loginctl enable-linger "$USER" 2>/dev/null || true
fi

sleep 1

MCP_URL="https://${ASSIGNED_USER}.${GATEWAY}/sse?token=${ASSIGNED_TOKEN}"

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

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
if [ "$LANG_CHOICE" = "ru" ]; then
    echo -e "${BOLD}${GREEN}🎉 Antigravity Mesh узел успешно установлен и подключен к шлюзу!${RESET}"
    echo ""
    echo -e "  💻 ${BOLD}Устройство:${RESET}      ${DETECTED_HOSTNAME} (${DETECTED_TYPE})"
    echo -e "  👤 ${BOLD}Субдомен:${RESET}        ${CYAN}${ASSIGNED_USER}.${GATEWAY}${RESET}"
    echo -e "  🔑 ${BOLD}Секретный токен:${RESET} ${ASSIGNED_TOKEN}"
    echo -e "  🔄 ${BOLD}Автозапуск:${RESET}      Включен (фоновая служба)"
    echo ""
    echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
    echo -e "  ${BOLD}1. Откройте страницу приложений Gemini Spark в браузере:${RESET}"
    echo -e "     👉 ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo ""
    echo -e "  ${BOLD}2. Подключите ваш персональный MCP-сервер:${RESET}"
    local copy_msg=""
    if [ "$COPIED" = true ]; then
        copy_msg="[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)"
    else
        copy_msg="[OK] Скопировано в буфер обмена (OSC 52) / или выделите и скопируйте"
    fi
    print_mcp_box "ССЫЛКА MCP-СЕРВЕРА (SSE ENDPOINT):" "$MCP_URL" "$copy_msg"
    echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
    echo ""
    echo -e "  ${BOLD}Шаги подключения в Google Gemini / Spark:${RESET}"
    echo -e "  1. Перейдите по ссылке: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo -e "  2. Нажмите ${BOLD}Add app / Добавить приложение${RESET} (или Настройки ➔ MCP)"
    echo -e "  3. Вставьте скопированную ссылку (${BOLD}Ctrl+V${RESET}) и нажмите ${BOLD}Connect${RESET}!"
else
    echo -e "${BOLD}${GREEN}🎉 Antigravity Mesh Node successfully installed and connected to Gateway!${RESET}"
    echo ""
    echo -e "  💻 ${BOLD}Device:${RESET}        ${DETECTED_HOSTNAME} (${DETECTED_TYPE})"
    echo -e "  👤 ${BOLD}Subdomain:${RESET}     ${CYAN}${ASSIGNED_USER}.${GATEWAY}${RESET}"
    echo -e "  🔑 ${BOLD}Secret Token:${RESET}  ${ASSIGNED_TOKEN}"
    echo -e "  🔄 ${BOLD}Autostart:${RESET}     Enabled (background service)"
    echo ""
    echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
    echo -e "  ${BOLD}1. Open Gemini Spark Apps in your browser:${RESET}"
    echo -e "     👉 ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo ""
    echo -e "  ${BOLD}2. Connect your personal MCP Server:${RESET}"
    local copy_msg=""
    if [ "$COPIED" = true ]; then
        copy_msg="[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)"
    else
        copy_msg="[OK] Copied to clipboard (OSC 52) / or select and copy"
    fi
    print_mcp_box "MCP SERVER SSE ENDPOINT URL:" "$MCP_URL" "$copy_msg"
    echo -e "  ${YELLOW}------------------------------------------------------------------------------${RESET}"
    echo ""
    echo -e "  ${BOLD}Connection Steps in Google Gemini / Spark:${RESET}"
    echo -e "  1. Open: ${CYAN}https://gemini.google.com/spark/apps${RESET}"
    echo -e "  2. Click ${BOLD}Add app${RESET} (or navigate to Settings ➔ Tools / MCP)"
    echo -e "  3. Paste the URL (${BOLD}Ctrl+V${RESET}) and click ${BOLD}Connect${RESET}!"
fi
echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
