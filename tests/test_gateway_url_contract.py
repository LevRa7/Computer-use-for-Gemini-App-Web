"""The gateway must advertise ONE shared domain; a node is selected by ``?user=``.

Regression guard for the per-device-subdomain design: every extra hostname needs
its own DNS record and its own SAN in the TLS certificate, and a node whose name
is missing from the certificate fails the handshake -- which the Gemini client
reports only as an opaque "cannot connect to host". The canonical contract is:

    https://<shared-domain>/sse?user=<node-name>&token=<token>

Legacy subdomain URLs must keep resolving (backwards compatibility) but must
never be advertised in registration responses or in the orchestration skill.
"""

import asyncio
import importlib
import json
from urllib.parse import urlparse

from starlette.requests import Request

import gateway


def _request(query: str = "", host: str = "smart-server.online", method: str = "GET",
             body: bytes = b"") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/sse",
        "headers": [(b"host", host.encode()), (b"content-type", b"application/json")],
        "query_string": urlparse(f"/{query}").query.encode(),
        "scheme": "https",
        "server": (host, 443),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


# ---------------------------------------------------------------------------
# Node resolution
# ---------------------------------------------------------------------------

def test_query_param_on_shared_domain_is_canonical():
    assert gateway.get_target_user(_request("?user=node-one")) == "node-one"


def test_query_param_wins_over_a_legacy_subdomain():
    """The shared domain is the contract; a stale subdomain must not override it."""
    req = _request("?user=node-one", host="legacy-node.smart-server.online")
    assert gateway.get_target_user(req) == "node-one"


def test_legacy_subdomain_still_resolves_when_no_user_param():
    req = _request("", host="legacy-node.smart-server.online")
    assert gateway.get_target_user(req) == "legacy-node"


def test_shared_domain_without_user_is_anonymous():
    assert gateway.get_target_user(_request("", host="smart-server.online")) == "anonymous"


# ---------------------------------------------------------------------------
# Advertised URLs
# ---------------------------------------------------------------------------

def test_public_url_uses_the_shared_domain():
    url = gateway.public_url("/sse", "node-one", "tok")
    assert url == f"{gateway.PUBLIC_BASE_URL}/sse?user=node-one&token=tok"
    assert "node-one." not in url, "the node name must never become a hostname"


def test_skill_advertises_the_shared_domain_only():
    skill = gateway.get_skill("node-one", gateway.PUBLIC_HOST)
    assert f"{gateway.PUBLIC_BASE_URL}/sse?user=node-one" in skill
    assert "node-one.smart-server.online" not in skill
    assert f"Target Node: node-one (shared gateway {gateway.PUBLIC_HOST})" in skill


def test_registration_response_has_no_subdomain(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    payload = json.dumps({"username": "fresh-node", "mac_address": "", "os": "linux"}).encode()
    response = asyncio.run(gateway.api_register(_request(method="POST", body=payload)))
    data = json.loads(response.body)

    assert data["status"] == "success"
    assert data["username"] == "fresh-node"
    assert "subdomain" not in data, "per-device subdomains must not be issued any more"
    assert data["gateway"] == gateway.PUBLIC_HOST
    assert data["sse_url"] == f"{gateway.PUBLIC_BASE_URL}/sse?user=fresh-node&token={data['token']}"
    assert f"{gateway.PUBLIC_HOST}/ws/tunnel?user=fresh-node" in data["tunnel_url"]


def test_shared_domain_is_configurable(monkeypatch):
    """MESH_PUBLIC_URL lets a self-hosted gateway reuse the same contract."""
    monkeypatch.setenv("MESH_PUBLIC_URL", "https://mesh.example.com/")
    try:
        reloaded = importlib.reload(gateway)
        assert reloaded.PUBLIC_BASE_URL == "https://mesh.example.com"
        assert reloaded.PUBLIC_HOST == "mesh.example.com"
        assert reloaded.public_url("/mcp", "node-one", "tok") == (
            "https://mesh.example.com/mcp?user=node-one&token=tok"
        )
    finally:
        monkeypatch.delenv("MESH_PUBLIC_URL", raising=False)
        importlib.reload(gateway)
