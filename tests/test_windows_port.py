"""Windows port: cmd/PowerShell first, and telemetry that actually answers.

On a stock Windows box the POSIX telemetry of ``system_info`` (``df``, ``free``,
``uptime``, ``ps``) does not exist and came back as ``'df' is not recognized...``
text, while ``bash_exec`` defaulted to Git Bash when it happened to be installed.
These tests pin the platform behaviour without needing a Windows machine.
"""

import os

import pytest

from core import mcp_tools


# ---------------------------------------------------------------------------
# Shell selection
# ---------------------------------------------------------------------------

@pytest.fixture()
def windows(monkeypatch):
    """Pretend the node runs Windows, with a reset shell cache."""
    monkeypatch.setattr(mcp_tools, "_IS_WINDOWS", True)
    monkeypatch.setattr(mcp_tools, "_SHELL_CACHE", None)
    monkeypatch.delenv("MESH_SHELL", raising=False)
    yield monkeypatch
    mcp_tools._SHELL_CACHE = None


def test_windows_defaults_to_powershell_not_bash(windows, monkeypatch):
    windows.setattr(mcp_tools, "powershell_argv", lambda: ["powershell.exe", "-Command"])
    name, argv = mcp_tools.command_shell()
    assert name == "powershell"
    assert argv[0].endswith("powershell.exe")


def test_powershell_argv_is_non_interactive(monkeypatch):
    """pwsh wins over powershell, and the flags keep a one-shot call quiet."""
    def fake_which(name):
        return r"C:\Program Files\PowerShell\7\pwsh.exe" if name == "pwsh" else None

    monkeypatch.setattr(mcp_tools.shutil, "which", fake_which)
    argv = mcp_tools.powershell_argv()
    assert argv[0].endswith("pwsh.exe")
    for flag in ("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"):
        assert flag in argv


def test_windows_falls_back_to_cmd_when_powershell_is_absent(windows, monkeypatch):
    windows.setattr(mcp_tools, "powershell_argv", lambda: None)
    windows.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    name, argv = mcp_tools.command_shell()
    assert name == "cmd"
    assert argv[0].endswith("cmd.exe")
    assert argv[1:] == ["/d", "/s", "/c"]


def test_mesh_shell_selects_cmd(windows, monkeypatch):
    windows.setenv("MESH_SHELL", "cmd")
    windows.setattr(mcp_tools, "powershell_argv", lambda: ["powershell.exe", "-Command"])
    name, argv = mcp_tools.command_shell()
    assert name == "cmd"
    assert argv[0].endswith("cmd.exe")


def test_git_bash_is_opt_in_only(windows, monkeypatch):
    """Git Bash is used only when asked for, and only when it exists."""
    windows.setattr(mcp_tools, "powershell_argv", lambda: ["powershell.exe", "-Command"])
    windows.setattr(mcp_tools, "_git_bash_windows", lambda: r"C:\Program Files\Git\bin\bash.exe")

    assert mcp_tools.command_shell()[0] == "powershell"          # default: no bash

    windows.setenv("MESH_SHELL", "git-bash")
    mcp_tools._SHELL_CACHE = None
    name, argv = mcp_tools.command_shell()
    assert name == "git-bash"
    assert argv[0].endswith("bash.exe")


def test_mesh_shell_git_bash_without_git_falls_back(windows, monkeypatch):
    windows.setenv("MESH_SHELL", "git-bash")
    windows.setattr(mcp_tools, "_git_bash_windows", lambda: None)
    windows.setattr(mcp_tools, "powershell_argv", lambda: ["powershell.exe", "-Command"])
    assert mcp_tools.command_shell()[0] == "powershell"


def test_linux_still_uses_bash(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_IS_WINDOWS", False)
    monkeypatch.setattr(mcp_tools, "_SHELL_CACHE", None)
    name, argv = mcp_tools.command_shell()
    assert name in ("bash", "sh")
    assert argv[-1] == "-c"
    mcp_tools._SHELL_CACHE = None


# ---------------------------------------------------------------------------
# Telemetry commands
# ---------------------------------------------------------------------------

def test_windows_telemetry_is_powershell_and_not_posix():
    scripts = mcp_tools.system_info_windows_scripts()
    assert set(scripts) == {"disks", "memory", "load", "top_processes"}
    joined = " ".join(scripts.values())
    for cmdlet in ("Get-CimInstance", "Win32_LogicalDisk", "Win32_OperatingSystem",
                   "Get-Process", "LastBootUpTime"):
        assert cmdlet in joined
    # None of the POSIX tools that produced "not recognized" text may survive.
    for posix in ("df -h", "free -h", "uptime", "ps -eo", "head -"):
        assert posix not in joined


def test_posix_telemetry_unchanged():
    commands = mcp_tools.system_info_posix_commands()
    assert commands["load"] == "uptime"
    assert commands["memory"].startswith("free -h")
    assert commands["disks"].startswith("df -h")
    assert commands["top_processes"].startswith("ps -eo")


def test_cmd_fallback_uses_cmd_tools():
    commands = mcp_tools.system_info_cmd_commands()
    assert commands["top_processes"].startswith("tasklist")
    assert commands["disks"].startswith("wmic")


# ---------------------------------------------------------------------------
# Wallpaper
# ---------------------------------------------------------------------------

def test_largest_wallpaper_image_picks_biggest_file(tmp_path):
    package = tmp_path / "WhiteSur"
    (package / "contents" / "images").mkdir(parents=True)
    small = package / "contents" / "images" / "small.png"
    big = package / "contents" / "images" / "big.webp"
    small.write_bytes(b"x" * 10)
    big.write_bytes(b"x" * 4096)
    (package / "notes.txt").write_text("ignored")

    assert mcp_tools.largest_wallpaper_image(str(package)) == str(big)


def test_largest_wallpaper_image_passes_a_file_through(tmp_path):
    image = tmp_path / "wall.jpg"
    image.write_bytes(b"x" * 32)
    assert mcp_tools.largest_wallpaper_image(str(image)) == str(image)
    assert mcp_tools.largest_wallpaper_image("") == ""


def test_windows_wallpaper_is_silent_off_windows():
    """Without winreg the helper reports nothing instead of raising."""
    assert mcp_tools.windows_wallpaper() == ""


# ---------------------------------------------------------------------------
# system_info payload
# ---------------------------------------------------------------------------

def test_system_info_reports_the_command_shell(monkeypatch):
    info = mcp_tools.call_tool("system_info", {})
    assert info["command_shell"] in ("bash", "sh", "powershell", "cmd", "git-bash")
    assert "hostname" in info


def test_system_info_uses_platform_branches(monkeypatch):
    """Windows scripts run for Windows, POSIX commands everywhere else."""
    import platform as _platform
    import subprocess as _sp

    seen = []

    class FakeProc:
        stdout = "stub"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return FakeProc()

    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(mcp_tools, "powershell_argv", lambda: ["pwsh", "-Command"])
    monkeypatch.setattr(mcp_tools, "windows_wallpaper", lambda: r"C:\wall.jpg")

    info = mcp_tools.call_tool("system_info", {})

    assert info["desktop"].startswith("Windows")
    assert info["session_type"]
    assert info["wallpaper"] == r"C:\wall.jpg"
    scripts = [" ".join(argv) for argv in seen]
    assert any("Get-CimInstance" in script for script in scripts)
    assert not any("df -h" in script for script in scripts)


def test_system_info_windows_uses_cmd_when_powershell_missing(monkeypatch):
    import platform as _platform
    import subprocess as _sp

    seen = []

    class FakeProc:
        stdout = "stub"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return FakeProc()

    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(mcp_tools, "powershell_argv", lambda: None)
    monkeypatch.setattr(mcp_tools, "_SHELL_CACHE", ("cmd", ["cmd.exe", "/d", "/s", "/c"]))
    monkeypatch.setattr(mcp_tools, "windows_wallpaper", lambda: "")

    mcp_tools.call_tool("system_info", {})

    scripts = [" ".join(argv) for argv in seen]
    assert any("tasklist" in script for script in scripts)
    assert not any("df -h" in script for script in scripts)
