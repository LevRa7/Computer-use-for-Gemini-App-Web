"""One Mcp-Session-Id per session, never the shared user name.

Google's frontends open several parallel connections per turn (a POST for the
tool call, a GET for the stream, a DELETE to tear the session down, often from
different egress IPs). While the gateway returned the user name as the session
id, all of those connections shared one identity, so a DELETE from one of them
looked like it terminated the session an in-flight tool call belonged to -- and
the client dropped the answer it had already received, which the model then
reported as "the host did not answer".
"""

import asyncio
import json

from starlette.requests import Request

import gateway


def _post_request(query: str, body: dict, headers: dict | None = None) -> Request:
    raw = json.dumps(body).encode()
    hdrs = [
        (b"host", b"smart-server.online"),
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
    ]
    for key, value in (headers or {}).items():
        hdrs.append((key.lower().encode(), value.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/sse",
        "headers": hdrs,
        "query_string": query.lstrip("?").encode(),
        "scheme": "https",
        "server": ("smart-server.online", 443),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def _registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"node-one": {"token": "tok"}}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))


def test_issue_session_id_is_unique_and_maps_to_the_user():
    gateway.active_sessions.clear()
    first = gateway.issue_session_id("node-one")
    second = gateway.issue_session_id("node-one")
    assert first != second
    assert len(first) >= 16
    assert gateway.active_sessions[first] == "node-one"
    assert gateway.active_sessions[second] == "node-one"


def test_initialize_returns_an_issued_session_id_not_the_user_name(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    gateway.active_sessions.clear()
    request = _post_request(
        "?user=node-one&token=tok",
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25"}},
    )
    response = asyncio.run(gateway.messages_endpoint(request))

    session_id = response.headers["mcp-session-id"]
    assert session_id != "node-one", "the session id must not be the shared user name"
    assert gateway.active_sessions[session_id] == "node-one"

    body = json.loads(response.body)
    assert body["result"]["protocolVersion"] == "2025-11-25"


def test_each_initialize_gets_its_own_session(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    gateway.active_sessions.clear()
    body = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"}}
    first = asyncio.run(gateway.messages_endpoint(_post_request("?user=node-one&token=tok", body)))
    second = asyncio.run(gateway.messages_endpoint(_post_request("?user=node-one&token=tok", body)))
    assert first.headers["mcp-session-id"] != second.headers["mcp-session-id"]


def test_client_supplied_session_id_is_echoed(tmp_path, monkeypatch):
    """A client that holds a session id keeps seeing the same one."""
    _registry(tmp_path, monkeypatch)
    request = _post_request(
        "?user=node-one&token=tok",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"mcp-session-id": "client-held-id"},
    )
    response = asyncio.run(gateway.messages_endpoint(request))
    assert response.headers["mcp-session-id"] == "client-held-id"
