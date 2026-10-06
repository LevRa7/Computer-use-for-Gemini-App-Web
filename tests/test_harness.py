import os
import tempfile
import pytest
import uuid
pytest.importorskip("google.antigravity")  # private SDK: skip, never a collection ERROR
from google.antigravity import LocalAgentConfig, types

def test_create_agent_config_harness():
    from core.agent_harness import create_agent_config, DEFAULT_SAVE_DIR

    test_id = str(uuid.uuid4())
    # A real temporary directory rather than "/tmp/...", which on Windows is a
    # drive-relative path that writes to D:\tmp.
    save_dir = tempfile.mkdtemp(prefix="agy-harness-")
    cfg = create_agent_config(
        conversation_id=test_id,
        save_dir=save_dir,
        max_model_calls=30
    )

    assert isinstance(cfg, LocalAgentConfig)
    assert cfg.conversation_id == test_id
    assert os.path.normcase(cfg.save_dir) == os.path.normcase(save_dir)
    assert len(cfg.hooks) >= 3
    assert len(cfg.subagents) == 3
    assert cfg.budget_config.max_model_calls == 30
    assert cfg.capabilities.enable_subagents is True
    assert cfg.capabilities.max_subagent_depth <= 2

def test_harness_session_persistence_defaults():
    from core.agent_harness import create_agent_config, DEFAULT_SAVE_DIR

    cfg = create_agent_config()
    assert os.path.normcase(cfg.save_dir) == os.path.normcase(DEFAULT_SAVE_DIR)
    assert cfg.conversation_id is not None
    assert len(cfg.conversation_id) >= 32
