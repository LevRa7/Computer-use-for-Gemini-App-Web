"""core/agent.py - Antigravity Mesh tunnel node (thin transport).

All tool logic lives in :mod:`core.mcp_tools`; this module only maintains the
websocket tunnel to the gateway and forwards ``tools/call`` requests.  The
response shape is unchanged (``stdout``/``stderr``/``exit_code`` and friends),
so an unmodified gateway keeps working.

Configuration (read once at import/startup)::

    MESH_GATEWAY      shared gateway domain
    MESH_USER         node name
    MESH_TOKEN        node token
    MESH_CONFIG_FILE  KEY=VALUE file used for any of the three above that is not
                      set in the environment (default:
                      ~/.config/antigravity-mesh/agent.env).  This keeps the token
                      out of launchers such as the Windows Startup script.  Any
                      other MESH_* key in that file is exported as well.
    MESH_SHELL        Windows command shell: "cmd" or "git-bash" (default:
                      PowerShell, falling back to cmd.exe when PowerShell is absent)
    MESH_WORKSPACE    base directory for relative paths (default: cwd)
    MESH_READ_ONLY    "1"/"true" -> mutating tools refuse to run
    MESH_WRITE_ROOTS  pathsep-separated list of roots allowed for writes

Resilience contract:

* every link failure (DNS, TLS, reset, half-open socket after sleep) is retried
  forever with capped exponential backoff plus jitter;
* tool calls run concurrently, so a slow command never delays another call or
  the keepalive pings;
* only one agent per node name runs on a machine.  The gateway keeps exactly one
  tunnel per node and a new tunnel supersedes the old one, so two agents would
  knock each other off in an endless loop and fail every in-flight call.  A
  second instance therefore waits for the lock instead of connecting.
"""

import asyncio
import json
import logging
import os
import random
import sys
import time

import websockets

from core import mcp_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("agy-agent")

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "antigravity-mesh")
DEFAULT_CONFIG_FILE = os.path.join(CONFIG_DIR, "agent.env")

#: Backoff cap for ordinary link failures.
MAX_BACKOFF = 15.0
#: Wait after the gateway rejects the token: retrying fast cannot help, but the
#: node must recover by itself once it is re-registered.
UNAUTHORIZED_BACKOFF = 60.0


def read_env_file(path: str) -> dict:
    """Parse a KEY=VALUE file; tolerates a UTF-8 BOM (Windows PowerShell 5 writes one)."""
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _load_settings() -> dict:
    file_values = read_env_file(os.environ.get("MESH_CONFIG_FILE") or DEFAULT_CONFIG_FILE)

    def pick(key: str, default: str) -> str:
        return os.environ.get(key) or file_values.get(key) or default

    # Every other MESH_* key the operator put in the same file is exported too, so
    # one agent.env (or one Windows Startup launcher) configures the whole node:
    # MESH_SHELL for the Windows command shell, MESH_WORKSPACE, MESH_READ_ONLY,
    # MESH_WRITE_ROOTS, MESH_JOBS_DIR, MESH_MAX_OUTPUT_CHARS. The real environment
    # always wins over the file.
    for key, value in file_values.items():
        if key.startswith("MESH_") and not os.environ.get(key):
            os.environ[key] = value

    return {
        "gateway": pick("MESH_GATEWAY", "smart-server.online"),
        "user": pick("MESH_USER", "levra7"),
        "token": pick("MESH_TOKEN", ""),
    }


_SETTINGS = _load_settings()
GATEWAY_HOST = _SETTINGS["gateway"]
USER = _SETTINGS["user"]
TOKEN = _SETTINGS["token"]


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


# ---------------------------------------------------------------------------
# Single instance per node name
# ---------------------------------------------------------------------------

class InstanceLock:
    """Non-blocking, OS-released file lock (fcntl on POSIX, msvcrt on Windows).

    The OS drops the lock when the process dies, so a crash never leaves a stale
    lock behind.
    """

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def try_acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True


def wait_for_instance_lock(user: str) -> InstanceLock:
    """Block until this process is the only agent for ``user`` on the machine."""
    safe = "".join(c for c in user if c.isalnum() or c in "-_") or "node"
    lock = InstanceLock(os.path.join(CONFIG_DIR, "agent-%s.lock" % safe))
    warned = False
    while not lock.try_acquire():
        if not warned:
            logger.warning("Another agent for node '%s' is already running on this machine; "
                           "waiting for it to exit instead of fighting over the tunnel.", user)
            warned = True
        time.sleep(10)
    return lock


# ---------------------------------------------------------------------------
# Tunnel
# ---------------------------------------------------------------------------

async def handle_tool_call(name: str, args: dict) -> dict:
    """Forward one tool call to the shared implementation in a worker thread."""
    logger.info("Executing %s", name)
    return await asyncio.to_thread(mcp_tools.call_tool, name, args or {})


async def _serve_call(ws, req_id, params: dict) -> None:
    tool_name = params.get("name")
    tool_args = params.get("arguments", {})
    try:
        res = await handle_tool_call(tool_name, tool_args)
    except Exception as tool_exc:
        # A tool must never take the tunnel down with it.
        logger.warning(f"Tool {tool_name} failed: {tool_exc}")
        res = {"error": f"{type(tool_exc).__name__}: {tool_exc}"}
    try:
        await ws.send(json.dumps({"id": req_id, "result": res}, ensure_ascii=False))
    except Exception as send_exc:
        # The link dropped while the tool ran; the gateway already failed the
        # call, and the reconnect loop restores the tunnel.
        logger.warning(f"Could not deliver result of {tool_name}: {send_exc}")


def _is_unauthorized(exc: BaseException) -> bool:
    """True when the gateway rejected the node's credentials.

    The gateway closes with 4001 before accepting the websocket; Starlette turns
    that into an HTTP 403 handshake response, so depending on timing the client
    sees either the close code or the HTTP status.  Handles websockets' legacy
    and new exception shapes.
    """
    for attr in ("rcvd", "received"):
        frame = getattr(exc, attr, None)
        if frame is not None and getattr(frame, "code", None) == 4001:
            return True
    if getattr(exc, "code", None) == 4001:
        return True
    status = getattr(exc, "status_code", None)                       # legacy InvalidStatusCode
    response = getattr(exc, "response", None)                        # websockets >= 14 InvalidStatus
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status in (401, 403)


def tunnel_uri() -> str:
    """Canonical tunnel URL: one shared domain, node selected by ?user=."""
    base = GATEWAY_HOST.rstrip("/")
    if not base.startswith(("ws://", "wss://")):
        base = "wss://" + base
    return f"{base}/ws/tunnel?user={USER}&token={TOKEN}"


async def run_agent():
    """Stay connected to the gateway, healing every kind of link failure.

    A dropped websocket is retried with exponential backoff plus jitter: a short
    first retry restores a flapping link quickly, while the growing cap stops a
    hammering loop when the network is genuinely down. The backoff resets after a
    healthy connection, so a long-lived session is not penalised.
    """
    if not TOKEN:
        logger.error("MESH_TOKEN is empty (env and %s): the gateway will reject this node. "
                     "Re-run the installer.", os.environ.get("MESH_CONFIG_FILE") or DEFAULT_CONFIG_FILE)
    uri = tunnel_uri()
    logger.info(f"Connecting to Gateway {uri.split('&token=')[0]} ...")
    backoff = 1.0
    in_flight = set()
    while True:
        try:
            async with websockets.connect(uri, ping_interval=10, ping_timeout=10,
                                            close_timeout=5, open_timeout=15,
                                            max_size=None) as ws:
                logger.info(f"Connected to Mesh Gateway as '{USER}'!")
                backoff = 1.0                      # healthy again
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                    except Exception:
                        continue
                    if data.get("method") == "tools/call":
                        # Each call runs on its own: Gemini issues parallel calls,
                        # and serialising them pushed later ones past the
                        # gateway's 28 s limit.
                        task = asyncio.create_task(_serve_call(ws, data.get("id"), data.get("params", {})))
                        in_flight.add(task)
                        task.add_done_callback(in_flight.discard)
            # A clean close (e.g. gateway restart) still needs a reconnect.
            raise ConnectionError("tunnel closed by gateway")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_unauthorized(e):
                delay = UNAUTHORIZED_BACKOFF
                logger.error("Gateway rejected node '%s' (4001 Unauthorized): the token is wrong or the "
                             "node was removed. Re-run the installer. Retrying in %.0fs.", USER, delay)
            else:
                delay = min(backoff, MAX_BACKOFF) + random.uniform(0, 0.5)
                logger.warning(f"Connection lost: {e}. Reconnecting in {delay:.1f}s...")
                backoff = min(backoff * 2, MAX_BACKOFF)
            await asyncio.sleep(delay)


def main() -> None:
    _lock = wait_for_instance_lock(USER)  # held for the life of the process
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
