#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/server.py - Antigravity Mesh Standalone MCP Server (superset module).

This module serves two purposes:

1. Typed coordinator vitals helper used by the cloud/tunnel deployment:
   ``get_coordinator_vitals() -> NodeVitals`` (imported by ``agy_mcp_server.py``
   and by the typed integration / resilience tests).

2. A fully self-contained, stdlib-only MCP (Model Context Protocol) HTTP server
   for the local "standalone" mode, launched by ``install.sh`` as::

       python3 -m core.server --host 127.0.0.1 --port=8096

   Endpoints:
       GET  /                     -> health JSON
       GET  /health               -> health JSON
       GET  /sse, /mcp            -> text/event-stream (MCP SSE transport)
       POST /messages, /sse, /mcp -> JSON-RPC 2.0
       OPTIONS                    -> CORS preflight
       other paths                -> 404

SECURITY
--------
``--host`` defaults to loopback (``127.0.0.1``) and must NEVER be changed to
``0.0.0.0`` for a default install: this server can execute arbitrary shell
commands (``bash_exec``) and read arbitrary files (``read_file``). Binding it to
a public interface without ``--token`` (or ``MESH_TOKEN``) plus a firewall
exposes remote code execution to the whole network. If binding a public
interface is really required, always configure a token and restrict access at
the firewall level.

The module depends only on the Python standard library plus the pre-existing
local helpers ``core.schemas`` (pydantic) and ``core.vitals``. Both are treated
as optional so the server still starts under a bare interpreter (e.g.
``/usr/bin/python3`` without pydantic): ``get_coordinator_vitals()`` then
returns a light-weight stand-in object exposing the very same attributes.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("antigravity-mesh.standalone")

# ---------------------------------------------------------------------------
# Optional local dependencies.
#
# The MCP server must keep working when pydantic (and therefore
# ``core.schemas``) is not installed, which is the case for the bare system
# interpreter that systemd uses on a fresh standalone install. Importing them
# lazily/guarded keeps ``python3 -m core.server`` functional while preserving
# the exact ``NodeVitals`` return type whenever pydantic *is* available.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly by the environment
    from core.schemas import NodeVitals  # type: ignore
except Exception as _schemas_import_error:  # pragma: no cover
    NodeVitals = None  # type: ignore
    logger.debug("core.schemas unavailable (%s); using fallback vitals object", _schemas_import_error)

try:  # pragma: no cover - exercised implicitly by the environment
    from core.vitals import get_host_vitals as _core_get_host_vitals  # type: ignore
except Exception as _vitals_import_error:  # pragma: no cover
    _core_get_host_vitals = None  # type: ignore
    logger.debug("core.vitals unavailable (%s); using inline vitals collector", _vitals_import_error)


class _FallbackVitals(object):
    """Minimal stand-in for :class:`core.schemas.NodeVitals` without pydantic."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    def model_dump(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def model_dump_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, default=str, indent=indent)

    def dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def json(self, indent: Optional[int] = None) -> str:
        return self.model_dump_json(indent=indent)

    def __repr__(self) -> str:
        return "NodeVitals(%r)" % (self.__dict__,)


def _fallback_host_vitals() -> Dict[str, Any]:
    """Stdlib-only equivalent of ``core.vitals.get_host_vitals`` (last resort)."""
    system = platform.system()
    load = {"1m": 0.0, "5m": 0.0, "15m": 0.0}
    try:
        if hasattr(os, "getloadavg"):
            la = os.getloadavg()
            load = {"1m": round(la[0], 2), "5m": round(la[1], 2), "15m": round(la[2], 2)}
    except Exception:
        pass

    total_mb = 0.0
    free_mb = 0.0
    if system == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_mb = round(int(line.split()[1]) / 1024.0, 1)
                    elif line.startswith("MemAvailable:"):
                        free_mb = round(int(line.split()[1]) / 1024.0, 1)
        except Exception:
            pass

    used_mb = max(0.0, round(total_mb - free_mb, 1))
    used_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0
    try:
        du = shutil.disk_usage("C:\\" if system == "Windows" else "/")
        disk = {
            "total_gb": round(du.total / (1024 ** 3), 2),
            "used_gb": round(du.used / (1024 ** 3), 2),
            "free_gb": round(du.free / (1024 ** 3), 2),
            "used_pct": round((du.used / du.total) * 100.0, 1) if du.total > 0 else 0.0,
        }
    except Exception:
        disk = {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "used_pct": 0.0}

    return {
        "hostname": socket.gethostname(),
        "os": system,
        "cpu_load": load,
        "ram": {"total_mb": total_mb, "used_mb": used_mb, "free_mb": free_mb, "used_pct": used_pct},
        "disk": disk,
    }


def get_coordinator_vitals() -> "NodeVitals":
    """Collect this coordinator's typed vitals.

    Behaviour is unchanged from the tunnel deployment: it returns a
    ``core.schemas.NodeVitals`` instance whenever pydantic is importable. When
    pydantic is unavailable (bare system interpreter) an attribute-compatible
    fallback object is returned instead so the standalone server keeps running.
    """
    hostname = socket.gethostname()
    ip = "127.0.0.1"
    try:
        load = os.getloadavg()[0]
    except Exception:
        load = 0.0

    total_mb = 1
    used_mb = 0
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                k = parts[0].strip()
                v = parts[1].strip().split()[0]
                if k in ["MemTotal", "MemAvailable"]:
                    mem[k] = int(v) // 1024
        if "MemTotal" in mem and "MemAvailable" in mem:
            total_mb = mem["MemTotal"]
            avail_mb = mem["MemAvailable"]
            used_mb = max(0, total_mb - avail_mb)
    except Exception:
        pass

    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
    usage_pct = min(100.0, max(0.0, usage_pct))

    total_disk_gb = 1.0
    free_disk_gb = 0.0
    try:
        d = shutil.disk_usage("/")
        total_disk_gb = round(d.total / (1024 ** 3), 2)
        free_disk_gb = round(d.free / (1024 ** 3), 2)
    except Exception:
        pass

    uptime_str = "unknown"
    try:
        with open("/proc/uptime", "r") as f:
            up_secs = float(f.read().split()[0])
            hours = int(up_secs // 3600)
            mins = int((up_secs % 3600) // 60)
            uptime_str = f"{hours}h {mins}m"
    except Exception:
        pass

    fields = dict(
        hostname=hostname,
        ip=ip,
        is_online=True,
        cpu_load_1m=round(load, 2),
        ram_used_mb=used_mb,
        ram_total_mb=total_mb,
        ram_usage_pct=usage_pct,
        disk_free_gb=free_disk_gb,
        disk_total_gb=total_disk_gb,
        uptime=uptime_str,
        timestamp=datetime.now(timezone.utc),
    )
    if NodeVitals is not None:
        return NodeVitals(**fields)
    return _FallbackVitals(**fields)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Output helpers (mirror core/agent.py sanitize_output/truncate_output without
# importing it, because core.agent imports websockets at module import time).
# ---------------------------------------------------------------------------

# ~3000 tokens per turn, keeps MCP payloads small for remote AI clients.
MAX_OUTPUT_CHARS = 12000

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def sanitize_output(text: str) -> str:
    """Strip ANSI escape codes and non-printable control characters."""
    if not text:
        return ""
    text = ANSI_ESCAPE_RE.sub("", text)
    return "".join(
        ch for ch in text if ch in ("\n", "\r", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )


def truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Keep head+tail and insert an explicit ``... Output truncated ...`` notice."""
    if len(text) <= max_chars:
        return text
    head_size = int(max_chars * 0.7)   # 8400 chars
    tail_size = int(max_chars * 0.25)  # 3000 chars
    omitted = len(text) - (head_size + tail_size)
    omitted_lines = text[head_size:-tail_size].count("\n")
    notice = (
        f"\n\n... [Output truncated: {omitted} characters / ~{omitted_lines} lines hidden "
        f"to prevent context overflow. Use head, tail, grep, or write to file for full data] ...\n\n"
    )
    return text[:head_size] + notice + text[-tail_size:]


def _workspace_dir() -> str:
    """Base directory used to resolve relative paths (current working dir)."""
    return os.getcwd()


# ---------------------------------------------------------------------------
# Local tool implementations (semantics mirrored from core/agent.py).
# ---------------------------------------------------------------------------

def run_bash_sync(command: str, timeout: int = 120) -> Dict[str, Any]:
    """Execute *command* through the shell, returning stdout/stderr/exit_code/duration."""
    start = time.time()
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=_workspace_dir(),
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return {
            "stdout": truncate_output(sanitize_output(proc.stdout)),
            "stderr": truncate_output(sanitize_output(proc.stderr)),
            "exit_code": proc.returncode,
            "duration": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": (
                f"Command timed out after {timeout} seconds. Tip: For long background tasks, "
                "run with 'nohup ... > output.log 2>&1 &'."
            ),
            "exit_code": 124,
            "duration": round(time.time() - start, 3),
        }
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "duration": round(time.time() - start, 3),
        }


def list_dir_sync(path: str = "") -> Dict[str, Any]:
    """List files/directories at *path*, defaulting to the current workspace."""
    try:
        target = path.strip() if path else _workspace_dir()
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(_workspace_dir(), target))
        if not os.path.exists(target):
            return {"exit_code": 1, "stdout": "", "stderr": f"Path not found: {target}"}
        if os.path.isfile(target):
            return read_file_sync(target)

        entries = []
        for entry in sorted(os.scandir(target), key=lambda x: (not x.is_dir(), x.name.lower())):
            suffix = "/" if entry.is_dir() else ""
            size = entry.stat().st_size if entry.is_file() else 0
            entries.append(f"{entry.name}{suffix}" + (f" ({size} bytes)" if entry.is_file() else ""))

        listing = "\n".join(entries) if entries else "(empty directory)"
        return {
            "exit_code": 0,
            "stdout": f"Directory: {target} ({len(entries)} items):\n{listing}",
            "stderr": "",
        }
    except Exception as exc:
        return {"exit_code": 1, "stdout": "", "stderr": str(exc)}


def read_file_sync(path: str, start_line: int = 1, end_line: Optional[int] = None) -> Dict[str, Any]:
    """Read *path* with the ``%4d | `` line-numbered format used by the agent."""
    try:
        target = path.strip()
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(_workspace_dir(), target))
        if not os.path.exists(target):
            return {"exit_code": 1, "stdout": "", "stderr": f"File not found: {target}"}
        if os.path.isdir(target):
            return list_dir_sync(target)

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        s = max(1, start_line or 1) - 1
        e = min(len(lines), end_line) if end_line else len(lines)
        selected = lines[s:e]
        content = "".join(f"{idx + s + 1:4d} | {line}" for idx, line in enumerate(selected))
        header = f"File: {target} (lines {s + 1}-{e} of {len(lines)} total)\n" + ("-" * 60) + "\n"
        return {
            "exit_code": 0,
            "stdout": truncate_output(sanitize_output(header + content)),
            "stderr": "",
        }
    except Exception as exc:
        return {"exit_code": 1, "stdout": "", "stderr": str(exc)}


def _load_orchestration_skill() -> str:
    """Read the live orchestrator skill, with an inline Antigravity fallback."""
    skill_path = Path(__file__).resolve().parent.parent / "skills" / "orchestrator.md"
    try:
        if skill_path.exists():
            return skill_path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - filesystem race
        logger.warning("Could not read %s: %s", skill_path, exc)
    return (
        "# Antigravity Mesh Orchestrator Skill (Standalone)\n"
        "Mode: Local Standalone Server (antigravity_mesh).\n"
        "All commands execute directly on this host through the Antigravity Mesh MCP tools.\n"
        "Rules:\n"
        "1. Never ask the user to run commands manually; use bash_exec.\n"
        "2. Verify exit codes and stdout/stderr before continuing.\n"
        "3. On non-zero exit code, halt and report the error details.\n"
    )


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

# Metadata advertised through ``tools/list``. Order defines the listed order.
TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "system_vitals",
        "title": "System Vitals",
        "description": "Retrieve CPU, RAM, and Disk metrics of the local host.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bash_exec",
        "title": "Execute Bash Command",
        "description": "Execute a shell command directly on the local host (120s timeout).",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command to execute."}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_orchestration_skill",
        "title": "Get Orchestration Skill",
        "description": "Load the current Antigravity orchestration skill and rules.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_dir",
        "title": "List Directory",
        "description": "List files and directories at the given path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory or file path."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "title": "Read File",
        "description": "Read a text file, optionally restricted to a line range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read."},
                "start_line": {"type": "integer", "description": "First line (1-based)."},
                "end_line": {"type": "integer", "description": "Last line (inclusive)."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]

_REQUIRED_ARGS: Dict[str, Tuple[str, ...]] = {
    "bash_exec": ("command",),
    "list_dir": ("path",),
    "read_file": ("path",),
}


class _InvalidParams(Exception):
    """Raised internally when a ``tools/call`` argument fails validation."""


class AntigravityMeshServer(object):
    """Standalone MCP server: a registry of tools plus a JSON-RPC dispatcher."""

    name = "antigravity_mesh"
    version = "1.0.0"

    def __init__(self, name: str = "antigravity_mesh") -> None:
        self.name = name
        self.tools: Dict[str, Callable[..., Any]] = {}
        self._specs: Dict[str, Dict[str, Any]] = {
            spec["name"]: spec for spec in TOOL_DEFINITIONS
        }
        self._spec_order: List[str] = [spec["name"] for spec in TOOL_DEFINITIONS]
        self._register_default_tools()

    # -- tool registry ----------------------------------------------------
    def _register_default_tools(self) -> None:
        self.register_tool("system_vitals", self.system_vitals)
        self.register_tool("bash_exec", self.bash_exec)
        self.register_tool("get_orchestration_skill", self.get_orchestration_skill)
        self.register_tool("list_dir", self.list_dir)
        self.register_tool("read_file", self.read_file)

    def register_tool(self, name: str, func: Callable[..., Any]) -> None:
        self.tools[name] = func
        if name not in self._specs:
            self._specs[name] = {
                "name": name,
                "title": name,
                "description": "Custom tool.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        if name not in self._spec_order:
            self._spec_order.append(name)

    def list_tool_specs(self) -> List[Dict[str, Any]]:
        specs = []
        for name in self._spec_order:
            if name in self.tools:
                specs.append(self._specs[name])
        return specs

    # -- tool implementations --------------------------------------------
    def system_vitals(self) -> Dict[str, Any]:
        if _core_get_host_vitals is not None:
            return _core_get_host_vitals()
        return _fallback_host_vitals()

    def bash_exec(self, command: str) -> Dict[str, Any]:
        return run_bash_sync(command, timeout=120)

    def get_orchestration_skill(self) -> str:
        return _load_orchestration_skill()

    def list_dir(self, path: str = "") -> Dict[str, Any]:
        return list_dir_sync(path)

    def read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> Dict[str, Any]:
        return read_file_sync(path, start_line=start_line, end_line=end_line)

    # -- JSON-RPC dispatch ------------------------------------------------
    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def handle_jsonrpc(self, body: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Handle one JSON-RPC message.

        Returns ``(http_status, response_or_None)``. ``(204, None)`` means the
        message was a notification and no body must be sent.
        """
        try:
            return self._handle_jsonrpc(body)
        except Exception:  # never let an unexpected failure drop the connection
            logger.exception("Unhandled error while processing JSON-RPC request")
            return 200, self._error(None, -32603, "Internal error")

    def _handle_jsonrpc(self, body: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
        # -- parse stage ---------------------------------------------------
        if isinstance(body, (bytes, bytearray)):
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception:
                return 200, self._error(None, -32700, "Parse error")
        elif isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                return 200, self._error(None, -32700, "Parse error")

        # -- structural stage ---------------------------------------------
        if isinstance(body, list):
            # Batches are explicitly unsupported, but the response is a normal
            # JSON-RPC error and the connection stays alive.
            return 200, self._error(None, -32600, "Invalid Request: batch requests are not supported")
        if not isinstance(body, dict):
            return 200, self._error(None, -32600, "Invalid Request")

        # ONLY an absent "id" means notification; an explicit "id": null is a
        # real request that must receive a response.
        if "id" not in body:
            return 204, None

        req_id = body.get("id")
        method = body.get("method")
        if not isinstance(method, str) or not method:
            return 200, self._error(req_id, -32600, "Invalid Request: missing or invalid method")

        params = body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return 200, self._error(req_id, -32602, "Invalid params: 'params' must be an object")

        # -- method dispatch ----------------------------------------------
        if method == "initialize":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": "Standalone Local Antigravity Mesh Server.",
                },
            }

        if method in ("notifications/initialized", "notifications/cancelled"):
            # Reached only when the client (incorrectly) sent an id: acknowledge.
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "ping":
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.list_tool_specs()},
            }

        if method == "tools/call":
            return self._handle_tools_call(req_id, params)

        if method == "prompts/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": [
                        {"name": "antigravity-orchestrator", "description": "Local Standalone Orchestrator"}
                    ]
                },
            }

        if method == "prompts/get":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "Antigravity Orchestrator Persona",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": self.get_orchestration_skill()},
                        }
                    ],
                },
            }

        if method == "resources/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": "resource://skills/orchestrator.md",
                            "name": "orchestrator.md",
                            "mimeType": "text/markdown",
                        }
                    ]
                },
            }

        if method == "resources/read":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "contents": [
                        {
                            "uri": params.get("uri", ""),
                            "mimeType": "text/markdown",
                            "text": self.get_orchestration_skill(),
                        }
                    ]
                },
            }

        return 200, self._error(req_id, -32601, "Method not found: %s" % method)

    # -- tools/call -------------------------------------------------------
    def _handle_tools_call(self, req_id: Any, params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return 200, self._error(req_id, -32602, "Invalid params: 'name' is required")
        if name not in self.tools:
            return 200, self._error(req_id, -32601, "Unknown tool: %s" % name)

        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return 200, self._error(req_id, -32602, "Invalid params: 'arguments' must be an object")

        try:
            self._validate_arguments(name, arguments)
        except _InvalidParams as exc:
            return 200, self._error(req_id, -32602, "Invalid params: %s" % exc)

        try:
            value = self._invoke_tool(name, arguments)
        except Exception as exc:
            # A failing tool is an MCP tool error, NOT a JSON-RPC error.
            logger.exception("Tool %r raised", name)
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": "Tool execution failed: %s: %s" % (type(exc).__name__, exc)}
                    ],
                    "isError": True,
                },
            }

        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": self._result_to_text(value)}],
                "isError": self._is_error_result(value),
            },
        }

    def _validate_arguments(self, name: str, arguments: Dict[str, Any]) -> None:
        for key in _REQUIRED_ARGS.get(name, ()):  # required arguments
            if key not in arguments or arguments[key] is None:
                raise _InvalidParams("'%s' is required" % key)
        if name == "bash_exec":
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                raise _InvalidParams("'command' is required and must be a non-empty string")
        if name in ("list_dir", "read_file") and not isinstance(arguments.get("path"), str):
            raise _InvalidParams("'path' must be a string")
        if name == "read_file":
            for key in ("start_line", "end_line"):
                value = arguments.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise _InvalidParams("'%s' must be an integer" % key)

    def _invoke_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        func = self.tools[name]
        if name == "bash_exec":
            return func(command=arguments["command"])
        if name == "list_dir":
            return func(path=arguments["path"])
        if name == "read_file":
            return func(
                path=arguments["path"],
                start_line=arguments.get("start_line") or 1,
                end_line=arguments.get("end_line"),
            )
        return func(**arguments)

    @staticmethod
    def _is_error_result(value: Any) -> bool:
        if isinstance(value, dict):
            if "exit_code" in value:
                return value.get("exit_code") not in (0, None)
            if "error" in value and value.get("error"):
                return True
        return False

    @staticmethod
    def _result_to_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)


def create_mcp_server() -> AntigravityMeshServer:
    """Factory used by the HTTP handler and by the test-suite."""
    return AntigravityMeshServer(name="antigravity_mesh")


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

# Endpoints that never require a token (liveness/readiness probes only).
_PUBLIC_PATHS = ("/", "/health")
# Endpoints accepting JSON-RPC POSTs.
_JSONRPC_POST_PATHS = ("/messages", "/sse", "/mcp")
# Matches a ?token=... / &token=... query value so it can be masked in logs.
_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s\"]*", re.IGNORECASE)


class StandaloneMCPHandler(BaseHTTPRequestHandler):
    """Threaded HTTP handler exposing the MCP endpoints of the standalone server."""

    server_version = "AntigravityMeshStandalone/1.0"

    # Bound slow/stalled clients (e.g. a Content-Length larger than the body it
    # sends) so a single request cannot pin a handler thread forever.
    timeout = 30

    # Set by ``run_standalone_server`` (class-level so each request thread sees it).
    server_mcp: Optional[AntigravityMeshServer] = None
    auth_token: str = ""

    # -- plumbing ---------------------------------------------------------
    def _clean_path(self) -> str:
        try:
            path = urlparse(self.path).path
        except Exception:
            path = self.path or "/"
        if not path:
            path = "/"
        return path

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _send_headers(self, status: int, content_type: str = "application/json") -> None:
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", content_type)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_headers(status, "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_unauthorized(self) -> None:
        body = json.dumps(
            {"error": "unauthorized", "message": "Missing or invalid token"}
        ).encode("utf-8")
        self.send_response(401)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", 'Bearer realm="antigravity-mesh"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _provided_token(self) -> Optional[str]:
        try:
            query = parse_qs(urlparse(self.path).query)
        except Exception:
            query = {}
        token = query.get("token", [None])[0]
        if token:
            return str(token)
        auth = self.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _token_valid(self) -> bool:
        """True when auth is disabled, or the supplied token matches.

        Unlike ``_authorized()`` this ignores the public-path exemption and is
        used by the liveness endpoints to decide how much detail to disclose.
        """
        token = self.auth_token or ""
        if not token:
            return True  # auth disabled (loopback-only default)
        try:
            provided = self._provided_token()
            if not provided:
                return False
            # Compare raw UTF-8 bytes: hmac.compare_digest() rejects str inputs
            # containing non-ASCII characters, which would otherwise raise a
            # TypeError and drop the connection on a malformed token.
            # surrogateescape keeps any undecodable byte round-trippable.
            return hmac.compare_digest(
                str(provided).encode("utf-8", "surrogateescape"),
                str(token).encode("utf-8", "surrogateescape"),
            )
        except Exception:
            # Defensive: authentication must never crash the handler.
            return False

    def _authorized(self) -> bool:
        if self._clean_path() in _PUBLIC_PATHS:
            return True
        return self._token_valid()

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        try:
            return self.rfile.read(length)
        except Exception:
            return b""

    # -- HTTP verbs -------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        # CORS preflight is unauthenticated: it executes nothing and must
        # succeed even when a token is configured, otherwise browsers cannot
        # discover the endpoint/headers.
        self._send_headers(200, "text/plain")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        if not self._authorized():
            self._send_unauthorized()
            return
        path = self._clean_path()
        if path in _PUBLIC_PATHS:
            self._send_headers(200, "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path in ("/sse", "/mcp"):
            self._send_headers(200, "text/event-stream")
            self.end_headers()
        else:
            self._send_headers(404, "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if not self._authorized():
            self._send_unauthorized()
            return

        path = self._clean_path()
        if path in _PUBLIC_PATHS:
            # Liveness is public so the installer health poll keeps working, but
            # host vitals are only disclosed to an authenticated caller when a
            # token is configured.
            payload: Dict[str, Any] = {"status": "healthy", "mode": "standalone"}
            if self._token_valid():
                payload["vitals"] = self.server_mcp.system_vitals() if self.server_mcp else {}
            self._send_json(200, payload)
        elif path in ("/sse", "/mcp"):
            self._stream_sse()
        elif path.startswith("/.well-known/oauth-protected-resource"):
            self._send_json(404, {"error": "not_found"})
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if not self._authorized():
            self._send_unauthorized()
            return

        path = self._clean_path()
        if path not in _JSONRPC_POST_PATHS:
            self._send_json(404, {"error": "not_found", "path": path})
            return

        raw_body = self._read_body()
        if self.server_mcp is None:
            self._send_json(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}})
            return

        status, response = self.server_mcp.handle_jsonrpc(raw_body)
        if status == 204 or response is None:
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
            return
        if isinstance(response, (dict, list)):
            self._send_json(status, response)
        else:  # defensive: never drop the connection on a malformed handler result
            self._send_json(500, {"error": "internal_error"})

    def _stream_sse(self) -> None:
        self._send_headers(200, "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
            self.wfile.flush()
            while True:
                time.sleep(15)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def log_request(self, code: Any = "-", size: Any = "-") -> None:  # noqa: N802
        # Log only the request path: the query string can carry ?token=<secret>
        # and must never reach journald / standalone.log.
        requestline = self.requestline or ""
        parts = requestline.split(" ")
        if len(parts) >= 2 and "?" in parts[1]:
            parts[1] = parts[1].split("?", 1)[0]
            requestline = " ".join(parts)
        self.log_message(
            '"%s" %s %s', requestline, str(getattr(code, "value", code)), str(size)
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        try:
            message = fmt % args
        except Exception:
            message = str(fmt)
        # Defense in depth: mask any token=... value that still reaches a log.
        message = _TOKEN_QUERY_RE.sub(r"\1***", message)
        logger.info("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), message)


def run_standalone_server(host: str = "127.0.0.1", port: int = 8096, token: Optional[str] = None) -> None:
    """Start the blocking standalone MCP HTTP server."""
    if token is None:
        token = os.environ.get("MESH_TOKEN", "")
    mcp = create_mcp_server()
    StandaloneMCPHandler.server_mcp = mcp
    StandaloneMCPHandler.auth_token = token or ""

    httpd = ThreadingHTTPServer((host, int(port)), StandaloneMCPHandler)
    httpd.daemon_threads = True
    logger.info(
        "Antigravity Mesh standalone MCP server listening on http://%s:%s/sse (auth: %s)",
        host,
        port,
        "enabled" if token else "disabled",
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        logger.info("Standalone server stopped.")
    finally:
        httpd.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="core.server",
        description="Antigravity Mesh Standalone MCP Server (localhost by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "SECURITY: --host defaults to 127.0.0.1 on purpose. This server executes\n"
            "arbitrary shell commands and reads arbitrary files. Never bind 0.0.0.0\n"
            "without setting --token/MESH_TOKEN and a firewall."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Defaults to loopback 127.0.0.1 for security; do NOT use 0.0.0.0 "
        "unless a token and firewall are configured.",
    )
    parser.add_argument("--port", type=int, default=8096, help="TCP port to listen on (default: 8096).")
    parser.add_argument(
        "--token",
        default=os.environ.get("MESH_TOKEN", ""),
        help="Shared auth token. Defaults to $MESH_TOKEN; empty disables auth (localhost-only).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )
    logger.info(
        "Starting standalone MCP server on %s:%s (bind address is %s)",
        args.host,
        args.port,
        "loopback" if args.host in ("127.0.0.1", "localhost", "::1") else args.host,
    )
    if args.host == "0.0.0.0" and not args.token:
        logger.warning(
            "Bound to 0.0.0.0 without a token: anyone on the network can execute shell commands. "
            "Use --host 127.0.0.1 or configure --token."
        )
    run_standalone_server(host=args.host, port=args.port, token=args.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
