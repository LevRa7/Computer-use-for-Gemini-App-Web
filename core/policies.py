import os
import re
from typing import List, Optional, Callable, Any, Awaitable
from google.antigravity import types
from google.antigravity.hooks import policy
from google.antigravity.hooks.policy import Policy, Decision

def _platform_root(posix_root: str) -> str:
    """Express a documented deployment root for the host running this node.

    ``/root/...`` is an absolute path on Linux but only drive-relative on Windows,
    where the SDK refuses it outright ("app_data_dir must be an absolute path") and
    ``os.makedirs`` would scatter a ``\\root\\...`` tree beside the drive root. The
    POSIX path is kept unchanged on POSIX hosts, so deployed services do not move.
    """
    if os.name != "nt":
        return posix_root
    leaf = posix_root.rstrip("/").rsplit("/", 1)[-1]
    return os.path.join(os.path.expanduser("~"), leaf)


# Workspace roots are read from the environment ONLY -- never hardcode a personal
# home directory. Point MESH_COORDINATOR_WORKSPACE / MESH_WORKSPACE at the real
# deployment paths in the service environment. The documented defaults are the
# Linux deployment paths; on Windows the same roots live under the user profile.
ALLOWED_WORKSPACES = [
    os.environ.get("MESH_COORDINATOR_WORKSPACE") or _platform_root("/root/agy-gdrive-runner"),
    os.environ.get("MESH_WORKSPACE") or _platform_root("/root/antigravity-mesh"),
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

def _is_within(path: str, root: str) -> bool:
    """True when *path* is *root* itself or lives inside it.

    The comparison has to use the platform separator: ``root + "/"`` never
    matches a normpath'ed Windows path, so every file inside an allowed workspace
    was rejected there and the policy could not be satisfied at all.
    """
    if path == root:
        return True
    root = root.rstrip("/\\")
    for sep in {os.sep, "/", "\\"}:
        if path.startswith(root + sep):
            return True
    return False


def validate_workspace_path(path: str, allowed_workspaces: Optional[List[str]] = None) -> bool:
    workspaces = allowed_workspaces or ALLOWED_WORKSPACES
    abs_path = os.path.abspath(path)
    for ws in workspaces:
        if _is_within(abs_path, os.path.abspath(ws)):
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
