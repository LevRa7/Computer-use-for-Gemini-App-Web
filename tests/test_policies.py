import pytest
from unittest.mock import AsyncMock
pytest.importorskip("google.antigravity")  # private SDK: skip, never a collection ERROR
from google.antigravity import types
from google.antigravity.hooks import policy

@pytest.mark.asyncio
async def test_policy_allows_safe_commands():
    from core.policies import get_mesh_policies, evaluate_policies

    policies = get_mesh_policies()
    
    for cmd in ["git status", "systemctl status agy-mcp", "uptime"]:
        call = types.ToolCall(name="run_command", args={"command": cmd})
        res = await evaluate_policies(policies, call)
        assert res.allow is True

@pytest.mark.asyncio
async def test_policy_asks_user_for_destructive_commands():
    from core.policies import get_mesh_policies, evaluate_policies

    mock_handler = AsyncMock(return_value=False)
    policies = get_mesh_policies(ask_user_handler=mock_handler)

    for cmd in ["reboot", "systemctl stop agy-mcp", "rm -f test.txt"]:
        call = types.ToolCall(name="run_command", args={"command": cmd})
        res = await evaluate_policies(policies, call)
        assert res.allow is False
        assert "User denied" in res.message or "confirm" in res.message

@pytest.mark.asyncio
async def test_policy_restricts_workspaces():
    import os

    from core.policies import validate_workspace_path, ALLOWED_WORKSPACES

    # Inside every allowed workspace. The roots themselves are platform-specific
    # (the documented /root/... on Linux, %USERPROFILE%\... on Windows), so they
    # are taken from the module instead of being hardcoded POSIX literals.
    for root in ALLOWED_WORKSPACES:
        assert validate_workspace_path(os.path.join(root, "core", "schemas.py")) is True
        assert validate_workspace_path(root) is True

    # Outside allowed workspaces. A POSIX path abspaths to a drive-relative path on
    # Windows, which is exactly what makes these cases outside on both platforms.
    assert validate_workspace_path("/etc/passwd") is False
    assert validate_workspace_path("/var/log/syslog") is False
    assert validate_workspace_path("/tmp/malicious.sh") is False
    if os.name == "nt":
        assert validate_workspace_path("C:\\Windows\\System32\\config\\SAM") is False
