"""The installer's bootstrap list must name every module the node imports.

``install.sh`` is often the only thing a new node has: when it runs without the code
beside it, it *fetches a list of files* rather than a tree. That list was written by
hand and stopped at the modules that existed at the time, which is how a fresh phone
install ended up advertising ``device_info`` while answering "the device layer
(core/device.py) is missing from this checkout" - the tool was there, its module was
not. The same holds for a Windows node bootstrapped from the domain.

These tests fail the moment a core module (or the orchestration skill) exists that
the installer would not fetch, so the next feature cannot ship half-installed.
"""

import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relative):
    with open(os.path.join(REPO, relative), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def bootstrap_list() -> str:
    """The file list inside install.sh's bootstrap loop (the one with __init__.py)."""
    text = _read("install.sh")
    match = re.search(r"for f in ([^;]*core/__init__\.py[^;]*); do", text)
    assert match, "install.sh no longer has the bootstrap loop with core/__init__.py"
    return match.group(1)


def test_every_core_module_is_bootstrapped():
    listed = set(re.findall(r"core/[A-Za-z_][A-Za-z0-9_]*\.py", bootstrap_list()))
    shipped = {name for name in os.listdir(os.path.join(REPO, "core")) if name.endswith(".py")}
    missing = sorted("core/%s" % name for name in shipped if "core/%s" % name not in listed)
    assert missing == [], (
        "install.sh would not fetch these modules on a fresh node: %s - add them to the "
        "bootstrap list (and to the payload the gateway serves)" % missing)


def test_the_orchestration_skill_is_bootstrapped():
    assert "skills/orchestrator.md" in bootstrap_list(), (
        "get_orchestration_skill reads skills/orchestrator.md, so a bootstrapped node "
        "must fetch it")


def test_the_bootstrap_list_names_only_files_that_exist():
    for relative in re.findall(r"(core/[A-Za-z_][A-Za-z0-9_]*\.py|skills/[A-Za-z0-9_.-]+)",
                               bootstrap_list()):
        assert os.path.isfile(os.path.join(REPO, relative)), (
            "install.sh fetches %s, which is not in this tree" % relative)


@pytest.mark.skipif(os.name == "nt", reason="the bootstrap loop is POSIX shell")
def test_the_bootstrap_list_parses_as_shell(tmp_path):
    """A line-continuation mistake here would silently fetch nothing."""
    result = pytest.importorskip("subprocess").run(
        ["bash", "-n", os.path.join(REPO, "install.sh")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
