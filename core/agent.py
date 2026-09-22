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

async def handle_tool_call(name: str, args: dict) -> dict:
    if name == "system_vitals":
        return await asyncio.to_thread(get_host_vitals)
    elif name == "bash_exec":
        cmd = args.get("command", "")
        logger.info(f"Executing bash_exec: {cmd[:120]}...")
        # Run in thread pool so WebSocket heartbeats (ping/pong) remain active
        return await asyncio.to_thread(run_bash_sync, cmd, CMD_TIMEOUT_SEC)
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
