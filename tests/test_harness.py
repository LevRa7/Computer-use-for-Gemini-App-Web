import pytest
import uuid
from google.antigravity import LocalAgentConfig, types

def test_create_agent_config_harness():
    from core.agent_harness import create_agent_config, DEFAULT_SAVE_DIR

    test_id = str(uuid.uuid4())
    cfg = create_agent_config(
        conversation_id=test_id,
        save_dir="/tmp/test_sessions",
        max_model_calls=30
    )

    assert isinstance(cfg, LocalAgentConfig)
    assert cfg.conversation_id == test_id
    assert cfg.save_dir == "/tmp/test_sessions"
    assert len(cfg.hooks) >= 3
    assert len(cfg.subagents) == 3
    assert cfg.budget_config.max_model_calls == 30
    assert cfg.capabilities.enable_subagents is True
    assert cfg.capabilities.max_subagent_depth <= 2

def test_harness_session_persistence_defaults():
    from core.agent_harness import create_agent_config, DEFAULT_SAVE_DIR

    cfg = create_agent_config()
    assert cfg.save_dir == DEFAULT_SAVE_DIR
    assert cfg.conversation_id is not None
    assert len(cfg.conversation_id) >= 32
