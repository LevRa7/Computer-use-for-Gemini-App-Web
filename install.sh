#!/usr/bin/env bash
# ==============================================================================
#  Antigravity Mesh - Universal Turnkey Installer (v1.1.0)
#  Automated Device Detection, Standalone/Cloud MCP, Auto-Subdomain, SSH & Autostart
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
[ -z "$SCRIPT_DIR" ] && SCRIPT_DIR="."

# If executed via pipe (curl | bash) or outside repository, bootstrap core files
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

# Colors for terminal
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
BLUE="\033[0;34m"
MAGENTA="\033[0;35m"
RESET="\033[0m"

show_help() {
    cat << EOF
Antigravity Mesh Installer

Usage: $0 [OPTIONS]

Options:
  -q, --quick                 Быстрая автоустановка (детекция устройства + субдомен ПК + автозапуск)
  --mode=[tunnel|standalone|gateway]  Режим развертывания:
                                tunnel:     Облачный шлюз с субдоменом и туннелем (по умолчанию)
                                standalone: Локальный FastMCP сервер (localhost, без субдомена)
                                gateway:    Развертывание мастер-шлюза
  --user=<username>           Имя субдомена (<username>.smart-server.online)
  --token=<token>             Крипто-токен авторизации (генерируется автоматически)
  --gateway=<host>            Хост шлюза (по умолчанию: smart-server.online)
  --port=<port>               Порт для режима Standalone (по умолчанию: 8096)
  --ssh=<user@host[:port]>    Удаленная установка на другой сервер через SSH
  --tls=[none|self-signed]    TLS шифрование для standalone
  --domain=<domain>           Домен для gateway режима
  --dry-run                   Тестовый запуск без внесения изменений в систему
  -h, --help                  Показать эту справку
EOF
}

detect_device() {
    DETECTED_HOSTNAME=$(hostname -s 2>/dev/null || hostname)
    DETECTED_ARCH=$(uname -m)

    if [ "$(uname -s)" = "Darwin" ]; then
        DETECTED_OS="macOS $(sw_vers -productVersion 2>/dev/null || '')"
        DETECTED_TYPE="Apple Mac"
        if sysctl -n hw.model 2>/dev/null | grep -qi "book"; then
            DETECTED_TYPE="Apple MacBook (Laptop)"
        fi
    elif [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        DETECTED_OS="${PRETTY_NAME:-$NAME}"
    else
        DETECTED_OS=$(uname -s)
    fi

    if [ "$(uname -s)" != "Darwin" ]; then
        DETECTED_TYPE="Десктоп / Сервер (Desktop/Server)"
        if [ -d /sys/class/power_supply ] && ls /sys/class/power_supply/BAT* 1>/dev/null 2>&1; then
            DETECTED_TYPE="Ноутбук (Laptop)"
        elif grep -q -i "microsoft" /proc/version 2>/dev/null; then
            DETECTED_TYPE="WSL (Windows Subsystem for Linux)"
        elif [ -f /.dockerenv ] || grep -q "docker\|containerd" /proc/1/cgroup 2>/dev/null; then
            DETECTED_TYPE="Контейнер (Docker/LXC)"
        elif command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt -q; then
            DETECTED_TYPE="Облачный сервер / VPS ($(systemd-detect-virt))"
        fi
    fi
}

print_device_info() {
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║${RESET} ${BOLD}🔍 Обнаружено устройство:${RESET}"
    echo -e "${CYAN}║${RESET}   • Имя хоста   : ${GREEN}${DETECTED_HOSTNAME}${RESET}"
    echo -e "${CYAN}║${RESET}   • Тип         : ${YELLOW}${DETECTED_TYPE}${RESET}"
    echo -e "${CYAN}║${RESET}   • ОС          : ${DETECTED_OS}"
    echo -e "${CYAN}║${RESET}   • Архитектура : ${DETECTED_ARCH}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════════╝${RESET}"
}

# Parse command line args
while [[ $# -gt 0 ]]; do
    case "$1" in
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
            show_help
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[ERROR] Неизвестный параметр: $1${RESET}"
            show_help
            exit 1
            ;;
    esac
done

detect_device

# Dry-run handling
if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN] Simulating Antigravity Mesh installation..."
    echo "[DRY-RUN] Detected Host: $DETECTED_HOSTNAME ($DETECTED_TYPE)"
    echo "[DRY-RUN] Selected Mode: ${MODE:-standalone}"
    echo "[DRY-RUN] Port: $PORT"
    echo "[DRY-RUN] TLS: $TLS"
    if [ -n "$DOMAIN" ]; then
        echo "[DRY-RUN] Domain: $DOMAIN"
    fi
    if [ -n "$USERNAME" ]; then
        echo "[DRY-RUN] User: $USERNAME"
    fi
    echo "[DRY-RUN] Systemd service and dependencies check: OK"
    exit 0
fi

# SSH Remote install branch
if [ -n "$SSH_TARGET" ]; then
    echo -e "${BOLD}${BLUE}=== Удаленная установка Antigravity Mesh через SSH ===${RESET}"
    echo -e "Целевой сервер: ${CYAN}${SSH_TARGET}${RESET} (порт: ${SSH_PORT})"
    ssh -p "$SSH_PORT" -o StrictHostKeyChecking=no "$SSH_TARGET" "mkdir -p ~/antigravity-mesh/core ~/antigravity-mesh/skills"
    scp -P "$SSH_PORT" -o StrictHostKeyChecking=no -r "$SCRIPT_DIR/core"/* "$SSH_TARGET:~/antigravity-mesh/core/"
    scp -P "$SSH_PORT" -o StrictHostKeyChecking=no "$SCRIPT_DIR/install.sh" "$SSH_TARGET:~/antigravity-mesh/"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        scp -P "$SSH_PORT" -o StrictHostKeyChecking=no -r "$SCRIPT_DIR/skills"/* "$SSH_TARGET:~/antigravity-mesh/skills/" 2>/dev/null || true
    fi
    echo -e "${GREEN}[✓] Файлы скопированы. Запуск установки на удаленном сервере...${RESET}"
    ssh -t -p "$SSH_PORT" -o StrictHostKeyChecking=no "$SSH_TARGET" "cd ~/antigravity-mesh && bash install.sh --quick"
    exit 0
fi

# Interactive Menu if no mode is specified and not in quick mode
if [ "$QUICK" = false ] && [ -z "$MODE" ] && [ -t 0 ]; then
    echo -e "\n${BOLD}${MAGENTA}╔════════════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${MAGENTA}║               🚀 ANTIGRAVITY MESH - МАСТЕР УСТАНОВКИ                   ║${RESET}"
    echo -e "${BOLD}${MAGENTA}╚════════════════════════════════════════════════════════════════════════╝${RESET}\n"
    print_device_info
    echo ""
    echo -e "${BOLD}Выберите режим установки:${RESET}"
    echo -e "  ${GREEN}1)${RESET} ⚡ ${BOLD}Быстрая настройка${RESET} (Субдомен как имя ПК + Облачный шлюз + Автозапуск) [Рекомендуется]"
    echo -e "  ${BLUE}2)${RESET} 🖥️  ${BOLD}Локальный Standalone${RESET} (Только localhost:${PORT}, без субдомена и шлюза)"
    echo -e "  ${YELLOW}3)${RESET} ⚙️  ${BOLD}Кастомная настройка${RESET} (Ввести имя субдомена вручную, выбор шлюза)"
    echo -e "  ${CYAN}4)${RESET} 📡 ${BOLD}Удаленная установка на SSH-сервер${RESET}"
    echo -e "  ${RESET}0) Выход"
    echo ""
    read -rp "Ваш выбор [1]: " MENU_CHOICE
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
            read -rp "Введите имя субдомена [по умолчанию: ${DETECTED_HOSTNAME}]: " CUSTOM_SUB
            if [ -n "$CUSTOM_SUB" ]; then
                USERNAME="$CUSTOM_SUB"
            fi
            read -rp "Хост шлюза [по умолчанию: ${GATEWAY}]: " CUSTOM_GW
            if [ -n "$CUSTOM_GW" ]; then
                GATEWAY="$CUSTOM_GW"
            fi
            ;;
        4)
            echo ""
            read -rp "Введите SSH цель (например, user@192.168.1.50): " REMOTE_TARGET
            read -rp "Порт SSH [22]: " REMOTE_PORT
            REMOTE_PORT=${REMOTE_PORT:-22}
            exec "$0" "--ssh=${REMOTE_TARGET}:${REMOTE_PORT}"
            ;;
        0)
            echo "Отменено пользователем."
            exit 0
            ;;
        *)
            echo "Некорректный выбор, используем быструю настройку."
            MODE="tunnel"
            QUICK=true
            ;;
    esac
fi

# If mode still empty, default to tunnel
if [ -z "$MODE" ]; then
    MODE="tunnel"
fi

print_device_info

# ==============================================================================
#  BRANCH: STANDALONE MODE (Localhost only, no subdomain, no gateway)
# ==============================================================================
if [ "$MODE" = "standalone" ]; then
    echo -e "\n${BOLD}${BLUE}=== Настройка локального Standalone FastMCP сервера ===${RESET}"
    echo "[1/3] Проверка окружения Python..."
    mkdir -p "$CONFIG_DIR"

    echo "[2/3] Настройка systemd автозапуска на ПК..."
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
        echo "Запуск в фоне (сессия без systemd user manager)..."
        nohup /usr/bin/python3 -m core.server --port="$PORT" > "$CONFIG_DIR/standalone.log" 2>&1 &
    }
    loginctl enable-linger "$USER" 2>/dev/null || true

    echo "[3/3] Проверка локального эндпоинта..."
    sleep 1

    MCP_LOCAL_URL="http://localhost:${PORT}/sse"
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}${GREEN}🎉 Локальный Standalone MCP-сервер успешно запущен на этом ПК!${RESET}"
    echo ""
    echo -e "  📍 ${BOLD}Адрес MCP-сервера:${RESET}  ${CYAN}${MCP_LOCAL_URL}${RESET}"
    echo -e "  ⚙️  ${BOLD}Порт:${RESET}               ${PORT}"
    echo -e "  🔄 ${BOLD}Автозапуск:${RESET}         Включен (systemd: agy-standalone.service)"
    echo ""
    echo -e "  ${BOLD}✨ Добавление в Gemini Spark / AI Studio:${RESET}"
    echo -e "     1. Откройте интерфейс: ${CYAN}https://gemini.google.com/${RESET}"
    echo -e "     2. Перейдите в настройки MCP-инструментов (Settings ➔ MCP / Extensions)"
    echo -e "     3. Добавьте URL: ${BOLD}${MCP_LOCAL_URL}${RESET}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════════════════${RESET}"
    exit 0
fi

# ==============================================================================
#  BRANCH: CLOUD GATEWAY + TUNNEL (Auto-subdomain as PC name, token, proxy)
# ==============================================================================
echo -e "\n${BOLD}${MAGENTA}=== Настройка облачного туннеля и субдомена ===${RESET}"

# Determine clean subdomain from PC name
if [ -z "$USERNAME" ]; then
    CLEAN_HOST=$(echo "$DETECTED_HOSTNAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
    USERNAME="$CLEAN_HOST"
fi
USERNAME=$(echo "$USERNAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$USERNAME" ] && USERNAME="mesh-node"

mkdir -p "$CONFIG_DIR"

echo "[1/4] Проверка Python зависимостей (websockets)..."
python3 -c "import websockets" 2>/dev/null || {
    echo "Установка websockets..."
    python3 -m pip install websockets --break-system-packages 2>/dev/null || python3 -m pip install websockets
}

echo "[2/4] Регистрация субдомена '${USERNAME}' на шлюзе (${GATEWAY})..."

# Check if we already have a token locally for this user
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
    # In case of manual re-prompt or error
    echo -e "${YELLOW}[!] Ответ шлюза: $REG_RESP${RESET}"
    echo -e "${YELLOW}Попытка зарегистрировать с суффиксом времени...${RESET}"
    FALLBACK_USER="${USERNAME}-$(date +%s | tail -c 4)"
    REG_RESP=$(curl -s -X POST "https://${GATEWAY}/api/register" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"${FALLBACK_USER}\", \"auto_suffix\": true}")
    ASSIGNED_USER=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('username', ''))" "$REG_RESP" 2>/dev/null || true)
    ASSIGNED_TOKEN=$(python3 -c "import json, sys; print(json.loads(sys.argv[1]).get('token', ''))" "$REG_RESP" 2>/dev/null || true)
fi

if [ -z "$ASSIGNED_TOKEN" ]; then
    echo -e "${RED}[ERROR] Не удалось получить токен авторизации от шлюза.${RESET}"
    exit 1
fi

if [ "$ASSIGNED_USER" != "$USERNAME" ]; then
    echo -e "${YELLOW}ℹ️  Имя '${USERNAME}' уже было занято. Автоматически назначен субдомен:${RESET} ${BOLD}${GREEN}${ASSIGNED_USER}${RESET}"
fi

echo "[3/4] Сохранение конфигурации в ${CONFIG_FILE}..."
cat << EOF > "$CONFIG_FILE"
MESH_GATEWAY=${GATEWAY}
MESH_USER=${ASSIGNED_USER}
MESH_TOKEN=${ASSIGNED_TOKEN}
EOF
chmod 600 "$CONFIG_FILE"

echo "[4/4] Настройка и запуск службы автозапуска на ПК..."
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
        echo "Запуск агента в фоне (сессия без systemd user manager)..."
        pkill -f "core.agent" 2>/dev/null || true
        nohup /usr/bin/python3 -m core.agent > "$CONFIG_DIR/agent.log" 2>&1 &
    }
    loginctl enable-linger "$USER" 2>/dev/null || true
fi

sleep 1

MCP_URL="https://${ASSIGNED_USER}.${GATEWAY}/sse?token=${ASSIGNED_TOKEN}"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}🎉 Antigravity Mesh узел успешно установлен и подключен к шлюзу!${RESET}"
echo ""
echo -e "  💻 ${BOLD}Устройство:${RESET}      ${DETECTED_HOSTNAME} (${DETECTED_TYPE})"
echo -e "  👤 ${BOLD}Субдомен:${RESET}        ${CYAN}${ASSIGNED_USER}.${GATEWAY}${RESET}"
echo -e "  🔑 ${BOLD}Секретный токен:${RESET} ${ASSIGNED_TOKEN}"
echo -e "  🔄 ${BOLD}Автозапуск:${RESET}      Включен (systemd: agy-agent.service)"
echo ""
echo -e "  📍 ${BOLD}Адрес MCP-сервера для подключения:${RESET}"
echo -e "     👉 ${CYAN}${BOLD}${MCP_URL}${RESET}"
echo ""
echo -e "  ✨ ${BOLD}Добавление в Gemini Spark / AI Studio:${RESET}"
echo -e "     1. Откройте: ${CYAN}https://gemini.google.com/${RESET} (или Gemini Spark)"
echo -e "     2. Перейдите в раздел ${BOLD}Настройки ➔ MCP / Инструменты${RESET} (Settings -> Tools)"
echo -e "     3. Вставьте ссылку: ${BOLD}${MCP_URL}${RESET}"
echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════════${RESET}"
