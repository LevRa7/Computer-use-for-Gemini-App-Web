"""install.ps1 must never pin a Python it has not proven usable.

The incident this covers: the wizard's autostart launcher pointed at
``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe`` - the App Execution Alias of
the Microsoft Store Python package. The alias sits on PATH, it answers like an
interpreter, and after a Store repair or a Windows update it stops working: the only
trace was a truncated ``Python `` line in ``agent.log`` and a node that stayed
offline for a day.

These tests pin the rules that keep that path out of an autostart entry, plus the
properties the shipped copy must keep (a UTF-8 BOM for PowerShell 5.1, the domain
placeholder that ``deploy_gateway.sh`` rewrites).
"""

import os
import re
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(REPO, "install.ps1")

needs_windows = pytest.mark.skipif(os.name != "nt", reason="install.ps1 is the Windows installer")


def _text() -> str:
    with open(INSTALL, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _bytes() -> bytes:
    with open(INSTALL, "rb") as handle:
        return handle.read()


def _powershell() -> str:
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return candidate if os.path.exists(candidate) else "powershell.exe"


# ---------------------------------------------------------------------------
# Source-level rules
# ---------------------------------------------------------------------------

def test_store_alias_is_rejected_by_path():
    text = _text()
    assert "\\WindowsApps\\" in text, "the Microsoft Store alias must be recognised by path"
    assert "function Test-RealPython" in text
    assert "function Select-RealPython" in text


def test_probe_checks_sys_executable_not_only_the_major_version():
    """An alias can answer '3' while sys.executable points back into WindowsApps."""
    assert "sys.executable" in _text()


def test_autostart_runs_unbuffered_with_the_resolved_interpreter():
    assert re.search(r'WshShell\.Run "cmd /c .*?-u -m core\.agent', _text()), (
        "the launcher must run the agent with -u, so a killed process cannot leave a "
        "torn line as the only evidence"
    )


def test_installer_refuses_to_write_an_alias_autostart():
    text = _text()
    assert 'like "*$WindowsAppsMarker*"' in text
    assert "Refusing to write an autostart entry" in text


def test_watchdog_task_is_registered_by_the_installer():
    text = _text()
    assert "AntigravityMeshWatchdog" in text, "the node needs a self-healing scheduled task"
    assert "agent-watchdog.ps1" in text
    assert "Register-ScheduledTask" in text


def test_post_install_check_asks_the_gateway_about_the_node():
    text = _text()
    assert "/health?user=" in text
    assert "node_online" in text
    # install-gui.ps1 only extracts the MCP URL from a successful run, and that
    # link matters most exactly when the node is not up yet.
    assert "no non-zero exit code" in text


def test_dry_run_reports_the_node_state_as_the_sixth_line():
    """install-gui.ps1 reads the dry-run lines positionally; the node row is #6."""
    labels = re.findall(r'\[DRY-RUN\]\s+([^\s:]+)', _text())
    assert len(labels) == 14, labels          # two language blocks of seven lines
    assert labels[:6] == ["Python", "websockets", "Домен", "Конфиг", "Автозапуск", "Узел"]
    assert labels[7:13] == ["Python", "websockets", "Domain", "Config", "Autostart", "Node"]


# ---------------------------------------------------------------------------
# Properties of the published copy
# ---------------------------------------------------------------------------

def test_repository_copy_keeps_its_utf8_bom():
    """Windows PowerShell 5.1 decodes a BOM-less script with the ANSI code page and
    the Russian strings then stop being parseable (see README)."""
    assert _bytes()[:3] == b"\xef\xbb\xbf", "install.ps1 lost its UTF-8 BOM"


def test_published_copy_keeps_the_domain_placeholder():
    """deploy_gateway.sh rewrites the $PublishedDomain line only."""
    text = _text()
    assert "__MESH_DOMAIN__" in text
    assert '$PublishedDomain = "__MESH_DOMAIN__"' in text


# ---------------------------------------------------------------------------
# The packaged executable must carry the self-healing scripts
# ---------------------------------------------------------------------------

def test_setup_executable_payload_contains_the_ops_directory():
    """install.ps1 registers ``ops/windows/agent-watchdog.ps1`` and the README
    points at ``ops/doctor.ps1``; a node installed from the .exe receives only what
    the payload manifest ships, so both must be in it."""
    with open(os.path.join(REPO, "build-installer-exe.ps1"), "r", encoding="utf-8") as handle:
        manifest = handle.read()

    assert "'ops'" in manifest, "the payload manifest must ship ops/"
    assert os.path.isfile(os.path.join(REPO, "ops", "windows", "agent-watchdog.ps1"))
    assert os.path.isfile(os.path.join(REPO, "ops", "doctor.ps1"))


def test_every_shipping_surface_carries_the_ops_directory():
    """A node can reach the watchdog and the doctor through four different paths
    (npm package, .exe payload, the gateway's HTTP bootstrap, a plain clone). Each
    one has to ship ops/, or the self-healing task silently disappears."""
    with open(os.path.join(REPO, "package.json"), "r", encoding="utf-8") as handle:
        assert '"ops"' in handle.read(), "package.json 'files' must include ops"

    with open(os.path.join(REPO, "ops", "nginx", "antigravity-mesh-mcp.conf"),
              "r", encoding="utf-8") as handle:
        assert "location /ops/" in handle.read(), "nginx must serve /ops/ for the bootstrap"

    with open(os.path.join(REPO, "deploy_gateway.sh"), "r", encoding="utf-8") as handle:
        deploy = handle.read()
    assert "ops/windows/agent-watchdog.ps1" in deploy
    assert "$WWW_DIR/ops" in deploy

    installer = _text()
    assert "ops/windows/agent-watchdog.ps1" in installer, "the HTTP bootstrap must fetch the watchdog"
    assert "ops/doctor.ps1" in installer


def test_installer_registers_the_watchdog_from_the_shipped_path():
    """The registered task must point at the payload copy, not at a build path."""
    assert "Join-Path $ScriptDir 'ops\\windows\\agent-watchdog.ps1'" in _text()


def test_cli_answers_help_for_its_local_subcommands():
    """`doctor --help` used to be forwarded as `-help`, which the script's
    parameter block rejects with a PowerShell error."""
    with open(os.path.join(REPO, "bin", "cli.js"), "r", encoding="utf-8") as handle:
        cli = handle.read()

    assert "usage: gemini-computer-use doctor" in cli
    assert "usage: gemini-computer-use restart" in cli


# ---------------------------------------------------------------------------
# Behaviour: a broken alias first on PATH is not an interpreter
# ---------------------------------------------------------------------------

@needs_windows
def test_dry_run_never_pins_the_microsoft_store_alias(tmp_path):
    """The stub mimics the real alias: it prints ``Python `` and exits non-zero,
    which is exactly what the incident left in agent.log."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "python.cmd").write_text("@echo Python \r\n@exit /b 1\r\n", encoding="ascii")

    environment = dict(os.environ)
    environment["PATH"] = str(stub_dir) + os.pathsep + environment.get("PATH", "")
    environment["MESH_PUBLIC_URL"] = "https://mesh.example.test"

    result = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", INSTALL,
         "-DryRun", "-Lang", "en"],
        capture_output=True, text=True, env=environment, timeout=600)

    lines = [line for line in (result.stdout or "").splitlines()
             if line.startswith("[DRY-RUN] Python")]
    assert lines, (result.stdout or "") + (result.stderr or "")
    reported = lines[-1]
    if "not found" in reported:
        pytest.skip("no real Python 3 on this machine to fall back to")
    assert "WindowsApps" not in reported, reported
    assert "python" in reported.lower(), reported
