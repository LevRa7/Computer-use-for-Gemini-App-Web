import os
import json
import re
from datetime import datetime, timezone
from typing import Optional
from google.antigravity import types
from google.antigravity.hooks import hooks

AUDIT_LOG_PATH = "live_logs/mcp_audit.jsonl"

DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/\s*($|\*)"),
    re.compile(r"mkfs"),
    re.compile(r"dd\s+if=/dev/zero\s+of=/dev/sd"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
]

@hooks.pre_tool_call_decide
async def mesh_pre_tool_call(data: types.ToolCall) -> types.HookResult:
    args = data.args or {}
    args_str = json.dumps(args)

    # 1. Block Google Drive Task Queues
    if "antigravity_tasks.json" in args_str or "antigravity_queue" in args_str:
        return types.HookResult(
            allow=False,
            message="Zero Drive Queue Policy Violation: Task queues on Google Drive are permanently disabled."
        )

    # 2. Block dangerous destructive commands
    for key, val in args.items():
        if isinstance(val, str):
            for pattern in DANGEROUS_PATTERNS:
                if pattern.search(val):
                    return types.HookResult(
                        allow=False,
                        message=f"Dangerous command blocked: matched pattern '{pattern.pattern}' in argument '{key}'"
                    )

    return types.HookResult(allow=True)

@hooks.on_tool_error
async def mesh_on_tool_error(data: Exception) -> Optional[str]:
    return f"[HARD ERROR HALT] Tool execution failed: {data}. Execution halted. Report to user immediately and wait for explicit instructions."

@hooks.post_tool_call
async def mesh_post_tool_call(data: types.ToolResult) -> None:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "name": str(data.name),
        "id": data.id,
        "status": "error" if (data.error or data.exception) else "success",
        "error": str(data.error) if data.error else (str(data.exception) if data.exception else None),
        "server_name": data.server_name,
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def get_mesh_hooks():
    return [mesh_pre_tool_call, mesh_on_tool_error, mesh_post_tool_call]
