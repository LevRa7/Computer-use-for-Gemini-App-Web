import os
import re
from typing import List, Optional, Callable, Any, Awaitable
from google.antigravity import types
from google.antigravity.hooks import policy
from google.antigravity.hooks.policy import Policy, Decision

ALLOWED_WORKSPACES = [
    "/root/agy-gdrive-runner",
    "/home/lev/MyProjects/antigravity-mesh",
]

SAFE_COMMAND_PREFIXES = [
    "git status",
    "git log",
    "git diff",
    "systemctl status",
    "uptime",
    "ls",
    "ps",
    "cat",
    "echo",
    "whoami",
    "uname",
    "df",
    "free",
]

DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bsystemctl\s+(stop|disable|mask|restart)\b"),
    re.compile(r"\brm\s+"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\bkill\s+-9\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+"),
]

def validate_workspace_path(path: str, allowed_workspaces: Optional[List[str]] = None) -> bool:
    workspaces = allowed_workspaces or ALLOWED_WORKSPACES
    abs_path = os.path.abspath(path)
    for ws in workspaces:
        ws_abs = os.path.abspath(ws)
        if abs_path == ws_abs or abs_path.startswith(ws_abs.rstrip("/") + "/"):
            return True
    return False

def _extract_command(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("command") or args.get("CommandLine") or "").strip()

def is_safe_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd:
        return False
    return any(cmd.startswith(prefix) for prefix in SAFE_COMMAND_PREFIXES)

def is_destructive_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd:
        return False
    return any(pat.search(cmd) for pat in DESTRUCTIVE_COMMAND_PATTERNS)

async def default_deny_handler(tool_call: types.ToolCall) -> bool:
    return False

def get_mesh_policies(
    allowed_workspaces: Optional[List[str]] = None,
    ask_user_handler: Optional[Callable[[types.ToolCall], Awaitable[bool]]] = None,
) -> List[Policy]:
    handler = ask_user_handler or default_deny_handler
    policies: List[Policy] = []

    # 1. Ask user for destructive commands
    policies.append(
        policy.ask_user(
            "run_command",
            when=is_destructive_command,
            handler=handler,
            name="confirm_destructive",
        )
    )

    # 2. Allow safe commands without confirmation
    policies.append(
        policy.allow(
            "run_command",
            when=is_safe_command,
            name="allow_safe_commands",
        )
    )

    return policies

async def evaluate_policies(policies: List[Policy], tool_call: types.ToolCall) -> types.HookResult:
    hook = policy._PolicyDecideHook(policies)
    return await hook.run(None, tool_call)
