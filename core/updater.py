"""core/updater.py - self-update from the project's GitHub releases.

The node installs itself from a release and then keeps itself current the same
way: it asks the GitHub Releases API what the newest published version is,
compares it with :mod:`core.version`, downloads the matching release asset,
verifies its SHA-256, swaps ``core/``/``skills/``/``ops/`` in place with a
backup it can roll back to, and restarts the agent through whatever supervises
it (a Windows Scheduled Task, a systemd user unit, a launchd agent, or a plain
detached ``python -m core.agent``).

Design contract
---------------

* **Standard library only.** The node has exactly one third-party dependency
  (``websockets``) and this module must not add a second one: it runs on a
  Windows box with nothing but python.org's Python, on a VPS, and on macOS.
* **Fail closed.** An update is applied only when the payload could be
  downloaded, its structure verified, and (unless the operator deliberately
  opted out) its SHA-256 matched a published checksum. A half-verified payload
  never touches an installed node.
* **Never leave a broken node.** Every swap is preceded by a backup and a
  journal entry. A process killed mid-swap rolls back on the next start
  (:func:`recover_pending`), so the worst case is "still on the old version".
* **One code path for check and apply.** The background thread in
  :mod:`core.agent`, the MCP tool ``mesh_update``, ``ops/update.ps1`` and
  ``bin/cli.js update`` all call the functions here, so they cannot disagree
  about what "up to date" means.

Configuration (``MESH_*`` keys are read from the environment, and therefore also
from ``agent.env``, which :mod:`core.agent` exports at startup)::

    MESH_UPDATE_CHECK          1/0, default 1: check at all
    MESH_UPDATE_AUTO           1/0, default 1: apply a found update by itself
    MESH_UPDATE_CHECK_INTERVAL seconds between checks, default 21600 (6 h), min 300
    MESH_UPDATE_CHANNEL        "stable" (default) or "prerelease"
    MESH_UPDATE_TOKEN          GitHub token, for the 60 -> 5000 requests/hour limit
                               (GITHUB_TOKEN / GH_TOKEN are honoured too)
    MESH_UPDATE_REPO           owner/name override, default core.version.REPO_SLUG
    MESH_UPDATE_DIR            install directory override (default: the directory
                               holding this package)
    MESH_UPDATE_RESTART        1/0, default 1: restart the agent after applying
    MESH_UPDATE_ALLOW_UNVERIFIED
                               1/0, default 0: accept a payload with no published
                               checksum (source tarball fallback)
    MESH_UPDATE_KEEP_BACKUPS   how many old versions to keep, default 3
    MESH_UPDATE_STATE          path of the state file
    MESH_UPDATE_STATE_FILE     alias of MESH_UPDATE_STATE
    MESH_UPDATE_DRY_RUN        1/0, default 0: stage and verify, change nothing

Command line (used by ``ops/update.ps1`` and the CLI)::

    python -m core.updater --status [--json]      report local state, no network
    python -m core.updater --check  [--json]      ask GitHub; exit 2 when an update exists
    python -m core.updater --apply  [--json]      check, download, verify, swap, restart
    python -m core.updater --restart-helper ...   internal: wait for a pid, then restart

Exit codes::

    0  up to date, or the requested action completed
    2  an update is available and was not applied (--check)
    3  the action failed (network, checksum mismatch, unusable payload)
    4  the update was applied and the node is restarting (nothing left to do)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import version as version_module

logger = logging.getLogger("agy-updater")

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

#: Repository that publishes the releases this node updates itself from.
REPO = version_module.REPO_SLUG

API_ROOT = "https://api.github.com"
USER_AGENT = "AntigravityMesh-Updater/%s (+https://github.com/%s)" % (
    version_module.__version__, REPO)

#: What an update replaces inside the install directory. Everything else in that
#: directory (install-gui.strings.json, a checkout's docs/, templates/) is left
#: exactly as it is: the updater owns the node payload, not the whole working tree.
PAYLOAD_DIRS: Tuple[str, ...] = ("core", "skills", "ops")
PAYLOAD_FILES: Tuple[str, ...] = ("package.json",)

#: A payload without these is not a node, whatever its zip says.
REQUIRED_PATHS: Tuple[str, ...] = (
    "core/agent.py",
    "core/mcp_tools.py",
    "core/updater.py",
    "core/version.py",
    "core/__init__.py",
)

#: Names the release is expected to carry, newest format first.
PAYLOAD_ASSET = "mesh-payload-%s.zip"
SETUP_ASSET = "AntigravityMesh-Setup-%s.exe"

HTTP_TIMEOUT = 20.0
DOWNLOAD_TIMEOUT = 120.0
#: Sanity ceiling for a payload download (a release asset is a few hundred KB).
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

DEFAULT_CHECK_INTERVAL = 6 * 60 * 60
MIN_CHECK_INTERVAL = 300
#: After a failed apply, do not hammer the same release again immediately.
APPLY_RETRY_BACKOFF = 60 * 60

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "antigravity-mesh")
STATE_FILE = os.path.join(CONFIG_DIR, "update.state.json")
JOURNAL_FILE = os.path.join(CONFIG_DIR, "update.journal.json")
LOG_FILE = os.path.join(CONFIG_DIR, "update.log")
BACKUP_ROOT = os.path.join(CONFIG_DIR, "backups")
HEARTBEAT_FILE = os.path.join(CONFIG_DIR, "agent.heartbeat")
WATCHDOG = os.path.join("ops", "windows", "agent-watchdog.ps1")
SYSTEMD_UNIT = "agy-agent.service"
LAUNCHD_LABEL = "com.antigravity.mesh.agent"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.\-]+))?$")
_VERSION_LITERAL_RE = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.M)
_SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")


class UpdateError(RuntimeError):
    """A failed check, download, verification or swap, with a human reason."""


class RateLimited(UpdateError):
    """GitHub refused the request because the rate limit was reached."""

    def __init__(self, message: str, retry_after: float = 0.0, reset_at: float = 0.0):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after or 0.0))
        self.reset_at = max(0.0, float(reset_at or 0.0))


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _env_flag(name: str, default: bool = False, env: Optional[Dict[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int, env: Optional[Dict[str, str]] = None) -> int:
    source = os.environ if env is None else env
    try:
        return int(str(source.get(name, "")).strip())
    except Exception:
        return default


def _env_str(name: str, default: str = "", env: Optional[Dict[str, str]] = None) -> str:
    source = os.environ if env is None else env
    value = source.get(name)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def file_log(message: str, path: Optional[str] = None) -> None:
    """Append one line to the updater journal file (never raises)."""
    target = path or LOG_FILE
    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("[%s] %s\n" % (stamp, message.replace("\n", " ")))
    except Exception:
        pass


def sha256_file(path: str, chunk: int = 1024 * 256) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text: Any) -> Optional[Tuple[int, int, int, int, Tuple]]:
    """Parse ``MAJOR.MINOR.PATCH[-pre]`` (a leading ``v`` is accepted).

    Returns a tuple that sorts the way semver requires: the release of a version
    is newer than its pre-releases (``0.3.0 > 0.3.0-rc.1``), and pre-release
    identifiers compare segment by segment, numbers before words.
    """
    if text is None:
        return None
    match = _VERSION_RE.match(str(text).strip())
    if not match:
        return None
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    pre = match.group(4) or ""
    if not pre:
        return (major, minor, patch, 1, ())
    segments: List[Tuple[int, int, str]] = []
    for chunk in re.split(r"[.\-]", pre):
        if chunk.isdigit():
            segments.append((0, int(chunk), ""))
        else:
            segments.append((1, 0, chunk))
    return (major, minor, patch, 0, tuple(segments))


def is_newer(candidate: Any, current: Any) -> bool:
    """True when ``candidate`` is a strictly newer, parseable version.

    An unparseable candidate is never newer: installing something the node
    cannot compare would risk a restart loop on a malformed tag.
    """
    left = parse_version(candidate)
    right = parse_version(current)
    if left is None:
        return False
    if right is None:
        return False
    return left > right


def local_version(install_dir: Optional[str] = None) -> str:
    """The version installed in ``install_dir`` (the running one by default).

    Read from ``core/version.py`` on disk rather than from the imported module,
    so a CLI or a watchdog that did not import the node still reports what is
    actually installed.
    """
    override = os.environ.get("MESH_UPDATE_LOCAL_VERSION")
    if override:
        return override.strip()
    root = install_dir or default_install_dir()
    path = os.path.join(root, "core", "version.py")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            match = _VERSION_LITERAL_RE.search(handle.read())
        if match:
            return match.group(1).strip()
    except OSError:
        pass
    marker = os.path.join(root, "VERSION")
    try:
        with open(marker, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read().strip()
        if text:
            return text.split()[0]
    except OSError:
        pass
    return version_module.__version__


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def default_install_dir() -> str:
    """The directory holding the ``core`` package this module was loaded from."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_from_env(env: Optional[Dict[str, str]] = None,
                    install_dir: Optional[str] = None) -> Dict[str, Any]:
    """Every updater knob in one dict, from the environment (and thus agent.env)."""
    source = os.environ if env is None else env
    channel = _env_str("MESH_UPDATE_CHANNEL", "stable", source).lower()
    prerelease = channel in ("prerelease", "pre", "beta", "nightly", "insiders")
    interval = _env_int("MESH_UPDATE_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL, source)
    if interval < MIN_CHECK_INTERVAL:
        interval = MIN_CHECK_INTERVAL
    token = (_env_str("MESH_UPDATE_TOKEN", "", source)
             or _env_str("GITHUB_TOKEN", "", source)
             or _env_str("GH_TOKEN", "", source))
    repo = _env_str("MESH_UPDATE_REPO", REPO, source)
    state = (_env_str("MESH_UPDATE_STATE", "", source)
             or _env_str("MESH_UPDATE_STATE_FILE", "", source)
             or STATE_FILE)
    root = (install_dir
            or _env_str("MESH_UPDATE_DIR", "", source)
            or _env_str("MESH_INSTALL_DIR", "", source)
            or default_install_dir())
    return {
        "enabled": _env_flag("MESH_UPDATE_CHECK", True, source),
        "auto": _env_flag("MESH_UPDATE_AUTO", True, source),
        "restart": _env_flag("MESH_UPDATE_RESTART", True, source),
        "interval": interval,
        "prerelease": prerelease,
        "channel": "prerelease" if prerelease else "stable",
        "token": token,
        "repo": repo,
        "install_dir": os.path.abspath(root),
        "allow_unverified": _env_flag("MESH_UPDATE_ALLOW_UNVERIFIED", False, source),
        "keep_backups": max(0, _env_int("MESH_UPDATE_KEEP_BACKUPS", 3, source)),
        "dry_run": _env_flag("MESH_UPDATE_DRY_RUN", False, source),
        "state_file": state,
        "journal_file": JOURNAL_FILE,
        "log_file": LOG_FILE,
        "backup_root": BACKUP_ROOT,
        "heartbeat_file": HEARTBEAT_FILE,
    }


# ---------------------------------------------------------------------------
# HTTP (injectable, so the whole updater is testable without a network)
# ---------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def http_get(url: str, *, headers: Optional[Dict[str, str]] = None,
             timeout: float = HTTP_TIMEOUT,
             max_bytes: Optional[int] = None) -> Tuple[int, Dict[str, str], bytes]:
    """One GET with a real User-Agent; returns ``(status, headers, body)``.

    HTTP error statuses come back as data (a 404 from the releases API is an
    answer, not an exception); transport failures raise :class:`UpdateError`.
    """
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    if not request.get_header("User-agent"):
        request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            body = response.read() if max_bytes is None else response.read(max_bytes)
            return (int(getattr(response, "status", 200) or 200),
                    {str(k).lower(): str(v) for k, v in response.headers.items()},
                    body)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read() or b""
        except Exception:
            raw = b""
        return (int(exc.code),
                {str(k).lower(): str(v) for k, v in (exc.headers or {}).items()},
                raw)
    except urllib.error.URLError as exc:
        raise UpdateError("network error reaching %s: %s" % (_short(url), exc.reason))
    except Exception as exc:                       # socket.timeout, ssl.SSLError, ...
        raise UpdateError("network error reaching %s: %s" % (_short(url), exc))


def _short(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return parsed.netloc or url


def _api_headers(token: str = "") -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer %s" % token
    return headers


def _raise_rate_limited(status: int, headers: Dict[str, str], body: bytes) -> None:
    retry_after = 0.0
    try:
        retry_after = float(headers.get("retry-after", "") or 0.0)
    except Exception:
        retry_after = 0.0
    reset_at = 0.0
    try:
        reset_at = float(headers.get("x-ratelimit-reset", "") or 0.0)
    except Exception:
        reset_at = 0.0
    text = body.decode("utf-8", "replace")[:200] if body else ""
    raise RateLimited(
        "GitHub rate limit reached (HTTP %d%s). Set MESH_UPDATE_TOKEN to raise the "
        "limit from 60 to 5000 requests per hour." % (status, (": " + text) if text else ""),
        retry_after=retry_after, reset_at=reset_at)


# ---------------------------------------------------------------------------
# GitHub releases
# ---------------------------------------------------------------------------

def version_from_tag(tag: Any) -> Optional[str]:
    """``v0.3.0-rc.1`` -> ``0.3.0-rc.1``; None when the tag is not a version."""
    match = _VERSION_RE.match(str(tag or "").strip())
    if not match:
        return None
    core = "%d.%d.%d" % (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return core + (("-" + match.group(4)) if match.group(4) else "")


def _release_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tag = str(payload.get("tag_name") or "").strip()
    canonical = version_from_tag(tag)
    if canonical is None:
        # A tag that is not a version (a date, a codename) cannot be compared, and
        # guessing is worse than skipping: another release in the list may parse.
        return None
    if payload.get("draft"):
        return None
    assets = []
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict) or not asset.get("name"):
            continue
        assets.append({
            "name": str(asset.get("name")),
            "url": str(asset.get("browser_download_url") or ""),
            "size": int(asset.get("size") or 0),
            "digest": str(asset.get("digest") or ""),
            "content_type": str(asset.get("content_type") or ""),
        })
    return {
        "tag": tag,
        "version": canonical,
        "parsed": parse_version(canonical),
        "name": str(payload.get("name") or tag),
        # Notes travel into the state file and the MCP answer, so they are
        # bounded: a long changelog must not bloat either.
        "notes": str(payload.get("body") or "")[:4000],
        "published_at": str(payload.get("published_at") or ""),
        "html_url": str(payload.get("html_url") or ""),
        "prerelease": bool(payload.get("prerelease")),
        "assets": assets,
        "tarball_url": str(payload.get("tarball_url") or ""),
        "zipball_url": str(payload.get("zipball_url") or ""),
    }


def _public_release(release: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The release without its internal sort key, for JSON results."""
    if not release:
        return None
    return {key: value for key, value in release.items() if key != "parsed"}


def fetch_latest_release(*, repo: str = REPO, token: str = "",
                         prerelease: bool = False, timeout: float = HTTP_TIMEOUT,
                         etag: str = "") -> Optional[Dict[str, Any]]:
    """The newest published release, or ``None`` when the request came back 304.

    ``prerelease`` asks the list endpoint for the newest release of any kind and
    picks the first one that carries a parseable version; the stable channel asks
    ``/releases/latest``, which already excludes drafts and pre-releases.
    """
    if prerelease:
        url = "%s/repos/%s/releases?per_page=20" % (API_ROOT, repo)
    else:
        url = "%s/repos/%s/releases/latest" % (API_ROOT, repo)
    headers = _api_headers(token)
    if etag:
        headers["If-None-Match"] = etag
    status, response_headers, body = http_get(url, headers=headers, timeout=timeout)
    if status == 304:
        return None
    if status in (403, 429):
        if response_headers.get("x-ratelimit-remaining") == "0" or status == 429:
            _raise_rate_limited(status, response_headers, body)
    if status == 404:
        raise UpdateError("no releases found in %s (HTTP 404)" % repo)
    if status != 200:
        raise UpdateError("GitHub answered HTTP %d for %s" % (status, _short(url)))
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception as exc:
        raise UpdateError("GitHub answered with invalid JSON: %s" % exc)
    if prerelease:
        if not isinstance(payload, list):
            raise UpdateError("the releases list was not a JSON array")
        for item in payload:
            if not isinstance(item, dict):
                continue
            release = _release_from_payload(item)
            if release is not None:
                return release
        raise UpdateError("no release in %s carries a version tag" % repo)
    if not isinstance(payload, dict):
        raise UpdateError("the latest-release answer was not a JSON object")
    release = _release_from_payload(payload)
    if release is None:
        raise UpdateError("release %s carries no parseable version tag"
                          % payload.get("tag_name"))
    return release


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def checksum_asset_name(name: str) -> str:
    return name + ".sha256"


def find_asset(release: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for asset in release.get("assets") or []:
        if asset.get("name") == name:
            return asset
    return None


def choose_asset(release: Dict[str, Any], *, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The best asset for this platform, in preference order.

    1. ``mesh-payload-<version>.zip`` - the node payload, built by the release
       workflow, verified with its published ``.sha256``;
    2. ``AntigravityMesh-Setup-<version>.exe`` - the Windows setup, unpacked with
       ``--unpack`` and applied from there (Windows only);
    3. the release source archive - only when the operator allowed unverified
       payloads, because GitHub publishes no checksum for it.
    """
    version = str(release.get("version") or "")
    payload = find_asset(release, PAYLOAD_ASSET % version)
    if payload and payload.get("size", 0) > 0:
        return {"kind": "payload", "asset": payload, "name": payload["name"],
                "url": payload["url"], "size": payload["size"], "verified": True}
    name = platform or ("windows" if os.name == "nt" else sys.platform)
    if name.startswith("win"):
        setup = find_asset(release, SETUP_ASSET % version)
        if setup and setup.get("url"):
            return {"kind": "setup", "asset": setup, "name": setup["name"],
                    "url": setup["url"], "size": setup["size"], "verified": True}
    tarball = str(release.get("tarball_url") or "")
    if tarball:
        return {"kind": "tarball", "asset": None, "name": "%s.tar.gz" % str(release.get("tag") or version),
                "url": tarball, "size": 0, "verified": False}
    return None


def _checksum_for(release: Dict[str, Any], asset_name: str,
                  *, timeout: float = HTTP_TIMEOUT) -> str:
    """The published SHA-256 of ``asset_name``, or "" when the release has none."""
    asset = find_asset(release, checksum_asset_name(asset_name))
    if asset and asset.get("url"):
        status, _headers, body = http_get(asset["url"], timeout=timeout, max_bytes=4096)
        if status == 200:
            match = _SHA256_RE.search(body.decode("utf-8", "replace"))
            if match:
                return match.group(1).lower()
    digest = ""
    for candidate in release.get("assets") or []:
        if candidate.get("name") == asset_name:
            digest = str(candidate.get("digest") or "")
            break
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return ""


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_to_file(url: str, destination: str, *, expected_sha256: str = "",
                     timeout: float = DOWNLOAD_TIMEOUT, token: str = "",
                     max_bytes: int = MAX_DOWNLOAD_BYTES) -> Dict[str, Any]:
    """Stream ``url`` into ``destination``, hashing as it goes.

    The file is written to ``destination + ".part"`` and only renamed into place
    once it is complete and verified, so an interrupted download can never be
    mistaken for a payload.
    """
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if token and "api.github.com" in url:
        headers["Authorization"] = "Bearer %s" % token
    request = urllib.request.Request(url, headers=headers, method="GET")
    digest = hashlib.sha256()
    written = 0
    partial = destination + ".part"
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise UpdateError("download failed: HTTP %d for %s" % (status, _short(url)))
            with open(partial, "wb") as handle:
                while True:
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise UpdateError("download exceeded %d bytes; refusing" % max_bytes)
                    digest.update(block)
                    handle.write(block)
    except urllib.error.HTTPError as exc:
        _remove(partial)
        raise UpdateError("download failed: HTTP %d for %s" % (exc.code, _short(url)))
    except UpdateError:
        _remove(partial)
        raise
    except Exception as exc:
        _remove(partial)
        raise UpdateError("download failed: %s" % exc)
    actual = digest.hexdigest().lower()
    if expected_sha256 and actual != expected_sha256.lower():
        _remove(partial)
        raise UpdateError("SHA-256 mismatch: published %s, downloaded %s"
                          % (expected_sha256[:16], actual[:16]))
    os.replace(partial, destination)
    return {"path": destination, "size": written, "sha256": actual,
            "verified": bool(expected_sha256)}


def _remove(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Staging a payload
# ---------------------------------------------------------------------------

def verify_payload(root: str) -> None:
    """Raise unless ``root`` holds a complete node payload."""
    missing = [relative for relative in REQUIRED_PATHS
               if not os.path.isfile(os.path.join(root, *relative.split("/")))]
    if missing:
        raise UpdateError("the payload is incomplete: missing %s" % ", ".join(missing))
    if not any(os.path.isdir(os.path.join(root, name)) for name in PAYLOAD_DIRS):
        raise UpdateError("the payload holds none of %s" % ", ".join(PAYLOAD_DIRS))


def _safe_extract_zip(archive: str, destination: str) -> None:
    """Extract ``archive``, refusing absolute paths and ``..`` escapes.

    Members are written one by one rather than through ``extractall``: archive
    entry names are normalised from Windows separators first. ``zipfile`` treats a
    backslash as an ordinary character on POSIX, so an archive built by
    ``Compress-Archive`` would otherwise unpack into files literally named
    ``core\\agent.py`` and the node would never find its own code.
    """
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
                raise UpdateError("the payload archive contains an unsafe path: %s" % member.filename)
            target = os.path.join(destination, *[part for part in name.split("/") if part])
            if member.is_dir() or not name.strip("/"):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target) or destination, exist_ok=True)
            with bundle.open(member) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, 1024 * 256)


def _safe_extract_tar(archive: str, destination: str) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or member.islnk() or member.issym():
                raise UpdateError("the payload archive contains an unsafe entry: %s" % member.name)
        try:                       # Python 3.12+: refuse device files and the like
            bundle.extractall(destination, filter="data")
        except TypeError:
            bundle.extractall(destination)


def _strip_single_root(directory: str) -> str:
    """Return the payload root inside ``directory``.

    GitHub's source archives wrap everything in one ``<repo>-<tag>/`` directory,
    while our own ``mesh-payload`` zip has ``core/`` at the top level. Both are
    accepted by looking for the level that actually holds ``core``.
    """
    if os.path.isdir(os.path.join(directory, "core")):
        return directory
    entries = [name for name in os.listdir(directory) if not name.startswith(".")]
    for name in entries:
        inner = os.path.join(directory, name)
        if os.path.isdir(inner) and os.path.isdir(os.path.join(inner, "core")):
            return inner
    return directory


def _no_window_flags() -> int:
    """CREATE_NO_WINDOW for a console child on Windows, 0 elsewhere.

    The agent itself runs in a hidden console (the Windows launcher starts it with
    ``WshShell.Run ..., 0``), but a child that does not inherit that console gets a
    NEW one - and a new console is a window on the user's desktop. The updater
    starts powershell.exe, taskkill.exe and schtasks.exe, so without this flag an
    update paints windows nobody asked for.
    """
    return 0x08000000 if os.name == "nt" else 0


def _run_capture(argv: Sequence[str], *, cwd: Optional[str] = None,
                 timeout: float = 180.0,
                 env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    try:
        completed = subprocess.run(list(argv), cwd=cwd, capture_output=True,
                                   timeout=timeout, check=False, env=env,
                                   creationflags=_no_window_flags())
    except FileNotFoundError as exc:
        return (127, str(exc))
    except subprocess.TimeoutExpired:
        return (124, "timed out after %.0fs" % timeout)
    output = b"\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return (completed.returncode, output.decode("utf-8", "replace"))


_UNPACK_PATH_RE = re.compile(r"^unpacked\s+\S+\s+to\s+(.+?)\s*$", re.M)


def _unpack_setup(exe_path: str, workdir: str, version: str = "") -> str:
    """Unpack a downloaded setup executable and return its payload root.

    ``AntigravityMesh-Setup-<version>.exe --unpack`` prints
    ``unpacked <version> to <path>`` and stops, which is the authoritative answer;
    the paths it defaults to are only used when that line cannot be parsed.
    """
    environment = dict(os.environ)
    setup_root = os.path.join(workdir, "setup")
    environment["MESH_SETUP_DIR"] = setup_root
    logger.info("Unpacking %s", os.path.basename(exe_path))
    code, output = _run_capture([exe_path, "--unpack"], timeout=300.0, env=environment)
    if code != 0:
        raise UpdateError("the setup executable could not unpack itself (exit %d): %s"
                          % (code, output.strip()[:300]))
    candidates: List[str] = []
    match = _UNPACK_PATH_RE.search(output)
    if match:
        candidates.append(match.group(1).strip().strip('"'))
    if version:
        candidates.append(os.path.join(setup_root, version))
    candidates.append(setup_root)
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "core")):
            return candidate
    if os.path.isdir(setup_root):
        for entry in sorted(os.listdir(setup_root), reverse=True):
            candidate = os.path.join(setup_root, entry)
            if os.path.isdir(os.path.join(candidate, "core")):
                return candidate
    raise UpdateError("the setup executable unpacked no payload: %s" % output.strip()[:300])


def stage_payload(release: Dict[str, Any], *, config: Dict[str, Any],
                  workdir: Optional[str] = None) -> Dict[str, Any]:
    """Download and unpack the release payload; returns the staging description.

    The returned ``root`` is a directory that :func:`verify_payload` accepted.
    """
    asset = choose_asset(release)
    if asset is None:
        raise UpdateError("release %s carries no usable asset" % release.get("tag"))
    if not asset["verified"] and not config.get("allow_unverified"):
        raise UpdateError(
            "release %s has no mesh-payload asset and no checksum; refusing the "
            "source-archive fallback (set MESH_UPDATE_ALLOW_UNVERIFIED=1 to accept it)"
            % release.get("tag"))
    workdir = workdir or tempfile.mkdtemp(prefix="mesh-update-")
    os.makedirs(workdir, exist_ok=True)
    expected = _checksum_for(release, asset["name"]) if asset["asset"] else ""
    if asset["verified"] and not expected and not config.get("allow_unverified"):
        raise UpdateError("no published SHA-256 for %s; refusing to install an "
                          "unverifiable payload (set MESH_UPDATE_ALLOW_UNVERIFIED=1 "
                          "to accept it)" % asset["name"])
    target = os.path.join(workdir, os.path.basename(asset["name"]))
    download = download_to_file(asset["url"], target, expected_sha256=expected,
                                token=str(config.get("token") or ""))
    root = workdir
    if asset["kind"] == "payload":
        _safe_extract_zip(target, workdir)
        root = _strip_single_root(workdir)
    elif asset["kind"] == "setup":
        root = _unpack_setup(target, workdir, str(release.get("version") or ""))
    else:
        _safe_extract_tar(target, workdir)
        root = _strip_single_root(workdir)
    verify_payload(root)
    return {"root": root, "workdir": workdir, "asset": asset["name"],
            "kind": asset["kind"], "size": download["size"],
            "sha256": download["sha256"], "verified": download["verified"],
            "archive": target}


# ---------------------------------------------------------------------------
# Applying a payload
# ---------------------------------------------------------------------------

def _move_with_retry(source: str, destination: str, attempts: int = 5) -> None:
    """Move ``source`` onto ``destination``, retrying Windows handle contention.

    A running Python may still hold ``__pycache__`` files open for a moment, and
    an antivirus scanner can hold a fresh ``.py`` for longer; both make a
    directory rename fail with a sharing violation that clears by itself.
    """
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            shutil.move(source, destination)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise UpdateError("could not move %s into place: %s" % (source, last))


def _prune_pycache(root: str) -> int:
    """Delete stale bytecode below ``root``; returns how many caches were removed.

    A payload built from a working tree can carry ``__pycache__`` directories
    whose timestamps match the sources they were compiled from, so Python would
    happily run the OLD bytecode under the NEW file names. Removing them makes
    the next import compile the sources that were actually installed.
    """
    removed = 0
    for current, directories, _files in os.walk(root):
        if "__pycache__" in directories:
            target = os.path.join(current, "__pycache__")
            shutil.rmtree(target, ignore_errors=True)
            directories.remove("__pycache__")
            removed += 1
    return removed


def _backup_destination(config: Dict[str, Any], from_version: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^0-9A-Za-z.\-_]", "_", from_version or "unknown")
    return os.path.join(str(config["backup_root"]), "%s-%s" % (safe, stamp))


def prune_backups(backup_root: str, keep: int) -> int:
    """Keep the newest ``keep`` backups; returns how many were deleted."""
    try:
        entries = sorted((os.path.join(backup_root, name) for name in os.listdir(backup_root)
                          if os.path.isdir(os.path.join(backup_root, name))),
                         key=lambda path: os.path.getmtime(path), reverse=True)
    except OSError:
        return 0
    deleted = 0
    for path in entries[max(0, keep):]:
        shutil.rmtree(path, ignore_errors=True)
        deleted += 1
    return deleted


def apply_payload(payload_root: str, install_dir: str, *, config: Dict[str, Any],
                  from_version: str = "", to_version: str = "") -> Dict[str, Any]:
    """Replace the node payload in ``install_dir`` with the staged one.

    Only :data:`PAYLOAD_DIRS` and :data:`PAYLOAD_FILES` are touched. Every
    replaced name is first moved into a timestamped backup and the operation is
    journalled before it starts, so :func:`recover_pending` can undo it after a
    crash or a power cut.
    """
    verify_payload(payload_root)
    install_dir = os.path.abspath(install_dir)
    if not os.path.isdir(install_dir):
        raise UpdateError("the install directory does not exist: %s" % install_dir)
    if os.path.abspath(payload_root) == install_dir:
        raise UpdateError("refusing to update a directory onto itself")
    names = [name for name in PAYLOAD_DIRS if os.path.isdir(os.path.join(payload_root, name))]
    files = [name for name in PAYLOAD_FILES if os.path.isfile(os.path.join(payload_root, name))]
    if not names:
        raise UpdateError("the payload holds no %s directory" % "/".join(PAYLOAD_DIRS))
    backup = _backup_destination(config, from_version)
    os.makedirs(backup, exist_ok=True)
    journal = {
        "state": "applying",
        "from": from_version,
        "to": to_version,
        "install_dir": install_dir,
        "backup": backup,
        "names": names + files,
        "started_at": now_iso(),
        "pid": os.getpid(),
    }
    _write_json_atomic(str(config["journal_file"]), journal)
    file_log("applying %s -> %s (backup %s)" % (from_version or "?", to_version or "?",
                                                backup), str(config.get("log_file")))

    moved: List[Tuple[str, str]] = []      # (name, backup path)
    placed: List[str] = []
    try:
        for name in names + files:
            source = os.path.join(payload_root, name)
            target = os.path.join(install_dir, name)
            if os.path.exists(target):
                saved = os.path.join(backup, name)
                _move_with_retry(target, saved)
                moved.append((name, saved))
            _move_with_retry(source, target)
            placed.append(name)
    except BaseException as exc:
        # Put back what was moved, newest first, so a half-applied update does not
        # survive this call. Failures here are reported, not swallowed: the
        # journal stays "applying" and recover_pending retries on the next start.
        for name in reversed(placed):
            _remove(os.path.join(install_dir, name))
        for name, saved in reversed(moved):
            try:
                _move_with_retry(saved, os.path.join(install_dir, name))
            except UpdateError as restore_exc:
                file_log("ROLLBACK FAILED for %s: %s" % (name, restore_exc),
                         str(config.get("log_file")))
        raise UpdateError("the update could not be applied (%s); the previous version "
                          "was restored" % exc)

    journal["state"] = "swapped"
    journal["swapped_at"] = now_iso()
    _write_json_atomic(str(config["journal_file"]), journal)

    removed_pycache = sum(_prune_pycache(os.path.join(install_dir, name)) for name in names)
    marker = os.path.join(install_dir, "VERSION")
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("%s\n" % (to_version or local_version(payload_root)))
    except OSError as exc:
        logger.debug("could not write %s: %s", marker, exc)

    journal["state"] = "applied"
    journal["applied_at"] = now_iso()
    _write_json_atomic(str(config["journal_file"]), journal)
    kept = max(0, int(config.get("keep_backups", 0)))
    prune_backups(str(config["backup_root"]), kept)
    file_log("applied %s -> %s (%d caches cleared)"
             % (from_version or "?", to_version or "?", removed_pycache),
             str(config.get("log_file")))
    return {"applied": True, "from": from_version, "to": to_version,
            "backup": backup, "replaced": names + files,
            "pycache_removed": removed_pycache}


def recover_pending(*, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Finish or undo an update that was interrupted, at agent start.

    Returns a description when something was done, ``None`` when the journal was
    clean. A journal left at ``applying`` means the swap did not finish: the
    backup is restored, which is the only safe direction - a node on the old
    version always works, a node with half of two versions may not.
    """
    settings = config or config_from_env()
    journal = _read_json(str(settings["journal_file"]))
    if not journal:
        return None
    state = str(journal.get("state") or "")
    install_dir = str(journal.get("install_dir") or settings["install_dir"])
    result: Dict[str, Any] = {"state": state, "to": journal.get("to"),
                              "from": journal.get("from"), "install_dir": install_dir}
    if state == "applying":
        backup = str(journal.get("backup") or "")
        restored: List[str] = []
        for name in journal.get("names") or []:
            saved = os.path.join(backup, str(name))
            if not os.path.exists(saved):
                continue
            target = os.path.join(install_dir, str(name))
            _remove(target)
            try:
                _move_with_retry(saved, target)
                restored.append(str(name))
            except UpdateError as exc:
                result["error"] = "could not restore %s: %s" % (name, exc)
                file_log("RECOVERY FAILED: %s" % result["error"], str(settings.get("log_file")))
                return result
        result["action"] = "rolled back"
        result["restored"] = restored
        file_log("recovered an interrupted update: rolled back %s to %s"
                 % (journal.get("to"), journal.get("from") or local_version(install_dir)),
                 str(settings.get("log_file")))
    elif state == "swapped":
        # The tree is consistent; only the post-swap bookkeeping is missing.
        result["action"] = "completed"
        try:
            with open(os.path.join(install_dir, "VERSION"), "w", encoding="utf-8") as handle:
                handle.write("%s\n" % (journal.get("to") or local_version(install_dir)))
        except OSError:
            pass
        file_log("completed an interrupted update to %s" % journal.get("to"),
                 str(settings.get("log_file")))
    else:
        result["action"] = "cleared"
    _remove(str(settings["journal_file"]))
    return result


# ---------------------------------------------------------------------------
# Restarting the node
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False                       # gone, or access denied: both mean "not ours anymore"
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _heartbeat_state(path: Optional[str] = None) -> Dict[str, Any]:
    return _read_json(path or HEARTBEAT_FILE) or {}


def running_agent_pid(config: Optional[Dict[str, Any]] = None) -> int:
    """The pid of the agent that currently owns this node, or 0.

    A fresh heartbeat naming a live process is the only accepted answer: the pid
    must not be reused by something unrelated, and a node that is not running
    needs no stop at all.
    """
    settings = config or config_from_env()
    state = _heartbeat_state(settings.get("heartbeat_file"))
    try:
        pid = int(state.get("pid") or 0)
        stamp = float(state.get("ts") or 0.0)
    except Exception:
        return 0
    if not pid or pid == os.getpid():
        return 0
    if (time.time() - stamp) > 300.0:
        return 0
    return pid if _pid_alive(pid) else 0


def stop_agent(pid: int, *, config: Optional[Dict[str, Any]] = None,
               grace: float = 15.0) -> Dict[str, Any]:
    """Ask a running agent to exit, then insist.

    A node whose old agent keeps running is a node that never actually updates:
    the new process waits for the instance lock the old one holds and the tunnel
    keeps saying the old version is current. A graceful signal comes first (the
    agent writes its clean-stop marker and releases everything); a process that
    ignores it for ``grace`` seconds is terminated.
    """
    if pid <= 0 or pid == os.getpid() or not _pid_alive(pid):
        return {"stopped": False, "reason": "nothing to stop", "pid": pid}
    if os.name == "nt":
        code, _output = _run_capture(["taskkill", "/PID", str(pid)], timeout=30.0)
        if code == 0:
            waited = _wait_for_exit(pid, 10.0)
            if waited:
                return {"stopped": True, "pid": pid, "method": "taskkill"}
        code, output = _run_capture(["taskkill", "/T", "/F", "/PID", str(pid)], timeout=60.0)
        alive = _pid_alive(pid)
        return {"stopped": not alive, "pid": pid, "method": "taskkill /T /F",
                "exit_code": code, "detail": output.strip()[-200:]}
    number = getattr(signal, "SIGTERM", 15)
    try:
        os.kill(pid, number)
    except OSError as exc:
        return {"stopped": False, "pid": pid, "method": "SIGTERM", "error": str(exc)}
    if _wait_for_exit(pid, grace):
        return {"stopped": True, "pid": pid, "method": "SIGTERM"}
    try:
        os.kill(pid, getattr(signal, "SIGKILL", 9))
    except OSError as exc:
        return {"stopped": False, "pid": pid, "method": "SIGKILL", "error": str(exc)}
    return {"stopped": _wait_for_exit(pid, 10.0), "pid": pid, "method": "SIGKILL"}


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.5)
    return not _pid_alive(pid)


def wait_for_node(heartbeat_file: str, *, previous_pid: int = 0,
                  after: float = 0.0, timeout: float = 45.0) -> bool:
    """True once a heartbeat newer than ``after`` comes from a different process."""
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        state = _heartbeat_state(heartbeat_file)
        try:
            stamp = float(state.get("ts") or 0.0)
        except Exception:
            stamp = 0.0
        pid = int(state.get("pid") or 0)
        if stamp > after and pid and pid != previous_pid:
            return True
        time.sleep(2.0)
    return False


def _detached_flags() -> Dict[str, Any]:
    if os.name == "nt":
        detached = 0x00000008                    # DETACHED_PROCESS
        new_group = 0x00000200                   # CREATE_NEW_PROCESS_GROUP
        no_window = 0x08000000                   # CREATE_NO_WINDOW
        return {"creationflags": detached | new_group | no_window}
    return {"start_new_session": True}


def _agent_log_handle(config: Dict[str, Any]):
    path = os.path.join(os.path.dirname(str(config.get("log_file") or LOG_FILE)), "agent.log")
    try:
        return open(path, "a", encoding="utf-8")
    except OSError:
        return subprocess.DEVNULL


def _start_agent_directly(install_dir: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Start ``python -m core.agent`` detached, the way a bare install would."""
    handle = _agent_log_handle(config)
    argv = [sys.executable or "python", "-u", "-m", "core.agent"]
    kwargs: Dict[str, Any] = {"cwd": install_dir, "stdout": handle,
                              "stderr": subprocess.STDOUT}
    kwargs.update(_detached_flags())
    try:
        process = subprocess.Popen(argv, **kwargs)
    except Exception as exc:
        return {"ok": False, "method": "direct", "error": str(exc)}
    finally:
        if handle is not subprocess.DEVNULL:
            try:
                handle.close()
            except Exception:
                pass
    return {"ok": True, "method": "direct", "pid": process.pid}


def _restart_windows(install_dir: str, config: Dict[str, Any],
                     previous_pid: int) -> Dict[str, Any]:
    watchdog = os.path.join(install_dir, *WATCHDOG.split("/"))
    if not os.path.isfile(watchdog):
        return _start_agent_directly(install_dir, config)
    heartbeat = str(config.get("heartbeat_file") or HEARTBEAT_FILE)
    started = time.time()
    attempts = [5, 0]
    last: Dict[str, Any] = {}
    for grace in attempts:
        argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", watchdog,
                "-GraceSeconds", str(grace), "-Quiet"]
        code, output = _run_capture(argv, cwd=install_dir, timeout=180.0)
        last = {"ok": code == 0, "method": "watchdog", "grace": grace,
                "exit_code": code, "output": output.strip()[-300:]}
        file_log("watchdog (grace %ss) exited %d" % (grace, code),
                 str(config.get("log_file")))
        if wait_for_node(heartbeat, previous_pid=previous_pid, after=started, timeout=30.0):
            last["ok"] = True
            last["verified"] = True
            return last
    # The watchdog can answer "online" from the gateway before the closed tunnel
    # is noticed; starting the agent directly is the deterministic last resort.
    direct = _start_agent_directly(install_dir, config)
    direct["watchdog"] = last
    direct["verified"] = wait_for_node(heartbeat, previous_pid=previous_pid,
                                       after=started, timeout=30.0)
    return direct


def _restart_posix(install_dir: str, config: Dict[str, Any],
                   previous_pid: int) -> Dict[str, Any]:
    heartbeat = str(config.get("heartbeat_file") or HEARTBEAT_FILE)
    started = time.time()
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        plist = os.path.join(home, "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")
        if os.path.isfile(plist) and shutil.which("launchctl"):
            target = "gui/%d/%s" % (os.getuid(), LAUNCHD_LABEL)
            code, output = _run_capture(["launchctl", "kickstart", "-k", target], timeout=60.0)
            if code == 0:
                verified = wait_for_node(heartbeat, previous_pid=previous_pid,
                                         after=started, timeout=45.0)
                if verified:
                    return {"ok": True, "method": "launchctl", "verified": True}
            return _start_agent_directly(install_dir, config)
        return _start_agent_directly(install_dir, config)
    unit = os.path.join(home, ".config", "systemd", "user", SYSTEMD_UNIT)
    if os.path.isfile(unit) and shutil.which("systemctl"):
        code, output = _run_capture(["systemctl", "--user", "restart", SYSTEMD_UNIT], timeout=60.0)
        if code == 0:
            verified = wait_for_node(heartbeat, previous_pid=previous_pid,
                                     after=started, timeout=45.0)
            if verified:
                return {"ok": True, "method": "systemctl", "verified": True}
        else:
            file_log("systemctl --user restart %s exited %d: %s"
                     % (SYSTEMD_UNIT, code, output.strip()[-200:]),
                     str(config.get("log_file")))
    return _start_agent_directly(install_dir, config)


def restart_node(install_dir: str, *, config: Optional[Dict[str, Any]] = None,
                 previous_pid: int = 0) -> Dict[str, Any]:
    """Bring the agent back through whatever supervises it."""
    settings = config or config_from_env()
    if os.name == "nt":
        return _restart_windows(install_dir, settings, previous_pid)
    return _restart_posix(install_dir, settings, previous_pid)


#: Set by :mod:`core.agent` at startup. The updater calls it after a successful
#: apply so the process that must be replaced stops itself; a caller that manages
#: the node externally (a CLI, a test) leaves it unset.
_RESTART_HOOK: Optional[Any] = None


def set_restart_hook(hook: Optional[Any]) -> None:
    """Register the callable that stops the running agent after an update."""
    global _RESTART_HOOK
    _RESTART_HOOK = hook


def _notify_restart() -> bool:
    hook = _RESTART_HOOK
    if hook is None:
        return False
    try:
        hook()
        return True
    except Exception as exc:                    # a broken hook must not undo an update
        logger.warning("the restart hook failed: %s", exc)
        return False


def spawn_restart_helper(install_dir: str, *, config: Dict[str, Any],
                         pid: Optional[int] = None, wait_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Start a detached helper that brings the node back on the new version.

    The helper is a separate process on purpose. It must outlive the process it
    replaces, wait for the instance lock and the tunnel to be released, and - when
    the update was applied by a CLI or by the Windows Scheduled Task while the old
    agent kept running - stop that agent, which would otherwise hold the lock
    forever and leave the node on the old code.

    ``pid`` defaults to the agent that currently owns the node, or to this process
    when the node is not running (an in-process apply).
    """
    self_pid = os.getpid()
    target = int(pid) if pid is not None else (running_agent_pid(config) or self_pid)
    foreign = target not in (0, self_pid)
    if wait_seconds is None:
        # Our own process stops itself within seconds once asked; a foreign agent
        # has to be asked by the helper, so a long wait would only delay the node.
        wait_seconds = 25.0 if foreign else 120.0
    argv = [sys.executable or "python", "-m", "core.updater",
            "--restart-helper", "--pid", str(target),
            "--dir", install_dir, "--wait", str(float(wait_seconds))]
    handle = _agent_log_handle(config)
    kwargs: Dict[str, Any] = {"cwd": install_dir, "stdout": handle,
                              "stderr": subprocess.STDOUT}
    kwargs.update(_detached_flags())
    try:
        process = subprocess.Popen(argv, **kwargs)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "target_pid": target}
    finally:
        if handle is not subprocess.DEVNULL:
            try:
                handle.close()
            except Exception:
                pass
    file_log("restart helper started (pid %s) for node pid %s (foreign=%s)"
             % (process.pid, target, foreign), str(config.get("log_file")))
    return {"ok": True, "pid": process.pid, "target_pid": target, "foreign": foreign}


def restart_helper_main(pid: int, install_dir: str, wait_seconds: float = 120.0) -> int:
    """The ``--restart-helper`` entry point: wait for ``pid``, then restart."""
    config = config_from_env(install_dir=install_dir)
    deadline = time.time() + max(5.0, wait_seconds)
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(1.0)
    if pid > 0 and pid != os.getpid() and _pid_alive(pid):
        stopped = stop_agent(pid, config=config)
        file_log("restart helper stopped the old agent: %s" % json.dumps(stopped, sort_keys=True),
                 str(config.get("log_file")))
    # A short grace period lets the OS release the instance lock and the gateway
    # notice the closed tunnel before the watchdog is asked whether the node is up.
    time.sleep(3.0)
    result = restart_node(install_dir, config=config, previous_pid=pid)
    file_log("restart helper result: %s" % json.dumps(result, sort_keys=True),
             str(config.get("log_file")))
    return 0 if result.get("ok") else 3


# ---------------------------------------------------------------------------
# The public operations: status, check, apply
# ---------------------------------------------------------------------------

def status(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Local state only: no network, no side effects."""
    settings = config or config_from_env()
    install_dir = str(settings["install_dir"])
    state = _read_json(str(settings["state_file"])) or {}
    journal = _read_json(str(settings["journal_file"])) or {}
    latest = state.get("latest") if isinstance(state.get("latest"), dict) else None
    return {
        "ok": True,
        "current": local_version(install_dir),
        "install_dir": install_dir,
        "channel": settings["channel"],
        "check_enabled": bool(settings["enabled"]),
        "auto": bool(settings["auto"]),
        "interval": int(settings["interval"]),
        "last_check": state.get("last_check") or "",
        "last_result": state.get("last_result") or "",
        "last_applied": state.get("last_applied") or "",
        "latest": (latest or {}).get("version") if latest else "",
        "latest_url": (latest or {}).get("html_url") if latest else "",
        "update_available": state.get("update_available") if isinstance(
            state.get("update_available"), bool) else None,
        "pending_journal": journal.get("state") or "",
        "log_file": str(settings["log_file"]),
    }


def check_for_update(*, config: Optional[Dict[str, Any]] = None, force: bool = False,
                     offline: bool = False) -> Dict[str, Any]:
    """Ask GitHub for the newest release and compare it with the installed version.

    The answer is cached in the state file for ``MESH_UPDATE_CHECK_INTERVAL``:
    an unauthenticated GitHub client is allowed 60 requests per hour, and a fleet
    of nodes polling every minute would spend that on nothing.
    """
    settings = config or config_from_env()
    install_dir = str(settings["install_dir"])
    current = local_version(install_dir)
    state = _read_json(str(settings["state_file"])) or {}
    now = time.time()

    def cached_result(reason: str, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        latest = state.get("latest") if isinstance(state.get("latest"), dict) else None
        result = {
            "ok": True,
            "current": current,
            "latest": (latest or {}).get("version") or current,
            "update_available": bool(latest and is_newer(latest.get("version"), current)),
            "reason": reason,
            "cached": True,
            "checked_at": state.get("last_check") or "",
            "release": latest,
            "asset": None,
            "channel": settings["channel"],
            "auto": bool(settings["auto"]),
            "install_dir": install_dir,
        }
        if latest:
            asset = choose_asset(latest)
            result["asset"] = None if asset is None else {
                "name": asset["name"], "kind": asset["kind"], "size": asset["size"],
                "verified": asset["verified"]}
            result["release"] = _public_release(latest)
        if extra:
            result.update(extra)
        return result

    if not settings["enabled"]:
        return cached_result("checking is disabled (MESH_UPDATE_CHECK=0)")
    if offline:
        return cached_result("offline: cached state only")
    last_check = float(state.get("last_check_epoch") or 0.0)
    if not force and last_check and (now - last_check) < int(settings["interval"]):
        return cached_result("checked %d s ago; next check in %d s"
                             % (now - last_check, int(settings["interval"] - (now - last_check))))

    try:
        release = fetch_latest_release(repo=str(settings["repo"]),
                                       token=str(settings["token"] or ""),
                                       prerelease=bool(settings["prerelease"]))
    except RateLimited as exc:
        state.update({"last_check": now_iso(), "last_check_epoch": now,
                      "last_result": "rate limited"})
        _write_json_atomic(str(settings["state_file"]), state)
        return cached_result("GitHub rate limit: %s" % exc)
    except UpdateError as exc:
        state.update({"last_check_epoch": now, "last_result": "check failed: %s" % exc})
        _write_json_atomic(str(settings["state_file"]), state)
        file_log("check failed: %s" % exc, str(settings.get("log_file")))
        return {"ok": False, "current": current, "latest": "",
                "update_available": False, "reason": "check failed: %s" % exc,
                "cached": False, "checked_at": now_iso(), "release": None,
                "asset": None, "channel": settings["channel"],
                "auto": bool(settings["auto"]), "install_dir": install_dir}

    latest_version = str(release["version"])
    available = is_newer(latest_version, current)
    asset = choose_asset(release)
    state.update({
        "last_check": now_iso(),
        "last_check_epoch": now,
        "latest": _public_release(release),
        "update_available": available,
        "last_result": ("update available: %s" % latest_version) if available
                       else ("up to date (%s)" % current),
    })
    _write_json_atomic(str(settings["state_file"]), state)
    if available:
        file_log("update available: %s -> %s" % (current, latest_version),
                 str(settings.get("log_file")))
    return {
        "ok": True,
        "current": current,
        "latest": latest_version,
        "update_available": available,
        "reason": ("release %s is newer than %s" % (latest_version, current)) if available
                  else "up to date",
        "cached": False,
        "checked_at": now_iso(),
        "release": _public_release(release),
        "asset": None if asset is None else {"name": asset["name"], "kind": asset["kind"],
                                             "size": asset["size"], "verified": asset["verified"]},
        "channel": settings["channel"],
        "auto": bool(settings["auto"]),
        "install_dir": install_dir,
    }


def apply_update(*, config: Optional[Dict[str, Any]] = None,
                 check: Optional[Dict[str, Any]] = None, force: bool = False,
                 restart: Optional[bool] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    """Check, download, verify and swap in the newest release.

    ``restart`` defaults to ``MESH_UPDATE_RESTART``. Applying without a restart
    is for tests and for an operator who supervises the node themselves.
    """
    settings = config or config_from_env()
    install_dir = str(settings["install_dir"])
    result = check if check is not None else check_for_update(config=settings, force=force)
    answer: Dict[str, Any] = {
        "ok": True,
        "applied": False,
        "current": result.get("current") or local_version(install_dir),
        "latest": result.get("latest") or "",
        "restart_scheduled": False,
        "reason": "",
        "release": result.get("release"),
    }
    if not result.get("ok"):
        answer.update({"ok": False, "reason": result.get("reason") or "check failed"})
        return answer
    if not result.get("update_available"):
        answer["reason"] = "already up to date (%s)" % answer["current"]
        return answer

    state = _read_json(str(settings["state_file"])) or {}
    last_failed = float(state.get("last_apply_failed_epoch") or 0.0)
    if last_failed and (time.time() - last_failed) < APPLY_RETRY_BACKOFF and not force:
        answer["reason"] = ("the previous attempt failed %d s ago; retrying in %d s"
                            % (time.time() - last_failed,
                               APPLY_RETRY_BACKOFF - (time.time() - last_failed)))
        return answer

    try:
        staged = stage_payload(result["release"], config=settings, workdir=workdir)
    except UpdateError as exc:
        state.update({"last_apply_failed_epoch": time.time(),
                      "last_result": "download failed: %s" % exc})
        _write_json_atomic(str(settings["state_file"]), state)
        file_log("download failed: %s" % exc, str(settings.get("log_file")))
        answer.update({"ok": False, "reason": str(exc)})
        return answer

    answer["asset"] = staged["asset"]
    answer["sha256"] = staged["sha256"]
    answer["verified"] = staged["verified"]
    if settings.get("dry_run"):
        answer.update({"reason": "dry run: payload %s staged and verified, nothing replaced"
                                 % staged["asset"], "staged": staged["root"]})
        return answer

    do_restart = bool(settings["restart"]) if restart is None else bool(restart)
    helper: Dict[str, Any] = {}
    if do_restart:
        # Spawned BEFORE the swap: if this process dies during the swap, the node
        # is still brought back - and recover_pending() then rolls the tree back.
        helper = spawn_restart_helper(install_dir, config=settings)
        answer["restart_scheduled"] = bool(helper.get("ok"))
        answer["restart_target_pid"] = helper.get("target_pid")
    try:
        applied = apply_payload(staged["root"], install_dir, config=settings,
                                from_version=str(answer["current"]),
                                to_version=str(answer["latest"]))
    except UpdateError as exc:
        state = _read_json(str(settings["state_file"])) or {}
        state.update({"last_apply_failed_epoch": time.time(),
                      "last_result": "apply failed: %s" % exc})
        _write_json_atomic(str(settings["state_file"]), state)
        file_log("apply failed: %s" % exc, str(settings.get("log_file")))
        answer.update({"ok": False, "reason": str(exc)})
        return answer

    state = _read_json(str(settings["state_file"])) or {}
    state.update({"last_applied": answer["latest"],
                  "last_applied_epoch": time.time(),
                  "last_result": "applied %s -> %s" % (answer["current"], answer["latest"]),
                  "update_available": False,
                  "last_apply_failed_epoch": 0})
    _write_json_atomic(str(settings["state_file"]), state)
    answer.update({"applied": True, "backup": applied["backup"],
                   "replaced": applied["replaced"],
                   "reason": "applied %s -> %s" % (answer["current"], answer["latest"]),
                   "staged": staged["root"]})
    if do_restart and answer["restart_scheduled"]:
        # In-process callers (the agent's background thread, the mesh_update tool)
        # stop themselves here; the helper is already waiting for exactly that.
        answer["restart_requested"] = _notify_restart()
    if not do_restart:
        answer["reason"] += " (restart not requested)"
    return answer


def background_tick(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One pass of the agent's background updater.

    Returns the check/apply result plus ``restart``: True when the caller must
    stop the agent so the helper can start the new version.
    """
    settings = config or config_from_env()
    result = check_for_update(config=settings)
    if not result.get("ok") or not result.get("update_available"):
        return {"restart": False, "check": result}
    if not settings["auto"]:
        return {"restart": False, "check": result,
                "reason": "an update is available but MESH_UPDATE_AUTO=0"}
    applied = apply_update(config=settings, check=result)
    restart = bool(applied.get("applied") and settings["restart"]
                   and applied.get("restart_scheduled"))
    return {"restart": restart, "check": result, "apply": applied}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _print_result(result: Dict[str, Any], *, as_json: bool, stream=None) -> None:
    stream = stream or sys.stdout
    if as_json:
        # ensure_ascii keeps the machine-readable answer 7-bit clean: release
        # notes carry emoji, and a Windows console whose code page cannot encode
        # them used to turn a successful check into a UnicodeEncodeError.
        json.dump(result, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        return
    if "current" in result:
        stream.write("current      : %s\n" % result.get("current"))
    if result.get("latest"):
        stream.write("latest       : %s\n" % result.get("latest"))
    if "update_available" in result:
        stream.write("update       : %s\n" % ("available" if result.get("update_available")
                                              else "none"))
    for key in ("reason", "action", "asset", "sha256", "verified", "backup",
                "restart_scheduled", "install_dir", "log_file"):
        if result.get(key) not in (None, "", {}):
            stream.write("%-13s: %s\n" % (key, result.get(key)))
    release = result.get("release") or {}
    if release.get("html_url"):
        stream.write("release      : %s\n" % release["html_url"])


def _make_output_safe() -> None:
    """Never let an unprintable character turn a result into a crash.

    Release notes are written by humans and carry emoji and non-Latin text; a
    Windows console is often a cp1251/cp866 code page. Replacing what the console
    cannot encode is right for a diagnostic tool - the exit code and the JSON
    answer are what scripts read.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.updater",
        description="Check for, download and apply Antigravity Mesh updates.")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true",
                         help="report local state; no network, no changes")
    actions.add_argument("--check", action="store_true",
                         help="ask GitHub whether a newer release exists")
    actions.add_argument("--apply", action="store_true",
                         help="check, download, verify, swap and restart")
    parser.add_argument("--restart-helper", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--dir", default="", help="install directory override")
    parser.add_argument("--wait", type=float, default=120.0, help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true",
                        help="ignore the check interval and the retry backoff")
    parser.add_argument("--offline", action="store_true",
                        help="report the cached state without touching the network")
    parser.add_argument("--no-restart", action="store_true",
                        help="apply the payload but do not restart the agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="download and verify, change nothing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    args = parser.parse_args(list(argv) if argv is not None else None)
    _make_output_safe()

    if args.restart_helper:
        return restart_helper_main(args.pid, args.dir or default_install_dir(), args.wait)

    config = config_from_env(install_dir=args.dir or None)
    if args.dry_run:
        config["dry_run"] = True

    if args.status:
        result = status(config=config)
        if not args.quiet:
            _print_result(result, as_json=args.json)
        return 0

    if args.apply:
        result = apply_update(config=config, force=args.force,
                              restart=False if args.no_restart else None)
        if not args.quiet:
            _print_result(result, as_json=args.json)
        if not result.get("ok"):
            return 3
        if result.get("applied") and result.get("restart_scheduled"):
            return 4
        return 0

    result = check_for_update(config=config, force=args.force, offline=args.offline)
    if not args.quiet:
        _print_result(result, as_json=args.json)
    if not result.get("ok"):
        return 3
    return 2 if result.get("update_available") else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
