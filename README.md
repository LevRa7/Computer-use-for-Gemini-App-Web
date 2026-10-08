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

**19 tools.** Every one of them runs on your machine under your own user account — the gateway only carries the calls.

### Status and host information

| Tool | What it does |
| :--- | :--- |
| `mesh_status()` | Confirms the node is reachable, with live evidence from the host itself. Call it first if the machine looks offline. |
| `system_info()` | One-call host summary: OS, desktop, user, home, disks, memory, load, top processes, the current wallpaper, and `command_shell` — the shell `bash_exec` will actually use. |
| `system_vitals()` | CPU, RAM and disk metrics. |

### Shell

| Tool | What it does |
| :--- | :--- |
| `bash_exec(command, timeout_sec, max_chars, cursor)` | Runs a shell command. The shell matches the **host**, not the tool's name — `bash` on Linux/macOS, PowerShell or `cmd.exe` on Windows; check `command_shell` from `system_info()` first. Output is paginated: when it is cut, call again with `cursor=next_cursor`, nothing is dropped; output above 2 MB is spooled to a file returned in `saved_to`. `timeout_sec` is 1–120 (default 25). |

### Files

| Tool | What it does |
| :--- | :--- |
| `list_dir(path)` | Lists files and directories (workspace by default). |
| `read_file(path, start_line, end_line, max_chars, cursor)` | Reads a text file with line numbers, optionally restricted to a line range. Paginated like `bash_exec`. |
| `write_file(path, content, create_dirs, mode)` | Atomically creates or overwrites a file (temp file + `os.replace`). `mode` is POSIX-only: on Windows it is not honoured, and the result says so instead of pretending. |
| `edit_file(path, old_string, new_string, expected_sha256, replace_all)` | Replaces an exact substring. `old_string` must match exactly once unless `replace_all` is set; `expected_sha256` guards against overwriting a file that changed since it was read. |

### Search

| Tool | What it does |
| :--- | :--- |
| `grep_search(pattern, path, glob, limit, ignore_case, fixed, context)` | Recursively searches file contents, skipping binary files and heavy directories. `fixed` treats the pattern as literal text, `context` adds surrounding lines, `limit` is 1–1000 (default 200). |
| `glob_find(pattern, path)` | Finds files by glob pattern (`*` and `**`). |

### Long-running work

| Tool | What it does |
| :--- | :--- |
| `run_job(command, cwd)` | Starts a command in the background and returns a `job_id`. |
| `job_output(job_id, wait_ms, max_chars, cursor)` | Reads a job's output, optionally waiting up to 20 s for completion. Paginated. |
| `job_kill(job_id, signal)` | Terminates a job. `signal` is `TERM` (default), `KILL`, `INT`, `HUP` or `QUIT`. |
| `job_list(limit)` | Lists recent jobs, newest first (20 by default, max 50). |

### Publishing files and web pages

These four make something on your machine readable from the internet. The link is served by the gateway on the shared domain, so **the node has to stay connected for it to work**.

| Tool | What it does |
| :--- | :--- |
| `share_file(path, name, overwrite)` | Publishes **one file**: it is copied into the node's share root and you get `https://<shared-domain>/<node>/<name>-<random>/<filename>`. `overwrite` replaces an existing share with the same name. |
| `serve_dir(path, name)` | Serves a **directory** in place — no copy is made. `index.html` is used when present, otherwise a directory listing is shown. Read-only (GET/HEAD only). Returns `https://<shared-domain>/<node>/<name>-<random>/`. |
| `share_list()` | Lists active shares with their URLs, their roots, and whether the local server is running. |
| `unshare(name)` | Stops a share and revokes its link. Accepts the share name, its slug or the full URL. A file share also deletes the copy in the share root; a served directory is left untouched on disk. |

> [!IMPORTANT]
> The random part of the path **is** the credential — anyone who has the link can read the file or browse the directory. Treat a share link like a password, and `unshare` it when you are done. Size limits: 32 MiB per file by default, 64 MiB ceiling via `MESH_WEB_MAX_BYTES`.

### Agent behaviour

| Tool | What it does |
| :--- | :--- |
| `get_orchestration_skill()` | Loads the agent's current operating rules and orchestration skill. |

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

**🪟 Windows — visual installer** (from a clone or a release)

```powershell
.\install-gui.cmd
```

A WinForms wizard that mirrors the console installer's variants, switches language at
runtime and shows the MCP link at the end — see [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

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

On Windows the same variants are available in the visual installer (`.\install-gui.cmd`); local standalone stays console-only there (`.\install.ps1 -Mode standalone -Port 8096`).

---

## 🪟 Windows installation

Windows already ships PowerShell 5.1, so there is nothing to prepare — no Python, no Node.js, no administrator rights. Both installers finish by putting the same MCP link on the clipboard.

|  | Console installer | Visual installer |
| :--- | :--- | :--- |
| Start | `irm https://smart-server.online/install.ps1 \| iex` | `.\install-gui.cmd` |
| Needs | nothing but PowerShell | a clone or an unpacked release — it drives `install.ps1`, `core/` and `install.sh` |
| Variants | `-Mode tunnel` (default), `-Mode standalone`, `-User`, `-Gateway`, `-Token`, `-Port`, `-DryRun` | Quick setup, Custom setup, Remote over SSH |
| Language | `-Lang en` / `-Lang ru` | switch in the window header, or `-Lang` |

### Download the setup executable

The release also ships a single compiled installer, for machines where you would rather
not clone or download anything else — `AntigravityMesh-Setup-<version>.exe`:

```powershell
.\AntigravityMesh-Setup-0.2.6.exe            # open the visual installer
.\AntigravityMesh-Setup-0.2.6.exe -Lang ru   # start in Russian
.\AntigravityMesh-Setup-0.2.6.exe -SelfTest  # headless self-check, prints JSON
.\AntigravityMesh-Setup-0.2.6.exe --version  # print the version
```

It carries the wizard, `install.ps1`, `core/` and `install.sh` inside itself, unpacks them
to `%LOCALAPPDATA%\AntigravityMesh\setup\<version>` (override with `MESH_SETUP_DIR`) and
runs the wizard from there. It needs nothing but the .NET Framework and PowerShell that
Windows already has, and it is built from this repository by
`.\build-installer-exe.ps1` — no SDK, no NuGet, no network.

It is **not code-signed**, so SmartScreen may ask for confirmation the first time you run
it.

### Visual installer

![Windows visual installer](docs/images/gui-welcome-en.png)

- **Quick setup** (recommended) — node name = PC name, shared gateway domain, Windows autostart. The one to pick on a home PC or laptop.
- **Custom setup** — your own node name, your own shared domain, optional token.
- **Remote over SSH** — copy the node code to a Linux host and run `install.sh` there. Needs the Windows *OpenSSH Client* feature and key-based authentication; interactive password prompts are not supported.

The first screen runs `install.ps1 -DryRun` and reports what it found — Python, `websockets`, the resolved gateway domain, the config directory, the autostart path — without changing anything. Before starting, the window shows the exact command it will run; during the run it streams the installer output; at the end it shows the MCP link with a copy button, the three-step Gemini instructions and the paths it created.

Full details, screenshots and the SSH notes: [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

### What a Windows installation creates

| Path | What it is |
| :--- | :--- |
| `%USERPROFILE%\.config\antigravity-mesh\agent.env` | `MESH_GATEWAY`, `MESH_USER`, `MESH_TOKEN` |
| `…\Start Menu\Programs\Startup\antigravity-agent.vbs` | autostart entry, so the node comes back after a reboot |
| `%USERPROFILE%\.config\antigravity-mesh\agent.log` | agent output, for when a silent autostart fails |

### Language

The visual installer starts in the language of your Windows language settings — the display language and the preferred-languages list are read from the registry — and falls back to English. `-Lang ru` / `-Lang en` overrides the detection. The switch in the window header changes every label immediately.

### Troubleshooting

- **"No gateway domain configured"** — this copy was not published with a domain. Pass one: use *Custom setup* in the wizard, or `.\install.ps1 -Gateway <shared-domain>`, or set `MESH_PUBLIC_URL`.
- **Registration fails** — the installer stops *before* writing anything when the gateway cannot be reached. Check that the domain resolves, then retry with an explicit `-Gateway`.
- **`python` opens the Microsoft Store** — that is the App Execution Alias, not an interpreter. Both installers resolve a real `python.exe` by full path and ignore the alias; the wizard never pins an alias into the autostart entry.
- **The node does not come back after a reboot** — run `.\ops\doctor.ps1`: it prints the autostart interpreter, the heartbeat age, the last `agent.log` lines (including a torn final line) and the gateway's own view of this node. `.\ops\windows\agent-watchdog.ps1` performs that check once and restarts the agent; the scheduled task `AntigravityMeshWatchdog` runs it every five minutes, so a dead agent comes back on its own.

### Standalone on Windows

The wizard does not offer it; use the console installer:

```powershell
.\install.ps1 -Mode standalone -Port 8096
```

That starts a local-only FastMCP server at `http://localhost:8096/sse` and puts that URL on the clipboard.

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

> [!TIP]
> **One place for the domain.** `MESH_PUBLIC_URL` in `domain.env`
> (`/etc/antigravity-mesh/domain.env` on Linux, `%USERPROFILE%\.config\antigravity-mesh\domain.env`
> on Windows) is the only setting that names it: the node, the gateway, the installers, the public
> file links and the nginx vhosts all resolve it through `core/domain.py`. Switching domains is one
> value plus `ops/nginx/render-domain.sh --apply` — see [docs/DOMAIN.md](docs/DOMAIN.md).

> [!IMPORTANT]
> **`install.ps1` encoding — one file, two representations.** The repository copy is UTF-8 *with* a BOM: Windows PowerShell 5.1 decodes a BOM-less script with the ANSI code page, the Russian strings then turn into smart quotes and the parser rejects the whole file, so `.\install.ps1` from a clone would not run. The copy the gateway serves is written *without* the BOM by `deploy_gateway.sh`, because `irm … | iex` receives the BOM as part of the first token and `param(...)` then stops being the first statement. nginx declares `charset utf-8` for that location, so the BOM-less copy still decodes correctly. Please keep both properties when editing the file.

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

**19 инструментов.** Все они выполняются на вашей машине под вашей учётной записью — шлюз только передаёт вызовы.

### Состояние и сведения о хосте

| Инструмент | Что делает |
| :--- | :--- |
| `mesh_status()` | Подтверждает, что узел доступен, живыми данными с самого хоста. Вызывать первым, если машина кажется offline. |
| `system_info()` | Сводка о хосте одним вызовом: ОС, рабочий стол, пользователь, домашний каталог, диски, память, загрузка, топ процессов, текущие обои и `command_shell` — та оболочка, которую реально использует `bash_exec`. |
| `system_vitals()` | Метрики CPU, ОЗУ и дисков. |

### Оболочка

| Инструмент | Что делает |
| :--- | :--- |
| `bash_exec(command, timeout_sec, max_chars, cursor)` | Выполняет команду оболочки. Оболочка соответствует **хосту**, а не названию инструмента — `bash` на Linux/macOS, PowerShell или `cmd.exe` на Windows; сначала посмотрите `command_shell` из `system_info()`. Вывод постраничный: если обрезан, вызовите снова с `cursor=next_cursor`, ничего не теряется; вывод больше 2 МБ сохраняется в файл, путь в `saved_to`. `timeout_sec` — 1–120 (по умолчанию 25). |

### Файлы

| Инструмент | Что делает |
| :--- | :--- |
| `list_dir(path)` | Список файлов и каталогов (по умолчанию — рабочий каталог). |
| `read_file(path, start_line, end_line, max_chars, cursor)` | Читает текстовый файл с номерами строк, при желании — диапазон строк. Постранично, как `bash_exec`. |
| `write_file(path, content, create_dirs, mode)` | Атомарно создаёт или перезаписывает файл (временный файл + `os.replace`). `mode` — только для POSIX: на Windows он не применяется, и результат об этом честно сообщает. |
| `edit_file(path, old_string, new_string, expected_sha256, replace_all)` | Заменяет точную подстроку. `old_string` должен встречаться ровно один раз, если не задан `replace_all`; `expected_sha256` защищает от перезаписи файла, изменившегося после чтения. |

### Поиск

| Инструмент | Что делает |
| :--- | :--- |
| `grep_search(pattern, path, glob, limit, ignore_case, fixed, context)` | Рекурсивно ищет по содержимому файлов, пропуская двоичные файлы и тяжёлые каталоги. `fixed` — поиск как по обычному тексту, `context` — строки вокруг совпадения, `limit` — 1–1000 (по умолчанию 200). |
| `glob_find(pattern, path)` | Ищет файлы по маске (`*` и `**`). |

### Долгие задачи

| Инструмент | Что делает |
| :--- | :--- |
| `run_job(command, cwd)` | Запускает команду в фоне и возвращает `job_id`. |
| `job_output(job_id, wait_ms, max_chars, cursor)` | Читает вывод задачи, при желании ожидая завершения до 20 с. Постранично. |
| `job_kill(job_id, signal)` | Завершает задачу. `signal` — `TERM` (по умолчанию), `KILL`, `INT`, `HUP` или `QUIT`. |
| `job_list(limit)` | Список последних задач, новые сверху (20 по умолчанию, максимум 50). |

### Публикация файлов и веб-страниц

Эти четыре делают что-то на вашей машине доступным из интернета. Ссылку отдаёт шлюз на общем домене, поэтому **узел должен оставаться подключённым**.

| Инструмент | Что делает |
| :--- | :--- |
| `share_file(path, name, overwrite)` | Публикует **один файл**: он копируется в каталог публикаций узла, и вы получаете `https://<общий-домен>/<узел>/<имя>-<случайное>/<файл>`. `overwrite` заменяет публикацию с тем же именем. |
| `serve_dir(path, name)` | Отдаёт **каталог** на месте — копия не создаётся. Если есть `index.html`, отдаётся он, иначе показывается список файлов. Только чтение (GET/HEAD). Возвращает `https://<общий-домен>/<узел>/<имя>-<случайное>/`. |
| `share_list()` | Список активных публикаций: ссылки, корни и запущен ли локальный сервер. |
| `unshare(name)` | Останавливает публикацию и отзывает ссылку. Принимает имя, slug или полную ссылку. Для файла удаляется и копия в каталоге публикаций; отдаваемый каталог на диске не трогается. |

> [!IMPORTANT]
> Случайная часть пути **и есть** пароль — любой, у кого есть ссылка, прочитает файл или просмотрит каталог. Относитесь к ссылке как к паролю и снимайте публикацию через `unshare`, когда она больше не нужна. Ограничения: 32 МиБ на файл по умолчанию, потолок 64 МиБ через `MESH_WEB_MAX_BYTES`.

### Поведение агента

| Инструмент | Что делает |
| :--- | :--- |
| `get_orchestration_skill()` | Загружает текущие рабочие правила агента и навык оркестрации. |

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

**🪟 Windows — визуальный установщик** (из клона или релиза)

```powershell
.\install-gui.cmd
```

Мастер на WinForms: те же варианты, что и в консольном установщике, переключение языка
на ходу и готовая ссылка MCP в конце — см. [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

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

На Windows те же варианты есть в визуальном установщике (`.\install-gui.cmd`); локальный автономный режим там остаётся консольным (`.\install.ps1 -Mode standalone -Port 8096`).

---

## 🪟 Установка на Windows

В Windows уже есть PowerShell 5.1, поэтому готовить ничего не нужно — ни Python, ни Node.js, ни права администратора. Оба установщика заканчивают одинаково: кладут ссылку MCP в буфер обмена.

|  | Консольный установщик | Визуальный установщик |
| :--- | :--- | :--- |
| Запуск | `irm https://smart-server.online/install.ps1 \| iex` | `.\install-gui.cmd` |
| Что нужно | только PowerShell | клон или распакованный релиз — он вызывает `install.ps1`, `core/` и `install.sh` |
| Варианты | `-Mode tunnel` (по умолчанию), `-Mode standalone`, `-User`, `-Gateway`, `-Token`, `-Port`, `-DryRun` | Быстрая настройка, Кастомная настройка, Удалённо по SSH |
| Язык | `-Lang en` / `-Lang ru` | переключатель в шапке окна или `-Lang` |

### Готовый .exe установщика

В релизе есть один собранный установщик — `AntigravityMesh-Setup-<версия>.exe`, для машин,
где не хочется ничего клонировать:

```powershell
.\AntigravityMesh-Setup-0.2.6.exe            # открыть визуальный установщик
.\AntigravityMesh-Setup-0.2.6.exe -Lang ru   # начать на русском
.\AntigravityMesh-Setup-0.2.6.exe -SelfTest  # самопроверка без окна, печатает JSON
.\AntigravityMesh-Setup-0.2.6.exe --version  # показать версию
```

Внутри него лежат мастер, `install.ps1`, `core/` и `install.sh`; он распаковывает их в
`%LOCALAPPDATA%\AntigravityMesh\setup\<версия>` (можно переопределить переменной
`MESH_SETUP_DIR`) и запускает мастера оттуда. Ему нужны только .NET Framework и
PowerShell, которые в Windows уже есть, а собирается он из этого же репозитория скриптом
`.\build-installer-exe.ps1` — без SDK, без NuGet и без сети.

Файл **не подписан**, поэтому SmartScreen при первом запуске может попросить
подтверждение.

### Визуальный установщик

![Визуальный установщик для Windows](docs/images/gui-welcome-ru.png)

- **Быстрая настройка** (рекомендуется) — имя узла = имя ПК, общий домен шлюза, автозапуск Windows. Это вариант для домашнего ПК или ноутбука.
- **Кастомная настройка** — своё имя узла, свой общий домен, необязательный токен.
- **Удалённо по SSH** — код узла копируется на Linux-хост, там запускается `install.sh`. Нужен компонент Windows *«Клиент OpenSSH»* и вход по ключу; ввод пароля не поддерживается.

Первый экран запускает `install.ps1 -DryRun` и показывает, что найдено: Python, `websockets`, разрешённый домен шлюза, каталог конфигурации и путь автозапуска — ничего не меняя. Перед запуском окно показывает точную команду, во время установки — построчный вывод установщика, в конце — ссылку MCP с кнопкой копирования, инструкцию из трёх шагов и созданные пути.

Подробности, снимки экрана и особенности SSH: [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

### Что создаётся при установке на Windows

| Путь | Что это |
| :--- | :--- |
| `%USERPROFILE%\.config\antigravity-mesh\agent.env` | `MESH_GATEWAY`, `MESH_USER`, `MESH_TOKEN` |
| `…\Start Menu\Programs\Startup\antigravity-agent.vbs` | запись автозапуска, чтобы узел поднимался после перезагрузки |
| `%USERPROFILE%\.config\antigravity-mesh\agent.log` | вывод агента — на случай, если автозапуск молча не сработал |

### Язык

Визуальный установщик стартует на языке ваших языковых настроек Windows — язык интерфейса и список предпочитаемых языков читаются из реестра, — а если ничего не найдено, на английском. Флаг `-Lang ru` / `-Lang en` переопределяет автоопределение. Переключатель в шапке окна меняет все подписи сразу.

### Если что-то не работает

- **«Домен шлюза не задан»** — эта копия не была опубликована с доменом. Укажите его: режим *«Кастомная настройка»* в мастере, либо `.\install.ps1 -Gateway <общий-домен>`, либо переменная `MESH_PUBLIC_URL`.
- **Регистрация не проходит** — установщик останавливается **до** записи файлов, если шлюз недоступен. Проверьте, что домен разрешается, и повторите с явным `-Gateway`.
- **`python` открывает Microsoft Store** — это заглушка App Execution Alias, а не интерпретатор. Оба установщика находят настоящий `python.exe` по полному пути; в автозапуск заглушка не попадает никогда.
- **Узел не поднимается после перезагрузки** — запустите `.\ops\doctor.ps1`: он покажет интерпретатор из автозапуска, свежесть heartbeat, последние строки `agent.log` (включая оборванную) и то, что о узле думает шлюз. `.\ops\windows\agent-watchdog.ps1` выполняет ту же проверку один раз и поднимает агент; задача `AntigravityMeshWatchdog` повторяет её каждые пять минут, поэтому умерший агент поднимается сам.

### Автономный режим на Windows

В мастере его нет, используйте консольный установщик:

```powershell
.\install.ps1 -Mode standalone -Port 8096
```

Он поднимает локальный FastMCP-сервер на `http://localhost:8096/sse` и кладёт эту ссылку в буфер обмена.

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

> [!TIP]
> **Домен задаётся в одном месте.** `MESH_PUBLIC_URL` в `domain.env`
> (`/etc/antigravity-mesh/domain.env` в Linux, `%USERPROFILE%\.config\antigravity-mesh\domain.env`
> в Windows) — единственная настройка, где он назван: узел, шлюз, установщики, публичные ссылки
> на файлы и конфиги nginx берут его через `core/domain.py`. Смена домена — это одно значение и
> `ops/nginx/render-domain.sh --apply`, подробности в [docs/DOMAIN.md](docs/DOMAIN.md).

> [!IMPORTANT]
> **Кодировка `install.ps1` — один файл, два представления.** В репозитории файл лежит в UTF-8 *с* меткой BOM: Windows PowerShell 5.1 читает скрипт без метки в ANSI-кодировке, русский текст превращается в «умные кавычки», и парсер отвергает файл целиком — то есть `.\install.ps1` из клона просто не запустится. Копию, которую отдаёт шлюз, `deploy_gateway.sh` записывает *без* метки: `irm … | iex` получает метку как часть первого токена, и `param(...)` перестаёт быть первым оператором. Для этого адреса nginx объявляет `charset utf-8`, поэтому копия без метки читается корректно. Пожалуйста, сохраняйте оба свойства при правке файла.

**Политика TLS-сертификата: только общий домен, без SAN на устройства.** Имена узлов в сертификат не добавляются никогда — узел выбирается `?user=`:

```bash
sudo certbot certificates | grep -A1 'Certificate Name: smart-server.online'
sudo certbot renew --dry-run --cert-name smart-server.online   # проверка продления
```
Старые SAN'ы устройств могут остаться с прежнего контракта: они безвредны и поддерживают совместимость старых ссылок. Убрать их можно при следующем продлении, когда субдоменными ссылками никто не пользуется.

---

## 📄 Лицензия

**MIT** — подробности в файле [LICENSE](LICENSE).
