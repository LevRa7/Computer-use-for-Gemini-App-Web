import pytest
from core.server import create_mcp_server

def test_mcp_server_initialization():
    server = create_mcp_server()
    assert server is not None
    assert hasattr(server, "name")
    assert server.name == "antigravity_mesh"

def test_bash_exec_tool():
    server = create_mcp_server()
    # Ensure bash_exec tool exists and executes correctly
    assert "bash_exec" in server.tools
    result = server.tools["bash_exec"](command="echo 'mcp_mesh_test'")
    assert "mcp_mesh_test" in result["stdout"]
    assert result["exit_code"] == 0

def test_system_vitals_tool():
    server = create_mcp_server()
    assert "system_vitals" in server.tools
    vitals = server.tools["system_vitals"]()
    assert "hostname" in vitals
    assert "ram" in vitals
    assert "disk" in vitals

def test_get_orchestration_skill_tool():
    server = create_mcp_server()
    assert "get_orchestration_skill" in server.tools
    skill = server.tools["get_orchestration_skill"]()
    assert isinstance(skill, str)
    assert "Antigravity" in skill
