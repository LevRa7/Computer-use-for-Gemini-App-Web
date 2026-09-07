import pytest
import os
import json
from google.antigravity import types

@pytest.mark.asyncio
async def test_pre_tool_call_blocks_drive_queues():
    from core.hooks import mesh_pre_tool_call

    call = types.ToolCall(
        name="write_file",
        args={"path": "antigravity_tasks.json", "content": "{}"}
    )
    res = await mesh_pre_tool_call(call)
    assert res.allow is False
    assert "Drive Queue" in res.message or "antigravity_tasks.json" in res.message

@pytest.mark.asyncio
async def test_pre_tool_call_blocks_dangerous_rm():
    from core.hooks import mesh_pre_tool_call

    call = types.ToolCall(
        name="run_command",
        args={"command": "rm -rf /"}
    )
    res = await mesh_pre_tool_call(call)
    assert res.allow is False
    assert "Dangerous" in res.message or "rm -rf" in res.message

@pytest.mark.asyncio
async def test_on_tool_error_halts_execution():
    from core.hooks import mesh_on_tool_error

    err = RuntimeError("Remote node unreachable: matebook (timeout)")
    msg = await mesh_on_tool_error(err)
    assert "[HARD ERROR HALT]" in msg
    assert "Remote node unreachable" in msg

@pytest.mark.asyncio
async def test_post_tool_call_logs_telemetry(tmp_path):
    from core.hooks import mesh_post_tool_call, AUDIT_LOG_PATH

    result = types.ToolResult(
        name="system_vitals",
        id="call-1",
        step_id="step-1",
        result="CPU 10%, RAM 20%",
        error=None,
        exception=None,
        server_name="coordinator"
    )
    await mesh_post_tool_call(result)
    assert os.path.exists(AUDIT_LOG_PATH)
    with open(AUDIT_LOG_PATH, "r") as f:
        lines = f.readlines()
        assert len(lines) > 0
        last_entry = json.loads(lines[-1])
        assert last_entry["name"] == "system_vitals"
        assert last_entry["status"] == "success"
