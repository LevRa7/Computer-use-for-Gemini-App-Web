"""Tests for public file shares: core/web_share.py plus the mcp_tools share tools.

The tools are exercised exactly as the gateway calls them - through
``mcp_tools.call_tool`` - against a temporary share directory, so nothing leaks
into the real ``~/.cache/antigravity-mesh/web`` and no port stays bound.

Two properties beyond "it works" are pinned here on purpose:

* the domain in a published URL comes from :mod:`core.domain` (an explicit
  ``configure(public_url=...)`` still wins), never from a chain kept inside
  ``web_share``;
* a checkout without ``core/web_share.py`` still serves every other tool and
  answers the share tools with the documented "update the node" error instead of
  raising.
"""

import base64
import os
import urllib.request

import pytest

from core import domain, mcp_tools, web_share

#: Node name used for every link in this module (the gateway routes
#: ``/<node>/<slug>/...`` back to the tunnel registered under this name).
NODE = "test-node"

#: The four tools the gateway advertises; they must be the last four in TOOLS.
SHARE_TOOLS = ("share_file", "serve_dir", "share_list", "unshare")

#: The device branch - the node's own phone or laptop - landed in the same slot as
#: self-update: between the legacy surface and the shares, so neither of the two
#: blocks that already had a position had to move. It is its own group rather than
#: more of PRE_EXISTING_TOOLS: those names really do predate the share surface,
#: and swallowing the device tools into them would make that name a lie.
DEVICE_TOOLS = ("device_info", "device_control", "device_capture", "device_messages")

#: The tool surface that existed before shares were added, in order. Self-update
#: (mesh_update) was added in the same slot: before the share tools, after the
#: surface that existed when shares landed.
PRE_EXISTING_TOOLS = (
    "mesh_status",
    "system_info",
    "system_vitals",
    "get_orchestration_skill",
    "list_dir",
    "bash_exec",
    "read_file",
    "write_file",
    "edit_file",
    "grep_search",
    "glob_find",
    "run_job",
    "job_output",
    "job_kill",
    "job_list",
    "mesh_update",
)


def _opener():
    """A urllib opener that never routes a loopback request through a proxy."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str):
    with _opener().open(url, timeout=20) as response:
        return response.status, response.read()


@pytest.fixture(autouse=True)
def isolated_shares(tmp_path, monkeypatch):
    """Point the share tools at a temporary web dir and a known node name."""
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL", "MESH_DOMAIN_FILE",
                "MESH_WEB_DIR", "MESH_WEB_MAX_BYTES", "MESH_WEB_MAX_SHARES",
                "MESH_WEB_LISTING"):
        monkeypatch.delenv(key, raising=False)
    domain.reset()

    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
        web_dir=str(tmp_path / "web"),
        mesh_user=NODE,
        max_share_bytes="",
        max_shares="",
        web_listing="",
        public_url="",
    )
    yield
    web_share.stop_all()
    domain.reset()
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/antigravity-mesh/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
        web_dir="",
        mesh_user="",
        max_share_bytes="",
        max_shares="",
        web_listing="",
        public_url="",
    )


# ---------------------------------------------------------------------------
# The tool surface
# ---------------------------------------------------------------------------

def test_the_share_tools_are_the_last_four_and_do_not_disturb_the_rest():
    names = [tool["name"] for tool in mcp_tools.TOOLS]
    # Three blocks, in the order they were added: legacy, device branch, shares.
    device_at = len(PRE_EXISTING_TOOLS)
    shares_at = device_at + len(DEVICE_TOOLS)
    assert names[:device_at] == list(PRE_EXISTING_TOOLS)
    assert names[device_at:shares_at] == list(DEVICE_TOOLS)
    assert tuple(names[shares_at:]) == SHARE_TOOLS
    assert len(names) == 24, (
        "the node must advertise 16 legacy tools, 4 device tools and 4 share tools")


def test_every_share_tool_has_a_handler_and_a_schema():
    for name in SHARE_TOOLS:
        assert name in mcp_tools._HANDLERS, name
        tool = next(tool for tool in mcp_tools.TOOLS if tool["name"] == name)
        assert tool["inputSchema"]["type"] == "object"


def test_the_internal_relay_is_not_advertised():
    """``_http_share`` is the gateway's relay target, never a model-facing tool."""
    assert "_http_share" in mcp_tools._HANDLERS
    assert "_http_share" not in [tool["name"] for tool in mcp_tools.TOOLS]


# ---------------------------------------------------------------------------
# share_file / share_list / unshare
# ---------------------------------------------------------------------------

def test_share_file_publishes_serves_lists_and_unshares(tmp_path):
    source = tmp_path / "report.txt"
    # Bytes, not text: Path.write_text translates "\n" to CRLF on Windows and the
    # assertions below compare the published copy byte for byte.
    source.write_bytes(b"hello share\n")

    result = mcp_tools.call_tool("share_file", {"path": str(source), "name": "My Report"})

    assert result.get("ok") is True, result
    assert result["name"] == "my-report"
    assert result["kind"] == "file"
    assert result["size"] == len(b"hello share\n")
    assert web_share.SLUG_RE.match(result["slug"]), result["slug"]
    assert result["url"] == "https://%s/%s/%s/report.txt" % (
        domain.public_host(), NODE, result["slug"])
    # The published bytes are a copy, so the link cannot change under the reader.
    assert os.path.isfile(result["file"])
    assert result["file"] != str(source)

    status, body = _get(result["local_url"])
    assert status == 200
    assert body == b"hello share\n"

    listed = mcp_tools.call_tool("share_list")
    assert listed["count"] == 1
    assert listed["user"] == NODE
    entry, = listed["shares"]
    assert entry["name"] == "my-report"
    assert entry["slug"] == result["slug"]
    assert entry["url"] == result["url"]
    assert entry["running"] is True

    revoked = mcp_tools.call_tool("unshare", {"name": "my-report"})
    assert revoked["ok"] is True
    assert revoked["slug"] == result["slug"]
    assert revoked["server_stopped"] is True
    assert revoked["files_removed"] is True
    assert not os.path.exists(result["file"]), "the copy must be deleted on unshare"

    assert mcp_tools.call_tool("share_list")["count"] == 0
    again = mcp_tools.call_tool("unshare", {"name": "my-report"})
    assert "error" in again and "no share named" in again["error"]


def test_share_file_refuses_a_directory_and_a_missing_path(tmp_path):
    on_dir = mcp_tools.call_tool("share_file", {"path": str(tmp_path)})
    assert "use serve_dir" in on_dir["error"]

    missing = mcp_tools.call_tool("share_file", {"path": str(tmp_path / "nope.txt")})
    assert "file not found" in missing["error"]

    assert "required" in mcp_tools.call_tool("share_file", {})["error"]


def test_share_file_does_not_silently_replace_a_name(tmp_path):
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    mcp_tools.call_tool("share_file", {"path": str(first), "name": "same"})
    clash = mcp_tools.call_tool("share_file", {"path": str(second), "name": "same"})
    assert "already exists" in clash["error"]
    assert "overwrite=true" in clash["error"]

    replaced = mcp_tools.call_tool(
        "share_file", {"path": str(second), "name": "same", "overwrite": True})
    assert replaced.get("ok") is True, replaced
    assert replaced["url"].endswith("/two.txt")
    assert mcp_tools.call_tool("share_list")["count"] == 1


def test_unshare_accepts_the_public_url(tmp_path):
    source = tmp_path / "doc.txt"
    source.write_text("doc", encoding="utf-8")
    published = mcp_tools.call_tool("share_file", {"path": str(source)})

    revoked = mcp_tools.call_tool("unshare", {"name": published["url"]})
    assert revoked["ok"] is True
    assert revoked["slug"] == published["slug"]


# ---------------------------------------------------------------------------
# serve_dir
# ---------------------------------------------------------------------------

def test_serve_dir_lists_in_place_and_survives_unshare(tmp_path):
    served = tmp_path / "site"
    served.mkdir()
    (served / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")

    result = mcp_tools.call_tool("serve_dir", {"path": str(served), "name": "Site"})

    assert result.get("ok") is True, result
    assert result["kind"] == "dir"
    assert result["url"] == "https://%s/%s/%s/" % (domain.public_host(), NODE, result["slug"])
    assert result["url"].endswith("/")

    status, body = _get(result["local_url"])
    assert status == 200
    assert b"hello" in body

    revoked = mcp_tools.call_tool("unshare", {"name": "site"})
    assert revoked["ok"] is True
    assert revoked["files_removed"] is False, "a served directory must never be deleted"
    assert served.is_dir()
    assert (served / "index.html").read_text(encoding="utf-8") == "<h1>hello</h1>"


def test_serve_dir_refuses_the_share_storage(tmp_path):
    """Publishing the web dir would expose shares.json - every slug and source path."""
    storage = tmp_path / "web"
    storage.mkdir()
    refused = mcp_tools.call_tool("serve_dir", {"path": str(storage)})
    assert "refusing to publish" in refused["error"]

    assert "not a directory" in mcp_tools.call_tool(
        "serve_dir", {"path": str(tmp_path / "absent")})["error"]


# ---------------------------------------------------------------------------
# The relay the gateway uses
# ---------------------------------------------------------------------------

def test_the_gateway_relay_serves_a_shared_file(tmp_path):
    """The deployed gateway calls ``_http_share`` over the tunnel; it must answer."""
    source = tmp_path / "relay.txt"
    source.write_text("relayed body", encoding="utf-8")
    published = mcp_tools.call_tool("share_file", {"path": str(source)})

    relayed = mcp_tools.call_tool("_http_share", {
        "slug": published["slug"], "path": "/relay.txt", "method": "GET", "query": ""})

    assert relayed["status"] == 200
    assert base64.b64decode(relayed["body_b64"]) == b"relayed body"

    # A wrong secret is an opaque 404, exactly like a share that never existed.
    assert mcp_tools.call_tool("_http_share", {
        "slug": "relay-0000000000000000", "path": "/relay.txt", "method": "GET"})["status"] == 404


# ---------------------------------------------------------------------------
# The domain comes from core.domain
# ---------------------------------------------------------------------------

def test_share_link_takes_its_domain_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_PUBLIC_URL", "https://links.example.com/")
    source = tmp_path / "domain.txt"
    source.write_text("domain", encoding="utf-8")

    result = mcp_tools.call_tool("share_file", {"path": str(source)})

    assert result["url"].startswith("https://links.example.com/%s/" % NODE)
    assert mcp_tools.call_tool("share_list")["public_base"] == "https://links.example.com"


def test_share_link_follows_the_domain_file(tmp_path, monkeypatch):
    domain_file = tmp_path / "domain.env"
    domain_file.write_text("MESH_PUBLIC_URL=https://file-domain.example.com\n", encoding="utf-8")
    monkeypatch.setenv("MESH_DOMAIN_FILE", str(domain_file))
    source = tmp_path / "file-domain.txt"
    source.write_text("x", encoding="utf-8")

    result = mcp_tools.call_tool("share_file", {"path": str(source)})

    assert result["url"].startswith("https://file-domain.example.com/%s/" % NODE)


def test_an_explicit_public_url_wins_over_the_environment(tmp_path, monkeypatch):
    """web_share still accepts its own public_url, and it outranks the domain chain."""
    monkeypatch.setenv("MESH_PUBLIC_URL", "https://from-env.example.com")
    mcp_tools.configure(public_url="https://explicit.example.com")
    source = tmp_path / "explicit.txt"
    source.write_text("explicit", encoding="utf-8")

    result = mcp_tools.call_tool("share_file", {"path": str(source)})

    assert result["url"].startswith("https://explicit.example.com/%s/" % NODE)


# ---------------------------------------------------------------------------
# A checkout without core/web_share.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,args", (
    ("share_file", {"path": "x"}),
    ("serve_dir", {"path": "x"}),
    ("share_list", {}),
    ("unshare", {"name": "x"}),
))
def test_a_missing_web_share_module_answers_honestly(monkeypatch, name, args):
    monkeypatch.setattr(mcp_tools, "_web_share", None)

    result = mcp_tools.call_tool(name, args)

    assert isinstance(result, dict), result
    assert "core/web_share.py is missing" in result["error"], result
    assert "unavailable on this node" in result["error"], result


def test_the_relay_answers_404_without_web_share(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_web_share", None)
    assert mcp_tools.call_tool("_http_share", {"slug": "a-0123456789abcdef"})["status"] == 404


def test_configure_does_not_fail_without_web_share(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_web_share", None)
    mcp_tools.configure(web_dir="", mesh_user="", public_url="")
    assert mcp_tools.call_tool("mesh_status") is not None


# ---------------------------------------------------------------------------
# read_only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,args", (
    ("share_file", {"path": "x"}),
    ("serve_dir", {"path": "x"}),
))
def test_publishing_is_refused_when_the_node_is_read_only(name, args):
    mcp_tools.configure(read_only=True)
    try:
        result = mcp_tools.call_tool(name, args)
        assert "MESH_READ_ONLY" in result["error"]
    finally:
        mcp_tools.configure(read_only=False)


# ---------------------------------------------------------------------------
# payload size
# ---------------------------------------------------------------------------

def test_the_published_limits_are_the_raised_ones():
    """8 MiB was the old default; the ceiling is what an operator opts into."""
    assert web_share.DEFAULT_MAX_BYTES == 32 * 1024 * 1024
    assert web_share.MAX_MAX_BYTES == 64 * 1024 * 1024
    assert web_share.MIN_MAX_BYTES < web_share.DEFAULT_MAX_BYTES < web_share.MAX_MAX_BYTES


def test_the_transport_cap_carries_the_share_ceiling():
    """The gateway must accept ONE websocket message holding a ceiling-sized file.

    Drift here fails silently: uvicorn's default is 16 MiB, so a share above about
    12 MiB would be published by the node and then dropped by the transport.
    """
    import re

    gateway = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gateway.py")
    with open(gateway, "r", encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    match = re.search(r"ws_max_size\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024", source)
    assert match, "gateway.py must set ws_max_size explicitly"
    wire_cap = int(match.group(1)) * 1024 * 1024
    # base64 grows the payload by ~4/3 before the JSON framing is added.
    assert wire_cap >= web_share.MAX_MAX_BYTES * 4 / 3


def test_a_file_over_the_limit_is_refused_with_a_usable_message(tmp_path):
    mcp_tools.configure(max_share_bytes="4096")
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * 5000)

    result = mcp_tools.call_tool("share_file", {"path": str(oversized), "name": "oversized"})

    assert "error" in result
    assert "MESH_WEB_MAX_BYTES" in result["error"]
    assert "4096" in result["error"]
    assert "5000" in result["error"]


def test_the_limit_can_be_raised_but_never_past_the_ceiling():
    mcp_tools.configure(max_share_bytes=str(web_share.MAX_MAX_BYTES * 4))
    assert web_share._max_bytes() == web_share.MAX_MAX_BYTES

    mcp_tools.configure(max_share_bytes=str(1))
    assert web_share._max_bytes() == web_share.MIN_MAX_BYTES


def test_the_default_applies_when_nothing_is_configured():
    mcp_tools.configure(max_share_bytes="")
    assert web_share._max_bytes() == web_share.DEFAULT_MAX_BYTES


def test_the_environment_raises_the_limit_within_the_ceiling(monkeypatch):
    monkeypatch.setenv("MESH_WEB_MAX_BYTES", str(48 * 1024 * 1024))
    mcp_tools.configure(max_share_bytes="")
    assert web_share._max_bytes() == 48 * 1024 * 1024

    monkeypatch.setenv("MESH_WEB_MAX_BYTES", str(web_share.MAX_MAX_BYTES * 8))
    assert web_share._max_bytes() == web_share.MAX_MAX_BYTES
