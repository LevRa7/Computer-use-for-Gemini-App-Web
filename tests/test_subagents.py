import pytest
from google.antigravity import types

def test_mesh_subagents_defined():
    from core.subagents import get_mesh_subagents

    subagents = get_mesh_subagents()
    names = [s.name for s in subagents]
    assert "coordinator_worker" in names
    assert "remote_node_worker" in names
    assert "code_researcher" in names

def test_code_researcher_is_readonly():
    from core.subagents import get_mesh_subagents

    subagents = get_mesh_subagents()
    researcher = next(s for s in subagents if s.name == "code_researcher")
    
    assert researcher.capabilities.agent_behavior == types.AgentBehavior.AUTONOMOUS
    assert types.BuiltinTools.RUN_COMMAND not in (researcher.capabilities.enabled_tools or [])
    assert types.BuiltinTools.VIEW_FILE in researcher.capabilities.enabled_tools
    assert types.BuiltinTools.SEARCH_DIR in researcher.capabilities.enabled_tools

def test_root_capabilities_depth_limit():
    from core.subagents import get_root_capabilities_config

    caps = get_root_capabilities_config()
    assert caps.enable_subagents is True
    assert caps.max_subagent_depth <= 2
    assert "coordinator_worker" in caps.allowed_subagents
    assert "remote_node_worker" in caps.allowed_subagents
    assert "code_researcher" in caps.allowed_subagents
