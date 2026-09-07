#!/usr/bin/env python3
"""
agy_mcp_server.py - Universal Model Context Protocol (MCP) Server for Antigravity & Gemini Web Spark.
Supports:
  1. Standard SSE transport (GET /sse -> event-stream, POST /messages -> session messages)
  2. Streamable HTTP / Direct JSON-RPC (POST /sse, POST /mcp, POST /)
  3. Pre-flight and probes (HEAD, OPTIONS, GET on all endpoints)
  4. RFC 9470 OAuth Protected Resource discovery (/.well-known/oauth-protected-resource)
"""

import os
import re
import sys
import time
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
import uvicorn

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from gdrive_client import GDriveClient

BASE_DIR = Path(__file__).resolve().parent
DEBIAN_IP = "100.86.180.81"
MATEBOOK_IP = "100.119.202.62"
RACKNERD2_IP = "192.129.148.93"
PROBE_LOG = BASE_DIR / "mcp_requests.log"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("agy_mcp")

# Tool functions
def racknerd2_exec(command: str) -> str:
    t0 = time.time()
    ssh_cmd = [
        "sshpass", "-p", "7tE95vUmzkTR3lI59A", "ssh",
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
        f"root@{RACKNERD2_IP}", command
    ]
    try:
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=120)
        duration = round(time.time() - t0, 2)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            err_f = "\n".join(l for l in err.splitlines() if "Permanently added" not in l).strip()
            if err_f:
                parts.append(f"[STDERR]\n{err_f}")
        body = "\n\n".join(parts) if parts else "(no output)"
        return f"[RackNerd-5a24bf9 192.129.148.93 | Exit: {proc.returncode} in {duration}s]\n{body}"
    except subprocess.TimeoutExpired:
        return "[RackNerd-5a24bf9 | Error]: Command timed out after 120s."
    except Exception as e:
        return f"[RackNerd-5a24bf9 | Error]: {e}"

def bash_exec(command: str) -> str:
    t0 = time.time()
    try:
        proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=120)
        duration = round(time.time() - t0, 2)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[STDERR]\n{err}")
        body = "\n\n".join(parts) if parts else "(no output)"
        return f"[Exit code: {proc.returncode} in {duration}s]\n{body}"
    except subprocess.TimeoutExpired:
        return "[Exit code: -1] Command timed out after 120s."
    except Exception as e:
        return f"[Execution Error]: {e}"

def debian_exec(command: str) -> str:
    t0 = time.time()
    ssh_cmd = [
        "sshpass", "-p", "ST720p", "ssh",
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
        f"root@{DEBIAN_IP}", command
    ]
    try:
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=180)
        duration = round(time.time() - t0, 2)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            err_f = "\n".join(l for l in err.splitlines() if "Permanently added" not in l).strip()
            if err_f:
                parts.append(f"[STDERR]\n{err_f}")
        body = "\n\n".join(parts) if parts else "(no output)"
        return f"[Debian Node 100.86.180.81 | Exit: {proc.returncode} in {duration}s]\n{body}"
    except subprocess.TimeoutExpired:
        return "[Debian Node 100.86.180.81 | Error]: Command timed out after 180s."
    except Exception as e:
        return f"[Debian Node 100.86.180.81 | Error]: {e}"

def matebook_exec(command: str) -> str:
    t0 = time.time()
    ssh_cmd = [
        "sshpass", "-p", "ST720p", "ssh",
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
        f"lev@{MATEBOOK_IP}", command
    ]
    try:
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=120)
        duration = round(time.time() - t0, 2)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            err_f = "\n".join(l for l in err.splitlines() if "Permanently added" not in l).strip()
            if err_f:
                parts.append(f"[STDERR]\n{err_f}")
        body = "\n\n".join(parts) if parts else "(no output)"
        return f"[Matebook16 100.119.202.62 | Exit: {proc.returncode} in {duration}s]\n{body}"
    except subprocess.TimeoutExpired:
        return "[Matebook16 | Error]: Command timed out after 120s."
    except Exception as e:
        return f"[Matebook16 | Error]: {e}"

def system_vitals() -> str:
    from core.server import get_coordinator_vitals
    vitals = get_coordinator_vitals()
    return vitals.model_dump_json(indent=2)

def matebook_vitals() -> str:
    ssh_cmd = [
        "sshpass", "-p", "ST720p", "ssh",
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=8",
        f"lev@{MATEBOOK_IP}",
        "uptime; free -h | grep Mem; cat /sys/class/power_supply/BAT*/capacity 2>/dev/null; cat /sys/class/power_supply/BAT*/status 2>/dev/null"
    ]
    try:
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode != 0:
            return f"[Matebook16]: Offline or unreachable (Exit: {proc.returncode})"
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        uptime_line = lines[0] if len(lines) > 0 else "unknown"
        mem_line = lines[1] if len(lines) > 1 else "unknown"
        bat_cap = lines[2] + "%" if len(lines) > 2 and lines[2].isdigit() else "unknown"
        bat_stat = lines[3] if len(lines) > 3 else "unknown"
        return f"Matebook16 Status:\n  Uptime: {uptime_line}\n  RAM: {mem_line}\n  Battery: {bat_cap} ({bat_stat})"
    except Exception as e:
        return f"[Matebook16 Error]: {e}"

def read_file(path: str, start_line: int = 1, end_line: int = 100) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = BASE_DIR / p
    if not p.exists():
        return f"Error: File not found: {p}"
    if p.is_dir():
        return f"Error: Path is a directory: {p}"
    try:
        from core.code_cache import get_code_index
        return get_code_index(str(BASE_DIR)).get_cached_chunk(str(p), start_line, end_line)
    except Exception:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            s = max(1, start_line) - 1
            e = min(total, end_line)
            slice_lines = lines[s:e]
            result = "".join(slice_lines)
            return f"[{p} | Lines {s+1}-{e} of {total}]\n{result}"
        except Exception as err:
            return f"Error reading file: {err}"

def write_file(path: str, content: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = BASE_DIR / p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to {p}"
    except Exception as err:
        return f"Error writing file: {err}"

def gdrive_copy(file_name_or_id: str, dest_path: str, target_node: str = "vps") -> str:
    """Copies a file from Google Drive (handles both regular files and Google Docs conversion) to VPS, Matebook16, or Debian node."""
    t0 = time.time()
    try:
        client = GDriveClient()
        tmp_dir = Path("/tmp/gdrive_downloads")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file_name_or_id).name or "gdrive_download.tmp"
        tmp_target = tmp_dir / safe_name

        ok = False
        msg = ""
        meta = {}

        # 1. Try by file_name if it looks like a filename
        if "." in file_name_or_id or len(file_name_or_id) <= 20:
            ok, msg, meta = client.download_file(file_name=file_name_or_id, dest_path=str(tmp_target))

        # 2. If not found or failed, try as file_id
        if not ok:
            ok, msg, meta = client.download_file(file_id=file_name_or_id, dest_path=str(tmp_target))

        # 3. Fallback search by name
        if not ok:
            ok, msg, meta = client.download_file(file_name=file_name_or_id, dest_path=str(tmp_target))

        if not ok:
            return f"[Google Drive | Error]: {msg}"

        actual_file_name = meta.get("name", safe_name)
        file_size = tmp_target.stat().st_size if tmp_target.exists() else 0

        target_node = (target_node or "vps").lower().strip()
        if target_node in ("vps", "localhost", "local"):
            final_dest = Path(dest_path).expanduser().resolve()
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp_target, final_dest)
            duration = round(time.time() - t0, 2)
            return f"[Exit code: 0 in {duration}s]\nSuccessfully copied '{actual_file_name}' ({file_size} bytes) from Google Drive to VPS: {final_dest}"
        elif target_node in ("matebook", "matebook16"):
            dest_clean = dest_path.strip()
            if dest_clean.startswith("~/"):
                dest_clean = "/home/lev/" + dest_clean[2:]
            elif dest_clean == "~":
                dest_clean = "/home/lev"
            parent_dir = str(Path(dest_clean).parent)
            if parent_dir and parent_dir != ".":
                subprocess.run([
                    "sshpass", "-p", "ST720p", "ssh",
                    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                    f"lev@{MATEBOOK_IP}", f"mkdir -p '{parent_dir}'"
                ], capture_output=True, timeout=15)
            scp_cmd = [
                "sshpass", "-p", "ST720p", "scp",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15",
                str(tmp_target), f"lev@{MATEBOOK_IP}:{dest_clean}"
            ]
            proc = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            duration = round(time.time() - t0, 2)
            if proc.returncode == 0:
                return f"[Exit code: 0 in {duration}s]\nSuccessfully copied '{actual_file_name}' ({file_size} bytes) from Google Drive to Matebook16: {dest_clean}"
            else:
                err_clean = "\n".join(l for l in proc.stderr.splitlines() if "Permanently added" not in l).strip()
                return f"[Matebook16 | Exit: {proc.returncode} in {duration}s]\n[STDERR]\n{err_clean}"
        elif target_node in ("debian", "ryzen", "5950x"):
            dest_clean = dest_path.strip()
            if dest_clean.startswith("~/"):
                dest_clean = "/root/" + dest_clean[2:]
            elif dest_clean == "~":
                dest_clean = "/root"
            parent_dir = str(Path(dest_clean).parent)
            if parent_dir and parent_dir != ".":
                subprocess.run([
                    "sshpass", "-p", "ST720p", "ssh",
                    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                    f"root@{DEBIAN_IP}", f"mkdir -p '{parent_dir}'"
                ], capture_output=True, timeout=15)
            scp_cmd = [
                "sshpass", "-p", "ST720p", "scp",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15",
                str(tmp_target), f"root@{DEBIAN_IP}:{dest_clean}"
            ]
            proc = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            duration = round(time.time() - t0, 2)
            if proc.returncode == 0:
                return f"[Exit code: 0 in {duration}s]\nSuccessfully copied '{actual_file_name}' ({file_size} bytes) from Google Drive to Debian Node: {dest_clean}"
            else:
                err_clean = "\n".join(l for l in proc.stderr.splitlines() if "Permanently added" not in l).strip()
                return f"[Debian Node | Exit: {proc.returncode} in {duration}s]\n[STDERR]\n{err_clean}"
        else:
            return f"[Google Drive | Error]: Unknown target_node '{target_node}'. Use 'vps', 'matebook', or 'debian'."
    except Exception as e:
        return f"[Google Drive | Error]: {e}"

def get_orchestration_skill() -> str:
    """Returns the complete up-to-date orchestration SKILL.md and rules for managing nodes via MCP."""
    skill_path = Path(__file__).resolve().parent / ".agents" / "skills" / "agy-task-dispatcher" / "SKILL.md"
    if not skill_path.exists():
        skill_path = Path(__file__).resolve().parent / "WEB_GEMINI_SKILL_INSTRUCTIONS.md"
    if skill_path.exists():
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    return "# Orchestration Skill not found on host."

def locate_symbol(name: str) -> str:
    """Instantly finds symbols (classes, functions, methods) across the codebase with line numbers without disk grep."""
    from core.code_cache import get_code_index
    idx = get_code_index(str(BASE_DIR))
    symbols = idx.find_symbol(name, exact=False)
    if not symbols:
        return f"No symbols matching '{name}' found."
    lines = [f"Found {len(symbols)} symbol(s) matching '{name}':"]
    for s in symbols:
        doc = f" - {s.docstring.strip()[:80]}..." if s.docstring else ""
        lines.append(f"  • [{s.kind}] {s.name} -> {s.file_path}:{s.line_start}-{s.line_end}{doc}")
    return "\n".join(lines)

def code_sitemap() -> str:
    """Returns the cached architectural sitemap of modules, files, classes and functions."""
    import json
    from core.code_cache import get_code_index
    idx = get_code_index(str(BASE_DIR))
    return json.dumps(idx.get_sitemap(), indent=2)

TOOLS_DEFINITIONS = [
    {
        "name": "get_orchestration_skill",
        "title": "Get Orchestration Skill",
        "description": "Returns the complete up-to-date orchestration SKILL.md and rules for managing nodes via MCP.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "bash_exec",
        "title": "Execute Bash on Local Coordinator",
        "description": "Executes a bash command locally on the coordinator host and returns stdout and stderr.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command line to execute on the coordinator host."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "debian_exec",
        "title": "Execute Bash on Debian Node",
        "description": "Executes a bash command on the dedicated compute node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command line to execute on the compute node."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "matebook_exec",
        "title": "Execute Bash on Matebook Node",
        "description": "Executes a bash command on the developer workstation node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command line to execute on the developer node."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "racknerd2_exec",
        "title": "Execute Bash on Secondary Worker Node",
        "description": "Executes a bash command on the secondary worker node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command line to execute on the secondary worker node."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "system_vitals",
        "title": "System Vitals",
        "description": "Returns real-time health metrics of the coordinator host (CPU load, RAM, Disk, Uptime).",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "matebook_vitals",
        "title": "Matebook Vitals",
        "description": "Returns battery percentage, power status, RAM and uptime for the developer workstation.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "read_file",
        "title": "Read File",
        "description": "Reads a slice of lines from a file on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to file."},
                "start_line": {"type": "integer", "description": "1-indexed start line."},
                "end_line": {"type": "integer", "description": "1-indexed end line."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "title": "Write File",
        "description": "Writes content to a file on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path."},
                "content": {"type": "string", "description": "Text content to write."}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "gdrive_copy",
        "title": "Google Drive Copy",
        "description": "Copies a file from Google Drive (handles both binary files and Google Docs conversion) directly to VPS, Matebook16, or Debian compute node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_name_or_id": {"type": "string", "description": "The file name on Google Drive (e.g. 'antigravity_mesh_spec.md') or Google Drive file ID."},
                "dest_path": {"type": "string", "description": "Destination file path on the target host (e.g. '~/MyProjects/antigravity-mesh/antigravity_mesh_spec.md')."},
                "target_node": {"type": "string", "description": "Target destination host: 'vps' (default), 'matebook', or 'debian'.", "default": "vps"}
            },
            "required": ["file_name_or_id", "dest_path"]
        }
    },
    {
        "name": "locate_symbol",
        "title": "Locate Symbol",
        "description": "Instantly finds any class, function, or method across the codebase using AST cache with exact file path and line range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name or substring to find."}
            },
            "required": ["name"]
        }
    },
    {
        "name": "code_sitemap",
        "title": "Code Sitemap",
        "description": "Returns the cached hierarchy of files, classes, and methods across the codebase for instant navigation.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

# Set up standard MCPServer instance for SSE
mcp_sdk = MCPServer(
    name="antigravity_mesh",
    instructions=get_orchestration_skill()
)

for t_def in TOOLS_DEFINITIONS:
    fn_name = t_def["name"]
    fn = globals().get(fn_name)
    if fn:
        mcp_sdk.add_tool(fn)

security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=["*"],
    allowed_origins=["*"]
)
sse_asgi_app = mcp_sdk.sse_app(transport_security=security)

def log_probe(method: str, path: str, headers: dict, body_sample: str):
    try:
        with open(PROBE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {method} {path} | UA: {headers.get('user-agent', '')} | Body: {body_sample[:250]}\n")
    except Exception:
        pass

def process_jsonrpc(data: dict) -> tuple[int, dict]:
    req_id = data.get("id")
    method = data.get("method")
    params = data.get("params") or {}

    if method == "initialize":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"listChanged": True},
                    "prompts": {"listChanged": True}
                },
                "serverInfo": {
                    "name": "antigravity_mesh",
                    "version": "1.0.0",
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "prompts": {"listChanged": True}
                    }
                },
                "instructions": "Antigravity Mesh Orchestrator. All execution commands must go directly through native MCP tools (bash_exec, debian_exec, matebook_exec, racknerd2_exec). Google Drive queue is disabled. Dynamic topology via server_facts.json."
            }
        }
    elif method == "notifications/initialized":
        return 204, {}
    elif method == "ping":
        return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS_DEFINITIONS
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            if tool_name == "get_orchestration_skill":
                out = get_orchestration_skill()
            elif tool_name == "bash_exec":
                out = bash_exec(tool_args.get("command", ""))
            elif tool_name == "debian_exec":
                out = debian_exec(tool_args.get("command", ""))
            elif tool_name == "matebook_exec":
                out = matebook_exec(tool_args.get("command", ""))
            elif tool_name == "racknerd2_exec":
                out = racknerd2_exec(tool_args.get("command", ""))
            elif tool_name == "system_vitals":
                out = system_vitals()
            elif tool_name == "matebook_vitals":
                out = matebook_vitals()
            elif tool_name == "read_file":
                out = read_file(tool_args.get("path", ""), tool_args.get("start_line", 1), tool_args.get("end_line", 100))
            elif tool_name == "write_file":
                out = write_file(tool_args.get("path", ""), tool_args.get("content", ""))
            elif tool_name == "gdrive_copy":
                out = gdrive_copy(
                    tool_args.get("file_name_or_id", ""),
                    tool_args.get("dest_path", ""),
                    tool_args.get("target_node", "vps")
                )
            elif tool_name == "locate_symbol":
                out = locate_symbol(tool_args.get("name", ""))
            elif tool_name == "code_sitemap":
                out = code_sitemap()
            else:
                return 200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}
                }
            out_str = str(out)
            is_error = False
            if re.search(r"\[Exit code:\s*(-?[1-9]\d*)", out_str):
                is_error = True
            elif re.search(r"\|\s*Exit:\s*(-?[1-9]\d*)", out_str):
                is_error = True
            elif " | Error]:" in out_str or "[Execution Error]:" in out_str or "Command timed out" in out_str:
                is_error = True

            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": out_str}],
                    "isError": is_error
                }
            }
        except Exception as e:
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True
                }
            }
    elif method == "resources/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": [
                    {
                        "uri": "resource://skills/core.md",
                        "name": "Antigravity Mesh Core Rules",
                        "description": "Essential execution policy and core rules (CORE.md)",
                        "mimeType": "text/markdown"
                    },
                    {
                        "uri": "resource://skills/dispatcher.md",
                        "name": "Antigravity Mesh Dispatcher Skill",
                        "description": "Complete orchestration skill instructions for all nodes (SKILL.md)",
                        "mimeType": "text/markdown"
                    },
                    {
                        "uri": "resource://topology/facts.json",
                        "name": "Server Facts and Topology",
                        "description": "Dynamic node topology and specifications (server_facts.json)",
                        "mimeType": "application/json"
                    }
                ]
            }
        }
    elif method == "resources/read":
        uri = (params or {}).get("uri", "")
        base_dir = Path(__file__).resolve().parent
        content = ""
        mime = "text/plain"
        if uri == "resource://skills/core.md":
            p = base_dir / "CORE.md"
            content = p.read_text(encoding="utf-8") if p.exists() else ""
            mime = "text/markdown"
        elif uri == "resource://skills/dispatcher.md":
            p = base_dir / ".agents" / "skills" / "agy-task-dispatcher" / "SKILL.md"
            if not p.exists():
                p = base_dir / "WEB_GEMINI_SKILL_INSTRUCTIONS.md"
            content = p.read_text(encoding="utf-8") if p.exists() else ""
            mime = "text/markdown"
        elif uri == "resource://topology/facts.json":
            p = base_dir / "server_facts.json"
            content = p.read_text(encoding="utf-8") if p.exists() else "{}"
            mime = "application/json"
        else:
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Resource URI '{uri}' not found"}
            }
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": mime,
                        "text": content
                    }
                ]
            }
        }
    elif method == "prompts/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "prompts": [
                    {
                        "name": "orchestrator",
                        "description": "System instructions and rules for the Antigravity Mesh Orchestrator (SKILL.md)",
                        "arguments": []
                    }
                ]
            }
        }
    elif method == "prompts/get":
        prompt_name = (params or {}).get("name", "")
        if prompt_name == "orchestrator":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "Antigravity Mesh Orchestrator Skill",
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": get_orchestration_skill()
                            }
                        }
                    ]
                }
            }
        else:
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Prompt '{prompt_name}' not found"}
            }
    else:
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented"}
        }

MCP_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, mcp-session-id, mcp-protocol-version, Accept",
    "Access-Control-Expose-Headers": "Content-Type, Authorization, mcp-session-id"
}

async def unified_mcp_handler(request: Request):
    method = request.method.upper()
    path = request.url.path
    headers = dict(request.headers)

    body_bytes = b""
    if method in ["POST", "PUT", "PATCH"]:
        body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="replace")

    log_probe(method, path, headers, body_str)

    if method == "HEAD":
        return Response(status_code=200, headers=MCP_CORS_HEADERS)

    if method == "OPTIONS":
        return Response(status_code=200, headers=MCP_CORS_HEADERS)

    # If POST: check if it is a JSON-RPC request (direct Streamable HTTP)
    if method == "POST":
        if body_str.strip():
            try:
                data = json.loads(body_str)
                if isinstance(data, dict) and ("method" in data or "jsonrpc" in data):
                    status_code, resp_dict = process_jsonrpc(data)
                    if status_code == 204:
                        return Response(status_code=204, headers=MCP_CORS_HEADERS)
                    resp_headers = {**MCP_CORS_HEADERS, "Content-Type": "application/json"}
                    return JSONResponse(resp_dict, status_code=status_code, headers=resp_headers)
            except Exception:
                pass

        # If not direct JSON-RPC on /sse, could be session post to /messages
        if path.startswith("/messages") or path.startswith("/sse/messages"):
            return await sse_asgi_app(request.scope, request.receive, request._send)

        # Fallback for empty POST: return standard initialize capability
        return JSONResponse({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "antigravity_mesh", "version": "1.0.0"}
            }
        }, headers={**MCP_CORS_HEADERS, "Content-Type": "application/json"})

    # If GET:
    accept = headers.get("accept", "")
    if "text/event-stream" in accept or path in ["/sse", "/mcp/sse"]:
        return await sse_asgi_app(request.scope, request.receive, request._send)

    # Standard browser / GET probe
    return JSONResponse({
        "status": "ok",
        "service": "antigravity_mesh",
        "protocolVersion": "2024-11-05",
        "endpoints": {
            "sse": "/sse",
            "messages": "/messages",
            "direct_http": "/mcp"
        },
        "tools_count": len(TOOLS_DEFINITIONS)
    }, headers={**MCP_CORS_HEADERS, "Content-Type": "application/json"})

async def oauth_metadata_handler(request: Request):
    log_probe(request.method, request.url.path, dict(request.headers), "")
    return Response(
        status_code=404,
        content=b'{"error": "OAuth metadata not available; resource does not require OAuth"}',
        media_type="application/json",
        headers=MCP_CORS_HEADERS
    )

routes = [
    Route("/health", lambda r: JSONResponse({"status": "ok", "service": "antigravity-mcp-server"})),
    Route("/.well-known/oauth-protected-resource", oauth_metadata_handler, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/.well-known/oauth-protected-resource/{rest:path}", oauth_metadata_handler, methods=["GET", "HEAD", "OPTIONS"]),
    Route("/sse", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/mcp", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/mcp/", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/messages", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/messages/{rest:path}", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/", unified_mcp_handler, methods=["GET", "POST", "HEAD", "OPTIONS"]),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

app = Starlette(routes=routes, middleware=middleware)

if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", 8096))
    logger.info(f"Starting Universal Antigravity MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
