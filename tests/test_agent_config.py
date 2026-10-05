"""The agent's MESH_* configuration comes from the environment or from the file.

``install.sh`` / ``install.ps1`` write gateway, node name and token into
``agent.env``. Extra keys in the same file (MESH_SHELL on Windows,
MESH_WORKSPACE, MESH_READ_ONLY, ...) must reach the process too, otherwise a
Windows node cannot switch its command shell without editing the launcher.
"""

import os

import pytest

from core import agent


def test_env_file_values_are_exported_for_every_mesh_key(tmp_path, monkeypatch):
    config = tmp_path / "agent.env"
    config.write_text(
        "MESH_GATEWAY=example.test\n"
        "MESH_USER=node-one\n"
        "MESH_TOKEN=tok\n"
        "MESH_SHELL=cmd\n"
        "MESH_READ_ONLY=1\n"
        "UNRELATED=ignored\n"
    )
    monkeypatch.setenv("MESH_CONFIG_FILE", str(config))
    for key in ("MESH_GATEWAY", "MESH_USER", "MESH_TOKEN", "MESH_SHELL",
                "MESH_READ_ONLY", "UNRELATED"):
        monkeypatch.delenv(key, raising=False)

    settings = agent._load_settings()

    assert settings == {"gateway": "example.test", "user": "node-one", "token": "tok"}
    assert os.environ["MESH_SHELL"] == "cmd"
    assert os.environ["MESH_READ_ONLY"] == "1"
    assert "UNRELATED" not in os.environ, "only MESH_* keys may be exported"


def test_environment_wins_over_the_file(tmp_path, monkeypatch):
    config = tmp_path / "agent.env"
    config.write_text("MESH_GATEWAY=from-file\nMESH_SHELL=cmd\n")
    monkeypatch.setenv("MESH_CONFIG_FILE", str(config))
    monkeypatch.setenv("MESH_GATEWAY", "from-env")
    monkeypatch.setenv("MESH_SHELL", "git-bash")

    settings = agent._load_settings()

    assert settings["gateway"] == "from-env"
    assert os.environ["MESH_SHELL"] == "git-bash"


def test_mesh_shell_from_the_file_selects_the_shell(tmp_path, monkeypatch):
    """The value from agent.env reaches command_shell(), not just os.environ."""
    from core import mcp_tools

    config = tmp_path / "agent.env"
    config.write_text("MESH_SHELL=cmd\nMESH_USER=node-one\nMESH_TOKEN=tok\n")
    monkeypatch.setenv("MESH_CONFIG_FILE", str(config))
    monkeypatch.delenv("MESH_SHELL", raising=False)
    monkeypatch.setattr(mcp_tools, "_IS_WINDOWS", True)
    monkeypatch.setattr(mcp_tools, "_SHELL_CACHE", None)
    monkeypatch.setattr(mcp_tools, "powershell_argv", lambda: ["powershell.exe", "-Command"])
    try:
        agent._load_settings()
        assert mcp_tools.command_shell()[0] == "cmd"
    finally:
        mcp_tools._SHELL_CACHE = None
