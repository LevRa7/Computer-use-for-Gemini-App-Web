# 🌐 Authoritative Remote Host & Infrastructure Profile
> **Синхронизировано:** `2026-09-06T17:12:22.563785+00:00` | **Источник:** Автоматический аудит ядра хоста

---

## 1. Основной VPS-сервер (Primary Host)
- **Имя хоста:** `racknerd-778284c`
- **Публичный IP:** `204.152.223.171`
- **Домен (HTTPS):** `https://levra7-ai.mooo.com`
- **Tailscale IP:** `100.75.165.28`
- **ОС и ядро:** `Ubuntu 22.04.5 LTS` (Kernel `5.15.0-190-generic`)
- **Аптайм:** `114.37` часов | **Load Average:** `[0.77, 0.72, 0.67]`
- **ОЗУ:** 2464 MB всего | **Свободно/Доступно:** 326 MB
- **Диск `/`:** 38.06 GB всего | **Занято:** 89.2% (2.38 GB свободно)

### Ключевые активные службы на основном сервере:
- 🟢 `omniroute.service`: **active**
- 🟢 `nginx.service`: **active**
- 🟢 `agy-webhook.service`: **active**
- 🔴 `agy-watcher.service`: **inactive**
- 🟢 `agy-mcp.service`: **active**
- 🟢 `mariadb.service`: **active**
- 🟢 `docker.service`: **active**
- 🟢 `containerd.service`: **active**
- 🟢 `tailscaled.service`: **active**
- 🔴 `sing-box.service`: **inactive**

### Предустановленные инструменты управления:
- `agy`: ✅ Доступен
- `sshpass`: ✅ Доступен
- `docker`: ✅ Доступен
- `python3`: ✅ Доступен
- `nginx`: ✅ Доступен

---

## 2. Известные удалённые узлы (Tailscale Network)
### 💻 matebook16-deb-1 (`Matebook16-Deb`)
- **Tailscale IP:** `100.119.202.62`
- **Пользователь SSH:** **`lev`** *(Внимание: логин именно `lev`, не `me`!)*
- **ОС:** `Debian GNU/Linux 13 (trixie)` | **CPU:** `AMD Ryzen 7 5800H (16 vCPU)` | **RAM:** `14 GiB`
- **Назначение:** Desktop Workstation & AI Development
- **Нативный вызов через MCP:** `matebook_exec('<команда>')`
- **Команда SSH на VPS хосте:** `sshpass -p 'ST720p' ssh -o StrictHostKeyChecking=no lev@100.119.202.62 '<cmd>'` *(Примечание: прямой SSH из Gemini невозможен!)*

### 💻 debian-compute-node (`debian`)
- **Tailscale IP:** `100.86.180.81`
- **Пользователь SSH:** **`root`** *(Внимание: логин именно `root`, не `me`!)*
- **ОС:** `Debian GNU/Linux 13 (trixie)` | **CPU:** `AMD Ryzen 9 5950X 16-Core Processor (32 vCPU)` | **RAM:** `62 GiB`
- **Назначение:** Heavy Compute & VM Host (QEMU / Antigravity / AI Services)
- **Нативный вызов через MCP:** `debian_exec('<команда>')`
- **Команда SSH на VPS хосте:** `sshpass -p 'ST720p' ssh -o StrictHostKeyChecking=no root@100.86.180.81 '<cmd>'` *(Примечание: прямой SSH из Gemini невозможен!)*

---

## 3. Регламент оркестрации и выполнения команд (Web-Gemini)
> 🚨 **МАНДАТ:** Все команды, чтение/запись файлов и телеметрия выполняются **ИСКЛЮЧИТЕЛЬНО НАПРЯМУЮ ЧЕРЕЗ API / MCP** (`https://levra7-ai.mooo.com/sse`). Google Диск используется **ТОЛЬКО** в случае, если необходимо передать пользователю файл, документ или архив. Создавать файлы очередей в Google Диске для обычных команд ЗАПРЕЩЕНО.

### ⛔ Антипаттерны и запреты:
1. 🚫 **Прямой SSH из браузера Gemini ЗАПРЕЩЁН:** В песочнице Google нет Tailscale. Прямой SSH всегда завершится ошибкой. Используйте инструменты `bash_exec`, `debian_exec`, `matebook_exec`.
2. 🚫 **Google Search для API ЗАПРЕЩЁН:** Поисковик не может опрашивать динамический API и блокируется шлюзом с 403-й ошибкой.
3. 🚫 **Браузер («Компьютер») ЗАПРЕЩЁН:** Не подходит для вызова API.
4. 🚫 **Перезапись файлов в Google Drive ЗАПРЕЩЕНА:** В Gemini нет `update_file`.
5. 🚫 **Обращение по прямому IP `204.152.223.171`:** Сертификат SSL привязан строго к домену `levra7-ai.mooo.com`.

### 🛠️ Нативные инструменты MCP (Gemini Web Spark):
- `bash_exec(command)`: моментальный запуск команд на VPS (`racknerd-778284c`).
- `debian_exec(command)`: ресурсоёмкие вычисления и тесты на **AMD Ryzen 9 5950X / 64GB RAM** (`100.86.180.81`).
- `matebook_exec(command)`: команды и кодинг-агент на **Matebook16** (`100.119.202.62`).
- `system_vitals()`, `matebook_vitals()`: мгновенная телеметрия.
- `read_file(path, start_line, end_line)`: чтение любых файлов на сервере.
- `write_file(path, content)`: создание и модификация файлов на сервере.

### 🧠 Обязательная фиксация в мыслях (`<thought>`):
Агент фиксирует: 1) имя инструмента MCP, 2) точные пути к файлам (`/root/agy-gdrive-runner/...`), 3) точную выполняемую команду целиком, 4) задействованные файлы, 5) целевой узел, замер времени и результат.

### 📋 Формат ответа:
Выдавать готовый результат в валидном Markdown-блоке `### ✅ Выполнено:` с блоком кода (` ```bash ... ``` `) и лаконичный текст после; категорически запрещено использовать HTML-теги `<details>` и `<summary>`; ход процесса описывать только в мыслях (`<thought>`).
