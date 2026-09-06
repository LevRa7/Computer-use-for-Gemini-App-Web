import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Any, Callable
from core.vitals import get_host_vitals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp-server")

class AntigravityMeshServer:
    def __init__(self, name: str = "antigravity_mesh"):
        self.name = name
        self.tools: Dict[str, Callable] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        self.register_tool("bash_exec", self.bash_exec)
        self.register_tool("system_vitals", self.system_vitals)
        self.register_tool("get_orchestration_skill", self.get_orchestration_skill)

    def register_tool(self, name: str, func: Callable):
        self.tools[name] = func

    def bash_exec(self, command: str) -> Dict[str, Any]:
        start = time.time()
        try:
            res = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration = round(time.time() - start, 3)
            return {
                "stdout": res.stdout,
                "stderr": res.stderr,
                "exit_code": res.returncode,
                "duration": duration,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": "Command timed out after 120s",
                "exit_code": 124,
                "duration": 120.0,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "duration": round(time.time() - start, 3),
            }

    def system_vitals(self) -> Dict[str, Any]:
        return get_host_vitals()

    def get_orchestration_skill(self) -> str:
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "orchestrator.md"
        if skill_path.exists():
            return skill_path.read_text(encoding="utf-8")
        return (
            "# 🌐 Role: Autonomous Infrastructure Orchestrator (Standalone)\n"
            "Mode: Local Standalone Server\n"
            "All commands execute directly on localhost."
        )

    def handle_jsonrpc(self, body: dict) -> tuple[int, dict | None]:
        method = body.get("method")
        params = body.get("params", {})
        req_id = body.get("id")

        if req_id is None and method:
            return 204, None

        resp = {"jsonrpc": "2.0", "id": req_id}

        if method == "initialize":
            resp["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {"name": self.name, "version": "1.0.0"},
                "instructions": "Standalone Local Antigravity Mesh Server.",
            }
        elif method == "notifications/initialized":
            return 204, None
        elif method == "ping":
            resp["result"] = {}
        elif method == "tools/list":
            resp["result"] = {
                "tools": [
                    {
                        "name": "system_vitals",
                        "title": "System Vitals",
                        "description": "Retrieve CPU, RAM, and Disk metrics on local machine",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "bash_exec",
                        "title": "Execute Bash Command",
                        "description": "Execute shell command directly on local machine",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                    {
                        "name": "get_orchestration_skill",
                        "title": "Get Orchestration Skill",
                        "description": "Load dynamic orchestration instructions and rules",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            }
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if name in self.tools:
                try:
                    if name == "bash_exec":
                        res = self.tools[name](command=args.get("command", ""))
                    else:
                        res = self.tools[name]()
                except Exception as e:
                    res = {"error": str(e)}
            else:
                resp["error"] = {"code": -32601, "message": f"Unknown tool: {name}"}
                return 200, resp

            resp["result"] = {
                "content": [{
                    "type": "text",
                    "text": json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
                }]
            }
        elif method == "prompts/list":
            resp["result"] = {
                "prompts": [{"name": "antigravity-orchestrator", "description": "Local Standalone Orchestrator"}]
            }
        elif method == "prompts/get":
            resp["result"] = {
                "description": "Antigravity Orchestrator Persona",
                "messages": [{"role": "user", "content": {"type": "text", "text": self.get_orchestration_skill()}}]
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
            resp["result"] = {
                "contents": [{
                    "uri": params.get("uri", ""),
                    "mimeType": "text/markdown",
                    "text": self.get_orchestration_skill()
                }]
            }
        else:
            resp["error"] = {"code": -32601, "message": f"Method not found: {method}"}

        return 200, resp

def create_mcp_server() -> AntigravityMeshServer:
    return AntigravityMeshServer(name="antigravity_mesh")

class StandaloneMCPHandler(BaseHTTPRequestHandler):
    server_mcp: AntigravityMeshServer = None

    def _set_cors(self, status: int = 200, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Type", content_type)

    def do_OPTIONS(self):
        self._set_cors(200)
        self.end_headers()

    def do_HEAD(self):
        if self.path.startswith("/sse") or self.path.startswith("/mcp"):
            self._set_cors(200, "text/event-stream")
            self.end_headers()
        else:
            self._set_cors(200)
            self.end_headers()

    def do_GET(self):
        clean_path = self.path.split("?")[0]
        if clean_path in ("/", "/health"):
            data = {"status": "healthy", "mode": "standalone", "vitals": self.server_mcp.system_vitals()}
            body = json.dumps(data).encode("utf-8")
            self._set_cors(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path.startswith("/.well-known/oauth-protected-resource"):
            self._set_cors(404)
            body = json.dumps({"error": "not_found"}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path in ("/sse", "/mcp"):
            self._set_cors(200, "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # Send endpoint
            self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
            self.wfile.flush()
            try:
                while True:
                    time.sleep(15)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._set_cors(404)
            self.end_headers()

    def do_POST(self):
        clean_path = self.path.split("?")[0]
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            body = {}

        if clean_path in ("/messages", "/sse", "/mcp"):
            code, resp = self.server_mcp.handle_jsonrpc(body)
            if code == 204 or resp is None:
                self._set_cors(204)
                self.end_headers()
            else:
                resp_bytes = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self._set_cors(code)
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)
        else:
            self._set_cors(404)
            self.end_headers()

    def log_message(self, format, *args):
        logger.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

def run_standalone_server(host: str = "0.0.0.0", port: int = 8096):
    mcp = create_mcp_server()
    StandaloneMCPHandler.server_mcp = mcp
    server = ThreadingHTTPServer((host, port), StandaloneMCPHandler)
    logger.info(f"Antigravity Mesh Standalone Server listening on http://{host}:{port}/sse")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")
    finally:
        server.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Mesh Standalone Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8096, help="Port to listen on")
    args = parser.parse_args()
    run_standalone_server(host=args.host, port=args.port)
