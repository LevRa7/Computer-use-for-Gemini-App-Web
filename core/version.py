"""core/version.py - the ONE place this node's version is declared.

Every other place that needs the node version asks this module:

* ``core/updater.py`` compares it against the newest GitHub release;
* ``core/mcp_tools.py`` reports it from ``mesh_status`` and ``mesh_update``;
* ``build-installer-exe.ps1`` refuses to build when ``package.json`` disagrees
  with it, so the npm package and the Python node can never ship as two
  different versions;
* ``tests/test_version_single_source.py`` pins that agreement.

The repository deliberately keeps no second literal: a version that lives in two
files is a version that eventually disagrees with itself, and an updater that
compares the wrong number either reinstalls forever or never updates at all.

Format: ``MAJOR.MINOR.PATCH``, optionally followed by a pre-release suffix
(``0.3.0-rc.1``). The suffix never changes the meaning of the three numbers; a
pre-release is older than the release it precedes, which is what
``core.updater.is_newer`` implements.
"""

#: The node version. Keep it in sync with ``package.json`` (the build enforces it).
__version__ = "0.4.0"

#: The repository that publishes the releases this node updates itself from.
#: Declared here as well so a checkout says where it came from; ``package.json``
#: carries the same slug under ``repository.url`` and a test pins the agreement.
REPO_SLUG = "LevRa7/Computer-use-for-Gemini-App-Web"

#: Anchored pattern for the only accepted spelling, used by the single-source test.
VERSION_PATTERN = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?$"
