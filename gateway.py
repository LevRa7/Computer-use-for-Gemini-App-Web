import asyncio
from contextlib import asynccontextmanager
import datetime
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import uuid
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gateway")

REGISTRY_PATH = "/etc/antigravity-mesh/registry.json"
active_tunnels = {}  # user -> {"ws": WebSocket, "pending": {req_id: Future}}
active_sse_subscribers = {}  # user -> set of asyncio.Queue
active_sse_sessions = {}  # session_id -> asyncio.Queue

def broadcast_sse(user: str, message: dict):
    if user in active_sse_subscribers:
        data = json.dumps(message, ensure_ascii=False)
        for q in list(active_sse_subscribers[user]):
            try:
                q.put_nowait(data)
            except Exception:
                pass

RESERVED_NAMES = {
    "admin", "administrator", "root", "api", "api-ag", "api-agy",
    "drive", "derp", "mcp-tg", "chamber", "proxy-api", "tg-bot-api",
    "anonymous", "gateway", "mesh", "mail", "vpn", "test", "null",
    "undefined", "localhost", "local", "internal", "public", "ws",
    "sse", "mcp", "messages", "health", "health-mesh", "core", "skills",
    "install", "static", "assets"
}

def load_registry() -> dict:
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read registry: {e}")
    return {}

def save_registry(data: dict):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(data, f, indent=2)

def tokens_equal(a: str, b: str) -> bool:
    """Constant-time token comparison that never raises on non-ASCII input.

    ``hmac.compare_digest`` rejects str arguments containing non-ASCII
    characters (TypeError -> HTTP 500). Comparing UTF-8 bytes with
    ``surrogateescape`` keeps the comparison constant-time and total.
    """
    try:
        return hmac.compare_digest(
            str(a).encode("utf-8", "surrogateescape"),
            str(b).encode("utf-8", "surrogateescape"),
        )
    except Exception:
        return False


def get_target_user(request: Request) -> str:
    host = request.headers.get("host", "")
    host_part = host.split(":")[0].lower()
    base = "smart-server.online"
    if host_part.endswith(f".{base}") and host_part != base:
        sub = host_part[:-len(base)-1]
        parts = sub.split(".")
        return parts[-1].lower()
    # Fallback to query param if directly accessing the root domain
    user = request.query_params.get("user")
    if user:
        return user.lower()
    return "anonymous"

def get_request_token(request: Request) -> str:
    token = request.query_params.get("token") or request.headers.get("x-mesh-token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    return token or ""

def verify_token(request: Request) -> tuple[bool, str, str]:
    user = get_target_user(request)
    if user == "anonymous":
        return False, "Host subdomain is missing user identity.", ""
    registry = load_registry()
    if user not in registry:
        return False, f"User '{user}' is not registered.", user
    expected = registry[user].get("token", "")
    token = get_request_token(request)
    if not token or not tokens_equal(token, expected):
        return False, "Invalid or missing token. Specify ?token=<your_token> in the URL.", user
    return True, "ok", user

def get_skill(user: str, host: str) -> str:
    return f"""# 🌐 Antigravity Mesh — Node Orchestrator ({user})

Target Node: {user}.smart-server.online
Endpoint: https://{user}.smart-server.online/sse
Mode: Secure Cloud Gateway + Reverse Tunnel

MANDATORY GROUNDING & EXECUTION DIRECTIVES:
1. You are connected directly to the user's execution node '{user}' via Antigravity Mesh MCP tools.
2. STRICT ZERO-HALLUCINATION POLICY:
   - You DO NOT know local directory structure, file names, or environment state in advance.
   - NEVER invent file paths, guess folder contents, or claim a directory is empty without checking.
   - You MUST call `list_dir` or `bash_exec` (e.g. `pwd`, `ls -la`) to inspect the real filesystem first.
3. DIRECT EXECUTION:
   - Always execute operations directly via MCP tools (`list_dir`, `read_file`, `bash_exec`, `system_vitals`).
   - Never ask the user to run shell commands manually.
4. Always verify return codes and stdout/stderr before reporting results.
"""

async def call_remote_tool(user: str, name: str, args: dict) -> dict:
    tunnel = active_tunnels.get(user)
    if not tunnel or not tunnel.get("ws"):
        # The agent reconnects within ~2s of a dropped link (VPN flap, network
        # change, service restart), so give it a moment instead of reporting the
        # node as offline immediately. A missing agent is then a rare answer.
        for _ in range(16):                      # up to ~8 seconds
            await asyncio.sleep(0.5)
            tunnel = active_tunnels.get(user)
            if tunnel and tunnel.get("ws"):
                break
    if not tunnel or not tunnel.get("ws"):
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": (f"[Mesh Gateway] Node '{user}' did not come back within 8 seconds - the agent "
                       f"on the host is not connected. Start/restart the mesh agent there, then retry; "
                       f"this is a transport problem, not a missing file or permission.")
        }
    req_id = str(uuid.uuid4())
    fut = asyncio.get_running_loop().create_future()
    tunnel["pending"][req_id] = fut
    payload = {
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }
    try:
        await tunnel["ws"].send_text(json.dumps(payload))
        # 28s timeout: Agent runs commands with 25s timeout, so 28s gives 3s margin and returns cleanly
        # BEFORE Google's 30s frontend timeout, completely preventing Gemini Error 1076
        res_msg = await asyncio.wait_for(fut, timeout=28.0)
        return res_msg.get("result", res_msg)
    except asyncio.TimeoutError:
        tunnel["pending"].pop(req_id, None)
        return {"exit_code": 124, "error": f"Command timed out after 25s on node '{user}'"}
    except Exception as e:
        tunnel["pending"].pop(req_id, None)
        # Self-heal: a dropped tunnel (network flap, agent restart) is transient.
        # Wait for the agent to come back and replay the call once instead of
        # failing the client's request.
        logger.warning(f"Tunnel call to '{user}' failed ({e}); waiting for reconnect and retrying once")
        for _ in range(20):                      # up to ~10 seconds
            await asyncio.sleep(0.5)
            fresh = active_tunnels.get(user)
            if fresh and fresh.get("ws") and fresh.get("ws") is not tunnel.get("ws"):
                try:
                    retry_id = str(uuid.uuid4())
                    retry_fut = asyncio.get_running_loop().create_future()
                    fresh["pending"][retry_id] = retry_fut
                    await fresh["ws"].send_text(json.dumps({"id": retry_id, "method": "tools/call",
                                                            "params": {"name": name, "arguments": args}}))
                    res_msg = await asyncio.wait_for(retry_fut, timeout=20.0)
                    logger.info(f"Retry after reconnect succeeded for '{user}'")
                    return res_msg.get("result", res_msg)
                except Exception as retry_exc:
                    fresh["pending"].pop(retry_id, None)
                    logger.warning(f"Retry after reconnect failed for '{user}': {retry_exc}")
                    break
        return {"exit_code": 1, "error": str(e)}

def remote_tool_error(res):
    """Return a human-readable error string if a remote tool response is a failure, else None.

    Covers three cases:
      * the node returned {"error": "..."} (tool-level failure);
      * the node returned {"ok": false, ...};
      * the gateway itself could not reach the node / the tunnel broke, which yields
        a bare {"exit_code": !=0, "stdout", "stderr"} with no tool payload.
    A legitimate job_output payload always carries a "status" key, so it is never
    mistaken for a transport failure even when the job's exit_code != 0.
    """
    if not isinstance(res, dict):
        return "invalid response from node"
    if res.get("error"):
        return str(res.get("error"))
    if res.get("ok") is False:
        return str(res.get("message") or res.get("stderr") or "operation failed (ok=false)")
    if res.get("exit_code") not in (None, 0) and set(res.keys()) <= {"exit_code", "stdout", "stderr"}:
        return str(res.get("stderr") or res.get("stdout") or f"remote call failed with exit code {res.get('exit_code')}")
    return None


async def health(request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers={"Allow": "GET, HEAD, OPTIONS", "Access-Control-Allow-Origin": "*"})
    user = get_target_user(request)
    is_online = (user in active_tunnels) if user != "anonymous" else False
    return JSONResponse({
        "status": "healthy",
        "service": "antigravity_mesh_gateway",
        "host": "smart-server.online",
        "target_user": user,
        "node_online": is_online,
        "active_tunnels_count": len(active_tunnels)
    }, headers={"Access-Control-Allow-Origin": "*"})

async def api_register(request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers={"Allow": "POST, OPTIONS", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"})
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    raw_user = data.get("username", "").strip()
    clean_user = re.sub(r"[^a-z0-9_-]", "", raw_user.lower())
    if not clean_user or len(clean_user) < 2:
        return JSONResponse({"error": "Invalid username (min 2 alphanumeric chars)"}, status_code=400)

    if clean_user in RESERVED_NAMES:
        return JSONResponse({
            "error": "reserved_username",
            "message": f"Username '{clean_user}' is reserved for system services."
        }, status_code=400)

    raw_mac = data.get("mac_address", "").strip()
    clean_mac = ""
    if raw_mac:
        mac_hex = re.sub(r"[^0-9a-fA-F]", "", raw_mac).lower()
        if len(mac_hex) == 12:
            clean_mac = ":".join(mac_hex[i:i+2] for i in range(0, 12, 2))
        else:
            clean_mac = raw_mac.lower()

    clean_os = str(data.get("os", "")).strip()[:100]
    registry = load_registry()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # --- DEVICE FINGERPRINTING (MAC & OS) ---
    # 1. Check if a device with this MAC address was already registered
    if clean_mac and clean_mac != "00:00:00:00:00:00":
        for u, entry in registry.items():
            entry_mac = entry.get("mac_address", "").lower()
            if entry_mac == clean_mac:
                logger.info(f"Reusing existing subdomain '{u}' for MAC {clean_mac}")
                entry["last_seen"] = now_iso
                if clean_os and not entry.get("os"):
                    entry["os"] = clean_os
                save_registry(registry)
                token = entry["token"]
                return JSONResponse({
                    "status": "success",
                    "username": u,
                    "token": token,
                    "subdomain": f"{u}.smart-server.online",
                    "sse_url": f"https://smart-server.online/sse?user={u}&token={token}",
                    "tunnel_url": f"wss://smart-server.online/ws/tunnel?user={u}&token={token}",
                    "reused": True,
                    "instructions": f"Antigravity Mesh node '{user}'. Direct access to the real remote host over a reverse tunnel. "
                f"Do not guess paths or outputs: use list_dir/glob_find/grep_search/read_file/bash_exec/"
                f"system_vitals. Use run_job for slow commands; page output with max_chars+cursor."
                })

    # 2. If not found by MAC, check if username already exists in registry
    auto_suffix = data.get("auto_suffix", False)
    if clean_user in registry:
        req_token = request.headers.get("authorization", "").replace("Bearer ", "").strip() or data.get("token")
        # If valid token provided, update device info for existing user
        if req_token and tokens_equal(req_token, registry[clean_user]["token"]):
            token = registry[clean_user]["token"]
            if clean_mac:
                registry[clean_user]["mac_address"] = clean_mac
            if clean_os:
                registry[clean_user]["os"] = clean_os
            registry[clean_user]["last_seen"] = now_iso
            save_registry(registry)
            return JSONResponse({
                "status": "success",
                "username": clean_user,
                "token": token,
                "subdomain": f"{clean_user}.smart-server.online",
                "sse_url": f"https://smart-server.online/sse?user={clean_user}&token={token}",
                "tunnel_url": f"wss://smart-server.online/ws/tunnel?user={clean_user}&token={token}",
                "reused": True,
                "instructions": "Authenticated and updated existing user registration."
            })
        elif auto_suffix:
            # Different device claiming same name -> generate unique suffix
            suffix = 2
            cand = f"{clean_user}-{suffix}"
            while cand in registry or cand in RESERVED_NAMES:
                suffix += 1
                cand = f"{clean_user}-{suffix}"
            clean_user = cand
            token = secrets.token_hex(16)
            registry[clean_user] = {
                "token": token,
                "created_at": now_iso,
                "mode": "tunnel",
                "status": "active",
                "mac_address": clean_mac,
                "os": clean_os
            }
            save_registry(registry)
        else:
            suffix = 2
            cand = f"{clean_user}-{suffix}"
            while cand in registry or cand in RESERVED_NAMES:
                suffix += 1
                cand = f"{clean_user}-{suffix}"
            return JSONResponse({
                "error": "conflict",
                "message": f"User '{clean_user}' already exists. Provide token to retrieve config.",
                "suggested_username": cand
            }, status_code=409)
    else:
        # Brand new registration
        token = secrets.token_hex(16)
        registry[clean_user] = {
            "token": token,
            "created_at": now_iso,
            "mode": "tunnel",
            "status": "active",
            "mac_address": clean_mac,
            "os": clean_os
        }
        save_registry(registry)

    return JSONResponse({
        "status": "success",
        "username": clean_user,
        "token": token,
        "subdomain": f"{clean_user}.smart-server.online",
        "sse_url": f"https://smart-server.online/sse?user={clean_user}&token={token}",
        "tunnel_url": f"wss://smart-server.online/ws/tunnel?user={clean_user}&token={token}",
        "reused": False,
        "instructions": "Add the sse_url to Google Gemini Web (Settings -> MCP)."
    })

async def oauth_discovery(request: Request):
    """RFC 9728 protected-resource metadata.

    Returning 404 here made every MCP client log a connection error while
    connecting (Gemini Spark probes this path twice). The mesh authenticates with
    a bearer token in the URL, so the honest answer is metadata that advertises
    no authorization server and no bearer requirement for discovery.
    """
    host = request.headers.get("host", "smart-server.online")
    base = f"https://{host}"
    return JSONResponse(
        {
            "resource": base,
            "authorization_servers": [],
            "bearer_methods_supported": ["query", "header"],
            "scopes_supported": [],
            "resource_documentation": "https://github.com/LevRa7/Computer-use-for-Gemini-App-Web",
            "resource_name": "Antigravity Mesh",
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "public, max-age=300",
        },
    )

async def sse_endpoint(request: Request):
    ok, msg, user = verify_token(request)
    if not ok:
        return JSONResponse(
            {"error": "unauthorized", "message": msg},
            status_code=401,
            headers={"Access-Control-Allow-Origin": "*"}
        )

    if request.method == "HEAD":
        return Response(
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                "mcp-protocol-version": "2024-11-05",
            }
        )

    token = get_request_token(request)
    # The session id MUST match the one returned by POST responses. Generating a
    # random UUID here while messages_endpoint answers with the user name made a
    # client see two different sessions for one connection: it aborted the
    # session (DELETE) and re-initialised in a loop.
    session_id = request.headers.get("mcp-session-id") or user
    queue = asyncio.Queue()
    if user not in active_sse_subscribers:
        active_sse_subscribers[user] = set()
    active_sse_subscribers[user].add(queue)
    active_sse_sessions[session_id] = queue

    async def event_generator():
        try:
            yield f"event: endpoint\ndata: /messages?user={user}&token={token}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if msg is None:
                        break
                    yield f"event: message\ndata: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            active_sse_sessions.pop(session_id, None)
            if user in active_sse_subscribers and queue in active_sse_subscribers[user]:
                active_sse_subscribers[user].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2024-11-05",
        }
    )

async def messages_endpoint(request: Request):
    ok, err_msg, user = verify_token(request)
    if not ok:
        return JSONResponse(
            {"error": "unauthorized", "message": err_msg},
            status_code=401,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                "mcp-protocol-version": "2024-11-05",
            }
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    if req_id is None and method:
        session_id = request.headers.get("mcp-session-id") or user
        return Response(
            status_code=204,
            headers={
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
            }
        )

    client_ip = request.client.host if request.client else "unknown"
    tool_note = ""
    if method == "tools/call":
        # Log which tool the client asked for: without it a retry loop in the
        # client is impossible to diagnose from the gateway side.
        try:
            tool_note = ", tool=%s, args=%s" % (
                params.get("name"),
                json.dumps(params.get("arguments", {}), ensure_ascii=False)[:120],
            )
        except Exception:
            tool_note = ", tool=?"
    logger.info(f"Incoming MCP RPC from {client_ip} [{user}]: method={method}, id={req_id}{tool_note}")

    resp = {"jsonrpc": "2.0", "id": req_id}

    if method == "initialize":
        resp["result"] = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False}
            },
            "serverInfo": {
                "name": "antigravity_mesh",
                "version": "1.0.0"
            },
            "instructions": (
                (
                (
                f"Antigravity Mesh node '{user}' - direct access to the real host over a reverse tunnel.\n"
                f"GROUNDING (mandatory): NEVER state a path, file, directory or process you have not seen "
                f"in a tool result in THIS conversation. Tool output marked [VERIFIED BY TOOL] is real; "
                f"anything marked [TOOL ERROR] means the path does NOT exist - never repeat it as real. "
                f"If you have not inspected something, say so and call list_dir / glob_find / grep_search first.\n"
                f"USE: list_dir/glob_find/grep_search to locate, read_file to read, write_file/edit_file to "
                f"modify, bash_exec for short commands, system_vitals for resources. Only touch paths you "
                f"have confirmed. For slow work (builds, installs, tests, downloads) use run_job, then "
                f"job_output / job_kill / job_list. Long output is paged: pass cursor=<next_cursor>."
            )
            )
            )
        }
    elif method == "notifications/initialized":
        session_id = request.headers.get("mcp-session-id") or user
        return Response(
            status_code=204,
            headers={
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
            }
        )
    elif method == "ping":
        resp["result"] = {}
    elif method == "tools/list":
        resp["result"] = {
            "tools": [
                {
                    "name": "list_dir",
                    "description": (
                        "List a directory on the remote host (default: workspace)."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional directory path to inspect. If omitted or empty, lists the agent's current working directory."
                            }
                        },
                        "required": []
                    }
                },
                {
                    "name": "read_file",
                    "description": (
                        "Read a file, line-numbered. Page with max_chars/cursor."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to read"},
                            "start_line": {"type": "integer", "description": "First line (1-based)"},
                            "end_line": {"type": "integer", "description": "Last line (1-based)"}
                        },
                        "required": ["path"]
                    }
                },
                {
                    "name": "bash_exec",
                    "description": (
                        f"Execute a shell command directly on {user}'s host machine. "
                        "MANDATORY: Always use this tool to inspect git status, run builds, check processes, or execute tasks. "
                        "NEVER guess command outputs."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Shell command to execute on the node"
                            }
                        },
                        "required": ["command"]
                    }
                },
                {
                    "name": "mesh_status",
                    "description": ("Confirm this node is reachable. Call this FIRST if you believe the "
                                    "host is offline: it returns live evidence from the host."),
                    "inputSchema": {"type": "object", "properties": {}, "required": []}
                },
                {
                    "name": "system_vitals",
                    "description": f"Retrieve real-time CPU, RAM, and Disk metrics on {user}'s host machine",
                    "inputSchema": {"type": "object", "properties": {}, "required": []}
                },
                {
                    "name": "get_orchestration_skill",
                    "description": "Load the node orchestration rules.",
                    "inputSchema": {"type": "object", "properties": {}, "required": []}
                },
                {
                    "name": "write_file",
                    "description": (
                        "Write a file atomically (creates parent dirs)."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path, or relative to the agent's workspace"},
                            "content": {"type": "string", "description": "Exact file content to write"},
                            "create_dirs": {"type": "boolean", "description": "Create parent directories"},
                            "mode": {"type": "string", "description": "File mode, e.g. 0644"}
                        },
                        "required": ["path", "content"]
                    }
                },
                {
                    "name": "edit_file",
                    "description": (
                        "Replace an exact string in a file; old_string must be unique."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File to edit"},
                            "old_string": {"type": "string", "description": "Exact text to replace (not a regex); must be unique unless replace_all is true"},
                            "new_string": {"type": "string", "description": "Replacement text"},
                            "expected_sha256": {"type": "string", "description": "Fail if file changed since read"},
                            "replace_all": {"type": "boolean", "description": "Replace every occurrence"}
                        },
                        "required": ["path", "old_string", "new_string"]
                    }
                },
                {
                    "name": "grep_search",
                    "description": (
                        "Recursively search file contents; returns path:line: text."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Text or regular expression to search for"},
                            "path": {"type": "string", "description": "Directory to search recursively (default \".\")"},
                            "glob": {"type": "string", "description": "Filename filter"},
                            "limit": {"type": "integer", "description": "Max results"},
                            "ignore_case": {"type": "boolean", "description": "Case-insensitive"},
                            "fixed": {"type": "boolean", "description": "Literal text, not regex"},
                            "context": {"type": "integer", "description": "Context lines"}
                        },
                        "required": ["pattern"]
                    }
                },
                {
                    "name": "glob_find",
                    "description": (
                        "Find files by glob pattern (* and **)."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Glob pattern, e.g. \"**/*.py\" or \"src/*.json\""},
                            "path": {"type": "string", "description": "Base directory to search from (default \".\")"}
                        },
                        "required": ["pattern"]
                    }
                },
                {
                    "name": "run_job",
                    "description": (
                        "Start a long command in the background; returns job_id."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command to run in the background"},
                            "cwd": {"type": "string", "description": "Optional working directory"}
                        },
                        "required": ["command"]
                    }
                },
                {
                    "name": "job_output",
                    "description": (
                        "Get a job status/output. wait_ms<=20000; page via next_cursor."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string", "description": "Job id returned by run_job"},
                            "wait_ms": {"type": "integer", "description": "Block up to this long (max 20000)"},
                            "max_chars": {"type": "integer", "description": "Chunk size"},
                            "cursor": {"type": "integer", "description": "Offset to continue from"}
                        },
                        "required": ["job_id"]
                    }
                },
                {
                    "name": "job_kill",
                    "description": "Stop a running job.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string", "description": "Job id to stop"},
                            "signal": {"type": "string", "description": "Signal to send, e.g. TERM or KILL (default TERM)"}
                        },
                        "required": ["job_id"]
                    }
                },
                {
                    "name": "job_list",
                    "description": "List recent jobs, newest first.",
                    "inputSchema": {"type": "object", "properties": {}, "required": []}
                }
            ]
        }
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        is_error = False
        if name == "get_orchestration_skill":
            content_text = get_skill(user, request.headers.get("host", "smart-server.online"))
        elif name == "mesh_status":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                parts = ["[NODE REACHABLE] This answer was produced on the host itself - the mesh "
                         "agent and the tunnel are working. Do not claim the node is offline."]
                for key in ("hostname", "agent_pid", "checked_at", "host_uptime_human", "workspace"):
                    if res.get(key) is not None:
                        parts.append(f"{key}: {res.get(key)}")
                content_text = "\n".join(parts)
        elif name == "system_vitals":
            res = await call_remote_tool(user, name, args)
            if "error" in res:
                is_error = True
                content_text = f"Error: {res.get('error')}"
            else:
                content_text = json.dumps(res, ensure_ascii=False, indent=2)
        elif name in ("bash_exec", "list_dir", "read_file"):
            # Gateway-level guard: call_remote_tool gives up after 28s, so never forward
            # a bash timeout longer than 25s (node may default to 25 anyway).
            if name == "bash_exec":
                timeout_sec = args.get("timeout_sec")
                if isinstance(timeout_sec, (int, float)) and timeout_sec > 25:
                    args = dict(args)
                    args["timeout_sec"] = 25
            res = await call_remote_tool(user, name, args)
            if "error" in res:
                is_error = True
                content_text = f"[Execution Error]: {res.get('error')}"
            else:
                exit_code = res.get("exit_code", 0)
                # Never strip: a paginated chunk must survive byte-exact so the
                # client can rebuild the output by following next_cursor.
                stdout = res.get("stdout", "")
                stderr = res.get("stderr", "")
                if exit_code != 0:
                    is_error = True
                    parts = [f"[Exit code: {exit_code}]"]
                    if stderr.strip():
                        parts.append(stderr)
                    if stdout.strip():
                        parts.append(stdout)
                    content_text = "\n".join(parts) if parts else f"[Exit code: {exit_code}]"
                else:
                    parts = []
                    if stdout:
                        parts.append(stdout)
                    if stderr:
                        parts.append(f"[STDERR]\n{stderr}")
                    content_text = "\n\n".join(parts) if parts else "(Command executed successfully with no output)"
                # Surface node-side pagination/spooling so the model knows there is more.
                if isinstance(res, dict):
                    if res.get("next_cursor") is not None:
                        content_text += (
                            f"\n\n[output continues on the node: call {name} again with "
                            f"cursor={res.get('next_cursor')} (optionally a larger max_chars)]"
                        )
                    if res.get("saved_to"):
                        content_text += f"\n\n[full output saved on the node at {res['saved_to']}]"
        elif name in ("write_file", "edit_file"):
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                path = res.get("path", args.get("path", ""))
                sha = res.get("sha256", "")
                if name == "write_file":
                    content_text = f"OK: wrote {res.get('bytes', 0)} bytes to {path} (sha256={sha})"
                else:
                    content_text = f"OK: edited {path} ({res.get('replacements', 0)} replacement(s), sha256={sha})"
        elif name == "grep_search":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                matches = res.get("matches") or []
                if not matches:
                    content_text = "No matches."
                else:
                    lines = []
                    for m in matches:
                        if not isinstance(m, dict):
                            continue
                        lines.append(f"{m.get('path')}:{m.get('line')}: {m.get('text')}")
                    content_text = "\n".join(lines) if lines else "No matches."
                    if res.get("truncated"):
                        content_text += f"\n\n(truncated: showing first {len(lines)} matches; narrow the pattern or raise limit)"
        elif name == "glob_find":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                files = res.get("files") or []
                content_text = "\n".join(str(f) for f in files) if files else "No files found."
                if res.get("truncated"):
                    content_text += f"\n\n(truncated: showing first {len(files)} paths)"
        elif name == "run_job":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                job_id = res.get("job_id", "")
                content_text = (
                    f"Started job {job_id} (pid {res.get('pid')})\n"
                    f"command: {res.get('command', args.get('command', ''))}\n"
                    f"Use job_output(job_id=\"{job_id}\") to poll progress; use job_kill(job_id=\"{job_id}\") to stop it."
                )
        elif name == "job_output":
            # Gateway-level guard: call_remote_tool gives up after 28s, keep wait below it.
            wait_ms = args.get("wait_ms")
            if isinstance(wait_ms, (int, float)) and wait_ms > 20000:
                args = dict(args)
                args["wait_ms"] = 20000
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                parts = [f"[status={res.get('status')} exit_code={res.get('exit_code')} duration={res.get('duration')}]"]
                stdout = res.get("stdout") or ""
                stderr = res.get("stderr") or ""
                if stdout:
                    parts.append(stdout)
                if stderr:
                    parts.append(f"[STDERR]\n{stderr}")
                if res.get("next_cursor") is not None:
                    parts.append(f"... output truncated; continue with cursor={res.get('next_cursor')}")
                content_text = "\n\n".join(parts)
        elif name == "job_kill":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                content_text = f"Job {args.get('job_id', '')}: status={res.get('status')} (ok={res.get('ok')})"
        elif name == "job_list":
            res = await call_remote_tool(user, name, args)
            err = remote_tool_error(res)
            if err:
                is_error = True
                content_text = f"[Error] {err}"
            else:
                jobs = res.get("jobs") or []
                if not jobs:
                    content_text = "No jobs."
                else:
                    lines = ["job_id | status | exit_code | duration | started_at | command"]
                    for j in jobs:
                        if not isinstance(j, dict):
                            continue
                        lines.append(
                            "{job_id} | {status} | {exit_code} | {duration} | {started_at} | {command}".format(
                                job_id=j.get("job_id", ""),
                                status=j.get("status", ""),
                                exit_code=j.get("exit_code"),
                                duration=j.get("duration"),
                                started_at=j.get("started_at", ""),
                                command=j.get("command", ""),
                            )
                        )
                    content_text = "\n".join(lines)
        else:
            resp["error"] = {"code": -32601, "message": f"Unknown tool: {name}"}
            broadcast_sse(user, resp)
            session_id = request.headers.get("mcp-session-id") or user
            return JSONResponse(
                resp,
                headers={
                    "mcp-session-id": session_id,
                    "mcp-protocol-version": "2024-11-05",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                }
            )

        # Size guard for the Gemini Web turn budget. The node already paginates
        # via next_cursor, so the gateway must NOT cut the middle out: that would
        # break byte-exact continuation. Keep the head and say how to get the
        # rest, so the model can ask for the next page instead of losing data.
        MAX_GATEWAY_TEXT = 45000
        if len(content_text) > MAX_GATEWAY_TEXT:
            kept = content_text[:MAX_GATEWAY_TEXT]
            omitted = len(content_text) - MAX_GATEWAY_TEXT
            content_text = (
                f"{kept}\n\n... [Output truncated by the gateway: {omitted} more characters. "
                f"Do not re-run the command; the node can page the rest: call the same tool again "
                f"with cursor=<next_cursor> and a larger max_chars, or read the saved file if the "
                f"result reported one.] ..."
            )

        resp["result"] = {
            "content": [{"type": "text", "text": content_text}],
            "isError": is_error
        }
    elif method == "prompts/list":
        resp["result"] = {
            "prompts": [{"name": "antigravity-orchestrator", "description": f"Orchestrator role for {user}"}]
        }
    elif method == "prompts/get":
        skill_text = get_skill(user, request.headers.get("host", "smart-server.online"))
        resp["result"] = {
            "description": "Antigravity Orchestrator Persona",
            "messages": [{"role": "user", "content": {"type": "text", "text": skill_text}}]
        }
    elif method == "resources/list":
        resp["result"] = {
            "resources": [{
                "uri": "resource://skills/orchestrator.md",
                "name": "orchestrator.md",
                "mimeType": "text/markdown"
            }]
        }
    elif method == "resources/read":
        uri = params.get("uri")
        if uri == "resource://skills/orchestrator.md":
            text = get_skill(user, request.headers.get("host", "smart-server.online"))
            resp["result"] = {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
        else:
            resp["error"] = {"code": -32602, "message": f"Resource not found: {uri}"}
            broadcast_sse(user, resp)
            session_id = request.headers.get("mcp-session-id") or user
            return JSONResponse(
                resp,
                headers={
                    "mcp-session-id": session_id,
                    "mcp-protocol-version": "2024-11-05",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                }
            )
    else:
        resp["error"] = {"code": -32601, "message": f"Method not found: {method}"}

    # Deliver the answer on the SSE stream only for the legacy endpoint that the
    # `event: endpoint` announcement points at (/messages). Clients that POST to
    # /sse or /mcp read the response from this HTTP body; broadcasting it as well
    # would hand them every answer twice, and a strict client reacts to the
    # unexpected message by aborting the session and starting over.
    if request.url.path.rstrip("/") == "/messages":
        broadcast_sse(user, resp)

    # Return response in HTTP body (for Streamable HTTP clients) with Mcp-Session-Id header
    session_id = request.headers.get("mcp-session-id") or user
    return JSONResponse(
        resp,
        headers={
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2024-11-05",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version"
        }
    )

async def mcp_unified_endpoint(request: Request):
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Allow": "GET, POST, DELETE, OPTIONS, HEAD",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS, HEAD",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                "mcp-protocol-version": "2024-11-05",
            }
        )
    if request.method == "DELETE":
        ok, msg, user = verify_token(request)
        if not ok:
            return JSONResponse(
                {"error": "unauthorized", "message": msg},
                status_code=401,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                    "mcp-protocol-version": "2024-11-05",
                }
            )
        client_ip = request.client.host if request.client else "unknown"
        session_id = request.headers.get("mcp-session-id") or user
        logger.info(f"Session teardown (DELETE) from {client_ip} [{user}] session_id={session_id}")

        # Clean up session queue if active
        if session_id in active_sse_sessions:
            q = active_sse_sessions.pop(session_id, None)
            if q:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

        return Response(
            status_code=204,
            headers={
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS, HEAD",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
            }
        )
    if request.method in ("GET", "HEAD"):
        path = request.url.path
        if path == "/messages":
            ok, msg, user = verify_token(request)
            if not ok:
                return JSONResponse(
                    {"error": "unauthorized", "message": msg},
                    status_code=401,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                        "mcp-protocol-version": "2024-11-05",
                    }
                )
            accept = (request.headers.get("accept") or "").lower()
            client_ip = request.client.host if request.client else "unknown"
            logger.info(
                f"GET /messages from {client_ip} [{user}] accept={accept[:60]!r} "
                f"-> {'sse-stream' if 'text/event-stream' in accept else 'json-ready'}"
            )
            # Some MCP clients (Gemini Spark among them) open the message endpoint
            # itself as the server->client stream instead of /sse. Answering that
            # with a small JSON body closes the stream immediately, which makes the
            # client drop the session and re-initialise in a loop. Serve a real SSE
            # stream when the caller asks for one, and keep the JSON readiness probe
            # for plain health checks.
            if "text/event-stream" in accept:
                return await sse_endpoint(request)
            return JSONResponse({
                "status": "ready",
                "endpoint": "messages",
                "user": user
            }, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
                "mcp-protocol-version": "2024-11-05"
            })
        return await sse_endpoint(request)
    if request.method == "POST":
        return await messages_endpoint(request)
    return Response(
        status_code=405,
        headers={
            "Allow": "GET, POST, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "mcp-session-id, mcp-protocol-version",
            "mcp-protocol-version": "2024-11-05",
        }
    )

async def ws_tunnel_endpoint(websocket: WebSocket):
    query = websocket.query_params
    user = query.get("user", "").lower()
    token = query.get("token", "")
    registry = load_registry()

    if not user or user not in registry or not tokens_equal(token, registry[user].get("token", "")):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Cleanly terminate any prior websocket and fail pending futures for this user
    old_tunnel = active_tunnels.get(user)
    if old_tunnel:
        for r_id, fut in list(old_tunnel.get("pending", {}).items()):
            if not fut.done():
                fut.set_exception(ConnectionResetError("Tunnel reconnected"))
        if old_tunnel.get("ws"):
            try:
                await old_tunnel["ws"].close(code=1000, reason="Superceded by new connection")
            except Exception:
                pass

    await websocket.accept()
    logger.info(f"Agent tunnel connected for user '{user}'")
    tunnel_data = {"ws": websocket, "pending": {}}
    active_tunnels[user] = tunnel_data

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            req_id = msg.get("id")
            if req_id and req_id in tunnel_data["pending"]:
                fut = tunnel_data["pending"].pop(req_id)
                if not fut.done():
                    fut.set_result(msg)
    except WebSocketDisconnect:
        logger.info(f"Agent tunnel disconnected for user '{user}'")
    finally:
        if active_tunnels.get(user, {}).get("ws") == websocket:
            active_tunnels.pop(user, None)

routes = [
    Route("/", health, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/health", health, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/health-mesh", health, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/api/register", api_register, methods=["POST", "OPTIONS"]),
    Route("/.well-known/oauth-protected-resource", oauth_discovery, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/.well-known/oauth-protected-resource/{path:path}", oauth_discovery, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/sse", mcp_unified_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"]),
    Route("/mcp", mcp_unified_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"]),
    Route("/messages", mcp_unified_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"]),
    WebSocketRoute("/ws/tunnel", ws_tunnel_endpoint),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id", "mcp-protocol-version"],
    )
]

async def tunnel_watchdog():
    """Background self-healing loop.

    A websocket can die without a clean close (NAT timeout, host sleep). The entry
    then stays in active_tunnels, so /health reports the node online while every
    call hangs until the 28 s timeout. This loop removes such entries, prunes SSE
    queues nobody consumes and keeps the state truthful.
    """
    logger.info("Self-heal watchdog started (30s interval)")
    while True:
        try:
            await asyncio.sleep(30)
            for user, tunnel in list(active_tunnels.items()):
                ws = tunnel.get("ws")
                state = getattr(getattr(ws, "client_state", None), "name", "UNKNOWN")
                if ws is None or state not in ("CONNECTED",):
                    logger.warning(f"Watchdog: removing stale tunnel for '{user}' (state={state})")
                    for fut in list(tunnel.get("pending", {}).values()):
                        if not fut.done():
                            fut.set_exception(ConnectionResetError("Tunnel was stale"))
                    active_tunnels.pop(user, None)
            # drop subscriber queues that no stream is reading any more
            for user, queues in list(active_sse_subscribers.items()):
                alive = {q for q in queues if not q.empty() or q in active_sse_sessions.values()}
                if alive != queues:
                    active_sse_subscribers[user] = alive
                if not alive:
                    active_sse_subscribers.pop(user, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                 # never let the watchdog die
            logger.warning(f"Watchdog error: {exc}")


@asynccontextmanager
async def lifespan(_app):
    """Start the self-healing watchdog with the app (modern Starlette API)."""
    task = asyncio.create_task(tunnel_watchdog())
    try:
        yield
    finally:
        task.cancel()

app = Starlette(debug=False, routes=routes, middleware=middleware, lifespan=lifespan)




if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8096, log_level="info", timeout_graceful_shutdown=2)
