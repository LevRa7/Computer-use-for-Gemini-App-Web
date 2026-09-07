from typing import List
from google.antigravity import types

coordinator_worker = types.SubagentConfig(
    name="coordinator_worker",
    description="Autonomous worker for coordinator tasks, executing shell commands and modifying files.",
    capabilities=types.SubagentCapabilities(
        agent_behavior=types.AgentBehavior.AUTONOMOUS,
        enabled_tools=[
            types.BuiltinTools.RUN_COMMAND,
            types.BuiltinTools.VIEW_FILE,
            types.BuiltinTools.EDIT_FILE,
            types.BuiltinTools.CREATE_FILE,
            types.BuiltinTools.SEARCH_DIR,
            types.BuiltinTools.FIND_FILE,
            types.BuiltinTools.LIST_DIR,
        ],
    ),
)

remote_node_worker = types.SubagentConfig(
    name="remote_node_worker",
    description="Autonomous worker for dispatching commands and collecting vitals on remote mesh nodes.",
    capabilities=types.SubagentCapabilities(
        agent_behavior=types.AgentBehavior.AUTONOMOUS,
        enabled_tools=[
            types.BuiltinTools.RUN_COMMAND,
            types.BuiltinTools.VIEW_FILE,
        ],
    ),
)

code_researcher = types.SubagentConfig(
    name="code_researcher",
    description="Read-only research worker for exploring codebase, search, and reading files.",
    capabilities=types.SubagentCapabilities(
        agent_behavior=types.AgentBehavior.AUTONOMOUS,
        enabled_tools=[
            types.BuiltinTools.VIEW_FILE,
            types.BuiltinTools.SEARCH_DIR,
            types.BuiltinTools.FIND_FILE,
            types.BuiltinTools.LIST_DIR,
        ],
    ),
)

def get_mesh_subagents() -> List[types.SubagentConfig]:
    return [coordinator_worker, remote_node_worker, code_researcher]

def get_root_capabilities_config() -> types.CapabilitiesConfig:
    return types.CapabilitiesConfig(
        enable_subagents=True,
        max_subagent_depth=2,
        allowed_subagents=["coordinator_worker", "remote_node_worker", "code_researcher"],
    )
