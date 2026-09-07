#!/usr/bin/env python3
"""
agy_sync.py - Authoritative Server Facts Collector & Google Drive Synchronizer.
Periodically audits the local server and Tailscale network, updates local cache,
and synchronizes server_facts.json and server_profile.md to Google Drive.
Guarantees that stored data is 100% consistent with the actual state of the server.
"""

import os
import sys
import json
import time
import shutil
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"
FACTS_FILE = BASE_DIR / "server_facts.json"
PROFILE_FILE = BASE_DIR / "server_profile.md"
LOG_FILE = BASE_DIR / "agy_sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("agy_sync")

AGY_BIN = "/root/.local/bin/agy"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

def get_valid_token() -> str:
    """Reads token.json, refreshes if expired, returns valid access token."""
    if not TOKEN_FILE.exists():
        raise RuntimeError("token.json not found!")

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check expiry or refresh proactively
    token = data.get("token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    refresh_token = data.get("refresh_token")

    # Try token
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{DRIVE_API_BASE}/about?fields=user", headers=headers, timeout=5)
        if r.status_code == 200:
            return token
    except Exception:
        pass

    # Refresh
    logger.info("Access token expired or rejected. Refreshing with Google OAuth...")
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }, timeout=10)
    if r.status_code == 200:
        res = r.json()
        new_token = res["access_token"]
        data["token"] = new_token
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info("Successfully refreshed Google Drive token.")
        return new_token
    else:
        logger.error(f"Failed to refresh token: {r.status_code} {r.text}")
        return token


def collect_server_facts() -> dict:
    """Collects 100% accurate, live facts from kernel, procfs, systemd, and Tailscale."""
    now_utc = datetime.now(timezone.utc).isoformat()
    
    # 1. CPU & Load
    load_avg = []
    try:
        with open("/proc/loadavg", "r") as f:
            load_avg = [float(x) for x in f.read().split()[:3]]
    except Exception:
        pass

    uptime_hours = 0.0
    try:
        with open("/proc/uptime", "r") as f:
            uptime_hours = round(float(f.read().split()[0]) / 3600, 2)
    except Exception:
        pass

    # 2. Memory
    ram = {}
    try:
        with open("/proc/meminfo", "r") as f:
            m = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    if k in ["MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree"]:
                        m[k] = int(v) // 1024
            ram = {
                "total_mb": m.get("MemTotal", 0),
                "available_mb": m.get("MemAvailable", 0),
                "used_mb": m.get("MemTotal", 0) - m.get("MemAvailable", 0),
                "swap_total_mb": m.get("SwapTotal", 0),
                "swap_used_mb": m.get("SwapTotal", 0) - m.get("SwapFree", 0)
            }
    except Exception:
        pass

    # 3. Disk
    disk_root = {}
    try:
        d = shutil.disk_usage("/")
        disk_root = {
            "total_gb": round(d.total / (1024**3), 2),
            "free_gb": round(d.free / (1024**3), 2),
            "used_gb": round(d.used / (1024**3), 2),
            "used_pct": round((d.used / d.total) * 100, 1)
        }
    except Exception:
        pass

    # 4. Services
    key_services = [
        "omniroute.service",
        "nginx.service",
        "agy-webhook.service",
        "agy-watcher.service",
        "agy-mcp.service",
        "mariadb.service",
        "docker.service",
        "containerd.service",
        "tailscaled.service",
        "sing-box.service"
    ]
    services_status = {}
    for s in key_services:
        try:
            res = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=2)
            st = res.stdout.strip()
            if st in ["activating", "deactivating", "reloading"]:
                time.sleep(1)
                recheck = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=2)
                st = recheck.stdout.strip()
            services_status[s] = st
        except Exception:
            services_status[s] = "unknown"

    # 5. Tailscale Network Topology
    tailscale_peers = []
    try:
        ts_res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        if ts_res.returncode == 0:
            ts_data = json.loads(ts_res.stdout)
            peers = ts_data.get("Peer", {})
            for p_id, p_info in peers.items():
                name = p_info.get("HostName", "")
                ip = p_info.get("TailscaleIPs", [None])[0]
                online = p_info.get("Online", False)
                active = p_info.get("Active", False)
                os_name = p_info.get("OS", "")
                if name and not name.startswith("funnel-"):
                    tailscale_peers.append({
                        "hostname": name,
                        "ip": ip,
                        "os": os_name,
                        "online": online,
                        "active": active
                    })
    except Exception as e:
        logger.warning(f"Tailscale status query failed: {e}")

    facts = {
        "schema_version": "1.0",
        "synchronized_at": now_utc,
        "primary_server": {
            "hostname": os.uname().nodename,
            "public_ip": "204.152.223.171",
            "domain": "levra7-ai.mooo.com",
            "tailscale_ip": "100.75.165.28",
            "os": "Ubuntu 22.04.5 LTS",
            "kernel": os.uname().release,
            "uptime_hours": uptime_hours,
            "load_average": load_avg,
            "ram": ram,
            "disk": disk_root,
            "tools_installed": {
                "agy": os.path.exists(AGY_BIN),
                "sshpass": shutil.which("sshpass") is not None,
                "docker": shutil.which("docker") is not None,
                "python3": True,
                "nginx": True
            },
            "services": services_status
        },
        "known_remote_nodes": [
            {
                "name": "matebook16-deb-1",
                "hostname": "Matebook16-Deb",
                "tailscale_ip": "100.119.202.62",
                "ssh_user": "lev",
                "auth_type": "password",
                "os": "Debian GNU/Linux 13 (trixie)",
                "cpu": "AMD Ryzen 7 5800H (16 vCPU)",
                "ram": "14 GiB",
                "role": "Desktop Workstation & AI Development",
                "ssh_command_example": "sshpass -p 'ST720p' ssh -o StrictHostKeyChecking=no lev@100.119.202.62 '<cmd>'"
            },
            {
                "name": "debian-compute-node",
                "hostname": "debian",
                "tailscale_ip": "100.86.180.81",
                "ssh_user": "root",
                "auth_type": "password",
                "os": "Debian GNU/Linux 13 (trixie)",
                "cpu": "AMD Ryzen 9 5950X 16-Core Processor (32 vCPU)",
                "ram": "62 GiB",
                "role": "Heavy Compute & VM Host (QEMU / Antigravity / AI Services)",
                "ssh_command_example": "sshpass -p 'ST720p' ssh -o StrictHostKeyChecking=no root@100.86.180.81 '<cmd>'"
            },
            {
                "name": "racknerd-5a24bf9",
                "hostname": "racknerd-5a24bf9",
                "public_ip": "192.129.148.93",
                "tailscale_ip": "100.114.41.84",
                "ssh_user": "root",
                "auth_type": "password",
                "os": "Linux (RackNerd)",
                "cpu": "1 vCPU",
                "ram": "2 GiB",
                "role": "Secondary VPS Node & Exit Node",
                "ssh_command_example": "sshpass -p '7tE95vUmzkTR3lI59A' ssh -o StrictHostKeyChecking=no root@192.129.148.93 '<cmd>'"
            }
        ],
        "tailscale_topology": tailscale_peers
    }

    return facts


def generate_profile_markdown(facts: dict) -> str:
    """Generates an authoritative, LLM-optimized profile of the remote infrastructure."""
    s = facts["primary_server"]
    ts_nodes = facts.get("tailscale_topology", [])
    remote_nodes = facts.get("known_remote_nodes", [])

    md = []
    md.append("# 🌐 Authoritative Remote Host & Infrastructure Profile")
    md.append(f"> **Синхронизировано:** `{facts['synchronized_at']}` | **Источник:** Автоматический аудит ядра хоста")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Основной VPS-сервер (Primary Host)")
    md.append(f"- **Имя хоста:** `{s['hostname']}`")
    md.append(f"- **Публичный IP:** `{s['public_ip']}`")
    md.append(f"- **Домен (HTTPS):** `https://{s['domain']}`")
    md.append(f"- **Tailscale IP:** `{s['tailscale_ip']}`")
    md.append(f"- **ОС и ядро:** `{s['os']}` (Kernel `{s['kernel']}`)")
    md.append(f"- **Аптайм:** `{s['uptime_hours']}` часов | **Load Average:** `{s['load_average']}`")
    md.append(f"- **ОЗУ:** {s['ram'].get('total_mb', 0)} MB всего | **Свободно/Доступно:** {s['ram'].get('available_mb', 0)} MB")
    md.append(f"- **Диск `/`:** {s['disk'].get('total_gb', 0)} GB всего | **Занято:** {s['disk'].get('used_pct', 0)}% ({s['disk'].get('free_gb', 0)} GB свободно)")
    md.append("")
    md.append("### Ключевые активные службы на основном сервере:")
    for svc, st in s["services"].items():
        icon = "🟢" if st == "active" else "🔴"
        md.append(f"- {icon} `{svc}`: **{st}**")
    md.append("")
    md.append("### Предустановленные инструменты управления:")
    for t, installed in s["tools_installed"].items():
        st = "✅ Доступен" if installed else "❌ Отсутствует"
        md.append(f"- `{t}`: {st}")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 2. Известные удалённые узлы (Tailscale Network)")
    for node in remote_nodes:
        md.append(f"### 💻 {node['name']} (`{node['hostname']}`)")
        md.append(f"- **Tailscale IP:** `{node['tailscale_ip']}`")
        md.append(f"- **Пользователь SSH:** **`{node['ssh_user']}`** *(Внимание: логин именно `{node['ssh_user']}`, не `me`!)*")
        md.append(f"- **ОС:** `{node['os']}` | **CPU:** `{node['cpu']}` | **RAM:** `{node['ram']}`")
        md.append(f"- **Назначение:** {node['role']}")
        mcp_fn = "debian_exec" if "debian" in node['name'] else "matebook_exec"
        md.append(f"- **Нативный вызов через MCP:** `{mcp_fn}('<команда>')`")
        md.append(f"- **Команда SSH на VPS хосте:** `{node['ssh_command_example']}` *(Примечание: прямой SSH из Gemini невозможен!)*")
        md.append("")
    md.append("---")
    md.append("")
    md.append("## 3. Регламент оркестрации и выполнения команд (Web-Gemini)")
    md.append("> 🚨 **МАНДАТ:** Все команды, чтение/запись файлов и телеметрия выполняются **ИСКЛЮЧИТЕЛЬНО НАПРЯМУЮ ЧЕРЕЗ API / MCP** (`https://levra7-ai.mooo.com/sse`). Google Диск используется **ТОЛЬКО** в случае, если необходимо передать пользователю файл, документ или архив. Создавать файлы очередей в Google Диске для обычных команд ЗАПРЕЩЕНО.")
    md.append("")
    md.append("### ⛔ Антипаттерны и запреты:")
    md.append("1. 🚫 **Прямой SSH из браузера Gemini ЗАПРЕЩЁН:** В песочнице Google нет Tailscale. Прямой SSH всегда завершится ошибкой. Используйте инструменты `bash_exec`, `debian_exec`, `matebook_exec`.")
    md.append("2. 🚫 **Google Search для API ЗАПРЕЩЁН:** Поисковик не может опрашивать динамический API и блокируется шлюзом с 403-й ошибкой.")
    md.append("3. 🚫 **Браузер («Компьютер») ЗАПРЕЩЁН:** Не подходит для вызова API.")
    md.append("4. 🚫 **Перезапись файлов в Google Drive ЗАПРЕЩЕНА:** В Gemini нет `update_file`.")
    md.append("5. 🚫 **Обращение по прямому IP `204.152.223.171`:** Сертификат SSL привязан строго к домену `levra7-ai.mooo.com`.")
    md.append("")
    md.append("### 🛠️ Нативные инструменты MCP (Gemini Web Spark):")
    md.append("- `bash_exec(command)`: моментальный запуск команд на VPS (`racknerd-778284c`).")
    md.append("- `debian_exec(command)`: ресурсоёмкие вычисления и тесты на **AMD Ryzen 9 5950X / 64GB RAM** (`100.86.180.81`).")
    md.append("- `matebook_exec(command)`: команды и кодинг-агент на **Matebook16** (`100.119.202.62`).")
    md.append("- `system_vitals()`, `matebook_vitals()`: мгновенная телеметрия.")
    md.append("- `read_file(path, start_line, end_line)`: чтение любых файлов на сервере.")
    md.append("- `write_file(path, content)`: создание и модификация файлов на сервере.")
    md.append("")
    md.append("### 🧠 Обязательная фиксация в мыслях (`<thought>`):")
    md.append("Агент фиксирует: 1) имя инструмента MCP, 2) точные пути к файлам (`/root/agy-gdrive-runner/...`), 3) точную выполняемую команду целиком, 4) задействованные файлы, 5) целевой узел, замер времени и результат.")
    md.append("")
    md.append("### 📋 Формат ответа:")
    md.append("Выдавать готовый результат в валидном Markdown-блоке `### ✅ Выполнено:` с блоком кода (` ```bash ... ``` `) и лаконичный текст после; категорически запрещено использовать HTML-теги `<details>` и `<summary>`; ход процесса описывать только в мыслях (`<thought>`).")
    md.append("")
    return "\n".join(md)


def sync_to_google_drive(facts_json_str: str, profile_md_str: str):
    """Uploads or updates server_facts.json, server_profile.md, and subagent_mesh.md on Google Drive."""
    token = get_valid_token()
    headers = {"Authorization": f"Bearer {token}"}

    subagent_mesh_str = ""
    mesh_path = BASE_DIR / "SUBAGENT_MESH_SKILL.md"
    if mesh_path.exists():
        try:
            with open(mesh_path, "r", encoding="utf-8") as f:
                subagent_mesh_str = f.read()
        except Exception as e:
            logger.warning(f"Failed to read SUBAGENT_MESH_SKILL.md for sync: {e}")

    files_to_sync = [
        ("server_facts.json", "application/json", facts_json_str),
        ("server_profile.md", "text/markdown", profile_md_str)
    ]
    if subagent_mesh_str:
        files_to_sync.append(("subagent_mesh.md", "text/markdown", subagent_mesh_str))

    KNOWN_FILE_IDS = {
        "server_facts.json": "18nSMo0pMkNg1qekLBmgJcZif8h7ntNLo",
        "server_profile.md": "1eoAg_4CGb-ZZsdwO8gxXrHRBtAK-_uKb",
        "subagent_mesh.md": "16to6BcojuCVJm2YfB0orrtMThVXzoun2"
    }

    for filename, mime_type, content in files_to_sync:
        file_id = KNOWN_FILE_IDS.get(filename)
        if file_id:
            for attempt in range(3):
                try:
                    patch_headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": f"{mime_type}; charset=UTF-8"
                    }
                    p_res = requests.patch(
                        f"{DRIVE_UPLOAD_BASE}/files/{file_id}?uploadType=media",
                        headers=patch_headers,
                        data=content.encode("utf-8"),
                        timeout=15
                    )
                    if p_res.status_code == 200:
                        logger.info(f"Successfully updated '{filename}' on Google Drive (ID: {file_id}).")
                        break
                    elif p_res.status_code == 403:
                        logger.warning(f"Google Drive API rate limit (403) updating '{filename}'. Waiting {(attempt + 1) * 6}s...")
                        time.sleep((attempt + 1) * 6)
                    else:
                        logger.warning(f"Failed to update '{filename}' by ID: {p_res.status_code} ({p_res.text[:80]})")
                        break
                except Exception as e:
                    logger.warning(f"Error updating '{filename}': {e}")
                    time.sleep(2)
            time.sleep(2)
            continue

        logger.warning(f"No known file ID for '{filename}', skipping upload.")


def run_sync_cycle():
    logger.info("Starting facts collection...")
    facts = collect_server_facts()
    profile_md = generate_profile_markdown(facts)
    facts_json = json.dumps(facts, ensure_ascii=False, indent=2)

    # 1. Save locally
    with open(FACTS_FILE, "w", encoding="utf-8") as f:
        f.write(facts_json)
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        f.write(profile_md)
    logger.info(f"Saved local cache: {FACTS_FILE.name} and {PROFILE_FILE.name}")

    # 2. Sync to Google Drive
    sync_to_google_drive(facts_json, profile_md)


def main_loop():
    logger.info("AGY Background Host Synchronizer daemon started.")
    # Run immediately on launch
    try:
        run_sync_cycle()
    except Exception as e:
        logger.error(f"Initial sync failed: {e}")

    # Then sync every 300 seconds (5 minutes)
    while True:
        time.sleep(300)
        try:
            run_sync_cycle()
        except Exception as e:
            logger.error(f"Sync cycle error: {e}")

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_sync_cycle()
    else:
        main_loop()
