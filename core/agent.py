"""core/agent.py - Antigravity Mesh tunnel node (thin transport).

All tool logic lives in :mod:`core.mcp_tools`; this module only maintains the
websocket tunnel to the gateway and forwards ``tools/call`` requests.  The
response shape is unchanged (``stdout``/``stderr``/``exit_code`` and friends),
so an unmodified gateway keeps working.

Configuration (read once at import/startup)::

    MESH_WORKSPACE    base directory for relative paths (default: cwd)
    MESH_READ_ONLY    "1"/"true" -> mutating tools refuse to run
    MESH_WRITE_ROOTS  pathsep-separated list of roots allowed for writes
"""

import asyncio
import json
import logging
import os

import websockets

from core import mcp_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("agy-agent")

GATEWAY_HOST = os.environ.get("MESH_GATEWAY", "smart-server.online")
USER = os.environ.get("MESH_USER", "levra7")
TOKEN = os.environ.get("MESH_TOKEN", "")


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def configure_from_env() -> None:
    """Apply MESH_* environment configuration to the shared tool module."""
    write_roots = os.environ.get("MESH_WRITE_ROOTS") or None
    jobs_dir = os.environ.get("MESH_JOBS_DIR") or None
    max_chars = os.environ.get("MESH_MAX_OUTPUT_CHARS")
    mcp_tools.configure(
        workspace=os.environ.get("MESH_WORKSPACE") or os.getcwd(),
        read_only=_env_flag("MESH_READ_ONLY"),
        write_roots=write_roots,
        jobs_dir=jobs_dir,
        max_output_chars=max_chars,
    )


# Configure as soon as the node module is imported so external callers (tests,
# harnesses) observe the environment-driven settings.
configure_from_env()


async def handle_tool_call(name: str, args: dict) -> dict:
    """Forward one tool call to the shared implementation in a worker thread."""
    logger.info("Executing %s", name)
    return await asyncio.to_thread(mcp_tools.call_tool, name, args or {})


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
