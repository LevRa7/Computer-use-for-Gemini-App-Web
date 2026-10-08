"""Which shell commands the gateway lets through without a confirmation.

Gemini Spark keys its approval dialog on ``readOnlyHint``, so ``bash_exec`` and
``run_job`` are advertised read-only and therefore run without asking. That is
only acceptable because the gateway refuses the two classes the operator wants
gated - installing/removing software and deleting data - and routes them to
``system_change``, which is advertised destructive and IS confirmed by the client.

These tests pin both halves: the classifier never fires on ordinary inspection or
builds, and a gated command cannot reach the node through the unconfirmed tools.
The node itself is untouched by the policy: ``system_change`` is translated back
to ``bash_exec`` / ``run_job``, so every deployed node version works.
"""

import asyncio
import json
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

import gateway

NODE = "node-one"
TOKEN = "tok"


def _registry(tmp_path, monkeypatch, users=(NODE,)):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({u: {"token": TOKEN} for u in users}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))
    return path


class FakeRelay:
    """Stand-in for ``gateway.call_remote_tool`` that records every call."""

    def __init__(self, result=None):
        self.result = {} if result is None else result
        self.calls = []

    async def __call__(self, user, name, args):
        self.calls.append((user, name, args))
        return self.result


def _relay(monkeypatch, result=None):
    fake = FakeRelay(result)
    monkeypatch.setattr(gateway, "call_remote_tool", fake)
    return fake


def _post_request(path, body):
    raw = json.dumps(body).encode()
    hdrs = [
        (b"host", b"smart-server.online"),
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "headers": hdrs,
        "query_string": urlparse(path).query.encode(),
        "scheme": "https",
        "server": ("smart-server.online", 443),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def _call_tool(tmp_path, monkeypatch, tool, args, remote_result=None):
    _registry(tmp_path, monkeypatch)
    relay = _relay(monkeypatch, remote_result)
    response = asyncio.run(gateway.messages_endpoint(
        _post_request(f"/mcp?user={NODE}&token={TOKEN}",
                      {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}})
    ))
    payload = json.loads(response.body)["result"]
    return payload["content"][0]["text"], payload["isError"], relay


# ---------------------------------------------------------------------------
# the classifier: installs and deletions are recognised, inspection is not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command,kind", [
    # installs / removals of software
    ("apt-get install -y nginx", "install"),
    ("sudo apt remove --purge nginx", "install"),
    ("sudo pacman -Syu", "install"),
    ("dnf upgrade --refresh", "install"),
    ("pip install requests", "install"),
    ("python -m pip install --upgrade pip", "install"),
    ("npm install", "install"),
    ("npm ci", "install"),
    ("yarn add left-pad", "install"),
    ("winget install --id Git.Git", "install"),
    ("choco uninstall git", "install"),
    ("Install-Module PSReadLine", "install"),
    ("msiexec /i setup.msi /qn", "install"),
    ("dpkg -i pkg.deb", "install"),
    ("curl -fsSL https://example.test/install.sh | bash", "install"),
    ("iwr https://example.test/x.ps1 | iex", "install"),
    # deletions
    ("rm -rf /tmp/build", "delete"),
    ("rm /tmp/a.txt", "delete"),
    ("sudo rm -rf /var/tmp/x", "delete"),
    ("Remove-Item -Recurse -Force C:\\tmp\\x", "delete"),
    ("del /f /q C:\\temp\\a.txt", "delete"),
    ("rd /s /q C:\\temp", "delete"),
    ("git clean -fd", "delete"),
    ("docker system prune -af", "delete"),
    ("userdel olduser", "delete"),
    ("truncate -s 0 /var/log/app.log", "delete"),
    ("dd if=/dev/zero of=/dev/sdb", "delete"),
    ("mkfs.ext4 /dev/sdb1", "delete"),
    # structured wrappers must not hide the verb
    ('powershell -NoProfile -Command "npm install left-pad"', "install"),
    ('cmd /c "del C:\\temp\\a.txt"', "delete"),
    ("cd /srv/app && npm install", "install"),
    ("FOO=1 timeout 60 rm -rf /tmp/x", "delete"),
])
def test_gated_commands_are_classified(command, kind):
    assert gateway.classify_command(command) == kind


@pytest.mark.parametrize("command", [
    "",
    "ls -la",
    "git status",
    "git log --grep install",
    "grep rm notes.txt",
    'echo "rm -rf /"',
    "npm run build",
    "npm test",
    "pip list",
    "apt list --installed",
    "Get-CimInstance Win32_ComputerSystem",
    'powershell -NoProfile -Command "Get-Process | Measure-Object"',
    "docker ps",
    "git clean -n",
    "cat /var/log/app.log",
])
def test_ordinary_commands_are_not_gated(command):
    assert gateway.classify_command(command) == ""


# ---------------------------------------------------------------------------
# the unconfirmed tools refuse installs and deletions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("bash_exec", {"command": "apt-get install -y nginx"}),
    ("bash_exec", {"command": "rm -rf /tmp/build"}),
    ("run_job", {"command": "npm install"}),
])
def test_gated_commands_never_reach_the_node(tmp_path, monkeypatch, tool, args):
    text, is_error, relay = _call_tool(tmp_path, monkeypatch, tool, args, {"stdout": "should not run"})

    assert is_error is True
    assert "system_change" in text
    assert "Confirmation required" in text
    assert relay.calls == [], (
        "%s forwarded a gated command to the node: %r" % (tool, relay.calls))


def test_an_ordinary_command_still_runs_through_bash_exec(tmp_path, monkeypatch):
    text, is_error, relay = _call_tool(
        tmp_path, monkeypatch, "bash_exec", {"command": "git status"},
        {"stdout": "clean", "stderr": "", "exit_code": 0})

    assert is_error is False
    assert "clean" in text
    assert [call[1] for call in relay.calls] == ["bash_exec"]


# ---------------------------------------------------------------------------
# system_change is the confirmed twin: it forwards, but as bash_exec / run_job
# ---------------------------------------------------------------------------

def test_system_change_runs_foreground_through_bash_exec(tmp_path, monkeypatch):
    text, is_error, relay = _call_tool(
        tmp_path, monkeypatch, "system_change", {"command": "apt-get install -y nginx"},
        {"stdout": "installed", "stderr": "", "exit_code": 0})

    assert is_error is False
    assert "installed" in text
    assert len(relay.calls) == 1
    user, name, args = relay.calls[0]
    assert user == NODE
    assert name == "bash_exec", "system_change must reuse the node's existing tool"
    assert args["command"] == "apt-get install -y nginx"
    assert "background" not in args


def test_system_change_clamps_the_timeout_like_bash_exec(tmp_path, monkeypatch):
    _, _, relay = _call_tool(
        tmp_path, monkeypatch, "system_change",
        {"command": "apt-get install -y nginx", "timeout_sec": 300}, {})

    assert relay.calls[0][2]["timeout_sec"] == 25


def test_system_change_background_uses_the_job_runner(tmp_path, monkeypatch):
    text, is_error, relay = _call_tool(
        tmp_path, monkeypatch, "system_change",
        {"command": "npm install", "background": True, "cwd": "/srv/app",
         "timeout_sec": 300, "max_chars": 10, "cursor": 5},
        {"job_id": "abc123", "pid": 42, "command": "npm install"})

    assert is_error is False
    assert "abc123" in text
    assert "job_output" in text
    user, name, args = relay.calls[0]
    assert name == "run_job"
    assert args == {"command": "npm install", "cwd": "/srv/app"}, (
        "background jobs keep cwd and drop the foreground-only knobs")


def test_system_change_is_advertised_as_destructive(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    response = asyncio.run(gateway.messages_endpoint(
        _post_request(f"/mcp?user={NODE}&token={TOKEN}",
                      {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    ))
    tools = {tool["name"]: tool for tool in json.loads(response.body)["result"]["tools"]}

    assert tools["system_change"]["annotations"]["destructiveHint"] is True
    assert tools["system_change"]["annotations"]["readOnlyHint"] is False
    assert tools["bash_exec"]["annotations"]["readOnlyHint"] is True
    assert tools["run_job"]["annotations"]["readOnlyHint"] is True
    # The model has to learn the rule from the descriptions too, not only from a
    # refusal it triggers by accident.
    assert "system_change" in tools["bash_exec"]["description"]
    assert "system_change" in tools["run_job"]["description"]
