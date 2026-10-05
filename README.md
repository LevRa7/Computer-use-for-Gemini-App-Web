# 📱 Gemini Computer Use — control any PC from Gemini Spark (App & Web)

> **An MCP server that turns Google Gemini from a chatbot into a full-fledged mobile agent.**
> Type a task in the Gemini app on your phone — your PC, laptop or server executes it, and Gemini reports back with real results.

[English](README.md) | [🇷🇺 Русская версия](#-русская-версия)

---

## 💡 The idea

Gemini in a browser or on a phone can talk, but it cannot *do* anything on your computer.
**Gemini Computer Use** closes that gap. It is a free, open-source **Model Context Protocol (MCP)** server that you connect to **Gemini Spark** ([gemini.google.com/spark/apps](https://gemini.google.com/spark/apps) or the Gemini mobile app) with a single link. After that, Gemini gets hands:

- runs shell commands, builds and tests code, manages git;
- reads, writes and edits files;
- searches the file system;
- starts long-running jobs and checks on them later;
- watches CPU, RAM and disk.

```text
 📱 Gemini app / 🌐 gemini.google.com (Spark)
              │  MCP (SSE / Streamable HTTP)
              ▼
     ☁️  Gateway  smart-server.online   ← relay only, TLS
              │  outbound WebSocket tunnel (no open ports)
              ▼
   💻 Your PC · laptop · VPS · home server  →  executes the task
```

**Result:** your phone becomes a remote control for every machine you own — and Gemini is the agent that operates them.

---

## 🔥 What you can do with it

Just ask Gemini in plain language, from anywhere:

- *"Check why my home server is slow and show the top processes"*
- *"Pull the latest changes in ~/projects/api, run the tests and tell me what failed"*
- *"Fix the typo in config.yaml and restart the service"*
- *"Start a full backup in the background and tell me when it's done"*
- *"How much free disk space is left on my laptop?"*

No SSH client, no terminal on your phone, no VPN — only a chat.

---

## ✨ Why it works this way

| | |
| :--- | :--- |
| 📱 **Mobile-first agent** | Works in the official Gemini mobile app and on the web through Gemini Spark. |
| 🖥️ **Any PC** | Linux, macOS, Windows — desktops, laptops, VPS, containers. |
| 🌐 **Behind any NAT** | The node opens an *outbound* WebSocket tunnel. No public IP, no port forwarding, no router setup. |
| ⚡ **One-line install** | Detects OS, architecture and device type; installs autostart (`systemd` / `launchd` / Windows task); copies your connection link to the clipboard. |
| 🛡️ **Token-gated** | Every call needs a personal 128-bit token (`?token=…` or `Authorization: Bearer`). Everything else gets HTTP 401. |
| 🔁 **Many machines, one gateway** | Each node is addressed by `?user=<node-name>` on one shared domain. |
| 🆓 **Free & open source** | MIT license, works with the free Gemini tier. |

---

## 🛠️ What Gemini can do on your machine (MCP tools)

| Category | Tools |
| :--- | :--- |
| Terminal | `bash_exec(command)` |
| Files | `list_dir(path)` · `read_file(path, start_line, end_line)` · `write_file(path, content)` · `edit_file(path, old_string, new_string)` |
| Search | `grep_search(pattern, path)` · `glob_find(pattern, path)` |
| Long-running jobs | `run_job(command)` → `job_output(job_id)` · `job_kill(job_id)` · `job_list()` |
| Status | `system_vitals()` · `system_info()` · `mesh_status()` |
| Agent behaviour | `get_orchestration_skill()` — loads the agent's operating rules |

> [!TIP]
> Gemini gives a single tool call roughly 30 seconds. For anything longer (builds, backups, downloads) the agent uses `run_job` and polls `job_output`, so tasks never get cut off.

---

## 🚀 Quick start — 2 minutes

### 1. Install the node on the PC you want to control

**🐧 Linux / 🍎 macOS**
```bash
curl -fsSL https://smart-server.online/install.sh | bash
```

**🪟 Windows (PowerShell)**
```powershell
irm https://smart-server.online/install.ps1 | iex
```

**📦 Node.js (any OS)**
```bash
npx gemini-computer-use
```

**🛠️ From source**
```bash
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick
```

At the end the installer prints your personal MCP link and copies it to the clipboard:
```text
https://smart-server.online/sse?user=<your-node-name>&token=<your_secret_token>
```

### 2. Connect it to Gemini Spark

1. Open **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** in a browser or in the Gemini mobile app.
2. Tap **Add App** (or **Settings ⚙️ → Tools / Extensions (MCP)**).
3. Paste the link (**Ctrl+V**) and save.

### 3. Give it a task

> *"Show system_vitals and list the 5 biggest folders in my home directory."*

That's it — Gemini is now an agent running on your machine. Repeat step 1 on other computers to control all of them from the same phone.

---

## 🖥️ Deployment modes

| Mode | Command | When to use |
| :--- | :--- | :--- |
| **Gateway + tunnel** (recommended) | `./install.sh --quick` | Home PCs and laptops behind NAT — the mobile-agent scenario. |
| **Remote over SSH** | `./install.sh --ssh=user@host` | Deploy a node onto a remote Linux server from your terminal. |
| **Local standalone** | `./install.sh --mode=standalone --port=8096` | Pure localhost FastMCP server at `http://localhost:8096/sse`, no cloud relay. |

---

## 🔗 One shared domain (canonical URL contract)

A node is **never** addressed by its own hostname. The gateway publishes a single shared domain, and the node name travels as a query parameter:

| Purpose | Canonical URL |
| :--- | :--- |
| MCP over SSE | `https://<shared-domain>/sse?user=<node-name>&token=<token>` |
| MCP Streamable HTTP | `https://<shared-domain>/mcp?user=<node-name>&token=<token>` |
| Reverse tunnel (agent) | `wss://<shared-domain>/ws/tunnel?user=<node-name>&token=<token>` |

Why: every extra hostname would need its own DNS record *and* its own SAN in the TLS certificate. A node whose name is missing from the certificate fails the TLS handshake, and the Gemini client then reports an opaque "cannot connect to host". With one shared domain the certificate covers every node forever, and adding a node is only a registration call.

- The shared domain defaults to `smart-server.online`; override it with `./install.sh --domain=<shared-domain>` (node side) or `MESH_PUBLIC_URL` (gateway side).
- Legacy per-device subdomain URLs still resolve for backwards compatibility and log a deprecation warning; set `MESH_LEGACY_SUBDOMAIN=0` on the gateway to reject them outright.

---

## 🔒 Security

- **Token-gated endpoints** — every request must carry the node's secret token.
- **Outbound-only tunnel** — nodes never listen on public ports; the agent dials out to the gateway.
- **Gateway is a relay** — it terminates TLS and forwards calls; commands run only on your node.
- **Unprivileged by default** — the agent runs in user space, without root.

> [!WARNING]
> Whoever has your MCP link can run commands on that machine. Treat it like a password: don't publish it, and reinstall the node to rotate the token if it leaks.

---

## 🚢 Self-hosting the gateway

The gateway is a single host serving every node. Publish it only through the deploy script, so the running gateway, the served installers and the node bootstrap payload cannot drift apart:

```bash
MESH_GATEWAY_SSH=root@<gateway-ip> ./deploy_gateway.sh              # deploy
MESH_GATEWAY_SSH=root@<gateway-ip> ./deploy_gateway.sh --dry-run    # preview
MESH_GATEWAY_SSH=root@<gateway-ip> MESH_GATEWAY_SSH_PASS_FILE=~/.ssh/gw-pass \
    ./deploy_gateway.sh                                            # password auth
```
It verifies every upload with an sha256 manifest on the host, takes a timestamped backup, installs with the right owner, restarts the service and finishes with a public health check. Nothing is installed if verification fails.

**TLS certificate policy — one name, no per-device SANs.** The certificate must cover the shared domain only; a node is selected by `?user=`, never by a hostname:

```bash
# what the certificate currently covers
sudo certbot certificates | grep -A1 'Certificate Name: smart-server.online'

# canonical renewal check (staging, does not touch the live certificate)
sudo certbot renew --dry-run --cert-name smart-server.online
```
Legacy per-device SANs may still be present from before this contract. They are harmless and keep old bookmarked URLs working; drop them at the next renewal once no client uses subdomain URLs any more (see the `domains =` line in `/etc/letsencrypt/renewal/<name>.conf`, or re-issue with a single `-d`).

---

## 📄 License

**MIT** — see [LICENSE](LICENSE).

---
---

# 🇷🇺 Русская версия

> **MCP-сервер, который превращает Google Gemini из чат-бота в полноценного мобильного агента.**
> Напишите задачу в приложении Gemini на телефоне — ваш ПК, ноутбук или сервер выполнит её, а Gemini вернётся с реальным результатом.

---

## 💡 Идея

Gemini в браузере или на телефоне умеет разговаривать, но ничего не может *сделать* на вашем компьютере.
**Gemini Computer Use** устраняет этот разрыв. Это бесплатный open-source сервер **Model Context Protocol (MCP)**, который подключается к **Gemini Spark** ([gemini.google.com/spark/apps](https://gemini.google.com/spark/apps) или мобильное приложение Gemini) одной ссылкой. После этого у Gemini появляются «руки»:

- выполняет команды терминала, собирает и тестирует код, работает с git;
- читает, создаёт и редактирует файлы;
- ищет по файловой системе;
- запускает долгие задачи в фоне и проверяет их позже;
- следит за CPU, RAM и диском.

```text
 📱 Приложение Gemini / 🌐 gemini.google.com (Spark)
              │  MCP (SSE / Streamable HTTP)
              ▼
     ☁️  Шлюз  smart-server.online   ← только релей, TLS
              │  исходящий WebSocket-туннель (без открытых портов)
              ▼
   💻 Ваш ПК · ноутбук · VPS · домашний сервер  →  выполняет задачу
```

**Итог:** телефон становится пультом управления всеми вашими машинами, а Gemini — агентом, который ими управляет.

---

## 🔥 Что можно делать

Просто попросите Gemini обычным языком, откуда угодно:

- *«Посмотри, почему тормозит домашний сервер, и покажи топ процессов»*
- *«Подтяни последние изменения в ~/projects/api, прогони тесты и скажи, что упало»*
- *«Исправь опечатку в config.yaml и перезапусти сервис»*
- *«Запусти полный бэкап в фоне и сообщи, когда закончится»*
- *«Сколько свободного места осталось на ноутбуке?»*

Без SSH-клиента, без терминала на телефоне, без VPN — только чат.

---

## ✨ Почему это удобно

| | |
| :--- | :--- |
| 📱 **Мобильный агент** | Работает в официальном приложении Gemini и в вебе через Gemini Spark. |
| 🖥️ **Любой ПК** | Linux, macOS, Windows — десктопы, ноутбуки, VPS, контейнеры. |
| 🌐 **За любым NAT** | Узел сам открывает *исходящий* WebSocket-туннель. Не нужны белый IP, проброс портов и настройка роутера. |
| ⚡ **Установка одной командой** | Определяет ОС, архитектуру и тип устройства; ставит автозапуск (`systemd` / `launchd` / задача Windows); копирует ссылку подключения в буфер обмена. |
| 🛡️ **Доступ по токену** | Каждый вызов требует личный 128-битный токен (`?token=…` или `Authorization: Bearer`). Остальным — HTTP 401. |
| 🔁 **Много машин, один шлюз** | Каждый узел выбирается параметром `?user=<имя-узла>` на одном общем домене. |
| 🆓 **Бесплатно и открыто** | Лицензия MIT, работает на бесплатном тарифе Gemini. |

---

## 🛠️ Что Gemini может делать на вашей машине (MCP-инструменты)

| Категория | Инструменты |
| :--- | :--- |
| Терминал | `bash_exec(command)` |
| Файлы | `list_dir(path)` · `read_file(path, start_line, end_line)` · `write_file(path, content)` · `edit_file(path, old_string, new_string)` |
| Поиск | `grep_search(pattern, path)` · `glob_find(pattern, path)` |
| Долгие задачи | `run_job(command)` → `job_output(job_id)` · `job_kill(job_id)` · `job_list()` |
| Состояние | `system_vitals()` · `system_info()` · `mesh_status()` |
| Поведение агента | `get_orchestration_skill()` — загружает рабочие правила агента |

> [!TIP]
> Gemini даёт одному вызову инструмента около 30 секунд. Всё, что дольше (сборки, бэкапы, загрузки), агент запускает через `run_job` и забирает результат через `job_output` — задачи не обрываются.

---

## 🚀 Быстрый старт — 2 минуты

### 1. Установите узел на ПК, которым хотите управлять

**🐧 Linux / 🍎 macOS**
```bash
curl -fsSL https://smart-server.online/install.sh | bash
```

**🪟 Windows (PowerShell)**
```powershell
irm https://smart-server.online/install.ps1 | iex
```

**📦 Node.js (любая ОС)**
```bash
npx gemini-computer-use
```

**🛠️ Из исходников**
```bash
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick
```

В конце установщик выведет вашу личную MCP-ссылку и скопирует её в буфер обмена:
```text
https://smart-server.online/sse?user=<имя-вашего-узла>&token=<ваш_секретный_токен>
```

### 2. Подключите её к Gemini Spark

1. Откройте **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** в браузере или в мобильном приложении Gemini.
2. Нажмите **Добавить приложение** (или **Настройки ⚙️ → Инструменты / Расширения (MCP)**).
3. Вставьте ссылку (**Ctrl+V**) и сохраните.

### 3. Дайте задачу

> *«Покажи system_vitals и 5 самых больших папок в домашнем каталоге.»*

Готово — Gemini теперь агент, работающий на вашей машине. Повторите шаг 1 на других компьютерах, чтобы управлять ими всеми с одного телефона.

---

## 🖥️ Режимы развёртывания

| Режим | Команда | Когда использовать |
| :--- | :--- | :--- |
| **Шлюз + туннель** (рекомендуется) | `./install.sh --quick` | Домашние ПК и ноутбуки за NAT — сценарий мобильного агента. |
| **Удалённо через SSH** | `./install.sh --ssh=user@host` | Развернуть узел на удалённом Linux-сервере прямо из терминала. |
| **Локальный автономный** | `./install.sh --mode=standalone --port=8096` | Чисто локальный FastMCP-сервер на `http://localhost:8096/sse`, без облачного релея. |

---

## 🔗 Один общий домен (канонический формат URL)

Узел **никогда** не адресуется собственным именем хоста. Шлюз публикует один общий домен, а имя узла передаётся параметром:

| Назначение | Канонический URL |
| :--- | :--- |
| MCP через SSE | `https://<общий-домен>/sse?user=<имя-узла>&token=<токен>` |
| MCP Streamable HTTP | `https://<общий-домен>/mcp?user=<имя-узла>&token=<токен>` |
| Обратный туннель (агент) | `wss://<общий-домен>/ws/tunnel?user=<имя-узла>&token=<токен>` |

Почему так: каждому дополнительному имени хоста нужна своя DNS-запись **и** свой SAN в TLS-сертификате. Если имени нет в сертификате, TLS-рукопожатие падает, и клиент Gemini показывает невнятную ошибку «не удаётся подключиться к хосту». С одним общим доменом сертификат покрывает все узлы сразу, а добавление узла — это только вызов регистрации.

- Общий домен по умолчанию `smart-server.online`; переопределяется через `./install.sh --domain=<общий-домен>` (на стороне узла) или `MESH_PUBLIC_URL` (на стороне шлюза).
- Старые ссылки с субдоменами устройства продолжают работать для совместимости и пишут предупреждение в лог; `MESH_LEGACY_SUBDOMAIN=0` на шлюзе отключает их.

---

## 🔒 Безопасность

- **Доступ только по токену** — каждый запрос должен содержать секретный токен узла.
- **Только исходящий туннель** — узлы не слушают публичные порты; агент сам подключается к шлюзу.
- **Шлюз — только релей** — он терминирует TLS и передаёт вызовы; команды выполняются только на вашем узле.
- **Без root по умолчанию** — агент работает в пространстве пользователя.

> [!WARNING]
> Любой, у кого есть ваша MCP-ссылка, может выполнять команды на этой машине. Храните её как пароль: не публикуйте, а при утечке переустановите узел, чтобы сменить токен.

---

## 🚢 Свой шлюз (self-hosting)

Шлюз — один хост на все узлы. Публикуйте его только скриптом, чтобы работающий шлюз, отдаваемые установщики и bootstrap-код узлов не расходились:

```bash
MESH_GATEWAY_SSH=root@<ip-шлюза> ./deploy_gateway.sh            # задеплоить
MESH_GATEWAY_SSH=root@<ip-шлюза> ./deploy_gateway.sh --dry-run  # предпросмотр
```
Скрипт проверяет каждую загрузку манифестом sha256 на хосте, делает бэкап с меткой времени, ставит файлы с нужным владельцем, перезапускает службу и завершает публичной проверкой здоровья. При несовпадении хэшей ничего не устанавливается.

**Политика TLS-сертификата: только общий домен, без SAN на устройства.** Имена узлов в сертификат не добавляются никогда — узел выбирается `?user=`:

```bash
sudo certbot certificates | grep -A1 'Certificate Name: smart-server.online'
sudo certbot renew --dry-run --cert-name smart-server.online   # проверка продления
```
Старые SAN'ы устройств могут остаться с прежнего контракта: они безвредны и поддерживают совместимость старых ссылок. Убрать их можно при следующем продлении, когда субдоменными ссылками никто не пользуется.

---

## 📄 Лицензия

**MIT** — подробности в файле [LICENSE](LICENSE).
