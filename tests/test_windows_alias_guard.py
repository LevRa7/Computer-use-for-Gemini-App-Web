"""The Microsoft Store Python alias must never be trusted as an interpreter.

The alias (``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe``) is on PATH, it
answers like an interpreter, and it stops working after a Store repair or a Windows
update. A node whose autostart pinned it stayed offline for a day, and the only trace
was a truncated ``Python `` line in ``agent.log``.

``install.ps1`` refuses it by path (see ``tests/test_installer_windows.py``). The
shell-side domain resolver has to refuse it too: leaving it as "the first python on
PATH" makes the resolver fall silent on exactly the machines that need the fallback.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVER = os.path.join(REPO, "ops", "mesh-domain.sh")


def _bash() -> str:
    """A usable bash: PATH on POSIX, Git for Windows' bash on Windows."""
    if os.name != "nt":
        return shutil.which("bash") or ""
    try:
        from core import mcp_tools

        return mcp_tools._git_bash_windows() or ""
    except Exception:
        return ""


BASH = _bash()
needs_bash = pytest.mark.skipif(not BASH, reason="bash is required for the shell-side check")


def test_the_resolver_skips_the_store_alias_by_path():
    with open(RESOLVER, "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "[Ww]indows[Aa]pps" in text, "the shell resolver must recognise the Store alias"
    assert "python3 python py" in text, "the py launcher is a real discovery path on Windows"


@needs_bash
def test_alias_only_path_is_refused_not_trusted(tmp_path):
    """A stub named like the real alias must never be asked for the domain.

    The stub prints ``Python `` and exits non-zero, exactly like the broken alias on
    the machine this was reported from.
    """
    alias_dir = tmp_path / "WindowsApps"
    alias_dir.mkdir()
    for name in ("python", "python3", "py"):
        stub = alias_dir / name
        stub.write_text('#!/bin/sh\nprintf "Python "\nexit 1\n', encoding="utf-8")
        stub.chmod(0o755)

    environment = dict(os.environ)
    environment["PATH"] = str(alias_dir) + os.pathsep + "/usr/bin:/bin"
    environment.pop("MESH_PUBLIC_URL", None)
    environment.pop("AGY_PUBLIC_BASE_URL", None)
    environment["MESH_DOMAIN_FILE"] = str(tmp_path / "absent.env")

    script = ('cd /tmp && . "%s" && mesh_resolve_domain "%s" && echo RESOLVED || echo REFUSED'
              % (RESOLVER.replace("\\", "/"), REPO.replace("\\", "/")))
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                            env=environment, timeout=60)

    assert "REFUSED" in result.stdout, (result.stdout or "") + (result.stderr or "")
    assert "RESOLVED" not in result.stdout
