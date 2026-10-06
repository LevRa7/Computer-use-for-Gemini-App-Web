#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/web_share.py - publish local files and directories through the Mesh gateway.

Why this module exists
----------------------
A node sits behind NAT and never opens a public port: the gateway on the shared
domain reaches it only through the outbound websocket tunnel. File sharing
therefore works in two halves:

* the node runs a real, loopback-only static HTTP server rooted at the shared
  directory (``serve_dir``) or at a per-file copy (``share_file``);
* the gateway relays ``GET/HEAD https://<domain>/<node>/<slug>/...`` over the
  existing tunnel into that local server (``handle_http_request``) and returns
  the response to the browser.

Public URL contract::

    https://<shared-domain>/<node-name>/<dir-name>-<16 hex>/[path]

``<shared-domain>`` is never decided here: the public host is owned by
:mod:`core.domain`. An explicit ``configure(public_url=...)`` still wins, because
a caller that knows the domain better than the environment does must be able to
say so; everything else asks :func:`core.domain.public_base_url`.

The trailing random suffix is the share secret. Node names are registered and
therefore guessable; the 64-bit suffix is not, which is what makes the link
itself the credential (MESH_WEB_SECRET_BITS). A wrong or missing suffix is
answered with a plain 404, exactly like a share that does not exist.

Security properties
-------------------
* the local server binds ``127.0.0.1`` on an ephemeral port only;
* the public surface is read-only: only ``GET``/``HEAD`` are proxied;
* path traversal is rejected by ``SimpleHTTPRequestHandler.translate_path``,
  which drops ``..`` components, and re-checked here before serving; symlinks
  inside a served directory are followed, exactly like any static web server;
* a response larger than ``MESH_WEB_MAX_BYTES`` is answered with 413 instead of
  being pushed through the tunnel;
* ``share_file`` copies the file into the share root, so the published bytes
  never change when the source file does; ``serve_dir`` serves in place and
  never deletes the user's directory on ``unshare``.

Standard library only (Python 3.8+), like the rest of ``core/``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

#: The public domain is owned by core/domain.py, exactly as it is for the gateway,
#: the tunnel node and core/mcp_tools.py: this module must not keep a fallback
#: chain of its own, or the link a share hands out could name a host the node is
#: not tunneled to. core.domain imports nothing but the standard library, so
#: importing it here keeps this module stdlib-only too.
from core import domain

logger = logging.getLogger("antigravity-mesh.web_share")

#: Bytes of entropy in a share secret -> 16 hex characters in the URL.
SECRET_BYTES = 8
#: Maximum length of the human-readable part of a share name/slug.
NAME_MAX = 40
DEFAULT_MAX_SHARES = 32
MIN_MAX_BYTES = 1024
#: Default public payload limit. The body travels through the tunnel as one
#: base64 JSON message, which grows it by ~4/3 plus framing, so the default must
#: stay well below the gateway's websocket message cap (uvicorn's ws_max_size,
#: raised to 64 MiB in gateway.py). 8 MiB -> ~10.7 MiB on the wire.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
#: Hard ceiling: 32 MiB -> ~42.7 MiB on the wire, safely under the 64 MiB cap.
MAX_MAX_BYTES = 32 * 1024 * 1024

#: The public slug is ``<name>-<hex secret>``. The gateway and the nginx snippet
#: recognise the same shape, so it is part of the wire contract.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}-[0-9a-f]{%d}$" % (SECRET_BYTES * 2))
_NAME_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")
_DASH_RUN_RE = re.compile(r"-{2,}")

#: Response headers a share may expose publicly. Everything else the local
#: server emits (Server, Date, ...) is dropped instead of being relayed.
_KEEP_HEADERS = (
    "content-type",
    "content-disposition",
    "last-modified",
    "etag",
    "cache-control",
    "location",
    "accept-ranges",
)

_CONFIG: Dict[str, Any] = {}
_LOCK = threading.RLock()
_SERVERS: Dict[str, Dict[str, Any]] = {}
#: Per-thread re-entrancy depth for :class:`_RegistryGuard`.
_GUARD_LOCAL = threading.local()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def configure(**kwargs: Any) -> None:
    """(Re)configure the module.

    Recognised keys: ``web_dir``, ``public_url``, ``user``, ``max_bytes``,
    ``max_shares``, ``listing``. Unset keys keep their previous value; an empty
    string resets a key so it falls back to the ``MESH_*`` environment and then
    to the built-in default, which keeps tests order-independent.
    """
    for key in ("web_dir", "public_url", "user", "max_bytes", "max_shares", "listing"):
        if key not in kwargs or kwargs[key] is None:
            continue
        if kwargs[key] == "":
            _CONFIG.pop(key, None)
        else:
            _CONFIG[key] = kwargs[key]


def _env_int(name: str, default: int, low: int = 1, high: int = 1 << 40) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _web_dir() -> str:
    raw = _CONFIG.get("web_dir") or os.environ.get("MESH_WEB_DIR") or os.path.join(
        "~", ".cache", "antigravity-mesh", "web"
    )
    return os.path.realpath(os.path.expanduser(str(raw)))


def _public_root() -> str:
    return os.path.join(_web_dir(), "public")


def _registry_path() -> str:
    return os.path.join(_web_dir(), "shares.json")


def _max_bytes() -> int:
    if _CONFIG.get("max_bytes") is not None:
        try:
            return max(MIN_MAX_BYTES, min(MAX_MAX_BYTES, int(_CONFIG["max_bytes"])))
        except (TypeError, ValueError):
            pass
    return _env_int("MESH_WEB_MAX_BYTES", DEFAULT_MAX_BYTES, MIN_MAX_BYTES, MAX_MAX_BYTES)


def _max_shares() -> int:
    if _CONFIG.get("max_shares") is not None:
        try:
            return max(1, int(_CONFIG["max_shares"]))
        except (TypeError, ValueError):
            pass
    return _env_int("MESH_WEB_MAX_SHARES", DEFAULT_MAX_SHARES, 1)


def _listing_enabled() -> bool:
    if _CONFIG.get("listing") is not None:
        return str(_CONFIG["listing"]).strip().lower() in ("1", "true", "yes", "on")
    return str(os.environ.get("MESH_WEB_LISTING", "1")).strip().lower() in ("1", "true", "yes", "on")


def _public_base() -> str:
    """Public base URL for share links.

    An explicit ``configure(public_url=...)`` is the highest priority - a caller
    that injected a domain knows it better than the environment does. Everything
    else is answered by :mod:`core.domain`, which owns the whole chain
    (``MESH_PUBLIC_URL``, the domain file, the legacy ``MESH_GATEWAY``, then the
    one project default); duplicating any of it here would let the link and the
    tunnel disagree about the host.
    """
    explicit = _CONFIG.get("public_url")
    if explicit:
        return domain.normalise_public_base_url(explicit)
    return domain.public_base_url()


def _user() -> str:
    raw = _CONFIG.get("user") or os.environ.get("MESH_USER") or "anonymous"
    user = str(raw).strip().lower()
    return user or "anonymous"


def _public_url(slug: str, suffix: str = "/") -> str:
    suffix = suffix or "/"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return "%s/%s/%s%s" % (_public_base(), _user(), slug, suffix)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Registry (persisted so a URL survives an agent restart)
# ---------------------------------------------------------------------------

def _load_registry() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_registry_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_registry(registry: Dict[str, Dict[str, Any]]) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # A per-process temp name: the standalone server and the tunnel agent can
    # share one MESH_WEB_DIR, and a common "shares.json.tmp" would make one
    # process rename the other's half-written file.
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


class _RegistryGuard(object):
    """Serialise a registry read-modify-write across processes and threads.

    Two node processes may point at one ``MESH_WEB_DIR`` (the tunnel agent and
    the standalone MCP server), so the in-process ``_LOCK`` alone would let one
    process overwrite a share the other just saved. The OS releases this lock
    when the process dies, so a crash cannot leave it behind.

    The guard is re-entrant per thread: a second ``flock`` on a *new* file
    descriptor would block even though this process already holds the lock, so a
    nested ``with _RegistryGuard():`` must not try to lock twice.
    """

    def __init__(self) -> None:
        self._fh = None
        self._outermost = False

    def __enter__(self) -> "_RegistryGuard":
        _LOCK.acquire()
        depth = getattr(_GUARD_LOCAL, "depth", 0)
        _GUARD_LOCAL.depth = depth + 1
        if depth:
            return self  # this thread already holds the file lock
        self._outermost = True
        try:
            directory = _web_dir()
            os.makedirs(directory, exist_ok=True)
            self._fh = open(os.path.join(directory, "registry.lock"), "a+")
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Locking is best effort: never fail a tool because the FS refuses a lock.
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
        return self

    def __exit__(self, *_exc: Any) -> None:
        depth = getattr(_GUARD_LOCAL, "depth", 1)
        _GUARD_LOCAL.depth = max(0, depth - 1)
        if self._outermost:
            self._outermost = False
            if self._fh is not None:
                try:
                    if os.name != "nt":
                        import fcntl
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
        _LOCK.release()


def _clean_name(value: Any, fallback: str = "share") -> str:
    text = _NAME_CLEAN_RE.sub("-", str(value or "").strip().lower())
    text = _DASH_RUN_RE.sub("-", text).strip("-_")
    return (text or fallback)[:NAME_MAX]


def _make_slug(name: str) -> str:
    return "%s-%s" % (_clean_name(name), secrets.token_hex(SECRET_BYTES))


def _find_by_name(registry: Dict[str, Dict[str, Any]], name: str) -> Optional[str]:
    for slug, share in registry.items():
        if share.get("name") == name:
            return slug
    return None


def _inside(path: str, base: str) -> bool:
    """True when ``path`` is ``base`` or lives below it (both real paths)."""
    try:
        path = os.path.realpath(path)
        base = os.path.realpath(base)
        return os.path.commonpath([path, base]) == base
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Local static server
# ---------------------------------------------------------------------------

class _ShareHandler(SimpleHTTPRequestHandler):
    """Loopback-only static handler that rewrites redirects to the public URL.

    ``SimpleHTTPRequestHandler`` answers a directory without a trailing slash
    with ``301 Location: /subdir/``. That path is relative to the *share root*,
    so it must be prefixed with the public ``/<node>/<slug>`` prefix before it
    reaches the browser - otherwise the redirect leaves the share namespace.

    ``translate_path`` is wrapped so a symlink inside the shared directory can
    never publish a file from outside it: the resolved path must stay under the
    share root, otherwise the request is answered with a plain 404.
    """

    server_version = "AntigravityMeshShare/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, directory: Optional[str] = None,
                 public_prefix: str = "", listing: bool = True, **kwargs: Any) -> None:
        self.public_prefix = public_prefix or ""
        self.listing_enabled = bool(listing)
        self._redirecting = False
        self._root = os.path.realpath(directory) if directory else ""
        super().__init__(*args, directory=directory, **kwargs)

    def translate_path(self, path: str) -> str:  # noqa: N802
        translated = super().translate_path(path)
        if not _inside(translated, self._root):
            # A symlink (or any other resolution) that leaves the share root is
            # not published; this path does not exist, so send_head answers 404.
            logger.warning("share %s: refused a path outside the root: %r", self.public_prefix, path)
            return os.path.join(self._root, ".mesh-share-denied")
        return translated

    def send_response(self, code: int, message: Optional[str] = None) -> None:  # noqa: N802
        try:
            self._redirecting = int(code) in (301, 302, 303, 307, 308)
        except (TypeError, ValueError):
            self._redirecting = False
        super().send_response(code, message)

    def send_header(self, keyword: str, value: Any) -> None:  # noqa: N802
        if (
            self._redirecting
            and str(keyword).lower() == "location"
            and isinstance(value, str)
            and value.startswith("/")
        ):
            value = self.public_prefix + value
        super().send_header(keyword, value)

    def list_directory(self, path: str):  # type: ignore[override]
        if not self.listing_enabled:
            self.send_error(403, "Directory listing is disabled")
            return None
        return super().list_directory(path)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)


def _start_server(slug: str, share: Dict[str, Any]) -> Dict[str, Any]:
    handler = partial(
        _ShareHandler,
        directory=share.get("root") or "",
        public_prefix="/%s/%s" % (_user(), slug),
        listing=_listing_enabled(),
    )
    # Port 0 -> the OS assigns a free loopback port; nothing is ever bound publicly.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="mesh-share-%s" % slug[:24], daemon=True)
    thread.start()
    entry = {"httpd": httpd, "thread": thread, "port": int(httpd.server_address[1])}
    _SERVERS[slug] = entry
    logger.info("share %s listening on 127.0.0.1:%s", slug, entry["port"])
    return entry


def _ensure_server(slug: str) -> Optional[Dict[str, Any]]:
    """Return the running server for ``slug``, starting it lazily if needed.

    Laziness is what makes a URL survive an agent restart: the registry is on
    disk, and the first request after the restart brings the server back up.
    """
    with _LOCK:
        share = _load_registry().get(slug)
        if not share:
            return None
        entry = _SERVERS.get(slug)
        if entry and entry["thread"].is_alive():
            return entry
        root = share.get("root") or ""
        if not root or not os.path.isdir(root):
            return None
        return _start_server(slug, share)


def _stop_server(slug: str) -> bool:
    entry = _SERVERS.pop(slug, None)
    if not entry:
        return False
    try:
        entry["httpd"].shutdown()
    except Exception:
        pass
    try:
        entry["httpd"].server_close()
    except Exception:
        pass
    return True


def stop_all() -> int:
    """Stop every local share server (shutdown / test cleanup). Returns the count."""
    with _LOCK:
        slugs = list(_SERVERS.keys())
    for slug in slugs:
        _stop_server(slug)
    return len(slugs)


# ---------------------------------------------------------------------------
# Public HTTP proxy (gateway -> node)
# ---------------------------------------------------------------------------

class _NoRedirect(HTTPRedirectHandler):
    """Do not follow redirects: the browser must see the (rewritten) 301."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = build_opener(_NoRedirect)


def _plain(status: int, text: str, content_type: str = "text/plain; charset=utf-8",
           extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    body = text.encode("utf-8", "replace")
    headers = {"content-type": content_type, "content-length": str(len(body))}
    if extra_headers:
        headers.update(extra_headers)
    return {"status": status, "headers": headers, "body_b64": base64.b64encode(body).decode("ascii")}


def handle_http_request(args: Dict[str, Any]) -> Dict[str, Any]:
    """Serve one public ``GET``/``HEAD`` from the gateway through the tunnel.

    Returns ``{"status", "headers", "body_b64"}`` for the gateway to relay. A
    missing share and a wrong secret are both an opaque 404, so the endpoint
    cannot be used to enumerate node names or share names.
    """
    if not isinstance(args, dict):
        return _plain(400, "bad request\n")
    method = str(args.get("method") or "GET").upper()
    if method not in ("GET", "HEAD"):
        return _plain(405, "method not allowed\n", extra_headers={"allow": "GET, HEAD"})
    slug = str(args.get("slug") or "")
    if not SLUG_RE.match(slug):
        return _plain(404, "not found\n")
    entry = _ensure_server(slug)
    if entry is None:
        return _plain(404, "not found\n")

    path = str(args.get("path") or "/")
    if "\x00" in path:
        # A NUL would make the local open() raise; answer the same opaque 404.
        return _plain(404, "not found\n")
    if not path.startswith("/"):
        path = "/" + path
    query = str(args.get("query") or "").lstrip("?")
    # ``safe="/%"`` keeps an existing percent-encoding intact and never lets a
    # literal "?" or "#" escape into the request line.
    url = "http://127.0.0.1:%d%s" % (entry["port"], quote(path, safe="/%"))
    if query:
        url += "?" + query

    request = Request(url, method=method, headers={
        "User-Agent": "antigravity-mesh-gateway/1.0",
        "Accept": "*/*",
    })
    response = None
    try:
        try:
            response = _OPENER.open(request, timeout=20)
        except HTTPError as exc:
            response = exc
        status = int(getattr(response, "status", None) or response.getcode())
        raw_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        limit = _max_bytes()
        body = b"" if method == "HEAD" else response.read(limit + 1)
    except Exception as exc:
        logger.warning("share %s: local server error: %s", slug, exc)
        return _plain(502, "share server error\n")
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    if len(body) > limit:
        logger.warning("share %s: response exceeds %d bytes", slug, limit)
        return _plain(413, "file exceeds the share response limit of %d bytes\n" % limit)

    headers = {k: v for k, v in raw_headers.items() if k in _KEEP_HEADERS}
    headers.setdefault("content-type", "application/octet-stream")
    if method == "HEAD":
        # A HEAD body is empty, so judge the size from the declared length: an
        # over-limit resource must answer 413 for HEAD exactly as it does for GET.
        try:
            declared = int(raw_headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > limit:
            logger.warning("share %s: resource exceeds %d bytes", slug, limit)
            return _plain(413, "file exceeds the share response limit of %d bytes\n" % limit)
        headers["content-length"] = raw_headers.get("content-length", "0")
    else:
        headers["content-length"] = str(len(body))
    return {"status": status, "headers": headers, "body_b64": base64.b64encode(body).decode("ascii")}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

def share_file(path: Any, name: Any = None, overwrite: Any = False) -> Dict[str, Any]:
    """Publish one file: copy it into the share root and return its public URL."""
    if not path:
        return {"error": "'path' is required"}
    source = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isfile(source):
        if os.path.isdir(source):
            return {"error": "not a file: %s (use serve_dir to publish a directory)" % path}
        return {"error": "file not found: %s" % path}
    size = os.path.getsize(source)
    limit = _max_bytes()
    if size > limit:
        return {
            "error": "file is %d bytes; the public share limit is %d bytes "
                     "(raise MESH_WEB_MAX_BYTES if this is intentional)" % (size, limit)
        }

    display = _clean_name(name or os.path.splitext(os.path.basename(source))[0], "file")
    with _RegistryGuard():
        registry = _load_registry()
        existing = _find_by_name(registry, display)
        if existing and not _truthy(overwrite):
            return {
                "error": "share name '%s' already exists (slug %s); pass overwrite=true to replace it "
                         "or choose another name" % (display, existing)
            }
        if existing:
            _drop_locked(registry, existing)
        if len(registry) >= _max_shares():
            return {
                "error": "share limit reached (%d); call unshare for a share you no longer need"
                         % _max_shares()
            }
        slug = _make_slug(display)
        root = os.path.join(_public_root(), slug)
        os.makedirs(root, exist_ok=True)
        filename = os.path.basename(source) or "file"
        destination = os.path.join(root, filename)
        try:
            shutil.copy2(source, destination)
        except Exception as exc:
            shutil.rmtree(root, ignore_errors=True)
            return {"error": "could not copy the file into the share root: %s" % exc}
        share = {
            "kind": "file",
            "name": display,
            "slug": slug,
            "root": root,
            "filename": filename,
            "source": source,
            "size": size,
            "created_at": _now_iso(),
        }
        registry[slug] = share
        _save_registry(registry)
        entry = _start_server(slug, share)

    url = _public_url(slug, "/" + quote(filename))
    return {
        "ok": True,
        "url": url,
        "slug": slug,
        "name": display,
        "kind": "file",
        "file": destination,
        "size": size,
        "local_url": "http://127.0.0.1:%d/%s" % (entry["port"], quote(filename)),
        "note": "The URL is the credential: anyone who has it can read this file. "
                "Call unshare to revoke it.",
    }


def serve_dir(path: Any, name: Any = None) -> Dict[str, Any]:
    """Start a loopback static web server for ``path`` and return its public URL."""
    if not path:
        return {"error": "'path' is required"}
    root = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isdir(root):
        return {"error": "not a directory: %s" % path}
    # Refuse the share storage itself (and any ancestor of it): publishing the
    # web dir would expose shares.json - every slug and every source path.
    for storage in (_web_dir(), _public_root()):
        if _inside(root, storage) or _inside(storage, root):
            return {
                "error": "refusing to publish '%s': it overlaps the share storage directory (%s); "
                         "the registry contains share secrets" % (root, storage)
            }

    display = _clean_name(name or os.path.basename(root), "dir")
    with _RegistryGuard():
        registry = _load_registry()
        existing = _find_by_name(registry, display)
        if existing:
            return {
                "error": "share name '%s' already exists (slug %s); call unshare first or pass "
                         "another name" % (display, existing)
            }
        if len(registry) >= _max_shares():
            return {
                "error": "share limit reached (%d); call unshare for a share you no longer need"
                         % _max_shares()
            }
        slug = _make_slug(display)
        share = {
            "kind": "dir",
            "name": display,
            "slug": slug,
            "root": root,
            "source": root,
            "created_at": _now_iso(),
        }
        registry[slug] = share
        _save_registry(registry)
        entry = _start_server(slug, share)

    return {
        "ok": True,
        "url": _public_url(slug, "/"),
        "slug": slug,
        "name": display,
        "kind": "dir",
        "root": root,
        "local_url": "http://127.0.0.1:%d/" % entry["port"],
        "note": "Served in place from the node (no copy). The URL is the credential; "
                "call unshare to stop the server.",
    }


def share_list() -> Dict[str, Any]:
    """List active shares with their public URLs."""
    with _RegistryGuard():
        registry = _load_registry()
        shares: List[Dict[str, Any]] = []
        for slug, share in sorted(registry.items(), key=lambda item: str(item[1].get("created_at") or "")):
            entry = _SERVERS.get(slug)
            running = bool(entry and entry["thread"].is_alive())
            suffix = "/"
            if share.get("kind") == "file" and share.get("filename"):
                suffix = "/" + quote(str(share["filename"]))
            shares.append({
                "name": share.get("name"),
                "slug": slug,
                "kind": share.get("kind"),
                "url": _public_url(slug, suffix),
                "local_url": ("http://127.0.0.1:%d/" % entry["port"]) if entry else None,
                "root": share.get("root"),
                "source": share.get("source"),
                "size_bytes": share.get("size"),
                "created_at": share.get("created_at"),
                "running": running,
            })
        return {
            "shares": shares,
            "count": len(shares),
            "public_base": _public_base(),
            "user": _user(),
        }


def unshare(target: Any) -> Dict[str, Any]:
    """Stop a share and revoke its URL. Accepts the share name, slug or public URL."""
    if not target:
        return {"error": "'name' is required"}
    with _RegistryGuard():
        registry = _load_registry()
        slug = _resolve_slug(registry, str(target))
        if isinstance(slug, dict):
            return slug
        share = registry.pop(slug)
        _save_registry(registry)
        stopped = _stop_server(slug)
        removed = False
        root = str(share.get("root") or "")
        # Only ever delete the directory this module created for a file share -
        # never the shared storage root itself, never a user directory.
        if share.get("kind") == "file" and _removable_copy(root):
            shutil.rmtree(root, ignore_errors=True)
            removed = True
        return {
            "ok": True,
            "slug": slug,
            "name": share.get("name"),
            "server_stopped": stopped,
            "files_removed": removed,
        }


def _resolve_slug(registry: Dict[str, Dict[str, Any]], target: str):
    """Resolve a name/slug/URL to a slug, or return an error dict.

    A public URL is ``/<node>/<slug>/<path>``, so the slug is the first segment
    after the node name. Segments are never scanned back-to-front: a file inside
    one share may be named exactly like another share's slug, and matching that
    would revoke the wrong share.
    """
    key = target.strip()
    if key in registry:
        return key
    path = key
    if "://" in key:
        try:
            path = urlsplit(key).path
        except Exception:
            path = ""
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if segments:
        if segments[0] == _user().lower() and len(segments) > 1:
            head = segments[1]
        else:
            head = segments[0]
        if head in registry:
            return head
    # Accept the human name as it was typed: share names are stored lowercased.
    folded = (segments[0] if len(segments) == 1 else key).lower()
    matches = [slug for slug, share in registry.items() if str(share.get("name") or "") == folded]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return {"error": "ambiguous share name '%s'; use one of the slugs: %s" % (key, ", ".join(matches))}
    return {"error": "no share named '%s' (call share_list to see active shares)" % target}


def _removable_copy(root: str) -> bool:
    """True only for a per-file share directory this module created."""
    if not root:
        return False
    public_root = os.path.realpath(_public_root())
    resolved = os.path.realpath(root)
    return resolved != public_root and _inside(resolved, public_root)


def _drop_locked(registry: Dict[str, Dict[str, Any]], slug: str) -> None:
    """Remove a share while the registry guard is held (used by overwrite)."""
    share = registry.pop(slug, {})
    _stop_server(slug)
    root = str(share.get("root") or "")
    if share.get("kind") == "file" and _removable_copy(root):
        shutil.rmtree(root, ignore_errors=True)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
