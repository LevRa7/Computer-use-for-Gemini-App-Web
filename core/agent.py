import asyncio
import json
import logging
import os
import subprocess
import websockets
from core.vitals import get_host_vitals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("agy-agent")

GATEWAY_HOST = os.environ.get("MESH_GATEWAY", "smart-server.online")
USER = os.environ.get("MESH_USER", "levra7")
TOKEN = os.environ.get("MESH_TOKEN", "")

async def handle_tool_call(name: str, args: dict) -> dict:
    if name == "system_vitals":
        return get_host_vitals()
    elif name == "bash_exec":
        cmd = args.get("command", "")
        logger.info(f"Executing bash_exec: {cmd}")
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except subprocess.TimeoutExpired:
            return {"exit_code": 124, "stdout": "", "stderr": "Command timed out after 60 seconds"}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}
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
                        await ws.send(json.dumps(resp))
        except Exception as e:
            logger.warning(f"Connection lost: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_agent())
