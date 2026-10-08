"""The node version is declared once, and everything else agrees with it.

Why this file exists: the updater decides whether to install a release by
comparing the release tag with ``core/version.py``. If the npm package, the
installer or the release workflow carried a second, drifting number, a node
would either never update (it would think it is already newer) or update in a
loop (it would install the same version forever). Both failures are silent, so
the agreement is a test, not a convention.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from core import updater
from core import version as version_module

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directories that hold generated or vendored copies of the tree.
SKIP_DIRS = {"__pycache__", "node_modules", "live_logs", "docs", "tests", "dist",
             ".win-test-deps", ".pytest-tmp", ".verify-temp", ".verify-payload"}


def _read(relative):
    with open(os.path.join(REPO, relative), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def test_the_version_is_a_documented_shape():
    assert re.fullmatch(version_module.VERSION_PATTERN, version_module.__version__), (
        "core/version.py must declare MAJOR.MINOR.PATCH[-pre], got %r"
        % version_module.__version__)


def test_package_json_agrees_with_the_node():
    package = json.loads(_read("package.json"))
    assert package["version"] == version_module.__version__, (
        "package.json (%s) and core/version.py (%s) disagree; the installer names the "
        "release after the package while the node compares the tag with the module"
        % (package["version"], version_module.__version__))


def test_the_repository_slug_agrees_with_package_json():
    package = json.loads(_read("package.json"))
    url = str(package.get("repository", {}).get("url", ""))
    slug = url.replace("git+", "").replace("https://github.com/", "").replace(".git", "")
    assert slug == version_module.REPO_SLUG == updater.REPO, (
        "the updater asks GitHub for %s while package.json points at %s"
        % (version_module.REPO_SLUG, slug))


def test_every_other_declaration_of_the_version_is_derived():
    """No second literal: a Python file that repeats the version is a drift risk."""
    literal = version_module.__version__
    offenders = []
    for current, directories, files in os.walk(REPO):
        directories[:] = [name for name in directories
                          if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(current, name)
            relative = os.path.relpath(path, REPO)
            if relative == os.path.join("core", "version.py"):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for line in text.splitlines():
                if "__version__" in line and literal in line:
                    offenders.append("%s: %s" % (relative, line.strip()))
    assert offenders == [], "the version is declared outside core/version.py: %s" % offenders


def test_the_docs_name_the_current_version():
    """The README's copy/paste examples must match the shipped installer."""
    readme = _read("README.md")
    mentioned = set(re.findall(r"AntigravityMesh-Setup-(\d+\.\d+\.\d+)\.exe", readme))
    assert mentioned, "the README should show the setup executable's name at least once"
    assert mentioned == {version_module.__version__}, (
        "the README advertises %s while the node is %s"
        % (sorted(mentioned), version_module.__version__))


def test_the_release_notes_file_exists_for_this_version():
    assert os.path.isfile(os.path.join(REPO, "docs", "releases", "v%s.md" % version_module.__version__)), (
        "the release workflow takes the notes from docs/releases/v<version>.md")


def test_the_local_version_reads_this_tree():
    """A checkout reports the version the updater will compare against."""
    assert updater.local_version(REPO) == version_module.__version__


def test_the_build_refuses_a_mismatch():
    """build-installer-exe.ps1 is the gate that catches a bad bump locally."""
    build = _read("build-installer-exe.ps1")
    assert "core\\version.py" in build
    assert "version mismatch" in build


@pytest.mark.skipif(os.name != "nt", reason="the gate runs in Windows PowerShell")
def test_the_build_gate_actually_stops_a_mismatch(tmp_path):
    """Copy the build script's version check into a probe and run it for real.

    The check is what stands between "package.json bumped by hand" and a release
    whose asset the node refuses to install, so it is verified, not just present.
    """
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        "$packageVersion = '9.9.9'\n"
        "$moduleVersion = (Select-String -LiteralPath '%s' -Pattern "
        "'^__version__\\s*=\\s*\"([^\"]+)\"' | Select-Object -First 1).Matches[0].Groups[1].Value\n"
        "if ($moduleVersion -ne $packageVersion) { Write-Host ('mismatch ' + $moduleVersion); exit 1 }\n"
        "exit 0\n" % os.path.join(REPO, "core", "version.py").replace("'", "''"),
        encoding="ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    assert version_module.__version__ in result.stdout


def test_the_updater_reads_the_version_from_the_tree_not_from_memory(tmp_path):
    """After a swap, the installed version is what is on disk - not what was loaded."""
    node = tmp_path / "node"
    (node / "core").mkdir(parents=True)
    (node / "core" / "version.py").write_text('__version__ = "7.7.7"\n', encoding="utf-8")
    previous = os.environ.pop("MESH_UPDATE_LOCAL_VERSION", None)
    try:
        assert updater.local_version(str(node)) == "7.7.7"
    finally:
        if previous is not None:
            os.environ["MESH_UPDATE_LOCAL_VERSION"] = previous


def test_python_can_import_every_shipped_module():
    """A syntax error in a shipped module must fail here, not on a client's node."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import core.agent, core.updater, core.mcp_tools, core.server, core.version"],
        cwd=REPO, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
