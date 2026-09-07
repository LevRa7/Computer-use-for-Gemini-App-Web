import pytest
import uuid
from google.antigravity import types
from core.schemas import NodeVitals
from core.server import get_coordinator_vitals
from core.agent_harness import create_agent_config
from core.hooks import mesh_pre_tool_call, mesh_on_tool_error
from core.policies import evaluate_policies

@pytest.mark.asyncio
async def test_end_to_end_antigravity_mesh_pipeline():
    # 1. Harness configuration
    conv_id = str(uuid.uuid4())
    config = create_agent_config(conversation_id=conv_id)
    assert config.conversation_id == conv_id
    assert len(config.hooks) >= 3
    assert len(config.triggers) >= 2
    assert len(config.subagents) == 3

    # 2. Schema and Server integration
    vitals = get_coordinator_vitals()
    assert isinstance(vitals, NodeVitals)
    assert vitals.is_online is True

    # 3. Policy evaluation on safe vs destructive
    safe_call = types.ToolCall(name="run_command", args={"command": "uptime"})
    policy_res = await evaluate_policies(config.policies, safe_call)
    assert policy_res.allow is True

    # 4. Lifecycle hook: Zero Drive Queue enforcement
    queue_call = types.ToolCall(
        name="write_file",
        args={"path": "antigravity_tasks.json", "content": "{}"}
    )
    hook_res = await mesh_pre_tool_call(queue_call)
    assert hook_res.allow is False
    assert "Zero Drive Queue Policy Violation" in hook_res.message

    # 5. Lifecycle hook: Hard Error Halt
    err_output = await mesh_on_tool_error(RuntimeError("Subagent execution timeout"))
    assert "[HARD ERROR HALT]" in err_output
