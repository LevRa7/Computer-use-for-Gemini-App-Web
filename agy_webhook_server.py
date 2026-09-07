#!/usr/bin/env python3
"""
agy_webhook_server.py - Ultra-fast & Secure micro-webhook server for Antigravity & Bash.
Protected with constant-time token verification, anti-brute-force IP rate limiting,
and strict access controls so ONLY holders of the secret token can execute commands.
"""

import os
import sys
import html
import json
import time
import shutil
import logging
import secrets
import threading
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import subagent_dispatcher

PORT = int(os.environ.get("AGY_WEBHOOK_PORT", 8095))
HOST = os.environ.get("AGY_WEBHOOK_HOST", "127.0.0.1")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(BASE_DIR, "webhook_secret.txt")
LOG_FILE = os.path.join(BASE_DIR, "webhook_server.log")
HISTORY_FILE = os.path.join(BASE_DIR, "webhook_history.json")
AGY_BIN = "/root/.local/bin/agy"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("agy-webhook")

def get_or_create_secret() -> str:
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            logger.warning(f"Failed to read secret file: {e}")
    token = secrets.token_hex(16)
    try:
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        os.chmod(SECRET_FILE, 0o600)
    except Exception as e:
        logger.error(f"Failed to save secret file: {e}")
    return token

SECRET_TOKEN = get_or_create_secret()
if not SECRET_TOKEN or len(SECRET_TOKEN) < 16:
    raise RuntimeError("FATAL: SECRET_TOKEN must be at least 16 characters long!")

logger.info(f"Loaded secret token: {SECRET_TOKEN[:4]}...{SECRET_TOKEN[-4:]}")

tasks_lock = threading.Lock()
tasks_store = {}
history_store = []
MAX_HISTORY = 100

# Anti-Brute-Force Rate Limiter
# Structure: { ip: {"attempts": [timestamp, ...], "blocked_until": timestamp} }
auth_limiter_lock = threading.Lock()
failed_attempts = {}
MAX_FAILED_ATTEMPTS = 5
WINDOW_SECONDS = 300       # 5 minutes window
BLOCK_DURATION = 900       # 15 minutes block if exceeded

def is_ip_blocked(client_ip: str) -> bool:
    now = time.time()
    with auth_limiter_lock:
        data = failed_attempts.get(client_ip)
        if not data:
            return False
        if data.get("blocked_until", 0) > now:
            return True
        # Expired block
        if data.get("blocked_until", 0) <= now and data.get("blocked_until", 0) > 0:
            del failed_attempts[client_ip]
            return False
    return False

def record_failed_attempt(client_ip: str):
    now = time.time()
    with auth_limiter_lock:
        if client_ip not in failed_attempts:
            failed_attempts[client_ip] = {"attempts": [now], "blocked_until": 0}
            return

        data = failed_attempts[client_ip]
        # Filter attempts within WINDOW_SECONDS
        recent = [t for t in data["attempts"] if now - t < WINDOW_SECONDS]
        recent.append(now)
        data["attempts"] = recent

        if len(recent) >= MAX_FAILED_ATTEMPTS:
            data["blocked_until"] = now + BLOCK_DURATION
            logger.warning(f"SECURITY ALERT: IP {client_ip} blocked for {BLOCK_DURATION}s due to {len(recent)} failed attempts!")

def record_successful_attempt(client_ip: str):
    with auth_limiter_lock:
        if client_ip in failed_attempts:
            del failed_attempts[client_ip]

def record_history(entry: dict):
    with tasks_lock:
        history_store.insert(0, entry)
        if len(history_store) > MAX_HISTORY:
            history_store.pop()
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history_store[:50], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def get_system_metrics() -> dict:
    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
    }
    try:
        with open("/proc/loadavg", "r") as f:
            load = f.read().strip().split()
            metrics["load_avg"] = [float(x) for x in load[:3]]
    except Exception:
        metrics["load_avg"] = []

    try:
        with open("/proc/uptime", "r") as f:
            up_sec = float(f.read().split()[0])
            metrics["uptime_hours"] = round(up_sec / 3600, 2)
    except Exception:
        pass

    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    if k in ["MemTotal", "MemFree", "MemAvailable"]:
                        meminfo[k] = int(v) // 1024
        metrics["ram_mb"] = {
            "total": meminfo.get("MemTotal", 0),
            "free": meminfo.get("MemFree", 0),
            "available": meminfo.get("MemAvailable", 0),
            "used": meminfo.get("MemTotal", 0) - meminfo.get("MemAvailable", 0)
        }
    except Exception:
        pass

    try:
        disk = shutil.disk_usage("/")
        metrics["disk_gb"] = {
            "total": round(disk.total / (1024**3), 2),
            "free": round(disk.free / (1024**3), 2),
            "used": round(disk.used / (1024**3), 2),
            "used_pct": round((disk.used / disk.total) * 100, 1)
        }
    except Exception:
        pass

    return metrics


def get_matebook_vitals() -> dict:
    t0 = time.time()
    ssh_cmd = [
        "sshpass", "-p", "ST720p",
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "lev@100.119.202.62",
        "free -h; echo '---'; uptime; echo '---'; cat /sys/class/power_supply/BAT*/capacity 2>/dev/null; cat /sys/class/power_supply/BAT*/status 2>/dev/null"
    ]
    try:
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
        dur = round((time.time() - t0) * 1000, 2)
        if res.returncode != 0:
            return {"status": "error", "error": f"SSH failed with code {res.returncode}", "details": res.stderr}
        
        parts = res.stdout.split("---")
        ram_section = parts[0] if len(parts) > 0 else ""
        uptime_section = parts[1] if len(parts) > 1 else ""
        battery_section = parts[2] if len(parts) > 2 else ""

        ram_info = {}
        for line in ram_section.splitlines():
            line_str = line.strip()
            if line_str.startswith("Mem:"):
                cols = line_str.split()
                if len(cols) >= 7:
                    ram_info = {"total": cols[1], "used": cols[2], "free": cols[3], "available": cols[6]}

        uptime_str = uptime_section.strip().splitlines()[0] if uptime_section.strip() else ""
        bat_lines = [l.strip() for l in battery_section.strip().splitlines() if l.strip()]
        bat_pct = bat_lines[0] + "%" if len(bat_lines) > 0 and bat_lines[0].isdigit() else (bat_lines[0] if len(bat_lines) > 0 else "unknown")
        bat_status = bat_lines[1] if len(bat_lines) > 1 else "unknown"

        return {
            "status": "success",
            "host": "matebook16-deb-1",
            "hostname": "Matebook16-Deb",
            "tailscale_ip": "100.119.202.62",
            "ram": ram_info,
            "uptime": uptime_str,
            "battery": {
                "percentage": bat_pct,
                "status": bat_status
            },
            "duration_ms": dur
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "duration_ms": round((time.time() - t0) * 1000, 2)}


def read_file_slice(path_param: str, start_line: int = 1, end_line: int = None, max_lines: int = 100) -> dict:
    if not path_param:
        return {"status": "error", "error": "Missing 'path' parameter"}
    
    if not os.path.isabs(path_param):
        full_path = os.path.abspath(os.path.join(BASE_DIR, path_param))
    else:
        full_path = os.path.abspath(path_param)

    if not os.path.exists(full_path):
        return {"status": "error", "error": f"File not found: '{full_path}'"}
    if os.path.isdir(full_path):
        return {"status": "error", "error": f"Path is a directory, not a file: '{full_path}'. Use /ls instead."}

    file_size = os.path.getsize(full_path)
    if file_size > 20 * 1024 * 1024:
        return {"status": "error", "error": f"File is too large ({file_size} bytes). Max limit is 20MB."}

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"status": "error", "error": f"Failed to read file: {e}"}

    total_lines = len(all_lines)
    start_idx = max(1, start_line) - 1
    
    if end_line is not None and end_line >= start_line:
        end_idx = min(total_lines, end_line)
    else:
        end_idx = min(total_lines, start_idx + min(max_lines, 500))

    selected_lines = all_lines[start_idx:end_idx]
    actual_start = start_idx + 1 if total_lines > 0 else 1
    actual_end = end_idx

    return {
        "status": "success",
        "path": full_path,
        "size_bytes": file_size,
        "total_lines": total_lines,
        "start_line": actual_start,
        "end_line": actual_end,
        "lines_returned": len(selected_lines),
        "content": "".join(selected_lines)
    }


def write_file_content(path_param: str, content: str, mode: str = "overwrite", start_line: int = None, end_line: int = None) -> dict:
    if not path_param:
        return {"status": "error", "error": "Missing 'path' parameter"}
    if content is None:
        content = ""

    if not os.path.isabs(path_param):
        full_path = os.path.abspath(os.path.join(BASE_DIR, path_param))
    else:
        full_path = os.path.abspath(path_param)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    mode = mode.lower().strip() if mode else "overwrite"
    if mode == "append":
        try:
            with open(full_path, "a", encoding="utf-8") as f:
                f.write(content)
            new_size = os.path.getsize(full_path)
            return {
                "status": "success",
                "path": full_path,
                "mode": "append",
                "bytes_appended": len(content.encode("utf-8")),
                "new_size": new_size
            }
        except Exception as e:
            return {"status": "error", "error": f"Append failed: {e}"}

    elif mode == "replace_lines":
        if start_line is None or end_line is None or start_line < 1 or end_line < start_line:
            return {"status": "error", "error": "Invalid start_line/end_line for replace_lines mode"}
        if not os.path.exists(full_path):
            return {"status": "error", "error": f"File '{full_path}' does not exist for replace_lines"}
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            total = len(lines)
            s_idx = start_line - 1
            e_idx = min(total, end_line)
            
            rep_lines = content.splitlines(keepends=True)
            if content.endswith("\n") and (not rep_lines or not rep_lines[-1].endswith("\n")):
                if rep_lines:
                    rep_lines[-1] += "\n"
            lines[s_idx:e_idx] = rep_lines

            tmp_path = full_path + f".tmp.{secrets.token_hex(4)}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.replace(tmp_path, full_path)
            return {
                "status": "success",
                "path": full_path,
                "mode": "replace_lines",
                "replaced_lines": f"{start_line}-{end_line}",
                "new_total_lines": len(lines)
            }
        except Exception as e:
            return {"status": "error", "error": f"Line replacement failed: {e}"}

    else:  # overwrite
        try:
            tmp_path = full_path + f".tmp.{secrets.token_hex(4)}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, full_path)
            bytes_written = len(content.encode("utf-8"))
            return {
                "status": "success",
                "path": full_path,
                "mode": "overwrite",
                "bytes_written": bytes_written
            }
        except Exception as e:
            return {"status": "error", "error": f"Write failed: {e}"}


def list_directory_items(path_param: str = None, recursive: bool = False, max_depth: int = 1, max_items: int = 100) -> dict:
    if not path_param:
        path_param = BASE_DIR
    if not os.path.isabs(path_param):
        full_path = os.path.abspath(os.path.join(BASE_DIR, path_param))
    else:
        full_path = os.path.abspath(path_param)

    if not os.path.exists(full_path):
        return {"status": "error", "error": f"Directory not found: '{full_path}'"}
    if not os.path.isdir(full_path):
        return {"status": "error", "error": f"Path is not a directory: '{full_path}'"}

    max_depth = min(max(1, max_depth), 5)
    max_items = min(max(1, max_items), 500)
    items = []

    def scan(current_dir: str, current_depth: int):
        if current_depth > max_depth or len(items) >= max_items:
            return
        try:
            with os.scandir(current_dir) as entries:
                sorted_entries = sorted(entries, key=lambda e: (not e.is_dir(), e.name.lower()))
                for entry in sorted_entries:
                    if len(items) >= max_items:
                        break
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_symlink = entry.is_symlink()
                        
                        item_type = "directory" if is_dir else ("symlink" if is_symlink else "file")
                        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                        ctime_iso = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
                        
                        items.append({
                            "name": entry.name,
                            "path": entry.path,
                            "type": item_type,
                            "size_bytes": stat.st_size if not is_dir else None,
                            "permissions": oct(stat.st_mode)[-4:],
                            "mtime": mtime_iso,
                            "ctime": ctime_iso
                        })
                        if is_dir and recursive and current_depth < max_depth:
                            scan(entry.path, current_depth + 1)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Error scanning {current_dir}: {e}")

    scan(full_path, 1)

    return {
        "status": "success",
        "directory": full_path,
        "total_items": len(items),
        "items": items
    }


def search_files_ripgrep(query: str, path_param: str = None, is_regex: bool = False, case_sensitive: bool = False, max_results: int = 50, includes: str = None) -> dict:
    if not query:
        return {"status": "error", "error": "Missing 'query' parameter"}

    if not path_param:
        path_param = BASE_DIR
    if not os.path.isabs(path_param):
        full_path = os.path.abspath(os.path.join(BASE_DIR, path_param))
    else:
        full_path = os.path.abspath(path_param)

    if not os.path.exists(full_path):
        return {"status": "error", "error": f"Search path not found: '{full_path}'"}

    max_results = min(max(1, max_results), 200)

    cmd = ["/usr/bin/rg", "--color", "never", "-n", "--column", "-m", str(max_results)]
    if not is_regex:
        cmd.append("-F")
    if not case_sensitive:
        cmd.append("-i")
    else:
        cmd.append("-s")
    if includes:
        cmd.extend(["--glob", includes])

    cmd.extend([query, full_path])

    t0 = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        duration_ms = round((time.time() - t0) * 1000, 2)
        
        matches = []
        for line in res.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) >= 4:
                matches.append({
                    "file": parts[0],
                    "line": int(parts[1]) if parts[1].isdigit() else None,
                    "column": int(parts[2]) if parts[2].isdigit() else None,
                    "content": parts[3].strip()
                })
            else:
                matches.append({"raw": line})

        return {
            "status": "success",
            "query": query,
            "path": full_path,
            "total_matches": len(matches),
            "matches": matches,
            "duration_ms": duration_ms
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Search timed out after 20s"}
    except Exception as e:
        return {"status": "error", "error": f"Search failed: {e}"}


WEB_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AGY Secure Webhook Terminal</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
    .container { max-width: 900px; margin: 0 auto; }
    h1 { color: #38bdf8; font-size: 1.5rem; margin-bottom: 8px; }
    .sub { color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }
    .card { background: #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 16px; border: 1px solid #334155; }
    .field { margin-bottom: 12px; }
    label { display: block; font-size: 0.8rem; color: #94a3b8; margin-bottom: 4px; text-transform: uppercase; font-weight: bold; }
    input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; background: #0f172a; border: 1px solid #475569; color: #fff; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 14px; }
    .row { display: flex; gap: 10px; }
    button { background: #0284c7; color: white; border: none; padding: 10px 18px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    button:hover { background: #0369a1; }
    .quick-btn { background: #334155; font-size: 0.8rem; padding: 6px 12px; }
    .quick-btn:hover { background: #475569; }
    pre { background: #020617; border: 1px solid #1e293b; border-radius: 4px; padding: 14px; overflow-x: auto; color: #4ade80; font-family: 'Courier New', monospace; font-size: 13px; line-height: 1.4; min-height: 80px; white-space: pre-wrap; word-break: break-all; }
    .meta { font-size: 0.8rem; color: #cbd5e1; margin-top: 8px; }
  </style>
</head>
<body>
  <div class="container">
    <h1>🔒 AGY Secure Webhook Console</h1>
    <div class="sub">Latency: ~25-70ms • Authenticated & Protected Remote Execution</div>
    
    <div class="card">
      <div class="field">
        <label>Token / Secret Key</label>
        <input type="password" id="token" placeholder="Enter webhook secret token">
      </div>
      <div class="field">
        <label>Bash Command</label>
        <div class="row">
          <input type="text" id="cmd" placeholder="e.g. free -h, uptime, ps aux | head -n 15" autofocus>
          <button id="runBtn" onclick="runCommand()">Run (Enter)</button>
        </div>
      </div>
      <div class="row" style="margin-top: 10px; flex-wrap: wrap;">
        <button class="quick-btn" onclick="setCmd('uptime')">uptime</button>
        <button class="quick-btn" onclick="setCmd('free -h')">free -h</button>
        <button class="quick-btn" onclick="setCmd('df -h /')">df -h /</button>
        <button class="quick-btn" onclick="setCmd('systemctl status agy-webhook')">webhook status</button>
        <button class="quick-btn" onclick="setCmd('ps aux --sort=-%mem | head -n 15')">top 15 RAM</button>
      </div>
    </div>

    <div class="card">
      <label>Terminal Output</label>
      <pre id="output">Ready. Enter a command and press Run.</pre>
      <div id="meta" class="meta"></div>
    </div>
  </div>

  <script>
    const savedToken = localStorage.getItem('agy_token') || new URLSearchParams(window.location.search).get('token') || '';
    if (savedToken) document.getElementById('token').value = savedToken;

    document.getElementById('cmd').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') runCommand();
    });

    function setCmd(c) {
      document.getElementById('cmd').value = c;
      runCommand();
    }

    async function runCommand() {
      const token = document.getElementById('token').value.trim();
      const cmd = document.getElementById('cmd').value.trim();
      if (!cmd) return;
      if (token) localStorage.setItem('agy_token', token);

      const outEl = document.getElementById('output');
      const metaEl = document.getElementById('meta');
      const runBtn = document.getElementById('runBtn');

      outEl.innerText = 'Executing: ' + cmd + '...';
      metaEl.innerText = '';
      runBtn.disabled = true;

      const t0 = performance.now();
      try {
        const resp = await fetch('exec', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
          },
          body: JSON.stringify({ command: cmd, token: token })
        });
        const roundtrip = Math.round(performance.now() - t0);
        const data = await resp.json();
        
        if (resp.status === 200 && data.status === 'success') {
          outEl.innerText = data.stdout || '(no stdout)';
          if (data.stderr) outEl.innerText += '\\n[STDERR]\\n' + data.stderr;
          metaEl.innerText = `Exit Code: ${data.exit_code} | Server Exec: ${data.duration_ms} ms | Client Round-Trip: ${roundtrip} ms`;
        } else {
          outEl.innerText = 'Error: ' + (data.error || JSON.stringify(data));
          metaEl.innerText = `HTTP ${resp.status} | Round-Trip: ${roundtrip} ms`;
        }
      } catch (err) {
        outEl.innerText = 'Fetch failed: ' + err.message;
      } finally {
        runBtn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""


class WebhookHandler(BaseHTTPRequestHandler):
    def get_client_ip(self) -> str:
        # Check X-Forwarded-For header from Nginx
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        # Fallback to X-Real-IP
        xri = self.headers.get("X-Real-IP")
        if xri:
            return xri.strip()
        return self.client_address[0]

    def log_message(self, format, *args):
        logger.debug("%s - - [%s] %s", self.get_client_ip(), self.log_date_time_string(), format % args)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Agy-Token, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, data: dict, status_code: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, status_code: int = 200):
        body = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html_content: str, status_code: int = 200):
        body = html_content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status_code: int = 400):
        self.send_json({"status": "error", "error": message}, status_code=status_code)

    def verify_token(self, candidate: str) -> bool:
        if not candidate or not isinstance(candidate, str):
            return False
        candidate = candidate.strip()
        if not candidate:
            return False
        # Constant-time comparison
        return secrets.compare_digest(candidate, SECRET_TOKEN)

    def check_auth(self, query_params: dict, body_json: dict = None) -> bool:
        client_ip = self.get_client_ip()

        # Check rate limiter
        if is_ip_blocked(client_ip):
            logger.warning(f"Rejected request from BLOCKED IP {client_ip}")
            return False

        # 1. Bearer token
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
            if self.verify_token(token):
                record_successful_attempt(client_ip)
                return True

        # 2. X-Agy-Token
        custom_hdr = self.headers.get("X-Agy-Token", "").strip()
        if self.verify_token(custom_hdr):
            record_successful_attempt(client_ip)
            return True

        # 3. Query param ?token=...
        query_token = query_params.get("token", [None])[0]
        if self.verify_token(query_token):
            record_successful_attempt(client_ip)
            return True

        # 4. JSON body token
        if body_json and isinstance(body_json, dict):
            body_token = body_json.get("token") or body_json.get("secret")
            if self.verify_token(body_token):
                record_successful_attempt(client_ip)
                return True

        # Failed authentication: record and penalize
        record_failed_attempt(client_ip)
        time.sleep(0.5)  # 500ms tarpit on failed attempt to defeat brute-force
        return False

    def parse_request_data(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if path.startswith("/api/agy"):
            path = path[len("/api/agy"):]
        if not path:
            path = "/"
        query_params = parse_qs(parsed_url.query)

        body_json = {}
        if self.command == "POST":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len > 0:
                raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
                try:
                    body_json = json.loads(raw_body)
                except json.JSONDecodeError:
                    form_dict = parse_qs(raw_body)
                    body_json = {k: v[0] for k, v in form_dict.items()}

        return path, query_params, body_json

    def handle_live_route(self, path: str, query_params: dict):
        live_dir = os.path.join(BASE_DIR, "live_logs")
        os.makedirs(live_dir, exist_ok=True)

        parts = [p for p in path.strip("/").split("/") if p]
        # /live -> list recent live tasks
        if len(parts) <= 1:
            task_files = []
            for f in sorted(os.listdir(live_dir), reverse=True):
                if f.endswith(".json"):
                    try:
                        with open(os.path.join(live_dir, f), "r", encoding="utf-8") as jf:
                            task_files.append(json.load(jf))
                    except Exception:
                        pass
            metrics_file = os.path.join(BASE_DIR, "task_metrics.json")
            recent_metrics = []
            if os.path.exists(metrics_file):
                try:
                    with open(metrics_file, "r", encoding="utf-8") as mf:
                        recent_metrics = json.load(mf)[:15]
                except Exception:
                    pass
            self.send_json({"status": "success", "live_tasks": task_files[:15], "recent_metrics": recent_metrics})
            return

        task_id = parts[1]
        log_file = os.path.join(live_dir, f"{task_id}.log")
        meta_file = os.path.join(live_dir, f"{task_id}.json")

        meta = {}
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
            except Exception:
                pass

        content = ""
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as lf:
                    content = lf.read()
            except Exception as e:
                content = f"[Error reading log: {e}]"
        elif meta:
            content = meta.get("output", "[Output not available yet]")
        else:
            metrics_file = os.path.join(BASE_DIR, "task_metrics.json")
            if os.path.exists(metrics_file):
                try:
                    with open(metrics_file, "r", encoding="utf-8") as mf:
                        for m in json.load(mf):
                            if m.get("task_id") == task_id:
                                meta = m
                                content = "\n".join(c.get("command", "") + "\n" + c.get("output", "") for c in m.get("commands", []))
                                break
                except Exception:
                    pass

        if not meta and not content:
            self.send_error_json(f"Live task '{task_id}' not found.", 404)
            return

        fmt = query_params.get("format", ["html"])[0].lower()
        if fmt in ["raw", "text", "txt"]:
            self.send_text(content)
            return
        elif fmt == "json":
            self.send_json({
                "status": "success",
                "task_id": task_id,
                "meta": meta,
                "output": content
            })
            return

        status = meta.get("status", "running")
        status_color = "#3fb950" if status == "completed" else ("#f85149" if status == "failed" else "#e3b341")
        status_badge = "✅ ЗАВЕРШЕНО" if status == "completed" else ("❌ ОШИБКА" if status == "failed" else "⏳ ВЫПОЛНЯЕТСЯ")
        cmd_text = meta.get("command", "")
        if isinstance(cmd_text, list):
            cmd_text = " && ".join(cmd_text)

        html_page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live Task Stream - {html.escape(task_id)}</title>
  <style>
    body {{
      background-color: #0d1117;
      color: #c9d1d9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      margin: 0;
      padding: 16px;
    }}
    .container {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    .header {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px 8px 0 0;
      padding: 14px 18px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .badge {{
      background-color: {status_color};
      color: #000;
      font-weight: bold;
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 13px;
    }}
    .meta-row {{
      background: #161b22;
      border-left: 1px solid #30363d;
      border-right: 1px solid #30363d;
      padding: 8px 18px;
      font-size: 13px;
      color: #8b949e;
      border-bottom: 1px solid #21262d;
    }}
    .meta-row code {{
      color: #58a6ff;
    }}
    pre.terminal {{
      background: #010409;
      color: #58a6ff;
      border: 1px solid #30363d;
      border-top: none;
      border-radius: 0 0 8px 8px;
      padding: 16px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.45;
      overflow-x: auto;
      white-space: pre-wrap;
      min-height: 250px;
      max-height: 70vh;
      overflow-y: auto;
    }}
    .links {{
      margin-top: 12px;
      font-size: 13px;
    }}
    .links a {{
      color: #58a6ff;
      text-decoration: none;
      margin-right: 14px;
    }}
    .links a:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div><strong>Задача:</strong> <code>{html.escape(task_id)}</code></div>
      <div id="statusBadge" class="badge">{status_badge}</div>
    </div>
    <div class="meta-row">
      <span><strong>Команда:</strong> <code>{html.escape(str(cmd_text))}</code></span>
      <span style="float:right;" id="durationText">Длительность: {meta.get("duration_seconds", "...")}s</span>
    </div>
    <pre id="terminalOutput" class="terminal">{html.escape(content)}</pre>
    <div class="links">
      <a href="?format=raw" target="_blank">📄 Raw Text</a>
      <a href="?format=json" target="_blank">⚙️ JSON</a>
      <a href="/api/agy/live" target="_blank">📋 Все задачи</a>
    </div>
  </div>

  <script>
    const taskId = "{html.escape(task_id)}";
    let isRunning = "{status}" === "running";
    const terminal = document.getElementById("terminalOutput");
    terminal.scrollTop = terminal.scrollHeight;

    async function pollLogs() {{
      if (!isRunning) return;
      try {{
        const r = await fetch("?format=json");
        if (r.ok) {{
          const data = await r.json();
          terminal.textContent = data.output || "";
          terminal.scrollTop = terminal.scrollHeight;
          if (data.meta && data.meta.status) {{
            if (data.meta.status === "completed") {{
              isRunning = false;
              document.getElementById("statusBadge").textContent = "✅ ЗАВЕРШЕНО (код " + (data.meta.exit_code || 0) + ")";
              document.getElementById("statusBadge").style.backgroundColor = "#3fb950";
              if (data.meta.duration_seconds) {{
                document.getElementById("durationText").textContent = "Длительность: " + data.meta.duration_seconds + "s";
              }}
            }} else if (data.meta.status === "failed") {{
              isRunning = false;
              document.getElementById("statusBadge").textContent = "❌ ОШИБКА";
              document.getElementById("statusBadge").style.backgroundColor = "#f85149";
            }}
          }}
        }}
      }} catch (e) {{}}
    }}

    if (isRunning) {{
      setInterval(pollLogs, 1000);
    }}
  </script>
</body>
</html>"""
        self.send_html(html_page)

    def do_GET(self):
        client_ip = self.get_client_ip()
        if is_ip_blocked(client_ip):
            self.send_error_json("Too many failed authentication attempts. Access blocked.", 429)
            return

        start_time = time.time()
        path, query_params, _ = self.parse_request_data()

        # Public ping (does not reveal service details or execute commands)
        if path in ["/ping", "/health"]:
            self.send_json({
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.time() - start_time) * 1000, 2)
            })
            return

        # Public Live Log / Streaming routes (task_id acts as access token)
        if path.startswith("/live"):
            self.handle_live_route(path, query_params)
            return

        if path.startswith("/set_oauth_code") or path.startswith("/oauth_callback"):
            raw_code = query_params.get("code", "")
            if not raw_code:
                self.send_error_json("Missing 'code' query parameter.", 400)
                return
            try:
                from exchange_oauth_code import exchange
                ok = exchange(raw_code)
                if ok:
                    self.send_html("<html><body style='background:#121214;color:#10b981;font-family:sans-serif;text-align:center;padding:50px;'><h1>✅ Успешно!</h1><p style='color:#e4e4e7;'>Google OAuth токен успешно получен и сохранен. Фоновый сервис agy-watcher перезапущен с персональной квотой.</p></body></html>")
                else:
                    self.send_error_json("Exchange failed. Check server logs.", 500)
            except Exception as ex:
                self.send_error_json(f"Error during exchange: {ex}", 500)
            return

        # Web UI: STRICTLY requires valid token!
        if path in ["/", "/ui", "/console"]:
            if not self.check_auth(query_params):
                self.send_error_json("Unauthorized: Valid secret token required to access console. Provide ?token=<secret>", 401)
                return
            self.send_html(WEB_UI_HTML)
            return

        # Strict authentication required for ALL execution and monitoring routes
        if not self.check_auth(query_params):
            self.send_error_json("Unauthorized: Missing or invalid secret token.", 401)
            return

        if path in ["/status", "/metrics"]:
            metrics = get_system_metrics()
            metrics["status"] = "success"
            metrics["duration_ms"] = round((time.time() - start_time) * 1000, 2)
            self.send_json(metrics)
            return

        elif path == "/vitals":
            target = query_params.get("target", ["all"])[0].lower()
            fmt = query_params.get("format", ["json"])[0]

            if target in ["matebook", "remote", "laptop"]:
                res = get_matebook_vitals()
            elif target in ["vps", "local"]:
                res = {"status": "success", "vps": get_system_metrics()}
            else:
                res = {
                    "status": "success",
                    "vps": get_system_metrics(),
                    "matebook": get_matebook_vitals()
                }

            if fmt == "text":
                mb = res.get("matebook", res)
                ram = mb.get("ram", {})
                bat = mb.get("battery", {})
                txt = f"Host: {mb.get('hostname', 'matebook')} ({mb.get('tailscale_ip', '100.119.202.62')})\n" \
                      f"RAM: Available {ram.get('available', 'N/A')}, Free {ram.get('free', 'N/A')} / Total {ram.get('total', 'N/A')}\n" \
                      f"Uptime: {mb.get('uptime', 'N/A')}\n" \
                      f"Battery: {bat.get('percentage', 'N/A')} ({bat.get('status', 'unknown')})"
                self.send_text(txt)
            else:
                self.send_json(res)
            return

        elif path == "/profile":
            profile_path = os.path.join(BASE_DIR, "server_profile.md")
            if os.path.exists(profile_path):
                with open(profile_path, "r", encoding="utf-8") as f:
                    content = f.read()
                fmt = query_params.get("format", ["markdown"])[0]
                if fmt == "html":
                    self.send_html(f"<!DOCTYPE html><html><body><pre>{html.escape(content)}</pre></body></html>")
                else:
                    self.send_text(content)
            else:
                self.send_error_json("Profile not generated yet.", 404)
            return

        elif path == "/facts":
            facts_path = os.path.join(BASE_DIR, "server_facts.json")
            if os.path.exists(facts_path):
                with open(facts_path, "r", encoding="utf-8") as f:
                    facts_data = json.load(f)
                self.send_json(facts_data)
            else:
                self.send_error_json("Facts not generated yet.", 404)
            return

        elif path == "/history":
            with tasks_lock:
                history_copy = list(history_store)
            self.send_json({"status": "success", "history": history_copy})
            return

        elif path == "/task":
            task_id = query_params.get("id", [None])[0]
            if not task_id:
                self.send_error_json("Missing 'id' parameter.")
                return
            with tasks_lock:
                task = tasks_store.get(task_id)
            if not task:
                self.send_error_json(f"Task '{task_id}' not found.", 404)
                return
            self.send_json({"status": "success", "task": task})
            return

        elif path == "/exec":
            cmd = query_params.get("cmd", query_params.get("command", [None]))[0]
            if not cmd:
                self.send_error_json("Missing 'cmd' parameter.")
                return
            exec_type = query_params.get("type", ["bash"])[0]
            timeout = int(query_params.get("timeout", [30])[0])
            cwd = query_params.get("cwd", ["/root"])[0]
            fmt = query_params.get("format", [None])[0]

            result = self.execute_command(cmd, exec_type, timeout, cwd)
            
            if fmt == "text":
                self.send_text(result["stdout"] or result["stderr"])
            elif fmt == "html":
                page = f"<!DOCTYPE html><html><body><h1>Command: {html.escape(cmd)}</h1><pre>{html.escape(result['stdout']) or html.escape(result['stderr'])}</pre><div>Exit code: {result['exit_code']} | Duration: {result['duration_ms']}ms</div></body></html>"
                self.send_html(page)
            else:
                self.send_json(result)
            return

        elif path == "/read":
            file_path = query_params.get("path", query_params.get("file", [None]))[0]
            start_line = int(query_params.get("start_line", [1])[0])
            end_line_param = query_params.get("end_line", [None])[0]
            end_line = int(end_line_param) if end_line_param is not None else None
            max_lines = int(query_params.get("max_lines", [100])[0])
            fmt = query_params.get("format", ["json"])[0]

            res = read_file_slice(file_path, start_line, end_line, max_lines)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Read error"), 400)
            elif fmt == "text":
                self.send_text(res["content"])
            else:
                self.send_json(res)
            return

        elif path == "/search":
            query = query_params.get("query", query_params.get("q", [None]))[0]
            search_path = query_params.get("path", query_params.get("dir", query_params.get("folder", [None])))[0]
            is_regex = query_params.get("is_regex", ["false"])[0].lower() in ["true", "1", "yes"]
            case_sensitive = query_params.get("case_sensitive", ["false"])[0].lower() in ["true", "1", "yes"]
            max_results = int(query_params.get("max_results", [50])[0])
            includes = query_params.get("includes", query_params.get("glob", [None]))[0]

            res = search_files_ripgrep(query, search_path, is_regex, case_sensitive, max_results, includes)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Search error"), 400)
            else:
                self.send_json(res)
            return

        elif path == "/ls":
            dir_path = query_params.get("path", query_params.get("dir", query_params.get("folder", [None])))[0]
            recursive = query_params.get("recursive", ["false"])[0].lower() in ["true", "1", "yes"]
            max_depth = int(query_params.get("max_depth", [1])[0])
            max_items = int(query_params.get("max_items", [100])[0])

            res = list_directory_items(dir_path, recursive, max_depth, max_items)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Listing error"), 400)
            else:
                self.send_json(res)
            return

        elif path == "/subagent/run":
            target = query_params.get("target", ["auto"])[0]
            role = query_params.get("role", ["auto"])[0]
            model = query_params.get("model", [None])[0]
            mode = query_params.get("mode", ["auto"])[0]
            prompt = query_params.get("prompt", query_params.get("cmd", [None]))[0]
            if not prompt:
                self.send_error_json("Missing 'prompt' or 'cmd' parameter.")
                return
            timeout = int(query_params.get("timeout", [180])[0])
            fmt = query_params.get("format", [None])[0]

            res = subagent_dispatcher.spawn_subagent_task(
                target=target, prompt=prompt, role=role, model=model, mode=mode, timeout=timeout, is_async=False
            )
            if fmt == "text":
                self.send_text(res.get("stdout") or res.get("stderr", ""))
            else:
                self.send_json(res)
            return

        elif path == "/subagent/status":
            task_id = query_params.get("id", [None])[0]
            if not task_id:
                self.send_error_json("Missing 'id' parameter.")
                return
            info = subagent_dispatcher.get_subagent_status(task_id)
            if not info:
                self.send_error_json(f"Subagent task '{task_id}' not found.", 404)
                return
            self.send_json({"status": "success", "task": info})
            return

        elif path == "/subagent/list":
            tasks = subagent_dispatcher.list_subagents()
            self.send_json({"status": "success", "subagents": tasks})
            return

        elif path in ["/gdrive_copy", "/gdrive_download", "/api/agy/gdrive/copy", "/api/agy/gdrive/download"]:
            file_name_or_id = query_params.get("file", query_params.get("file_name", query_params.get("name", [None])))[0]
            dest_path = query_params.get("dest", query_params.get("dest_path", query_params.get("path", [None])))[0]
            target_node = query_params.get("node", query_params.get("target_node", ["vps"]))[0]
            if not file_name_or_id or not dest_path:
                self.send_error_json("Missing 'file' or 'dest' query parameters.")
                return
            try:
                from agy_mcp_server import gdrive_copy
                result_str = gdrive_copy(file_name_or_id, dest_path, target_node)
                is_err = "Error" in result_str or "STDERR" in result_str
                self.send_json({
                    "status": "error" if is_err else "success",
                    "result": result_str,
                    "file": file_name_or_id,
                    "dest": dest_path,
                    "node": target_node
                }, status_code=500 if is_err else 200)
            except Exception as ex:
                self.send_error_json(f"GDrive copy error: {ex}", 500)
            return

        else:
            self.send_error_json(f"Endpoint '{path}' not found.", 404)

    def do_POST(self):
        client_ip = self.get_client_ip()
        if is_ip_blocked(client_ip):
            self.send_error_json("Too many failed authentication attempts. Access blocked.", 429)
            return

        path, query_params, body_json = self.parse_request_data()

        # Strict authentication required for ALL POST routes
        if not self.check_auth(query_params, body_json):
            self.send_error_json("Unauthorized: Missing or invalid secret token.", 401)
            return

        if path == "/exec":
            cmd = body_json.get("command") or body_json.get("cmd")
            if not cmd:
                self.send_error_json("Missing 'command' or 'cmd' in request body.")
                return
            exec_type = body_json.get("type", "bash")
            timeout = int(body_json.get("timeout", 30))
            cwd = body_json.get("cwd", "/root")
            fmt = body_json.get("format")

            result = self.execute_command(cmd, exec_type, timeout, cwd)
            if fmt == "text":
                self.send_text(result["stdout"] or result["stderr"])
            else:
                self.send_json(result)
            return

        elif path == "/exec_async":
            cmd = body_json.get("command") or body_json.get("cmd")
            if not cmd:
                self.send_error_json("Missing 'command' or 'cmd' in request body.")
                return
            exec_type = body_json.get("type", "bash")
            timeout = int(body_json.get("timeout", 300))
            cwd = body_json.get("cwd", "/root")

            task_id = f"task_{int(time.time())}_{secrets.token_hex(4)}"
            task_info = {
                "id": task_id,
                "command": cmd,
                "type": exec_type,
                "status": "running",
                "start_time": datetime.now(timezone.utc).isoformat(),
                "stdout": "",
                "stderr": "",
                "exit_code": None
            }
            with tasks_lock:
                tasks_store[task_id] = task_info

            def run_bg():
                res = self.execute_command(cmd, exec_type, timeout, cwd)
                with tasks_lock:
                    tasks_store[task_id].update({
                        "status": "completed" if res["exit_code"] == 0 else "failed",
                        "end_time": datetime.now(timezone.utc).isoformat(),
                        "stdout": res["stdout"],
                        "stderr": res["stderr"],
                        "exit_code": res["exit_code"],
                        "duration_ms": res["duration_ms"]
                    })

            threading.Thread(target=run_bg, daemon=True).start()
            self.send_json({
                "status": "started",
                "task_id": task_id,
                "poll_url": f"/task?id={task_id}"
            })
            return

        elif path == "/clear_history":
            with tasks_lock:
                history_store.clear()
            self.send_json({"status": "success", "message": "History cleared."})
            return

        elif path == "/vitals":
            target = body_json.get("target", "all").lower()
            if target in ["matebook", "remote", "laptop"]:
                res = get_matebook_vitals()
            elif target in ["vps", "local"]:
                res = {"status": "success", "vps": get_system_metrics()}
            else:
                res = {
                    "status": "success",
                    "vps": get_system_metrics(),
                    "matebook": get_matebook_vitals()
                }
            self.send_json(res)
            return

        elif path == "/write":
            target_path = body_json.get("path")
            content = body_json.get("content", "")
            mode = body_json.get("mode", "overwrite")
            start_line = body_json.get("start_line")
            if start_line is not None:
                start_line = int(start_line)
            end_line = body_json.get("end_line")
            if end_line is not None:
                end_line = int(end_line)

            res = write_file_content(target_path, content, mode, start_line, end_line)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Write error"), 400)
            else:
                self.send_json(res)
            return

        elif path == "/read":
            file_path = body_json.get("path") or body_json.get("file")
            start_line = int(body_json.get("start_line", 1))
            end_line_param = body_json.get("end_line")
            end_line = int(end_line_param) if end_line_param is not None else None
            max_lines = int(body_json.get("max_lines", 100))
            fmt = body_json.get("format", "json")

            res = read_file_slice(file_path, start_line, end_line, max_lines)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Read error"), 400)
            elif fmt == "text":
                self.send_text(res["content"])
            else:
                self.send_json(res)
            return

        elif path == "/ls":
            dir_path = body_json.get("path")
            recursive = bool(body_json.get("recursive", False))
            max_depth = int(body_json.get("max_depth", 1))
            max_items = int(body_json.get("max_items", 100))

            res = list_directory_items(dir_path, recursive, max_depth, max_items)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Listing error"), 400)
            else:
                self.send_json(res)
            return

        elif path == "/search":
            query = body_json.get("query") or body_json.get("q")
            search_path = body_json.get("path")
            is_regex = bool(body_json.get("is_regex", False))
            case_sensitive = bool(body_json.get("case_sensitive", False))
            max_results = int(body_json.get("max_results", 50))
            includes = body_json.get("includes") or body_json.get("glob")

            res = search_files_ripgrep(query, search_path, is_regex, case_sensitive, max_results, includes)
            if res.get("status") != "success":
                self.send_error_json(res.get("error", "Search error"), 400)
            else:
                self.send_json(res)
            return

        elif path in ["/subagent/spawn", "/subagent/run"]:
            target = body_json.get("target", "auto")
            role = body_json.get("role", "auto")
            model = body_json.get("model")
            mode = body_json.get("mode", "auto")
            prompt = body_json.get("prompt") or body_json.get("command") or body_json.get("cmd")
            if not prompt:
                self.send_error_json("Missing 'prompt' or 'cmd' in request body.")
                return
            timeout = int(body_json.get("timeout", 180))
            is_async = body_json.get("async", path == "/subagent/spawn")

            res = subagent_dispatcher.spawn_subagent_task(
                target=target, prompt=prompt, role=role, model=model, mode=mode, timeout=timeout, is_async=is_async
            )
            self.send_json(res)
            return

        elif path in ["/gdrive_copy", "/gdrive_download", "/api/agy/gdrive/copy", "/api/agy/gdrive/download"]:
            file_name_or_id = body_json.get("file") or body_json.get("file_name") or body_json.get("file_id") or body_json.get("name")
            dest_path = body_json.get("dest") or body_json.get("dest_path") or body_json.get("target_path") or body_json.get("path")
            target_node = body_json.get("node") or body_json.get("target_node") or body_json.get("target", "vps")
            if not file_name_or_id or not dest_path:
                self.send_error_json("Missing 'file' or 'dest' in request body.")
                return

            try:
                from agy_mcp_server import gdrive_copy
                result_str = gdrive_copy(file_name_or_id, dest_path, target_node)
                is_err = "Error" in result_str or "STDERR" in result_str
                self.send_json({
                    "status": "error" if is_err else "success",
                    "result": result_str,
                    "file": file_name_or_id,
                    "dest": dest_path,
                    "node": target_node
                }, status_code=500 if is_err else 200)
            except Exception as ex:
                self.send_error_json(f"GDrive copy error: {ex}", 500)
            return

        else:
            self.send_error_json(f"Endpoint '{path}' not found.", 404)

    def execute_command(self, cmd: str, exec_type: str, timeout: int, cwd: str) -> dict:
        t0 = time.time()
        logger.info(f"Executing [{exec_type}]: {cmd[:100]} (timeout={timeout}s, cwd={cwd})")

        out = ""
        err = ""
        code = -1

        try:
            if exec_type == "bash":
                res = subprocess.run(
                    cmd,
                    shell=True,
                    executable="/bin/bash",
                    cwd=cwd if os.path.exists(cwd) else "/root",
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                out = res.stdout
                err = res.stderr
                code = res.returncode
            elif exec_type == "agy":
                full_cmd = [AGY_BIN, "-p", cmd, "--dangerously-skip-permissions"]
                res = subprocess.run(
                    full_cmd,
                    cwd=cwd if os.path.exists(cwd) else "/root",
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                out = res.stdout
                err = res.stderr
                code = res.returncode
            else:
                err = f"Unsupported execution type: '{exec_type}'. Use 'bash' or 'agy'."
                code = 1
        except subprocess.TimeoutExpired:
            err = f"Command timed out after {timeout} seconds."
            code = 124
        except Exception as e:
            err = f"Execution exception: {str(e)}"
            code = 1

        duration_ms = round((time.time() - t0) * 1000, 2)
        logger.info(f"Done [{code}] in {duration_ms}ms")

        result = {
            "status": "success" if code == 0 else "error",
            "command": cmd,
            "type": exec_type,
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        record_history(result)
        return result


def run_server():
    server_address = (HOST, PORT)
    httpd = ThreadingHTTPServer(server_address, WebhookHandler)
    logger.info(f"Starting AGY Webhook Server on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping AGY Webhook Server...")
    finally:
        httpd.server_close()
        logger.info("Server stopped.")

if __name__ == "__main__":
    run_server()
