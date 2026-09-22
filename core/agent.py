import asyncio
import json
import logging
import os
import re
import subprocess
import websockets
from core.vitals import get_host_vitals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("agy-agent")

GATEWAY_HOST = os.environ.get("MESH_GATEWAY", "smart-server.online")
USER = os.environ.get("MESH_USER", "levra7")
TOKEN = os.environ.get("MESH_TOKEN", "")
DEFAULT_WORKSPACE = os.environ.get("MESH_WORKSPACE", os.getcwd())

# 12,000 characters (~3000 tokens) maximum output per turn to keep Gemini chat context lightweight
# and completely prevent Gemini Error 1076 (context/payload limit exhaustion)
MAX_OUTPUT_CHARS = 12000
CMD_TIMEOUT_SEC = 25

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def sanitize_output(text: str) -> str:
    """Strips ANSI escape codes (terminal colors) and non-printable control characters."""
    if not text:
        return ""
    text = ANSI_ESCAPE_RE.sub('', text)
    # Filter non-printable ASCII/Unicode control characters, keeping valid newlines, tabs, and carriage returns
    return "".join(ch for ch in text if ch in ('\n', '\r', '\t') or (ord(ch) >= 32 and ord(ch) != 127))

def truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Intelligently truncates output keeping both head and tail with a informative notice."""
    if len(text) <= max_chars:
        return text
    head_size = int(max_chars * 0.7)   # 8400 chars
    tail_size = int(max_chars * 0.25)  # 3000 chars
    omitted = len(text) - (head_size + tail_size)
    omitted_lines = text[head_size:-tail_size].count('\n')
    notice = f"\n\n... [Output truncated: {omitted} characters / ~{omitted_lines} lines hidden to prevent Gemini context overflow. Use head, tail, grep, or write to file for full data] ...\n\n"
    return text[:head_size] + notice + text[-tail_size:]

def run_bash_sync(cmd: str, timeout: int = CMD_TIMEOUT_SEC) -> dict:
    """Executes bash command synchronously in thread pool, keeping the main asyncio loop unblocked."""
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd],
            cwd=DEFAULT_WORKSPACE if os.path.exists(DEFAULT_WORKSPACE) else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace"
        )
        clean_stdout = truncate_output(sanitize_output(proc.stdout))
        clean_stderr = truncate_output(sanitize_output(proc.stderr))
        return {
            "exit_code": proc.returncode,
            "stdout": clean_stdout,
            "stderr": clean_stderr
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds. Tip: For long background tasks, run with 'nohup ... > output.log 2>&1 &'."
        }
    except Exception as e:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": str(e)
        }

def list_dir_sync(path: str = "") -> dict:
    """Lists files and directories at path, defaulting to DEFAULT_WORKSPACE."""
    try:
        target = path.strip() if path else DEFAULT_WORKSPACE
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(DEFAULT_WORKSPACE, target))
        if not os.path.exists(target):
            return {"exit_code": 1, "stdout": "", "stderr": f"Path not found: {target}"}
        if os.path.isfile(target):
            return read_file_sync(target)

        entries = []
        for e in sorted(os.scandir(target), key=lambda x: (not x.is_dir(), x.name.lower())):
            suffix = "/" if e.is_dir() else ""
            size = e.stat().st_size if e.is_file() else 0
            entries.append(f"{e.name}{suffix}" + (f" ({size} bytes)" if e.is_file() else ""))

        listing = "\n".join(entries) if entries else "(empty directory)"
        return {
            "exit_code": 0,
            "stdout": f"Directory: {target} ({len(entries)} items):\n{listing}",
            "stderr": ""
        }
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

def read_file_sync(path: str, start_line: int = 1, end_line: int = None) -> dict:
    """Reads lines from a file on disk."""
    try:
        target = path.strip()
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(DEFAULT_WORKSPACE, target))
        if not os.path.exists(target):
            return {"exit_code": 1, "stdout": "", "stderr": f"File not found: {target}"}
        if os.path.isdir(target):
            return list_dir_sync(target)

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        s = max(1, start_line or 1) - 1
        e = min(len(lines), end_line) if end_line else len(lines)
        selected = lines[s:e]
        content = "".join(f"{idx+s+1:4d} | {line}" for idx, line in enumerate(selected))
        header = f"File: {target} (lines {s+1}-{e} of {len(lines)} total)\n" + ("-" * 60) + "\n"
        full = header + content
        clean = truncate_output(sanitize_output(full))
        return {
            "exit_code": 0,
            "stdout": clean,
            "stderr": ""
        }
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

async def handle_tool_call(name: str, args: dict) -> dict:
    if name == "system_vitals":
        return await asyncio.to_thread(get_host_vitals)
    elif name == "bash_exec":
        cmd = args.get("command", "")
        logger.info(f"Executing bash_exec: {cmd[:120]}...")
        return await asyncio.to_thread(run_bash_sync, cmd, CMD_TIMEOUT_SEC)
    elif name == "list_dir":
        path = args.get("path", "")
        logger.info(f"Executing list_dir: {path}")
        return await asyncio.to_thread(list_dir_sync, path)
    elif name == "read_file":
        path = args.get("path", "")
        start_line = args.get("start_line", 1)
        end_line = args.get("end_line")
        logger.info(f"Executing read_file: {path}")
        return await asyncio.to_thread(read_file_sync, path, start_line, end_line)
    return {"error": f"Unknown tool {name}"}

async def run_agent():
    uri = f"wss://{GATEWAY_HOST}/ws/tunnel?user={USER}&token={TOKEN}"
    logger.info(f"Connecting to Gateway {uri}...")
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                logger.info(f"Connected to Mesh Gateway as '{USER}'!")
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                    except Exception:
                        continue
                    req_id = data.get("id")
                    method = data.get("method")
                    params = data.get("params", {})
                    if method == "tools/call":
                        tool_name = params.get("name")
                        tool_args = params.get("arguments", {})
                        res = await handle_tool_call(tool_name, tool_args)
                        resp = {"id": req_id, "result": res}
                        await ws.send(json.dumps(resp, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Connection lost: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_agent())
