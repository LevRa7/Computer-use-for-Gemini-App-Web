"""core/updater.py - the self-update contract, checked without a network.

What these tests pin down, in the order the failures would hurt:

* a version that cannot be compared is never installed (no restart loops);
* a payload is only applied when its published SHA-256 matches - the download,
  the extraction and the swap are exercised through real ``file://`` assets and
  a real archive, not mocks;
* an interrupted swap rolls back on the next start instead of leaving a node
  with half of two versions;
* the agent is actually replaced: the helper stops a foreign agent that would
  otherwise hold the instance lock forever;
* the update is found the same way from the agent, the tool, the CLI and the
  Windows scheduled task, because all four call these functions.
"""

import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from core import updater
from core import version as version_module

REPO = Path(__file__).resolve().parent.parent

#: The files verify_payload() insists on - a payload without them is not a node.
REQUIRED_FILES = ("agent.py", "mcp_tools.py", "updater.py", "version.py", "__init__.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_payload_builder():
    """Import tools/build-payload-zip.py (its name is not a module name)."""
    path = REPO / "tools" / "build-payload-zip.py"
    spec = importlib.util.spec_from_file_location("mesh_build_payload_zip", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_payload_builder()


def make_node(root: Path, version: str, marker: str = "") -> Path:
    """A minimal but valid node install tree."""
    root = Path(root)
    (root / "core").mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        (root / "core" / name).write_text("# %s %s\n" % (name, marker), encoding="utf-8")
    (root / "core" / "version.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")
    (root / "skills").mkdir(exist_ok=True)
    (root / "skills" / "orchestrator.md").write_text("skill %s\n" % marker, encoding="utf-8")
    (root / "ops").mkdir(exist_ok=True)
    (root / "ops" / "doctor.ps1").write_text("# doctor %s\n" % marker, encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"version": version}) + "\n", encoding="utf-8")
    # A file the updater must never touch: it belongs to the operator, not the payload.
    (root / "install-gui.strings.json").write_text('{"keep": true}\n', encoding="utf-8")
    return root


def make_payload_zip(source_root: Path, destination: Path, version: str) -> Path:
    """Build a real payload archive with the project's own builder."""
    BUILDER.build(str(destination), root=str(source_root), version=version)
    return Path(destination)


def asset(name: str, url: str, *, size: int = 0, digest: str = "") -> dict:
    """One asset in the shape core/updater.py normalises GitHub's answer into."""
    return {"name": name, "url": url, "size": size, "digest": digest,
            "content_type": "application/octet-stream"}


def release_dict(version: str, payload_zip: Path, *, tag: str = "", assets=None) -> dict:
    """A release as the updater sees it, with file:// asset URLs."""
    tag = tag or ("v%s" % version)
    if assets is None:
        digest = updater.sha256_file(str(payload_zip))
        checksum = payload_zip.with_name(payload_zip.name + ".sha256")
        checksum.write_text("%s  %s\n" % (digest, payload_zip.name), encoding="ascii")
        assets = [
            asset(payload_zip.name, payload_zip.as_uri(), size=payload_zip.stat().st_size),
            asset(checksum.name, checksum.as_uri(), size=checksum.stat().st_size),
        ]
    return {
        "tag": tag,
        "version": version,
        "parsed": updater.parse_version(version),
        "name": tag,
        "notes": "notes",
        "published_at": "2026-01-01T00:00:00Z",
        "html_url": "https://example.invalid/%s" % tag,
        "prerelease": False,
        "assets": assets,
        "tarball_url": "",
        "zipball_url": "",
    }


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A config whose every path lives in the test's temporary directory."""
    node = make_node(tmp_path / "node", "0.2.6", marker="old")
    work = tmp_path / "release"
    work.mkdir()
    for name in ("MESH_UPDATE_CHECK", "MESH_UPDATE_AUTO", "MESH_UPDATE_RESTART",
                 "MESH_UPDATE_CHECK_INTERVAL", "MESH_UPDATE_CHANNEL", "MESH_UPDATE_TOKEN",
                 "MESH_UPDATE_REPO", "MESH_UPDATE_ALLOW_UNVERIFIED", "MESH_UPDATE_KEEP_BACKUPS",
                 "MESH_UPDATE_DRY_RUN", "MESH_UPDATE_STATE", "MESH_UPDATE_STATE_FILE",
                 "MESH_UPDATE_DIR", "MESH_INSTALL_DIR", "MESH_UPDATE_LOCAL_VERSION",
                 "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MESH_UPDATE_DIR", str(node))
    monkeypatch.setenv("MESH_UPDATE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(updater, "_RESTART_HOOK", None)
    # Every other path the module derives from its module-level constants is
    # redirected too: a test must never write into the live node's config
    # directory, or the running agent would find a journal about a tree that
    # does not exist.
    for name, filename in (("STATE_FILE", "state.json"), ("JOURNAL_FILE", "journal.json"),
                           ("LOG_FILE", "update.log"), ("BACKUP_ROOT", "backups"),
                           ("HEARTBEAT_FILE", "agent.heartbeat")):
        monkeypatch.setattr(updater, name, str(tmp_path / filename))
    config = updater.config_from_env()
    config.update({
        "state_file": str(tmp_path / "state.json"),
        "journal_file": str(tmp_path / "journal.json"),
        "log_file": str(tmp_path / "update.log"),
        "backup_root": str(tmp_path / "backups"),
        "heartbeat_file": str(tmp_path / "agent.heartbeat"),
        "install_dir": str(node),
    })
    return {"config": config, "node": node, "work": work, "tmp": tmp_path}


def staged_release(settings, version="0.3.0", *, marker="new"):
    """A release whose payload zip is a real archive of a newer node tree."""
    source = make_node(settings["tmp"] / ("src-%s" % version), version, marker=marker)
    archive = settings["work"] / ("mesh-payload-%s.zip" % version)
    make_payload_zip(source, archive, version)
    return release_dict(version, archive), source


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3, 1, ())),
    ("v1.2.3", (1, 2, 3, 1, ())),
    (" v0.0.1 ", (0, 0, 1, 1, ())),
    ("2.0.0-rc.1", (2, 0, 0, 0, ((1, 0, "rc"), (0, 1, "")))),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "main", "1.2", "1.2.3.4", "release-2026", None])
def test_parse_version_rejects_what_it_cannot_compare(text):
    assert updater.parse_version(text) is None


@pytest.mark.parametrize("candidate,current,newer", [
    ("0.3.0", "0.2.6", True),
    ("0.2.6", "0.2.6", False),
    ("0.2.5", "0.2.6", False),
    ("1.0.0", "0.99.99", True),
    # A release outranks its own pre-releases...
    ("0.3.0", "0.3.0-rc.1", True),
    # ...and a pre-release never outranks the release it precedes.
    ("0.3.0-rc.1", "0.3.0", False),
    ("0.3.0-rc.2", "0.3.0-rc.1", True),
    ("0.3.0-beta", "0.3.0-rc", False),
    # npm-style build metadata is not a pre-release marker here.
    ("0.3.0-rc.10", "0.3.0-rc.9", True),
])
def test_is_newer(candidate, current, newer):
    assert updater.is_newer(candidate, current) is newer


def test_an_unparseable_candidate_is_never_newer():
    """Guessing here is what makes a node reinstall the same release forever."""
    assert updater.is_newer("main", "0.2.6") is False
    assert updater.is_newer("v0.3.0", "not-a-version") is False


def test_version_from_tag():
    assert updater.version_from_tag("v0.3.0") == "0.3.0"
    assert updater.version_from_tag("0.3.0-rc.1") == "0.3.0-rc.1"
    assert updater.version_from_tag("release-2026") is None


def test_local_version_reads_the_install_tree(settings, monkeypatch):
    monkeypatch.delenv("MESH_UPDATE_LOCAL_VERSION", raising=False)
    assert updater.local_version(str(settings["node"])) == "0.2.6"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_config_defaults_are_the_documented_ones(monkeypatch, tmp_path):
    for name in ("MESH_UPDATE_CHECK", "MESH_UPDATE_AUTO", "MESH_UPDATE_RESTART",
                 "MESH_UPDATE_CHANNEL", "MESH_UPDATE_ALLOW_UNVERIFIED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MESH_UPDATE_DIR", str(tmp_path))
    config = updater.config_from_env()
    assert config["enabled"] is True
    assert config["auto"] is True
    assert config["restart"] is True
    assert config["channel"] == "stable"
    assert config["prerelease"] is False
    assert config["allow_unverified"] is False
    assert config["interval"] == updater.DEFAULT_CHECK_INTERVAL
    assert config["install_dir"] == str(tmp_path)


@pytest.mark.parametrize("value,expected", [("0", False), ("false", False), ("no", False),
                                            ("1", True), ("TRUE", True), ("on", True)])
def test_env_flags(monkeypatch, value, expected):
    monkeypatch.setenv("MESH_UPDATE_AUTO", value)
    assert updater.config_from_env()["auto"] is expected


def test_a_too_short_check_interval_is_clamped(monkeypatch):
    """A node must not poll GitHub once a minute: 60 requests/hour is the limit."""
    monkeypatch.setenv("MESH_UPDATE_CHECK_INTERVAL", "5")
    assert updater.config_from_env()["interval"] == updater.MIN_CHECK_INTERVAL


def test_prerelease_channel_and_token_precedence(monkeypatch):
    monkeypatch.setenv("MESH_UPDATE_CHANNEL", "prerelease")
    monkeypatch.setenv("GITHUB_TOKEN", "from-github")
    monkeypatch.setenv("MESH_UPDATE_TOKEN", "from-mesh")
    config = updater.config_from_env()
    assert config["prerelease"] is True
    assert config["channel"] == "prerelease"
    assert config["token"] == "from-mesh"
    monkeypatch.delenv("MESH_UPDATE_TOKEN")
    assert updater.config_from_env()["token"] == "from-github"


# ---------------------------------------------------------------------------
# Release payloads and assets
# ---------------------------------------------------------------------------

def test_release_payload_is_normalised():
    release = updater._release_from_payload({
        "tag_name": "v0.3.0", "name": "Antigravity Mesh 0.3.0", "body": "notes",
        "published_at": "2026-01-01T00:00:00Z", "html_url": "https://example.invalid",
        "prerelease": False, "draft": False,
        "assets": [{"name": "mesh-payload-0.3.0.zip", "browser_download_url": "https://x/y.zip",
                    "size": 10}, {"name": "", "browser_download_url": "https://x/z"}],
        "tarball_url": "https://x/tar.gz",
    })
    assert release["version"] == "0.3.0"
    assert release["tag"] == "v0.3.0"
    assert [asset["name"] for asset in release["assets"]] == ["mesh-payload-0.3.0.zip"]


def test_drafts_and_unversioned_tags_are_skipped():
    assert updater._release_from_payload({"tag_name": "v0.3.0", "draft": True}) is None
    assert updater._release_from_payload({"tag_name": "release-2026"}) is None


def test_long_release_notes_are_bounded():
    release = updater._release_from_payload({"tag_name": "v1.0.0", "body": "x" * 10000})
    assert len(release["notes"]) == 4000


def test_asset_ladder_prefers_the_payload_archive(tmp_path):
    archive = tmp_path / "mesh-payload-0.3.0.zip"
    archive.write_bytes(b"payload")
    release = release_dict("0.3.0", archive)
    chosen = updater.choose_asset(release)
    assert chosen["kind"] == "payload"
    assert chosen["verified"] is True


def test_asset_ladder_falls_back_to_the_setup_exe_on_windows(tmp_path):
    release = release_dict("0.3.0", tmp_path / "mesh-payload-0.3.0.zip", assets=[
        asset("AntigravityMesh-Setup-0.3.0.exe", "https://x/AntigravityMesh-Setup-0.3.0.exe", size=10),
    ])
    release["tarball_url"] = "https://x/tar.gz"
    chosen = updater.choose_asset(release, platform="win32")
    assert chosen["kind"] == "setup"
    # POSIX has no setup executable, so the source archive is the last resort -
    # and it is the one asset GitHub publishes no checksum for.
    fallback = updater.choose_asset(release, platform="linux")
    assert fallback["kind"] == "tarball"
    assert fallback["verified"] is False


def test_no_usable_asset_is_reported(tmp_path):
    release = release_dict("0.3.0", tmp_path / "x.zip", assets=[])
    assert updater.choose_asset(release) is None


def test_checksum_comes_from_the_published_sha256_asset(tmp_path):
    archive = tmp_path / "mesh-payload-0.3.0.zip"
    archive.write_bytes(b"payload")
    release = release_dict("0.3.0", archive)
    assert updater._checksum_for(release, archive.name) == updater.sha256_file(str(archive))


def test_checksum_falls_back_to_the_asset_digest(tmp_path):
    release = release_dict("0.3.0", tmp_path / "mesh-payload-0.3.0.zip", assets=[
        asset("mesh-payload-0.3.0.zip", "https://x/y.zip", size=1, digest="sha256:" + "a" * 64),
    ])
    assert updater._checksum_for(release, "mesh-payload-0.3.0.zip") == "a" * 64


# ---------------------------------------------------------------------------
# Checking for an update
# ---------------------------------------------------------------------------

def test_check_reports_a_newer_release(settings, monkeypatch):
    release, _source = staged_release(settings)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    result = updater.check_for_update(config=settings["config"], force=True)
    assert result["ok"] is True
    assert result["current"] == "0.2.6"
    assert result["latest"] == "0.3.0"
    assert result["update_available"] is True
    assert result["asset"]["kind"] == "payload"
    assert result["cached"] is False


def test_check_says_up_to_date_for_the_same_version(settings, monkeypatch):
    release, _source = staged_release(settings, version="0.2.6")
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    result = updater.check_for_update(config=settings["config"], force=True)
    assert result["update_available"] is False
    assert result["reason"] == "up to date"


def test_the_second_check_is_served_from_the_state_file(settings, monkeypatch):
    """An unauthenticated client gets 60 requests an hour; a node must not spend them."""
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return staged_release(settings)[0]

    monkeypatch.setattr(updater, "fetch_latest_release", fake_fetch)
    first = updater.check_for_update(config=settings["config"], force=True)
    second = updater.check_for_update(config=settings["config"])
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["update_available"] is True
    assert len(calls) == 1


def test_force_ignores_the_interval(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: (calls.append(1), staged_release(settings)[0])[1])
    updater.check_for_update(config=settings["config"], force=True)
    updater.check_for_update(config=settings["config"], force=True)
    assert len(calls) == 2


def test_checking_can_be_switched_off(settings, monkeypatch):
    config = dict(settings["config"], enabled=False)
    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: pytest.fail("a disabled check must not call GitHub"))
    result = updater.check_for_update(config=config, force=True)
    assert result["ok"] is True
    assert "disabled" in result["reason"]


def test_offline_check_never_touches_the_network(settings, monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: pytest.fail("offline means offline"))
    result = updater.check_for_update(config=settings["config"], offline=True)
    assert result["ok"] is True
    assert "offline" in result["reason"]


def test_a_rate_limit_is_an_answer_not_a_crash(settings, monkeypatch):
    def limited(**kwargs):
        raise updater.RateLimited("GitHub rate limit reached (HTTP 403)", retry_after=60)

    monkeypatch.setattr(updater, "fetch_latest_release", limited)
    result = updater.check_for_update(config=settings["config"], force=True)
    assert result["ok"] is True
    assert result["update_available"] is False
    assert "rate limit" in result["reason"]


def test_a_failed_check_is_reported_and_not_remembered_as_an_update(settings, monkeypatch):
    def broken(**kwargs):
        raise updater.UpdateError("network error reaching api.github.com")

    monkeypatch.setattr(updater, "fetch_latest_release", broken)
    result = updater.check_for_update(config=settings["config"], force=True)
    assert result["ok"] is False
    assert "check failed" in result["reason"]
    state = json.loads(Path(settings["config"]["state_file"]).read_text(encoding="utf-8"))
    assert "update_available" not in state


def test_http_get_turns_github_statuses_into_data(settings, monkeypatch):
    """404 and 403 are answers: only transport failures raise."""
    monkeypatch.setattr(updater, "http_get", lambda url, **kwargs: (404, {}, b"{}"))
    with pytest.raises(updater.UpdateError) as error:
        updater.fetch_latest_release(repo="someone/nothing")
    assert "404" in str(error.value)

    monkeypatch.setattr(updater, "http_get",
                        lambda url, **kwargs: (403, {"x-ratelimit-remaining": "0"}, b"limit"))
    with pytest.raises(updater.RateLimited):
        updater.fetch_latest_release(repo="someone/nothing")


def test_prerelease_channel_picks_the_newest_parseable_release(monkeypatch):
    payload = [
        {"tag_name": "nightly-2026", "draft": False},
        {"tag_name": "v0.4.0-rc.2", "draft": False, "assets": []},
        {"tag_name": "v0.3.9", "draft": False, "assets": []},
    ]
    monkeypatch.setattr(updater, "http_get",
                        lambda url, **kwargs: (200, {}, json.dumps(payload).encode()))
    release = updater.fetch_latest_release(repo="owner/name", prerelease=True)
    assert release["version"] == "0.4.0-rc.2"


def test_a_304_means_nothing_changed(monkeypatch):
    monkeypatch.setattr(updater, "http_get", lambda url, **kwargs: (304, {}, b""))
    assert updater.fetch_latest_release(repo="owner/name") is None


# ---------------------------------------------------------------------------
# Downloading and staging
# ---------------------------------------------------------------------------

def test_download_verifies_the_published_hash(tmp_path):
    source = tmp_path / "asset.bin"
    source.write_bytes(b"payload-bytes")
    digest = updater.sha256_file(str(source))
    result = updater.download_to_file(source.as_uri(), str(tmp_path / "copy.bin"),
                                      expected_sha256=digest)
    assert result["verified"] is True
    assert result["sha256"] == digest
    assert (tmp_path / "copy.bin").read_bytes() == b"payload-bytes"


def test_download_refuses_a_hash_mismatch_and_leaves_nothing_behind(tmp_path):
    source = tmp_path / "asset.bin"
    source.write_bytes(b"payload-bytes")
    with pytest.raises(updater.UpdateError) as error:
        updater.download_to_file(source.as_uri(), str(tmp_path / "copy.bin"),
                                 expected_sha256="b" * 64)
    assert "SHA-256 mismatch" in str(error.value)
    assert not (tmp_path / "copy.bin").exists()
    assert not (tmp_path / "copy.bin.part").exists()


def test_stage_payload_extracts_a_verified_archive(settings):
    release, _source = staged_release(settings)
    staged = updater.stage_payload(release, config=settings["config"])
    assert staged["kind"] == "payload"
    assert staged["verified"] is True
    updater.verify_payload(staged["root"])
    assert Path(staged["root"], "core", "version.py").is_file()


def test_stage_refuses_a_payload_without_a_published_checksum(settings, tmp_path):
    """Fail closed: nothing is installed that cannot be checked."""
    source = make_node(tmp_path / "src", "0.3.0")
    archive = tmp_path / "mesh-payload-0.3.0.zip"
    make_payload_zip(source, archive, "0.3.0")
    release = release_dict("0.3.0", archive, assets=[
        asset(archive.name, archive.as_uri(), size=archive.stat().st_size),
    ])
    with pytest.raises(updater.UpdateError) as error:
        updater.stage_payload(release, config=settings["config"])
    assert "no published SHA-256" in str(error.value)

    config = dict(settings["config"], allow_unverified=True)
    staged = updater.stage_payload(release, config=config)
    assert staged["verified"] is False


def test_stage_refuses_the_source_tarball_unless_allowed(settings, tmp_path):
    release = release_dict("0.3.0", tmp_path / "mesh-payload-0.3.0.zip", assets=[])
    release["tarball_url"] = "https://example.invalid/tar.gz"
    with pytest.raises(updater.UpdateError) as error:
        updater.stage_payload(release, config=settings["config"])
    assert "MESH_UPDATE_ALLOW_UNVERIFIED" in str(error.value)


def test_an_incomplete_payload_is_refused(tmp_path):
    incomplete = tmp_path / "incomplete"
    (incomplete / "core").mkdir(parents=True)
    (incomplete / "core" / "agent.py").write_text("x", encoding="utf-8")
    with pytest.raises(updater.UpdateError) as error:
        updater.verify_payload(str(incomplete))
    assert "incomplete" in str(error.value)


def test_extraction_handles_windows_separators_and_refuses_escapes(tmp_path):
    """A Compress-Archive payload names its entries ``core\\agent.py``."""
    archive = tmp_path / "backslashes.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("core\\agent.py", "content")
    target = tmp_path / "out"
    target.mkdir()
    updater._safe_extract_zip(str(archive), str(target))
    assert (target / "core" / "agent.py").read_text(encoding="utf-8") == "content"

    hostile = tmp_path / "hostile.zip"
    with zipfile.ZipFile(hostile, "w") as bundle:
        bundle.writestr("../escaped.py", "content")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract_zip(str(hostile), str(target))


def test_the_payload_builder_is_reproducible_and_leaves_bytecode_out(settings):
    source = make_node(settings["tmp"] / "src-build", "0.3.0", marker="new")
    cache = source / "core" / "__pycache__"
    cache.mkdir()
    (cache / "agent.cpython-313.pyc").write_bytes(b"stale bytecode")
    first = settings["work"] / "first.zip"
    second = settings["work"] / "second.zip"
    make_payload_zip(source, first, "0.3.0")
    make_payload_zip(source, second, "0.3.0")
    assert updater.sha256_file(str(first)) == updater.sha256_file(str(second))
    with zipfile.ZipFile(first) as bundle:
        names = bundle.namelist()
    assert "core/agent.py" in names
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc")]


# ---------------------------------------------------------------------------
# Applying: backup, rollback, recovery
# ---------------------------------------------------------------------------

def test_apply_payload_replaces_the_node_and_keeps_a_backup(settings):
    release, source = staged_release(settings)
    staged = updater.stage_payload(release, config=settings["config"])
    result = updater.apply_payload(staged["root"], str(settings["node"]),
                                   config=settings["config"], from_version="0.2.6",
                                   to_version="0.3.0")
    assert result["applied"] is True
    assert result["pycache_removed"] >= 0            # a count, not a failure
    assert updater.local_version(str(settings["node"])) == "0.3.0"
    # The operator's own files are untouched.
    assert (settings["node"] / "install-gui.strings.json").is_file()
    # The previous version is recoverable, and the marker names the new one.
    assert (Path(result["backup"]) / "core" / "version.py").is_file()
    assert (settings["node"] / "VERSION").read_text(encoding="utf-8").strip() == "0.3.0"
    journal = json.loads(Path(settings["config"]["journal_file"]).read_text(encoding="utf-8"))
    assert journal["state"] == "applied"


def test_apply_payload_rolls_back_when_a_move_fails(settings, monkeypatch):
    """A failure halfway through must not leave a node with two versions mixed."""
    release, _source = staged_release(settings)
    staged = updater.stage_payload(release, config=settings["config"])
    original = settings["node"] / "core" / "version.py"
    before = original.read_text(encoding="utf-8")
    calls = {"count": 0}
    real_move = updater._move_with_retry

    def flaky(source, destination, attempts=5):
        calls["count"] += 1
        if calls["count"] == 4:               # midway through the swap
            raise updater.UpdateError("simulated sharing violation")
        return real_move(source, destination, attempts)

    monkeypatch.setattr(updater, "_move_with_retry", flaky)
    with pytest.raises(updater.UpdateError) as error:
        updater.apply_payload(staged["root"], str(settings["node"]),
                              config=settings["config"], from_version="0.2.6",
                              to_version="0.3.0")
    assert "previous version was restored" in str(error.value)
    assert updater.local_version(str(settings["node"])) == "0.2.6"
    assert original.read_text(encoding="utf-8") == before
    # The journal still says "applying": recover_pending() settles it next start.
    journal = json.loads(Path(settings["config"]["journal_file"]).read_text(encoding="utf-8"))
    assert journal["state"] == "applying"


def test_recovery_rolls_back_an_interrupted_swap(settings):
    release, _source = staged_release(settings)
    staged = updater.stage_payload(release, config=settings["config"])
    # Simulate a process killed after the first directory was replaced.
    backup = Path(settings["config"]["backup_root"]) / "0.2.6-test"
    backup.mkdir(parents=True)
    shutil.move(str(settings["node"] / "core"), str(backup / "core"))
    shutil.move(str(Path(staged["root"]) / "core"), str(settings["node"] / "core"))
    assert updater.local_version(str(settings["node"])) == "0.3.0"
    Path(settings["config"]["journal_file"]).write_text(json.dumps({
        "state": "applying", "from": "0.2.6", "to": "0.3.0",
        "install_dir": str(settings["node"]), "backup": str(backup),
        "names": ["core", "skills", "ops"],
    }), encoding="utf-8")

    result = updater.recover_pending(config=settings["config"])
    assert result["action"] == "rolled back"
    assert updater.local_version(str(settings["node"])) == "0.2.6"
    assert not Path(settings["config"]["journal_file"]).exists()


def test_recovery_completes_a_swap_that_only_missed_bookkeeping(settings):
    Path(settings["config"]["journal_file"]).write_text(json.dumps({
        "state": "swapped", "from": "0.2.6", "to": "0.3.0",
        "install_dir": str(settings["node"]), "backup": "",
        "names": ["core"],
    }), encoding="utf-8")
    result = updater.recover_pending(config=settings["config"])
    assert result["action"] == "completed"
    assert (settings["node"] / "VERSION").read_text(encoding="utf-8").strip() == "0.3.0"
    assert not Path(settings["config"]["journal_file"]).exists()


def test_recovery_is_a_no_op_without_a_journal(settings):
    assert updater.recover_pending(config=settings["config"]) is None


def test_old_backups_are_pruned(settings, tmp_path):
    root = tmp_path / "backups"
    for index in range(5):
        entry = root / ("0.2.%d" % index)
        entry.mkdir(parents=True)
        os.utime(entry, (1000 + index, 1000 + index))
    assert updater.prune_backups(str(root), 2) == 3
    assert sorted(path.name for path in root.iterdir()) == ["0.2.3", "0.2.4"]


# ---------------------------------------------------------------------------
# apply_update and the background tick
# ---------------------------------------------------------------------------

def test_apply_update_installs_the_release(settings, monkeypatch):
    release, _source = staged_release(settings)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    result = updater.apply_update(config=settings["config"], force=True, restart=False)
    assert result["ok"] is True
    assert result["applied"] is True
    assert result["restart_scheduled"] is False
    assert updater.local_version(str(settings["node"])) == "0.3.0"
    state = json.loads(Path(settings["config"]["state_file"]).read_text(encoding="utf-8"))
    assert state["last_applied"] == "0.3.0"
    assert state["update_available"] is False


def test_apply_update_does_nothing_when_up_to_date(settings, monkeypatch):
    release, _source = staged_release(settings, version="0.2.6")
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    result = updater.apply_update(config=settings["config"], force=True, restart=False)
    assert result["applied"] is False
    assert "up to date" in result["reason"]


def test_a_failed_apply_is_backed_off(settings, monkeypatch):
    """A node must not re-download a broken release on every single check."""
    release, _source = staged_release(settings)
    release["assets"] = [
        asset("mesh-payload-0.3.0.zip", "file:///nonexistent.zip", size=10),
    ]
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    first = updater.apply_update(config=settings["config"], force=True, restart=False)
    assert first["ok"] is False
    second = updater.apply_update(config=settings["config"], restart=False)
    assert second["applied"] is False
    assert "previous attempt failed" in second["reason"]
    # A forced attempt ignores the backoff, which is what an operator asking for
    # "update now" expects.
    third = updater.apply_update(config=settings["config"], force=True, restart=False)
    assert third["ok"] is False


def test_a_dry_run_verifies_and_changes_nothing(settings, monkeypatch):
    release, _source = staged_release(settings)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    config = dict(settings["config"], dry_run=True)
    result = updater.apply_update(config=config, force=True, restart=False)
    assert result["applied"] is False
    assert "dry run" in result["reason"]
    assert updater.local_version(str(settings["node"])) == "0.2.6"


def test_background_tick_applies_an_update_and_asks_for_a_restart(settings, monkeypatch):
    release, _source = staged_release(settings)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    monkeypatch.setattr(updater, "spawn_restart_helper",
                        lambda *a, **k: {"ok": True, "pid": 1, "target_pid": 1, "foreign": False})
    restarted = []
    updater.set_restart_hook(lambda: restarted.append(True))
    tick = updater.background_tick(config=settings["config"])
    assert tick["restart"] is True
    assert tick["apply"]["applied"] is True
    assert restarted == [True]
    assert updater.local_version(str(settings["node"])) == "0.3.0"


def test_the_background_tick_only_reports_when_auto_is_off(settings, monkeypatch):
    release, _source = staged_release(settings)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    monkeypatch.setattr(updater, "spawn_restart_helper",
                        lambda *a, **k: pytest.fail("MESH_UPDATE_AUTO=0 must not install"))
    tick = updater.background_tick(config=dict(settings["config"], auto=False))
    assert tick["restart"] is False
    assert "MESH_UPDATE_AUTO=0" in tick["reason"]
    assert updater.local_version(str(settings["node"])) == "0.2.6"


def test_status_reads_local_state_only(settings, monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: pytest.fail("status must not use the network"))
    status = updater.status(config=settings["config"])
    assert status["current"] == "0.2.6"
    assert status["install_dir"] == str(settings["node"])
    assert status["auto"] is True


# ---------------------------------------------------------------------------
# Restarting the node
# ---------------------------------------------------------------------------

def test_a_stale_heartbeat_does_not_name_a_running_agent(settings):
    heartbeat = Path(settings["config"]["heartbeat_file"])
    heartbeat.write_text(json.dumps({"ts": 1.0, "pid": os.getpid()}), encoding="utf-8")
    assert updater.running_agent_pid(settings["config"]) == 0


def test_running_agent_pid_ignores_this_process(settings):
    import time
    heartbeat = Path(settings["config"]["heartbeat_file"])
    heartbeat.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}), encoding="utf-8")
    assert updater.running_agent_pid(settings["config"]) == 0


def test_running_agent_pid_ignores_a_pid_that_is_gone(settings):
    import time
    heartbeat = Path(settings["config"]["heartbeat_file"])
    heartbeat.write_text(json.dumps({"ts": time.time(), "pid": 2 ** 22}), encoding="utf-8")
    assert updater.running_agent_pid(settings["config"]) == 0
    assert updater._pid_alive(2 ** 22) is False
    assert updater._pid_alive(os.getpid()) is True


def test_wait_for_node_needs_a_newer_heartbeat_from_another_pid(settings):
    import time
    heartbeat = Path(settings["config"]["heartbeat_file"])
    now = time.time()
    heartbeat.write_text(json.dumps({"ts": now - 100, "pid": 4242}), encoding="utf-8")
    assert updater.wait_for_node(str(heartbeat), previous_pid=4242, after=now - 50,
                                 timeout=1.0) is False
    heartbeat.write_text(json.dumps({"ts": now + 1, "pid": 4343}), encoding="utf-8")
    assert updater.wait_for_node(str(heartbeat), previous_pid=4242, after=now - 50,
                                 timeout=1.0) is True


def test_an_in_process_apply_targets_its_own_pid(settings, monkeypatch):
    spawned = {}

    class FakeProcess:
        pid = 999

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    result = updater.spawn_restart_helper(str(settings["node"]), config=settings["config"])
    assert result["ok"] is True
    assert result["target_pid"] == os.getpid()
    assert result["foreign"] is False
    assert "--restart-helper" in spawned["argv"]
    assert "core.updater" in spawned["argv"]


def test_the_helper_stops_a_foreign_agent_that_would_hold_the_lock(settings, monkeypatch):
    """An update from the CLI or the scheduled task leaves the old agent running."""
    import time
    heartbeat = Path(settings["config"]["heartbeat_file"])
    heartbeat.write_text(json.dumps({"ts": time.time(), "pid": 5150}), encoding="utf-8")
    monkeypatch.setattr(updater, "_pid_alive", lambda pid: True)
    spawned = {}

    class FakeProcess:
        pid = 1

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    result = updater.spawn_restart_helper(str(settings["node"]), config=settings["config"])
    assert result["target_pid"] == 5150
    assert result["foreign"] is True
    # A foreign agent has to be asked by the helper, so its wait is short.
    assert spawned["argv"][spawned["argv"].index("--wait") + 1] == "25.0"


def test_stop_agent_refuses_to_kill_this_process(settings, monkeypatch):
    monkeypatch.setattr(updater, "_pid_alive", lambda pid: True)
    result = updater.stop_agent(os.getpid(), config=settings["config"])
    assert result["stopped"] is False
    assert result["reason"] == "nothing to stop"


def test_restart_helper_main_reports_success(settings, monkeypatch):
    monkeypatch.setattr(updater, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(updater, "restart_node", lambda *a, **k: {"ok": True, "method": "test"})
    assert updater.restart_helper_main(12345, str(settings["node"]), 5.0) == 0


def test_restart_helper_main_reports_failure(settings, monkeypatch):
    monkeypatch.setattr(updater, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(updater, "restart_node", lambda *a, **k: {"ok": False})
    assert updater.restart_helper_main(12345, str(settings["node"]), 5.0) == 3


def test_a_broken_restart_hook_does_not_undo_an_update():
    updater.set_restart_hook(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        assert updater._notify_restart() is False
    finally:
        updater.set_restart_hook(None)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def test_cli_status_prints_json(settings, monkeypatch, capsys):
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    assert updater.main(["--status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current"] == "0.2.6"


def test_cli_check_exits_two_when_an_update_exists(settings, monkeypatch, capsys):
    release, _source = staged_release(settings)
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    assert updater.main(["--check", "--json", "--force"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["update_available"] is True


def test_cli_check_exits_zero_when_up_to_date(settings, monkeypatch, capsys):
    release, _source = staged_release(settings, version="0.2.6")
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    assert updater.main(["--check", "--json", "--force"]) == 0


def test_cli_check_exits_three_on_failure(settings, monkeypatch, capsys):
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: (_ for _ in ()).throw(updater.UpdateError("offline")))
    assert updater.main(["--check", "--json", "--force"]) == 3


def test_cli_apply_reports_what_it_did(settings, monkeypatch, capsys):
    release, _source = staged_release(settings)
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    assert updater.main(["--apply", "--json", "--no-restart", "--force"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert updater.local_version(str(settings["node"])) == "0.3.0"


def test_cli_output_survives_unprintable_release_notes(settings, monkeypatch, capsys):
    """A Windows console is a cp1251 code page; notes carry emoji."""
    release, _source = staged_release(settings)
    release["notes"] = "shipped \U0001f680 today"
    monkeypatch.setenv("MESH_UPDATE_DIR", str(settings["node"]))
    monkeypatch.setenv("MESH_UPDATE_STATE", settings["config"]["state_file"])
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kwargs: release)
    assert updater.main(["--check", "--json", "--force"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["release"]["notes"].startswith("shipped")


# ---------------------------------------------------------------------------
# Wiring: the agent, the tool, the CLI, the installer and CI
# ---------------------------------------------------------------------------

def test_the_tool_exposes_status_check_and_apply():
    from core import mcp_tools

    names = [tool["name"] for tool in mcp_tools.TOOLS]
    assert "mesh_update" in names
    tool = next(tool for tool in mcp_tools.TOOLS if tool["name"] == "mesh_update")
    assert tool["inputSchema"]["properties"]["action"]["enum"] == ["status", "check", "apply"]
    assert "mesh_update" in mcp_tools._HANDLERS


def test_the_tool_reports_local_state_without_network(monkeypatch):
    from core import mcp_tools

    monkeypatch.setattr(updater, "fetch_latest_release",
                        lambda **kwargs: pytest.fail("status must not use the network"))
    result = mcp_tools.call_tool("mesh_update", {"action": "status"})
    assert result["ok"] is True
    assert "current" in result
    assert mcp_tools.call_tool("mesh_update", {"action": "nonsense"})["error"].startswith("unknown action")


def test_the_tool_refuses_to_apply_on_a_read_only_node(monkeypatch):
    from core import mcp_tools

    monkeypatch.setattr(mcp_tools, "_READ_ONLY", True)
    try:
        result = mcp_tools.call_tool("mesh_update", {"action": "apply"})
    finally:
        monkeypatch.setattr(mcp_tools, "_READ_ONLY", False)
    assert "read-only" in result["error"]


def test_mesh_status_reports_the_node_version():
    from core import mcp_tools

    status = mcp_tools.call_tool("mesh_status", {})
    assert status["node_version"] == version_module.__version__
    assert "update" in status


def test_the_agent_records_the_update_state_in_its_heartbeat(monkeypatch):
    from core import agent

    written = []
    monkeypatch.setattr(agent, "write_heartbeat", lambda *a, **k: written.append(k) or {})
    agent.note_update_state({"check": {"current": "0.2.6", "latest": "0.3.0",
                                       "update_available": True, "checked_at": "now",
                                       "reason": "newer"}})
    assert agent._HEARTBEAT_STATE["update"]["latest"] == "0.3.0"
    assert agent._HEARTBEAT_STATE["update"]["available"] is True
    assert written, "the heartbeat must be refreshed after an update answer"


def test_the_agent_asks_to_restart_only_once(monkeypatch):
    """Two restarts would fight over the instance lock."""
    from core import agent

    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(agent.threading, "Thread", NoThread)
    agent._RESTART_EVENT.clear()
    try:
        assert agent.request_restart(60) is True
        assert agent.request_restart(60) is False
    finally:
        agent._RESTART_EVENT.clear()


def test_the_agent_does_not_start_the_updater_when_it_is_disabled(monkeypatch):
    from core import agent

    monkeypatch.setenv("MESH_UPDATE_CHECK", "0")
    assert agent.start_update_thread() is None


def test_the_agent_registers_its_restart_hook(monkeypatch):
    from core import agent

    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setenv("MESH_UPDATE_CHECK", "1")
    monkeypatch.setattr(agent.threading, "Thread", NoThread)
    try:
        assert agent.start_update_thread() is not None
        assert updater._RESTART_HOOK is agent.request_restart
    finally:
        updater.set_restart_hook(None)


def test_runtime_facts_carry_the_node_version():
    from core import agent

    assert agent.runtime_facts()["node_version"] == version_module.__version__


def test_the_repository_wires_every_entry_point():
    """The updater is only useful if all four doors to it exist."""
    cli = (REPO / "bin" / "cli.js").read_text(encoding="utf-8")
    assert "update.ps1" in cli
    assert "update:" in cli

    installer = (REPO / "install.ps1").read_text(encoding="utf-8-sig")
    assert "AntigravityMeshUpdater" in installer
    assert "ops\\update.ps1" in installer
    assert "core/updater.py" in installer
    assert "core/version.py" in installer

    shell_installer = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "core/updater.py" in shell_installer
    assert "core/version.py" in shell_installer

    build = (REPO / "build-installer-exe.ps1").read_text(encoding="utf-8")
    assert "core\\version.py" in build, "the build must refuse a package/module mismatch"


def test_the_windows_wrapper_is_shippable():
    script = REPO / "ops" / "update.ps1"
    assert script.is_file()
    raw = script.read_bytes()
    assert raw, "ops/update.ps1 is empty"
    offenders = sorted({byte for byte in raw if byte > 0x7F})
    assert offenders == [], "ops/update.ps1 must stay ASCII for Windows PowerShell 5.1"
    text = raw.decode("ascii")
    assert "core.updater" in text
    assert "WindowsApps" in text, "the wrapper must refuse the Microsoft Store alias"
    assert "EXIT CODES" in text
    for code in ("    0  ", "    2  ", "    3  ", "    4  "):
        assert code in text, "exit code %r must be documented" % code.strip()
    assert "exit 2" in text and "exit 3" in text and "exit $exitCode" in text


def test_the_ci_workflow_publishes_what_the_updater_reads():
    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "tools/build-payload-zip.py" in workflow
    assert "mesh-payload-" in workflow
    assert "AntigravityMesh-Setup-" in workflow
    assert "core/version.py" in workflow, "the tag must be checked against the module version"
    assert "gh release upload" in workflow
