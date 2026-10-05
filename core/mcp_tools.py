#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/mcp_tools.py - shared Antigravity Mesh tool implementation.

This module is the single place that contains tool *logic*.  Both consumers
(the asyncio tunnel node ``core/agent.py`` and the standalone HTTP server
``core/server.py``) are thin transports that only forward to
:func:`call_tool`.

Design rules (see the project contract):

* standard library only, Python 3.8+;
* :func:`call_tool` never raises - every failure is returned as
  ``{"error": "<human readable>"}``;
* output pagination never throws the middle away: callers advance a ``cursor``;
* large outputs are spooled to a file instead of being dropped;
* every mutating operation is checked against ``read_only`` / ``write_roots``.

Public interface::

    TOOLS: list[dict]
    configure(**kwargs) -> None
    call_tool(name: str, args: dict) -> dict
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import hashlib
import json
import os
import re
import shutil
import signal as _signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional dependency: the host vitals collector.  Kept optional so the module
# stays importable under a bare interpreter.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly by the environment
    from core.vitals import get_host_vitals as _core_get_host_vitals  # type: ignore
except Exception:  # pragma: no cover
    _core_get_host_vitals = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration / module state
# ---------------------------------------------------------------------------

_DEFAULT_MAX_OUTPUT_CHARS = 50000
_DEFAULT_JOBS_DIR = os.path.join("~", ".cache", "antigravity-mesh", "jobs")

#: bash_exec never blocks a transport thread for longer than this.
MAX_TIMEOUT_SEC = 120
#: job_output caps its wait so it stays well inside the 28s gateway limit.
MAX_WAIT_MS = 20000
#: run_job refuses to start more than this many concurrent jobs.
MAX_RUNNING_JOBS = 32
#: Per-job output written to disk before a marker is appended and growth stops.
JOB_OUTPUT_CAP = 64 * 1024 * 1024
#: bash_exec spools its complete output to disk above this size.
SPOOL_THRESHOLD = 2 * 1024 * 1024
#: Hard ceiling for glob_find result sets.
GLOB_MAX_RESULTS = 10000

_SKIP_DIRS = frozenset((".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"))

_WORKSPACE = os.path.realpath(os.getcwd())
_READ_ONLY = False
_WRITE_ROOTS: Optional[List[str]] = None
_JOBS_DIR = os.path.realpath(os.path.expanduser(_DEFAULT_JOBS_DIR))
_MAX_OUTPUT_CHARS = _DEFAULT_MAX_OUTPUT_CHARS

_META_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}


def _split_roots(value: Any) -> Optional[List[str]]:
    """Normalise ``write_roots`` (list/tuple/pathsep string) into real paths."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p for p in value.split(os.pathsep) if p.strip()]
        if len(parts) == 1 and os.pathsep not in value:
            # A single root with no separator: still accept it verbatim.
            parts = [value]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(p) for p in value if str(p).strip()]
    else:
        parts = [str(value)]
    roots: List[str] = []
    for part in parts:
        if not str(part).strip():
            continue
        roots.append(os.path.realpath(os.path.expanduser(str(part).strip())))
    return roots or None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def configure(**kwargs: Any) -> None:
    """(Re)configure the module.  All keys are optional.

    Recognised keys: ``workspace``, ``read_only``, ``allow_write``,
    ``write_roots``, ``jobs_dir``, ``max_output_chars``.
    """
    global _WORKSPACE, _READ_ONLY, _WRITE_ROOTS, _JOBS_DIR, _MAX_OUTPUT_CHARS

    if kwargs.get("workspace"):
        _WORKSPACE = os.path.realpath(os.path.expanduser(str(kwargs["workspace"])))
    if "read_only" in kwargs and kwargs["read_only"] is not None:
        _READ_ONLY = _parse_bool(kwargs["read_only"])
    elif "allow_write" in kwargs and kwargs["allow_write"] is not None:
        _READ_ONLY = not _parse_bool(kwargs["allow_write"])
    if "write_roots" in kwargs:
        _WRITE_ROOTS = _split_roots(kwargs["write_roots"])
    if kwargs.get("jobs_dir"):
        _JOBS_DIR = os.path.realpath(os.path.expanduser(str(kwargs["jobs_dir"])))
    if kwargs.get("max_output_chars") is not None:
        try:
            value = int(kwargs["max_output_chars"])
            if value > 0:
                _MAX_OUTPUT_CHARS = value
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(path: Any) -> str:
    """expanduser -> relative to workspace -> realpath."""
    raw = "" if path is None else str(path)
    raw = raw.strip()
    if not raw:
        raw = _WORKSPACE
    raw = os.path.expanduser(raw)
    if not os.path.isabs(raw):
        raw = os.path.join(_WORKSPACE, raw)
    return os.path.realpath(raw)


def _within_write_roots(target: str) -> bool:
    if _WRITE_ROOTS is None:
        return True
    candidate = os.path.realpath(target)
    for root in _WRITE_ROOTS:
        root = os.path.realpath(root)
        if candidate == root:
            return True
        if candidate.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _write_guard(target: str) -> Optional[str]:
    """Return an error string when writing to *target* is not permitted."""
    if _READ_ONLY:
        return "server is in read-only mode"
    if not _within_write_roots(target):
        return "path is outside allowed write roots: %s" % target
    return None


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < low:
        return low
    if result > high:
        return high
    return result


def _max_chars(value: Any) -> int:
    if value is None:
        return _MAX_OUTPUT_CHARS
    try:
        result = int(value)
    except (TypeError, ValueError):
        return _MAX_OUTPUT_CHARS
    if result < 1:
        return 1
    if result > 50 * 1024 * 1024:
        return 50 * 1024 * 1024
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize_output(text: str) -> str:
    """Strip ANSI escapes and non-printable control characters."""
    if not text:
        return ""
    text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    return "".join(
        ch for ch in text if ch in ("\n", "\r", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )


def _paginate(full: str, cursor: Any, max_chars: int) -> Tuple[str, bool, Optional[int]]:
    """Return ``(chunk, truncated, next_cursor)`` for a character offset cursor."""
    offset = max(0, _safe_int(cursor, 0))
    total = len(full)
    if offset >= total:
        return "", False, None
    end = offset + max_chars
    chunk = full[offset:end]
    if end < total:
        return chunk, True, end
    return chunk, False, None


def _atomic_write_bytes(target: str, data: bytes, mode: Optional[int] = None) -> None:
    """Write *data* to *target* atomically (tmp file in the same dir + replace).

    ``mkstemp`` creates the temporary file with mode 0600, so the mode has to be
    set explicitly or an overwrite would silently strip a file's permissions
    (e.g. make a launcher script non-executable). Precedence:
    explicit ``mode`` > the mode of the file being replaced > 0644 for new files.
    """
    parent = os.path.dirname(target) or "."
    existing_mode = None
    if mode is None:
        try:
            existing_mode = os.stat(target).st_mode & 0o7777
        except OSError:
            existing_mode = None
    fd, tmp = tempfile.mkstemp(prefix=".mcp-tmp-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        elif existing_mode is not None:
            os.chmod(tmp, existing_mode)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_mode(mode: Any) -> Optional[int]:
    if mode is None:
        return None
    if isinstance(mode, bool):
        raise ValueError("invalid mode")
    if isinstance(mode, int):
        return mode
    text = str(mode).strip()
    if not text:
        return None
    return int(text, 8)


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(path, data)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _fallback_host_vitals() -> Dict[str, Any]:
    """Stdlib-only vitals collector used when core.vitals is unavailable."""
    import platform

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
    try:
        with open("/proc/meminfo", "r") as handle:
            for line in handle:
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


def _skill_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "orchestrator.md")


def _load_orchestration_skill() -> str:
    try:
        with open(_skill_path(), "r", encoding="utf-8") as handle:
            content = handle.read()
        if content.strip():
            return content
    except Exception:
        pass
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
# Output spooling
# ---------------------------------------------------------------------------

def _outputs_dir() -> str:
    path = os.path.join(_JOBS_DIR, "outputs")
    os.makedirs(path, exist_ok=True)
    return path


def _spool_output(text: str) -> Optional[str]:
    """Persist a large text blob and return its absolute path (best effort)."""
    try:
        path = os.path.join(_outputs_dir(), "%s.out" % uuid.uuid4().hex)
        _atomic_write_bytes(path, text.encode("utf-8", "replace"))
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tool 1-3: legacy tools (kept as-is for compatibility)
# ---------------------------------------------------------------------------

def _tool_mesh_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Report that this node is reachable, with hard evidence.

    The answer exists because a model may otherwise claim the host is offline or
    invent a reason. Every field here was just measured on the node: if this text
    arrives at all, the agent and the tunnel are working by definition.
    """
    import datetime
    import socket as _socket

    now = datetime.datetime.now().astimezone()
    uptime_s = None
    try:
        with open("/proc/uptime", "r") as handle:
            uptime_s = float(handle.read().split()[0])
    except Exception:
        pass
    try:
        hostname = _socket.gethostname()
    except Exception:
        hostname = "unknown"

    # The pid and the process start prove the agent is the one answering.
    agent_pid = os.getpid()
    payload = {
        "status": "online",
        "reachable": True,
        "message": ("This node IS reachable: the request that produced this answer was executed "
                    "on the host by the mesh agent. If a previous attempt reported the node as "
                    "offline, that report was wrong - retry the operation instead of concluding "
                    "the service is down."),
        "hostname": hostname,
        "agent_pid": agent_pid,
        "checked_at": now.isoformat(timespec="seconds"),
        "node_utc_offset": now.strftime("%z"),
        "workspace": _WORKSPACE,
        "pid_namespace": os.uname().nodename if hasattr(os, "uname") else hostname,
    }
    if uptime_s is not None:
        payload["host_uptime_seconds"] = int(uptime_s)
        payload["host_uptime_human"] = "%dh %dm" % (int(uptime_s // 3600), int((uptime_s % 3600) // 60))
    return payload


def _tool_system_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """A one-call summary of the host.

    Exploratory agents spend several tool calls discovering the same basics (OS,
    desktop, home, disks). Gemini's client allows only a handful of calls per
    turn, so this answers them in one round trip.
    """
    import platform
    import socket as _socket
    import subprocess as _sp

    def run(cmd: str) -> str:
        try:
            out = _sp.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
            return (out.stdout or out.stderr).strip()
        except Exception:
            return ""

    info: Dict[str, Any] = {
        "hostname": _socket.gethostname(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "home": os.path.expanduser("~"),
        "cwd": os.getcwd(),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "",
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "shell": os.environ.get("SHELL", ""),
        "disks": run("df -h --output=target,size,used,avail,pcent 2>/dev/null | head -8"),
        "memory": run("free -h 2>/dev/null | head -3"),
        "load": run("uptime"),
        "top_processes": run("ps -eo comm,%mem --sort=-%mem 2>/dev/null | head -6"),
    }
    # Current wallpaper, the usual first question for a desktop node.
    # Current wallpaper. KDE keeps the last applied value in the appletsrc as
    # `Image=<path-or-dir>` (sometimes with a file:// prefix); GNOME uses
    # gsettings. A directory means a wallpaper package, so resolve the image
    # inside it the same way Plasma does.
    wallpaper = ""
    kde_cfg = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")
    if os.path.isfile(kde_cfg):
        try:
            with open(kde_cfg, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("Image="):
                        wallpaper = line.strip()[len("Image="):]
        except Exception:
            wallpaper = ""
    wallpaper = wallpaper.replace("file://", "")
    if not wallpaper:
        gnome = run("gsettings get org.gnome.desktop.background picture-uri 2>/dev/null")
        if gnome and "file://" in gnome:
            wallpaper = gnome.split("file://", 1)[1].strip().strip("'\"")
    if wallpaper:
        if os.path.isdir(wallpaper):
            # A wallpaper package holds many resolutions and formats (png, jpg,
            # webp, avif); pick the largest image inside it.
            best, best_size = "", 0
            for root, _dirs, files in os.walk(wallpaper):
                for name in files:
                    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".avif")):
                        path = os.path.join(root, name)
                        try:
                            size = os.path.getsize(path)
                        except OSError:
                            continue
                        if size > best_size:
                            best, best_size = path, size
            wallpaper = best or wallpaper
        info["wallpaper"] = wallpaper
        info["wallpaper_exists"] = os.path.isfile(wallpaper)
    return info


def _tool_system_vitals(args: Dict[str, Any]) -> Any:
    try:
        if _core_get_host_vitals is not None:
            return _core_get_host_vitals()
    except Exception:
        pass
    return _fallback_host_vitals()


def _tool_get_orchestration_skill(args: Dict[str, Any]) -> Any:
    return _load_orchestration_skill()


def _tool_list_dir(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = args.get("path", "") or ""
        target = _resolve(raw)
        if not os.path.exists(target):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": ("[TOOL ERROR] Path not found: %s -- this path does NOT exist. "
                           "Never report it as real; check the parent directory first." % target),
            }
        if os.path.isfile(target):
            return _tool_read_file({"path": target})

        entries = []
        for entry in sorted(os.scandir(target), key=lambda x: (not x.is_dir(), x.name.lower())):
            suffix = "/" if entry.is_dir() else ""
            size = entry.stat().st_size if entry.is_file() else 0
            entries.append(
                "%s%s" % (entry.name, suffix)
                + (" (%s bytes)" % size if entry.is_file() else "")
            )
        listing = "\n".join(entries) if entries else "(empty directory)"
        return {
            "exit_code": 0,
            # [VERIFIED BY TOOL] tells the model this listing came from the real
            # filesystem in this call: only such data may be reported as fact.
            "stdout": "[VERIFIED BY TOOL] Directory: %s (%d items):\n%s" % (target, len(entries), listing),
            "stderr": "",
        }
    except Exception as exc:
        return {"error": "list_dir failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool 4: bash_exec
# ---------------------------------------------------------------------------

def _run_bash(command: str, timeout: int) -> Tuple[str, str, int, float]:
    started = time.time()
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=_WORKSPACE if os.path.isdir(_WORKSPACE) else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return proc.stdout or "", proc.stderr or "", proc.returncode, round(time.time() - started, 3)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        err = (err + "\n" if err else "") + (
            "Command timed out after %d seconds. Tip: for long background tasks use "
            "'nohup ... > output.log 2>&1 &' or run_job." % timeout
        )
        return out, err, 124, round(time.time() - started, 3)
    except Exception as exc:
        return "", str(exc), 1, round(time.time() - started, 3)


def _tool_bash_exec(args: Dict[str, Any]) -> Dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"error": "command is required and must be a non-empty string"}
    timeout = _clamp_int(args.get("timeout_sec"), 1, MAX_TIMEOUT_SEC, 25)
    max_chars = _max_chars(args.get("max_chars"))
    cursor = max(0, _safe_int(args.get("cursor"), 0))

    stdout_raw, stderr_raw, exit_code, duration = _run_bash(command, timeout)
    out = _sanitize_output(stdout_raw)
    err = _sanitize_output(stderr_raw)

    full = out + err
    saved_to: Optional[str] = None
    if len(full.encode("utf-8", "replace")) > SPOOL_THRESHOLD:
        saved_to = _spool_output(full)

    out_len = len(out)
    offset = min(cursor, len(full))
    end = offset + max_chars
    if offset < out_len:
        stdout_part = full[offset:min(end, out_len)]
    else:
        stdout_part = ""
    if end > out_len:
        stderr_part = full[max(offset, out_len):end]
    else:
        stderr_part = ""
    next_cursor = end if end < len(full) else None

    return {
        "stdout": stdout_part,
        "stderr": stderr_part,
        "exit_code": exit_code,
        "duration": duration,
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
        "saved_to": saved_to,
    }


# ---------------------------------------------------------------------------
# Tool 5: read_file
# ---------------------------------------------------------------------------

def _tool_read_file(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"error": "path is required"}
        target = _resolve(path)
        if not os.path.exists(target):
            return {"error": "[TOOL ERROR] File not found: %s -- does not exist, do not report it as real." % target}
        if os.path.isdir(target):
            return _tool_list_dir({"path": target})

        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()

        start_line = max(1, _safe_int(args.get("start_line"), 1) or 1)
        end_line = args.get("end_line")
        end = min(len(lines), _safe_int(end_line, len(lines))) if end_line else len(lines)
        selected = lines[start_line - 1:end]
        width = max(4, len(str(end)))  # keep the column stable for files >= 10000 lines
        content = "".join(
            "%*d | %s" % (width, idx + start_line, line) for idx, line in enumerate(selected)
        )
        header = "[VERIFIED BY TOOL] File: %s (lines %d-%d of %d total)\n%s\n" % (
            target,
            start_line,
            end,
            len(lines),
            "-" * 60,
        )
        full = _sanitize_output(header + content)

        max_chars = _max_chars(args.get("max_chars"))
        cursor = max(0, _safe_int(args.get("cursor"), 0))
        chunk, truncated, next_cursor = _paginate(full, cursor, max_chars)
        # No marker is appended inside the page: the text must stay byte-exact so
        # a client can stitch pages using next_cursor. Callers surface that value.

        return {
            "stdout": chunk,
            "stderr": "",
            "exit_code": 0,
            "truncated": truncated,
            "next_cursor": next_cursor,
        }
    except Exception as exc:
        return {"error": "read_file failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool 6: write_file
# ---------------------------------------------------------------------------

def _tool_write_file(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"error": "path is required"}
        if "content" not in args or args.get("content") is None:
            return {"error": "content is required"}
        content = args.get("content")
        if not isinstance(content, str):
            content = str(content)

        target = _resolve(path)
        guard = _write_guard(target)
        if guard:
            return {"error": guard}

        create_dirs = args.get("create_dirs", True)
        parent = os.path.dirname(target) or "."
        if create_dirs and parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        if not os.path.isdir(parent):
            return {"error": "parent directory does not exist: %s" % parent}

        try:
            mode = _parse_mode(args.get("mode"))
        except Exception:
            return {"error": "invalid mode: %r" % (args.get("mode"),)}

        data = content.encode("utf-8", "replace")
        _atomic_write_bytes(target, data, mode=mode)
        return {"ok": True, "path": target, "bytes": len(data), "sha256": _sha256_hex(data)}
    except Exception as exc:
        return {"error": "write_file failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool 7: edit_file
# ---------------------------------------------------------------------------

def _tool_edit_file(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"error": "path is required"}
        old_string = args.get("old_string")
        if not isinstance(old_string, str) or old_string == "":
            return {"error": "old_string is required and must be a non-empty string"}
        new_string = args.get("new_string")
        if not isinstance(new_string, str):
            return {"error": "new_string is required and must be a string"}

        target = _resolve(path)
        guard = _write_guard(target)
        if guard:
            return {"error": guard}
        if not os.path.isfile(target):
            return {"error": "[TOOL ERROR] File not found: %s -- does not exist, do not report it as real." % target}

        with open(target, "rb") as handle:
            raw = handle.read()
        current_sha = _sha256_hex(raw)

        expected = args.get("expected_sha256")
        if expected:
            if str(expected).strip().lower() != current_sha:
                return {"error": "file changed since read (sha256 mismatch)"}

        # Byte-level replacement: decoding with errors="replace" would silently
        # corrupt non-UTF-8 files (0xff -> U+FFFD). Matching the raw bytes keeps
        # the edit surgical for any encoding while old/new stay valid UTF-8.
        try:
            old_bytes = old_string.encode("utf-8")
            new_bytes = new_string.encode("utf-8")
        except UnicodeEncodeError as exc:
            return {"error": "old_string/new_string must be valid UTF-8 text: %s" % exc}
        count = raw.count(old_bytes)
        if count == 0:
            return {"error": "old_string not found"}
        replace_all = _parse_bool(args.get("replace_all"))
        if count > 1 and not replace_all:
            return {"error": "old_string is not unique (%d matches)" % count}

        replacements = count if replace_all else 1
        if replace_all:
            data = raw.replace(old_bytes, new_bytes)
        else:
            data = raw.replace(old_bytes, new_bytes, 1)

        _atomic_write_bytes(target, data)
        return {
            "ok": True,
            "path": target,
            "replacements": replacements,
            "sha256": _sha256_hex(data),
        }
    except Exception as exc:
        return {"error": "edit_file failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool 8: grep_search
# ---------------------------------------------------------------------------

def _is_probably_binary(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\x00" in handle.read(4096)
    except Exception:
        return True


def _iter_files(base: str):
    if os.path.isfile(base):
        yield base
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            yield os.path.join(root, name)


def _tool_grep_search(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or pattern == "":
            return {"error": "pattern is required and must be a non-empty string"}
        base = _resolve(args.get("path") or ".")
        if not os.path.exists(base):
            return {"error": "Path not found: %s" % base}

        limit = _clamp_int(args.get("limit"), 1, 1000, 200)
        ignore_case = _parse_bool(args.get("ignore_case"))
        fixed = _parse_bool(args.get("fixed"))
        try:
            context = max(0, min(20, _safe_int(args.get("context"), 0)))
        except Exception:
            context = 0
        glob_filter = args.get("glob")
        if glob_filter is not None and not isinstance(glob_filter, str):
            return {"error": "glob must be a string"}

        flags = re.IGNORECASE if ignore_case else 0
        if fixed:
            needle = pattern.lower() if ignore_case else pattern
        else:
            try:
                regex = re.compile(pattern, flags)
            except re.error as exc:
                return {"error": "invalid regular expression: %s" % exc}

        matches: List[Dict[str, Any]] = []
        truncated = False
        for file_path in _iter_files(base):
            if glob_filter and not fnmatch.fnmatch(os.path.basename(file_path), glob_filter):
                continue
            if _is_probably_binary(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()
            except Exception:
                continue

            hit_lines = []
            for idx, line in enumerate(lines):
                found = (needle in (line.lower() if ignore_case else line)) if fixed else bool(regex.search(line))
                if found:
                    hit_lines.append(idx)
            if not hit_lines:
                continue

            emitted = set()
            for idx in hit_lines:
                first = max(0, idx - context)
                last = min(len(lines) - 1, idx + context)
                for lineno in range(first, last + 1):
                    if lineno in emitted:
                        continue
                    if len(matches) >= limit:
                        truncated = True
                        break
                    emitted.add(lineno)
                    matches.append(
                        {"path": os.path.realpath(file_path), "line": lineno + 1, "text": lines[lineno]}
                    )
                if truncated:
                    break
            if truncated:
                break

        return {"matches": matches, "count": len(matches), "truncated": truncated,
                "note": "[VERIFIED BY TOOL] matches come from the real filesystem"}
    except Exception as exc:
        return {"error": "grep_search failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool 9: glob_find
# ---------------------------------------------------------------------------

def _skip_path(path: str) -> bool:
    parts = path.split(os.sep)
    return any(part in _SKIP_DIRS for part in parts)


def _tool_glob_find(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return {"error": "pattern is required and must be a non-empty string"}
        base = _resolve(args.get("path") or ".")
        if not os.path.exists(base):
            return {"error": "Path not found: %s" % base}

        found: List[str] = []
        if "/" in pattern or "**" in pattern:
            # Patterns containing a path separator must be matched against the
            # path relative to `base` (sub/*.py, */b.py, sub/b.py, **/b.py);
            # matching a bare basename against them always returned nothing.
            try:
                found = _glob.glob(os.path.join(_glob.escape(base), pattern), recursive=True)
            except Exception as exc:
                return {"error": "glob_find failed: %s" % exc}
            found = [p for p in found if os.path.isfile(p) and not _skip_path(p)]
        else:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in files:
                    if fnmatch.fnmatch(name, pattern):
                        found.append(os.path.join(root, name))

        # Deduplicate: glob.glob itself returns duplicates for patterns such as
        # "**/**/*.py", which would otherwise be reported several times.
        files = sorted({os.path.realpath(p) for p in found})
        truncated = len(files) > GLOB_MAX_RESULTS
        if truncated:
            files = files[:GLOB_MAX_RESULTS]
        return {"files": files, "count": len(files), "truncated": truncated,
                "note": "[VERIFIED BY TOOL] paths come from the real filesystem"}
    except Exception as exc:
        return {"error": "glob_find failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tools 10-13: background jobs
# ---------------------------------------------------------------------------

class _OutputBudget(object):
    """Shared 64 MB write budget for one job's stdout+stderr streams."""

    def __init__(self, cap: int = JOB_OUTPUT_CAP) -> None:
        self.cap = cap
        self.used = 0
        self.notified = False
        self.lock = threading.Lock()

    def write(self, handle, chunk: bytes) -> None:
        with self.lock:
            remaining = self.cap - self.used
            if remaining <= 0:
                return
            take = chunk[:remaining]
            handle.write(take)
            self.used += len(take)
            if len(take) < len(chunk) and not self.notified:
                handle.write(b"\n... [job output truncated at 64 MB] ...\n")
                self.notified = True


def _jobs_root() -> str:
    os.makedirs(_JOBS_DIR, exist_ok=True)
    return _JOBS_DIR


def _job_dir(job_id: str) -> str:
    return os.path.join(_JOBS_DIR, job_id)


def _meta_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "meta.json")


def _read_meta(job_id: str) -> Optional[Dict[str, Any]]:
    if not job_id or not re.match(r"^[A-Za-z0-9_.-]{1,128}$", job_id):
        return None
    return _read_json(_meta_path(job_id))


def _update_meta(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    with _META_LOCK:
        meta = _read_json(_meta_path(job_id))
        if meta is None:
            return None
        meta.update(fields)
        try:
            _write_json_atomic(_meta_path(job_id), meta)
        except Exception:
            return meta
        return meta


def _proc_start_time(pid: Any) -> Optional[int]:
    """Start time of a process (field 22 of /proc/<pid>/stat), used to detect PID reuse."""
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as handle:
            data = handle.read()
        rest = data.rsplit(b")", 1)[1].split()
        return int(rest[19])
    except Exception:
        return None


def _pid_alive(pid: Any, start_time: Any = None) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    # After a restart the in-memory entry is gone; without the start time a
    # recycled PID would keep a finished job looking "running" forever.
    if start_time is not None:
        current = _proc_start_time(pid_int)
        if current is None:
            return False
        try:
            if int(start_time) != current:
                return False
        except (TypeError, ValueError):
            pass
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def _duration_from_meta(meta: Dict[str, Any]) -> Optional[float]:
    started = meta.get("started_at")
    finished = meta.get("finished_at")
    if not started:
        return meta.get("duration")
    try:
        start_ts = datetime.fromisoformat(str(started))
    except Exception:
        return meta.get("duration")
    if finished:
        try:
            end_ts = datetime.fromisoformat(str(finished))
        except Exception:
            end_ts = datetime.now(timezone.utc)
    else:
        end_ts = datetime.now(timezone.utc)
    try:
        return round((end_ts - start_ts).total_seconds(), 3)
    except Exception:
        return meta.get("duration")


def _refresh_meta(job_id: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Bring a job's persisted status up to date.  Never raises."""
    try:
        if meta is None:
            meta = _read_json(_meta_path(job_id))
        if meta is None:
            return None
        if meta.get("status") != "running":
            return meta

        entry = _JOBS.get(job_id) or {}
        proc = entry.get("proc")
        if proc is not None:
            returncode = proc.poll()
            if returncode is None:
                return meta
            # The process is gone: let the output pumps drain their pipes so a
            # subsequent file read cannot miss the tail of the output.
            for thread in entry.get("readers", []):
                try:
                    thread.join(timeout=0.5)
                except Exception:
                    pass
            status = "killed" if meta.get("status") == "killed" else "done"
            return _update_meta(
                job_id,
                status=status,
                exit_code=returncode,
                duration=_duration_from_meta(meta),
                finished_at=meta.get("finished_at") or _now_iso(),
            )

        if not _pid_alive(meta.get("pid"), meta.get("pid_start")):
            return _update_meta(
                job_id,
                status="done",
                duration=_duration_from_meta(meta),
                finished_at=meta.get("finished_at") or _now_iso(),
            )
        return meta
    except Exception:
        return meta


def _all_metas() -> List[Dict[str, Any]]:
    metas: List[Dict[str, Any]] = []
    try:
        if not os.path.isdir(_JOBS_DIR):
            return metas
        for name in os.listdir(_JOBS_DIR):
            path = os.path.join(_JOBS_DIR, name, "meta.json")
            if not os.path.isfile(path):
                continue
            meta = _read_json(path)
            if meta is not None:
                meta.setdefault("job_id", name)
                metas.append(meta)
    except Exception:
        pass
    return metas


def _running_jobs() -> List[Dict[str, Any]]:
    running = []
    for meta in _all_metas():
        refreshed = _refresh_meta(meta.get("job_id", ""), meta)
        if refreshed and refreshed.get("status") == "running":
            running.append(refreshed)
    return running


def _pump(stream, path: str, budget: _OutputBudget) -> None:
    try:
        with open(path, "ab", buffering=0) as handle:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                budget.write(handle, chunk)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _reap(job_id: str, proc: "subprocess.Popen", started_mono: float) -> None:
    """Background thread: collect a finished job and persist its result."""
    try:
        returncode = proc.wait()
    except Exception:
        returncode = None

    entry = _JOBS.get(job_id) or {}
    for thread in entry.get("readers", []):
        try:
            thread.join(timeout=5)
        except Exception:
            pass

    with _META_LOCK:
        meta = _read_json(_meta_path(job_id))
        if meta is None:
            return
        if meta.get("status") == "killed":
            status = "killed"
        else:
            status = "done"
        meta.update(
            {
                "status": status,
                "exit_code": returncode,
                "duration": round(time.time() - started_mono, 3),
                "finished_at": _now_iso(),
            }
        )
        try:
            _write_json_atomic(_meta_path(job_id), meta)
        except Exception:
            pass


def _tool_run_job(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"error": "command is required and must be a non-empty string"}

        if len(_running_jobs()) >= MAX_RUNNING_JOBS:
            return {"error": "too many running jobs (limit %d)" % MAX_RUNNING_JOBS}

        cwd = None
        if args.get("cwd"):
            cwd = _resolve(args.get("cwd"))
            if not os.path.isdir(cwd):
                return {"error": "cwd is not a directory: %s" % cwd}
        if cwd is None:
            cwd = _WORKSPACE if os.path.isdir(_WORKSPACE) else None

        job_id = uuid.uuid4().hex
        job_dir = _job_dir(job_id)
        os.makedirs(job_dir, exist_ok=True)
        stdout_path = os.path.join(job_dir, "stdout.log")
        stderr_path = os.path.join(job_dir, "stderr.log")
        for path in (stdout_path, stderr_path):
            with open(path, "ab"):
                pass

        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )
        except Exception as exc:
            return {"error": "failed to start job: %s" % exc}

        started_at = _now_iso()
        budget = _OutputBudget()
        readers = [
            threading.Thread(target=_pump, args=(proc.stdout, stdout_path, budget), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, stderr_path, budget), daemon=True),
        ]
        for thread in readers:
            thread.start()
        started_mono = time.time()
        _JOBS[job_id] = {
            "proc": proc,
            "readers": readers,
            "started_mono": started_mono,
            "pid_start": _proc_start_time(proc.pid),
        }

        meta = {
            "job_id": job_id,
            "command": command,
            "pid": proc.pid,
            "pid_start": _proc_start_time(proc.pid),
            "cwd": cwd,
            "started_at": started_at,
            "status": "running",
            "exit_code": None,
            "duration": None,
            "finished_at": None,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
        }
        try:
            _write_json_atomic(_meta_path(job_id), meta)
        except Exception as exc:
            _JOBS.pop(job_id, None)
            return {"error": "failed to persist job metadata: %s" % exc}

        reaper = threading.Thread(target=_reap, args=(job_id, proc, started_mono), daemon=True)
        reaper.start()
        _JOBS[job_id]["reaper"] = reaper

        return {
            "job_id": job_id,
            "pid": proc.pid,
            "pid_start": meta.get("pid_start"),
            "command": command,
            "started_at": started_at,
        }
    except Exception as exc:
        return {"error": "run_job failed: %s" % exc}


def _wait_for_job(job_id: str, wait_ms: int) -> None:
    deadline = time.time() + (wait_ms / 1000.0)
    while True:
        meta = _refresh_meta(job_id)
        if meta is None or meta.get("status") != "running":
            return
        if time.time() >= deadline:
            return
        entry = _JOBS.get(job_id) or {}
        proc = entry.get("proc")
        remaining = max(0.0, deadline - time.time())
        if proc is not None:
            try:
                proc.wait(timeout=min(remaining, 0.2) or 0.01)
            except Exception:
                pass
        else:
            time.sleep(min(0.1, remaining or 0.01))


def _tool_job_output(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        job_id = args.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            return {"error": "job_id is required"}
        job_id = job_id.strip()
        meta = _read_meta(job_id)
        if meta is None:
            return {"error": "job not found: %s" % job_id}

        wait_ms = _clamp_int(args.get("wait_ms"), 0, MAX_WAIT_MS, 0)
        if wait_ms:
            _wait_for_job(job_id, wait_ms)
        meta = _refresh_meta(job_id) or meta

        stdout_path = meta.get("stdout_path") or os.path.join(_job_dir(job_id), "stdout.log")
        stderr_path = meta.get("stderr_path") or os.path.join(_job_dir(job_id), "stderr.log")

        def _read_text(path: str) -> str:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    return _sanitize_output(handle.read())
            except Exception:
                return ""

        stdout_full = _read_text(stdout_path)
        stderr_full = _read_text(stderr_path)

        max_chars = _max_chars(args.get("max_chars"))
        cursor = max(0, _safe_int(args.get("cursor"), 0))
        stdout_part, truncated, next_cursor = _paginate(stdout_full, cursor, max_chars)
        stderr_part = stderr_full[:max_chars]
        stderr_truncated = len(stderr_full) > max_chars

        return {
            "status": meta.get("status", "running"),
            "exit_code": meta.get("exit_code"),
            "stdout": stdout_part,
            "stderr": stderr_part,
            "next_cursor": next_cursor,
            "duration": meta.get("duration") if meta.get("duration") is not None else _duration_from_meta(meta),
            "truncated": bool(truncated or stderr_truncated),
        }
    except Exception as exc:
        return {"error": "job_output failed: %s" % exc}


_SIGNALS = {
    "TERM": _signal.SIGTERM,
    "KILL": _signal.SIGKILL,
    "INT": _signal.SIGINT,
    "HUP": _signal.SIGHUP,
    "QUIT": _signal.SIGQUIT,
}


def _tool_job_kill(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if _READ_ONLY:
            return {"error": "server is in read-only mode"}
        job_id = args.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            return {"error": "job_id is required"}
        job_id = job_id.strip()
        meta = _read_meta(job_id)
        if meta is None:
            return {"error": "job not found: %s" % job_id}
        meta = _refresh_meta(job_id) or meta

        raw_signal = args.get("signal", "TERM")
        if isinstance(raw_signal, int) and not isinstance(raw_signal, bool):
            sig = raw_signal
        else:
            sig = _SIGNALS.get(str(raw_signal).strip().upper())
            if sig is None:
                return {"error": "unsupported signal: %r" % (raw_signal,)}

        if meta.get("status") != "running":
            return {"ok": True, "status": meta.get("status", "done")}

        pid = meta.get("pid")
        delivered = False
        try:
            os.killpg(os.getpgid(int(pid)), sig)
            delivered = True
        except Exception:
            try:
                os.kill(int(pid), sig)
                delivered = True
            except Exception as exc:
                return {"error": "failed to signal job %s: %s" % (job_id, exc)}

        if delivered:
            _update_meta(job_id, status="killed", finished_at=_now_iso(), duration=_duration_from_meta(meta))
        return {"ok": True, "status": "killed"}
    except Exception as exc:
        return {"error": "job_kill failed: %s" % exc}


def _tool_job_list(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        metas = []
        for meta in _all_metas():
            refreshed = _refresh_meta(meta.get("job_id", ""), meta) or meta
            metas.append(refreshed)
        metas.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        # The full history is unbounded (one entry per job ever started), and every
        # byte here lands in the model's context. Show the most recent few by
        # default and let the caller ask for more explicitly.
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))
        jobs = []
        for meta in metas[:limit]:
            command = str(meta.get("command") or "")
            if len(command) > 80:
                command = command[:77] + "..."
            jobs.append({
                "job_id": meta.get("job_id"),
                "command": command,
                "status": meta.get("status", "running"),
                "exit_code": meta.get("exit_code"),
                "duration": meta.get("duration"),
                "started_at": meta.get("started_at"),
            })
        return {"jobs": jobs, "count": len(jobs), "total": len(metas),
                "note": ("showing the %d most recent of %d; pass limit=<n> for more"
                         % (len(jobs), len(metas))) if len(metas) > len(jobs) else None}
    except Exception as exc:
        return {"error": "job_list failed: %s" % exc}


# ---------------------------------------------------------------------------
# Tool definitions (MCP schemas)
# ---------------------------------------------------------------------------

def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "mesh_status",
        "title": "Mesh Status",
        "description": ("Confirm this node is reachable. Call this first if you think the host is "
                        "offline; it reports live evidence from the host itself."),
        "inputSchema": _schema({}),
    },
    {
        "name": "system_info",
        "title": "System Info",
        "description": ("One-call host summary: OS, desktop, user, home, disks, memory, load, "
                        "top processes and the current wallpaper."),
        "inputSchema": _schema({}),
    },
    {
        "name": "system_vitals",
        "title": "System Vitals",
        "description": "Retrieve CPU, RAM and disk metrics of the local host.",
        "inputSchema": _schema({}),
    },
    {
        "name": "get_orchestration_skill",
        "title": "Get Orchestration Skill",
        "description": "Load the current Antigravity orchestration skill and rules.",
        "inputSchema": _schema({}),
    },
    {
        "name": "list_dir",
        "title": "List Directory",
        "description": "List files and directories at the given path (workspace by default).",
        "inputSchema": _schema({"path": {"type": "string", "description": "Directory or file path."}}),
    },
    {
        "name": "bash_exec",
        "title": "Execute Bash Command",
        "description": (
            "Execute a shell command on the local host. Output is paginated: when the result is "
            "cut, call again with cursor=next_cursor to continue (nothing is dropped). Large "
            "outputs (>256 KB) are spooled to a file returned in saved_to."
        ),
        "inputSchema": _schema(
            {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout_sec": {"type": "integer", "description": "Timeout in seconds (1-120, default 25)."},
                "max_chars": {"type": "integer", "description": "Maximum characters per response."},
                "cursor": {"type": "integer", "description": "Character offset to continue from."},
            },
            ["command"],
        ),
    },
    {
        "name": "read_file",
        "title": "Read File",
        "description": (
            "Read a text file with %4d | line numbers, optionally restricted to a line range. "
            "Supports max_chars/cursor pagination."
        ),
        "inputSchema": _schema(
            {
                "path": {"type": "string", "description": "File path to read."},
                "start_line": {"type": "integer", "description": "First line (1-based)."},
                "end_line": {"type": "integer", "description": "Last line (inclusive)."},
                "max_chars": {"type": "integer", "description": "Maximum characters per response."},
                "cursor": {"type": "integer", "description": "Character offset to continue from."},
            },
            ["path"],
        ),
    },
    {
        "name": "write_file",
        "title": "Write File",
        "description": "Atomically create or overwrite a file (tmp file + os.replace).",
        "inputSchema": _schema(
            {
                "path": {"type": "string", "description": "File path to write."},
                "content": {"type": "string", "description": "Full file content."},
                "create_dirs": {"type": "boolean", "description": "Create parent directories (default true)."},
                "mode": {"type": "string", "description": "Octal file mode, e.g. \"0644\"."},
            },
            ["path", "content"],
        ),
    },
    {
        "name": "edit_file",
        "title": "Edit File",
        "description": (
            "Replace an exact substring in a file. old_string must match exactly once unless "
            "replace_all is set. Optional expected_sha256 guards against overwriting a file "
            "that changed since it was read."
        ),
        "inputSchema": _schema(
            {
                "path": {"type": "string", "description": "File path to edit."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "expected_sha256": {"type": "string", "description": "Expected current sha256 of the file."},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
            },
            ["path", "old_string", "new_string"],
        ),
    },
    {
        "name": "grep_search",
        "title": "Grep Search",
        "description": "Recursively search file contents; skips binary files and heavy directories.",
        "inputSchema": _schema(
            {
                "pattern": {"type": "string", "description": "Regular expression (or literal text with fixed)."},
                "path": {"type": "string", "description": "Root directory or file (default workspace)."},
                "glob": {"type": "string", "description": "File name filter, e.g. *.py."},
                "limit": {"type": "integer", "description": "Maximum matches (1-1000, default 200)."},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search."},
                "fixed": {"type": "boolean", "description": "Treat pattern as literal text."},
                "context": {"type": "integer", "description": "Context lines around each match."},
            },
            ["pattern"],
        ),
    },
    {
        "name": "glob_find",
        "title": "Glob Find",
        "description": "Find files by glob pattern (* and **).",
        "inputSchema": _schema(
            {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py."},
                "path": {"type": "string", "description": "Root directory (default workspace)."},
            },
            ["pattern"],
        ),
    },
    {
        "name": "run_job",
        "title": "Run Background Job",
        "description": "Start a command in the background; returns a job_id for job_output/job_kill.",
        "inputSchema": _schema(
            {
                "command": {"type": "string", "description": "Shell command to run in the background."},
                "cwd": {"type": "string", "description": "Working directory (default workspace)."},
            },
            ["command"],
        ),
    },
    {
        "name": "job_output",
        "title": "Job Output",
        "description": "Read a background job's output, optionally waiting up to 20 s for completion.",
        "inputSchema": _schema(
            {
                "job_id": {"type": "string", "description": "Job id returned by run_job."},
                "wait_ms": {"type": "integer", "description": "Milliseconds to wait (0-20000)."},
                "max_chars": {"type": "integer", "description": "Maximum stdout characters per response."},
                "cursor": {"type": "integer", "description": "stdout character offset to continue from."},
            },
            ["job_id"],
        ),
    },
    {
        "name": "job_kill",
        "title": "Job Kill",
        "description": "Terminate a running background job.",
        "inputSchema": _schema(
            {
                "job_id": {"type": "string", "description": "Job id returned by run_job."},
                "signal": {"type": "string", "description": "TERM (default), KILL, INT, HUP or QUIT."},
            },
            ["job_id"],
        ),
    },
    {
        "name": "job_list",
        "title": "Job List",
        "description": "List recent background jobs, newest first (20 by default, max 50).",
        "inputSchema": _schema({
            "limit": {"type": "integer", "description": "How many jobs to return (1-50, default 20)."},
        }),
    },
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "mesh_status": _tool_mesh_status,
    "system_info": _tool_system_info,
    "system_vitals": _tool_system_vitals,
    "get_orchestration_skill": _tool_get_orchestration_skill,
    "list_dir": _tool_list_dir,
    "bash_exec": _tool_bash_exec,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "edit_file": _tool_edit_file,
    "grep_search": _tool_grep_search,
    "glob_find": _tool_glob_find,
    "run_job": _tool_run_job,
    "job_output": _tool_job_output,
    "job_kill": _tool_job_kill,
    "job_list": _tool_job_list,
}


def call_tool(name: str, args: Any = None) -> Any:
    """Dispatch a tool call.  Never raises: any failure becomes ``{"error": ...}``."""
    try:
        handler = _HANDLERS.get(name) if isinstance(name, str) else None
        if handler is None:
            return {"error": "Unknown tool: %s" % (name,)}
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"error": "arguments must be an object"}
        result = handler(args)
        if result is None:
            return {"error": "tool %s returned no result" % name}
        return result
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
