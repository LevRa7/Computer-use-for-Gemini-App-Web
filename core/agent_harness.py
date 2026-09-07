import os
import uuid
from typing import Optional, List
from google.antigravity import LocalAgentConfig, types
from core.hooks import get_mesh_hooks
from core.triggers import create_mesh_watchdog_trigger, create_config_trigger
from core.policies import get_mesh_policies, ALLOWED_WORKSPACES
from core.subagents import get_mesh_subagents, get_root_capabilities_config

DEFAULT_SAVE_DIR = "/root/agy-gdrive-runner/.state/sessions"
DEFAULT_APP_DATA_DIR = "/root/agy-gdrive-runner/.state/artifacts"

ROOT_SYSTEM_INSTRUCTIONS = (
    "You are the Root Orchestrator for the Antigravity Remote Execution Mesh. "
    "Execute commands strictly through native tools and verified subagents. "
    "Adhere to Zero Drive Queue Policy and Hard Error Halt invariants."
)

def create_agent_config(
    conversation_id: Optional[str] = None,
    save_dir: Optional[str] = None,
    app_data_dir: Optional[str] = None,
    interactive: bool = True,
    max_model_calls: int = 30,
) -> LocalAgentConfig:
    resolved_save_dir = save_dir or DEFAULT_SAVE_DIR
    resolved_app_data_dir = app_data_dir or DEFAULT_APP_DATA_DIR
    resolved_conv_id = conversation_id or str(uuid.uuid4())

    os.makedirs(resolved_save_dir, exist_ok=True)
    os.makedirs(resolved_app_data_dir, exist_ok=True)

    hooks = get_mesh_hooks()
    triggers = [
        create_mesh_watchdog_trigger(interval_seconds=60.0),
        create_config_trigger(path="server_facts.json"),
    ]
    policies = get_mesh_policies()
    subagents = get_mesh_subagents()
    capabilities = get_root_capabilities_config()
    
    budget = types.BudgetConfig(
        max_model_calls=max_model_calls,
        max_tool_calls=60,
        max_total_tokens=200_000,
    )

    return LocalAgentConfig(
        system_instructions=ROOT_SYSTEM_INSTRUCTIONS,
        conversation_id=resolved_conv_id,
        save_dir=resolved_save_dir,
        app_data_dir=resolved_app_data_dir,
        hooks=hooks,
        triggers=triggers,
        policies=policies,
        subagents=subagents,
        capabilities=capabilities,
        workspaces=ALLOWED_WORKSPACES,
        budget_config=budget,
    )
