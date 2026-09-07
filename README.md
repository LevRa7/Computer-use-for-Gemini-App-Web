# 🚀 Gemini Computer Use (Web & Mobile App) — FREE & Open Source

> **Turn your PC, Laptop, or Server into an autonomous execution node for Google Gemini (Web & Mobile App) via Native Model Context Protocol (MCP).**

[English](README.md) | [🇷🇺 Русская версия](#-русская-версия)

---

## 💡 What is Gemini Computer Use?

**Gemini Computer Use** is an open-source, free solution that gives **Google Gemini** direct, autonomous command-execution capability on your local machine, laptop, or remote cloud server directly from the chat interface of [gemini.google.com](https://gemini.google.com), the official Gemini mobile app, or Google AI Studio.

Unlike proprietary paid alternatives, this project uses the official open **Model Context Protocol (MCP)** specification from Google. It operates over a secure, outbound WebSocket reverse tunnel (no open router ports or public IPs required) or in a pure local standalone mode.

---

## ✨ Key Features

- 🆓 **100% Free**: Operates directly with the free tier of Google Gemini (Web & Mobile app).
- ⚡ **Zero-Config One-Line Install**: Auto-detects device type (laptop, desktop, VPS, container), OS, and architecture.
- 🌐 **Two Deployment Modes**:
  1. **Cloud Gateway + Reverse Tunnel (Recommended)**: Works behind NAT, firewalls, and dynamic IPs. No inbound ports required.
  2. **Local Standalone**: Pure localhost FastMCP server on `localhost:8096` with zero external dependencies.
- 🛡️ **Cryptographic Token Security**: Every tool invocation is secured by a 128-bit secret token (`?token=...`). Unauthorized requests are strictly blocked with HTTP 401.
- 📡 **Remote SSH Deployment**: Deploy nodes to remote Linux servers in one command directly from your terminal (`--ssh=user@host`).
- 🔄 **Native Background Autostart**: Installs and enables system background services (`systemd` on Linux, `launchd` on macOS, background task on Windows).
- 📱 **Mobile & Web Ready**: Prompt Gemini from your smartphone on the go — your home laptop or cloud server will execute tasks in real time!
- 💻 **Cross-Platform**: Fully supports Linux, macOS, and Windows.

---

## 🛠️ Available MCP Tools

Once connected, Google Gemini gains direct native access to:
- `bash_exec(command)`: Execute shell commands, build code, run test suites, manage git repositories.
- `system_vitals()`: Real-time telemetry monitoring: CPU load, RAM usage, and disk space.
- `get_orchestration_skill()`: Dynamic delivery of infrastructure orchestrator personas and operational guidelines.

---

## 🚀 Quick Start (One-Liner Install)

### 🐧 Linux & 🍎 macOS (via curl)
```bash
curl -fsSL https://smart-server.online/install.sh | bash
```

### 🪟 Windows (via PowerShell)
```powershell
irm https://smart-server.online/install.ps1 | iex
```

### 📦 Node.js / NPM (Cross-platform)
```bash
npx gemini-computer-use
```

### 🛠️ Or clone via Git
```bash
git clone https://github.com/LevRa7/Gemini-APP-Web-for-computer-use---FREE.git
cd Gemini-APP-Web-for-computer-use---FREE
./install.sh --quick
```

---

## 📱 Connecting to Google Gemini

1. Open **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** (or the Gemini mobile app).
2. Click **Add App** (or go to **Settings ⚙️ ➔ Tools / Extensions (MCP)**).
3. The MCP SSE URL is **automatically copied to your clipboard** during install — just paste it (**Ctrl+V**):
   ```text
   https://<your-device-name>.smart-server.online/sse?token=<your_secret_token>
   ```
4. Done! You can now prompt Gemini:
   - *"Check system resources using system_vitals"*
   - *"Create a FastAPI application and run test coverage via bash_exec"*
   - *"Git commit and push changes"*

---

## 🖥️ Deployment Modes

### Mode 1: Cloud Gateway + Tunnel (Recommended)
```bash
./install.sh --quick
```
Perfect for personal laptops and home workstations behind NAT. The connection is maintained via an outbound secure WebSocket tunnel.

### Mode 2: Local Standalone
```bash
./install.sh --mode=standalone --port=8096
```
Runs an isolated, local FastMCP server on `http://localhost:8096/sse` without cloud relaying.

### Mode 3: Remote SSH Install
```bash
./install.sh --ssh=user@my-remote-server.com
```
Automatically transfers files and configures the node on your remote server via SSH.

---

## 🔒 Security Architecture

- **Token-Gated Endpoints**: Every incoming request must contain the cryptographic token via `?token=...` or `Authorization: Bearer`.
- **Outbound-Only Tunnel**: Local nodes do not open listening public ports. The agent initiates an outbound WebSocket connection to the gateway.
- **Unprivileged Execution**: Agents execute in user-space by default without requiring root privileges.

---

## 📄 License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---
---

# 🇷🇺 Русская версия

> **Превратите свой ПК, ноутбук или сервер в автономный исполнительный узел для Google Gemini (Web и Mobile App) через нативный протокол Model Context Protocol (MCP).**

---

## 💡 Что это такое?

**Gemini Computer Use** — это открытое и бесплатное решение для прямого управления вашим компьютером, ноутбуком или удалённым сервером из диалогового окна **Google Gemini** (включая веб-версию [gemini.google.com](https://gemini.google.com), мобильное приложение Gemini и Google AI Studio).

Проект использует официальный протокол **Model Context Protocol (MCP)** от Google и работает поверх защищённого WebSocket-туннеля или в локальном автономном режиме.

---

## ✨ Ключевые возможности

- 🆓 **100% Бесплатно**: Работает со стандартным бесплатным тарифом Google Gemini (Web и Mobile App).
- ⚡ **Установка в одну команду (One-Line Setup)**: Скрипт автоматически определяет тип устройства (ноутбук/десктоп/VPS/сервер), ОС и архитектуру.
- 🌐 **Два режима работы**:
  1. **Cloud Gateway + Reverse Tunnel**: Работает из-за любого «серого» IP, роутера и NAT без белого IP и проброса портов.
  2. **Local Standalone**: Полностью локальный FastMCP-сервер на `localhost:8096` без внешних зависимостей.
- 🛡️ **Строгая безопасность**: Каждый запрос к инструментам защищён персональным криптографическим токеном (`?token=...`). Доступ посторонних строго отсекается (HTTP 401).
- 📡 **Удалённая установка через SSH**: Развёртывание узла на удалённом сервере одной командой (`--ssh=user@host`).
- 🔄 **Автозапуск**: Интеграция со службами автозапуска (`systemd` в Linux, `launchd` в macOS, автозапуск в Windows).
- 📱 **Управление со смартфона**: Пишите команды в мобильном приложении Gemini — ваша домашняя машина выполнит их в реальном времени!
- 💻 **Кроссплатформенность**: Полная поддержка Linux, macOS и Windows.

---

## 🛠️ Доступные MCP-инструменты

- `bash_exec(command)` — автономное выполнение команд терминала, компиляция кода, тесты, git-операции.
- `system_vitals()` — мониторинг нагрузки в реальном времени (CPU, RAM, диск).
- `get_orchestration_skill()` — системные правила и скилл автономного оркестратора инфраструктуры.

---

## 🚀 Быстрый старт (Установка в 1 команду)

#### 🐧 Linux & 🍎 macOS (через curl):
```bash
curl -fsSL https://smart-server.online/install.sh | bash
```

#### 🪟 Windows (через PowerShell):
```powershell
irm https://smart-server.online/install.ps1 | iex
```

#### 📦 Node.js / NPM (Кроссплатформенно):
```bash
npx gemini-computer-use
```

#### 🛠️ Либо вручную через Git:
```bash
git clone https://github.com/LevRa7/Gemini-APP-Web-for-computer-use---FREE.git
cd Gemini-APP-Web-for-computer-use---FREE
./install.sh --quick
```

---

## 📱 Подключение к Google Gemini

1. Откройте страницу **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** (в браузере или приложении).
2. Нажмите **Добавить приложение** (Add App / Настройки ➔ MCP).
3. Ссылка на ваш MCP-сервер **автоматически скопирована в буфер обмена** при установке — просто вставьте её (**Ctrl+V**):
   ```text
   https://<имя-вашего-пк>.smart-server.online/sse?token=<ваш_секретный_токен>
   ```
4. Готово! Теперь Gemini может выполнять команды прямо на вашей машине.

---

## 📄 Лицензия

Распространяется под свободной лицензией **MIT**. Подробности в файле [LICENSE](LICENSE).
