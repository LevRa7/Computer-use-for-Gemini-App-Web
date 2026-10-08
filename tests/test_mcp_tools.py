"""Tests for the shared Antigravity Mesh tool implementation (core/mcp_tools.py).

The tests configure the module against a temporary workspace/jobs directory so
nothing leaks into the real ``~/.cache`` or the repository.
"""

import hashlib
import json
import os
import sys
import time

import pytest

from core import mcp_tools


IS_WINDOWS = os.name == "nt"


def py_command(code: str) -> str:
    """A command that runs *code* with this interpreter in the host's own shell.

    ``bash_exec``/``run_job`` deliberately run the host shell - bash on POSIX,
    PowerShell or cmd.exe on Windows - so a test that hardcoded ``python3``,
    ``sleep`` or ``1>&2`` was really asserting "the host is POSIX" and failed on
    Windows for reasons that had nothing to do with the tool. Building the
    command from ``sys.executable`` keeps the same coverage on both platforms.
    """
    exe = sys.executable
    if IS_WINDOWS:
        # PowerShell: the call operator is what runs a quoted path.
        return '& "%s" -c "%s"' % (exe.replace('"', '`"'), code)
    return "'%s' -c \"%s\"" % (exe.replace("'", "'\\''"), code)


def py_sleep(seconds: float) -> str:
    return py_command("import time; time.sleep(%r)" % seconds)


@pytest.fixture(autouse=True)
def isolated_tools(tmp_path):
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )
    yield
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/antigravity-mesh/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )


# ---------------------------------------------------------------------------
# tool surface
# ---------------------------------------------------------------------------

def test_tools_surface_contains_all_tools():
    names = [t["name"] for t in mcp_tools.TOOLS]
    assert names == [
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
        # Self-update, added before the share surface so the pre-existing order of
        # the fifteen tools above is untouched.
        "mesh_update",
        # Public shares, last so the pre-existing surface keeps its order. The
        # deployed gateway advertises the same four names; the internal relay
        # ("_http_share") is deliberately not part of TOOLS.
        "share_file",
        "serve_dir",
        "share_list",
        "unshare",
    ]
    for spec in mcp_tools.TOOLS:
        assert spec["description"]
        assert spec["inputSchema"]["type"] == "object"


def test_call_tool_never_raises_on_bad_input():
    assert "error" in mcp_tools.call_tool("no_such_tool", {})
    assert "error" in mcp_tools.call_tool("bash_exec", {"command": 123})
    assert "error" in mcp_tools.call_tool("bash_exec", "not-a-dict")
    assert "error" in mcp_tools.call_tool("write_file", {})
    assert "error" in mcp_tools.call_tool("edit_file", {"path": "x"})
    assert "error" in mcp_tools.call_tool(None, {})
    assert "error" in mcp_tools.call_tool("read_file", {"path": "does-not-exist"})


def test_legacy_tools_still_work():
    vitals = mcp_tools.call_tool("system_vitals", {})
    assert "hostname" in vitals and "ram" in vitals and "disk" in vitals
    skill = mcp_tools.call_tool("get_orchestration_skill", {})
    assert isinstance(skill, str) and "Antigravity" in skill
    listing = mcp_tools.call_tool("list_dir", {})
    assert listing["exit_code"] == 0
    assert "Directory:" in listing["stdout"]


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def test_write_file_creates_dirs_and_reports_sha256(tmp_path):
    content = "alpha\nbeta\nGAMMA = 1\n"
    result = mcp_tools.call_tool(
        "write_file", {"path": "sub/dir/file.txt", "content": content}
    )
    assert result["ok"] is True
    assert result["bytes"] == len(content.encode())
    assert result["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    target = tmp_path / "sub" / "dir" / "file.txt"
    assert target.read_text() == content


def test_write_file_mode_is_applied(tmp_path):
    if IS_WINDOWS:
        pytest.skip("Windows has no POSIX permission bits; see test_write_file_mode_on_windows")
    result = mcp_tools.call_tool(
        "write_file", {"path": "script.sh", "content": "#!/bin/sh\n", "mode": "0755"}
    )
    assert result["ok"] is True
    assert result["mode_applied"] is True
    assert (tmp_path / "script.sh").stat().st_mode & 0o777 == 0o755


def test_write_file_mode_on_windows_reports_the_truth(tmp_path):
    """A 0755 request on Windows must not look like it produced a launcher.

    os.chmod there only toggles the read-only attribute, so reporting plain
    success made the model believe an executable script existed.
    """
    if not IS_WINDOWS:
        pytest.skip("Windows-only behaviour")
    result = mcp_tools.call_tool(
        "write_file", {"path": "script.sh", "content": "#!/bin/sh\n", "mode": "0755"}
    )
    assert result["ok"] is True
    assert result["mode_applied"] is False
    assert "execute bit" in result["mode_note"]


def test_write_file_read_only_mode(tmp_path):
    mcp_tools.configure(read_only=True)
    result = mcp_tools.call_tool("write_file", {"path": "nope.txt", "content": "x"})
    assert "error" in result and "read-only" in result["error"]
    assert not (tmp_path / "nope.txt").exists()


def test_write_file_outside_write_roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    mcp_tools.configure(write_roots=[str(allowed)])
    inside = mcp_tools.call_tool("write_file", {"path": str(allowed / "ok.txt"), "content": "ok"})
    assert inside.get("ok") is True
    outside = mcp_tools.call_tool("write_file", {"path": str(tmp_path / "bad.txt"), "content": "x"})
    assert "error" in outside and "write roots" in outside["error"]


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def test_edit_file_replaces_unique_string(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "one\ntwo\nthree\n"})
    result = mcp_tools.call_tool(
        "edit_file", {"path": "f.txt", "old_string": "two", "new_string": "TWO"}
    )
    assert result["ok"] is True and result["replacements"] == 1
    assert (tmp_path / "f.txt").read_text() == "one\nTWO\nthree\n"


def test_edit_file_not_found(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "only\n"})
    result = mcp_tools.call_tool(
        "edit_file", {"path": "f.txt", "old_string": "missing", "new_string": "x"}
    )
    assert result == {"error": "old_string not found"}


def test_edit_file_not_unique(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "dup\ndup\n"})
    result = mcp_tools.call_tool(
        "edit_file", {"path": "f.txt", "old_string": "dup", "new_string": "x"}
    )
    assert "error" in result and "not unique" in result["error"] and "2 matches" in result["error"]


def test_edit_file_replace_all(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "dup\ndup\ndup\n"})
    result = mcp_tools.call_tool(
        "edit_file",
        {"path": "f.txt", "old_string": "dup", "new_string": "x", "replace_all": True},
    )
    assert result["ok"] is True and result["replacements"] == 3
    assert (tmp_path / "f.txt").read_text() == "x\nx\nx\n"


def test_edit_file_sha256_mismatch(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "hello\n"})
    result = mcp_tools.call_tool(
        "edit_file",
        {"path": "f.txt", "old_string": "hello", "new_string": "bye", "expected_sha256": "0" * 64},
    )
    assert "error" in result and "sha256" in result["error"]
    assert (tmp_path / "f.txt").read_text() == "hello\n"


def test_edit_file_sha256_match(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "hello\n"})
    digest = hashlib.sha256(b"hello\n").hexdigest()
    result = mcp_tools.call_tool(
        "edit_file",
        {"path": "f.txt", "old_string": "hello", "new_string": "bye", "expected_sha256": digest},
    )
    assert result["ok"] is True


def test_edit_file_read_only(tmp_path):
    mcp_tools.call_tool("write_file", {"path": "f.txt", "content": "hello\n"})
    mcp_tools.configure(read_only=True)
    result = mcp_tools.call_tool(
        "edit_file", {"path": "f.txt", "old_string": "hello", "new_string": "bye"}
    )
    assert "error" in result and "read-only" in result["error"]


# ---------------------------------------------------------------------------
# bash_exec pagination + spooling
# ---------------------------------------------------------------------------

def test_bash_exec_basic_fields():
    result = mcp_tools.call_tool("bash_exec", {"command": "echo hello"})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert result["stderr"] == ""
    assert result["next_cursor"] is None
    assert result["truncated"] is False


def test_bash_exec_pagination_has_no_gaps():
    command = py_command("print('X' * 5000)")
    full = mcp_tools.call_tool("bash_exec", {"command": command, "max_chars": 1000})
    assert full["truncated"] is True
    assert len(full["stdout"]) <= 1000
    assert isinstance(full["next_cursor"], int)

    collected = full["stdout"]
    cursor = full["next_cursor"]
    guard = 0
    while cursor is not None and guard < 50:
        page = mcp_tools.call_tool(
            "bash_exec", {"command": command, "max_chars": 1000, "cursor": cursor}
        )
        collected += page["stdout"]
        cursor = page["next_cursor"]
        guard += 1
    assert collected == "X" * 5000 + "\n"


def test_bash_exec_spools_large_output(tmp_path):
    # The spool threshold is 2 MiB, so the test output must exceed it; below the
    # threshold the whole chunk is paginated in-memory and saved_to stays None.
    size = mcp_tools.SPOOL_THRESHOLD + 100_000
    result = mcp_tools.call_tool(
        "bash_exec",
        {"command": py_command("print('Y' * %d)" % size), "max_chars": 100},
    )
    assert result["truncated"] is True
    assert result["saved_to"], "output above the spool threshold must be saved to a file"
    assert os.path.isfile(result["saved_to"])
    with open(result["saved_to"], "r", encoding="utf-8") as handle:
        assert len(handle.read()) == size + 1


def test_bash_exec_below_spool_threshold_is_paginated_not_spooled():
    # Just below the threshold: pagination must still reach the end without loss.
    size = mcp_tools.SPOOL_THRESHOLD - 100_000
    command = py_command("print('Z' * %d)" % size)
    first = mcp_tools.call_tool(
        "bash_exec",
        {"command": command, "max_chars": 50000},
    )
    assert first["saved_to"] is None
    assert first["truncated"] is True
    collected = first["stdout"]
    cursor = first["next_cursor"]
    guard = 0
    while cursor is not None and guard < 200:
        page = mcp_tools.call_tool(
            "bash_exec",
            {"command": command, "max_chars": 50000, "cursor": cursor},
        )
        collected += page["stdout"]
        cursor = page["next_cursor"]
        guard += 1
    assert len(collected.replace("\n", "")) == size


def test_bash_exec_timeout_is_clamped_and_reported():
    result = mcp_tools.call_tool("bash_exec", {"command": py_sleep(3), "timeout_sec": 1})
    assert result["exit_code"] == 124
    assert "timed out" in result["stderr"]


# ---------------------------------------------------------------------------
# output decoding (non-ASCII on Windows)
# ---------------------------------------------------------------------------

def _can_encode(encoding: str, text: str) -> bool:
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def test_decode_output_reads_utf8_first(monkeypatch):
    """UTF-8 wins over the console code page: git, node and python children."""
    monkeypatch.setattr(mcp_tools, "_DECODING_CACHE", ["utf-8", "cp866", "cp1251"])
    text = "привет мир"
    assert mcp_tools._decode_output(text.encode("utf-8")) == text


def test_decode_output_reads_the_console_code_page(monkeypatch):
    """cmd.exe/PowerShell builtins write the OEM console page, not the locale.

    Decoding those bytes with ``locale.getpreferredencoding()`` - the default of
    ``text=True`` - produced ``ЇаЁўҐв`` for ``echo привет`` on a Russian Windows.
    """
    monkeypatch.setattr(mcp_tools, "_DECODING_CACHE", ["utf-8", "cp866", "cp1251"])
    text = "привет мир"
    assert mcp_tools._decode_output(text.encode("cp866")) == text


def test_decode_output_reaches_ansi_when_oem_is_absent(monkeypatch):
    """The chain is a priority list; a failing decode moves to the next entry."""
    monkeypatch.setattr(mcp_tools, "_DECODING_CACHE", ["utf-8", "cp1251"])
    text = "привет мир"
    assert mcp_tools._decode_output(text.encode("cp1251")) == text


def test_decode_output_normalises_windows_line_endings():
    assert mcp_tools._decode_output(b"a\r\nb\rc\n") == "a\nb\nc\n"
    assert mcp_tools._decode_output("") == ""
    assert mcp_tools._decode_output(None) == ""


def test_decode_output_survives_undecodable_bytes(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_DECODING_CACHE", ["utf-8", "ascii"])
    assert mcp_tools._decode_output(b"\xff\xfe") == "\ufffd\ufffd"


def test_bash_exec_returns_readable_non_ascii():
    """End-to-end through the real host shell, whichever shell that is."""
    sample = "привет мир"
    # Ask the child what it can actually emit: under an un-coerced C locale a
    # python child escapes non-ASCII instead of writing it, and that is the
    # interpreter's choice, not the tool's decoding.
    probe = mcp_tools.call_tool(
        "bash_exec",
        {"command": py_command("import sys; print(sys.stdout.encoding or 'none')")},
    )
    encoding = (probe.get("stdout") or "").strip()
    if not encoding or not _can_encode(encoding, sample):
        pytest.skip("this interpreter cannot write the sample text (%r)" % encoding)

    result = mcp_tools.call_tool("bash_exec", {"command": py_command("print('%s')" % sample)})
    assert result["exit_code"] == 0
    assert sample in result["stdout"]


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows console code page behaviour")
def test_bash_exec_decodes_a_shell_builtin_write():
    """A shell builtin's own non-ASCII text must survive the round trip."""
    text = "привет мир"
    pages = mcp_tools._windows_code_pages()
    if not any(_can_encode(page, text) for page in pages):
        pytest.skip("this console code page cannot represent the sample text")
    result = mcp_tools.call_tool("bash_exec", {"command": "echo %s" % text})
    assert result["exit_code"] == 0
    # PowerShell's echo writes one object per line, bash's one line: compare the
    # words in order rather than the exact layout.
    assert text in result["stdout"].replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# read_file pagination
# ---------------------------------------------------------------------------

def test_read_file_pagination_reaches_the_end(tmp_path):
    lines = ["line-%03d" % i for i in range(1, 201)]
    (tmp_path / "big.txt").write_text("\n".join(lines) + "\n")

    first = mcp_tools.call_tool("read_file", {"path": "big.txt", "max_chars": 200})
    assert first["exit_code"] == 0
    assert first["truncated"] is True
    assert isinstance(first["next_cursor"], int)
    # Pages must stay byte-exact: no marker is injected into the content, the
    # client follows next_cursor instead (a marker would break stitching).
    assert "cursor=" not in first["stdout"]

    collected = first["stdout"]
    cursor = first["next_cursor"]
    guard = 0
    while cursor is not None and guard < 200:
        page = mcp_tools.call_tool(
            "read_file", {"path": "big.txt", "max_chars": 200, "cursor": cursor}
        )
        collected += page["stdout"]
        cursor = page["next_cursor"]
        guard += 1
    assert " 200 | line-200" in collected


def test_read_file_missing_reports_error():
    result = mcp_tools.call_tool("read_file", {"path": "ghost.txt"})
    assert "error" in result and "File not found" in result["error"]


# ---------------------------------------------------------------------------
# grep_search / glob_find
# ---------------------------------------------------------------------------

def test_grep_search_finds_and_respects_limit(tmp_path):
    (tmp_path / "a.py").write_text("import os\nNEEDLE = 1\n")
    (tmp_path / "b.txt").write_text("needle\nneedle\n")
    (tmp_path / "binary.bin").write_bytes(b"\x00NEEDLE\x00")

    found = mcp_tools.call_tool("grep_search", {"pattern": "NEEDLE"})
    assert found["count"] >= 1
    assert all(not m["path"].endswith("binary.bin") for m in found["matches"])

    limited = mcp_tools.call_tool("grep_search", {"pattern": "needle", "ignore_case": True, "limit": 1})
    assert limited["count"] == 1
    assert limited["truncated"] is True

    fixed = mcp_tools.call_tool("grep_search", {"pattern": "NEEDLE = 1", "fixed": True})
    assert fixed["count"] == 1


def test_glob_find_matches_recursive_and_flat(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("x")
    (tmp_path / "top.txt").write_text("x")
    (tmp_path / "note.md").write_text("x")

    recursive = mcp_tools.call_tool("glob_find", {"pattern": "**/*.txt"})
    assert any(p.endswith("deep.txt") for p in recursive["files"])
    assert any(p.endswith("top.txt") for p in recursive["files"])

    flat = mcp_tools.call_tool("glob_find", {"pattern": "*.md"})
    assert flat["files"] == [str(tmp_path / "note.md")]


# ---------------------------------------------------------------------------
# background jobs
# ---------------------------------------------------------------------------

def _wait_status(job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = mcp_tools.call_tool("job_output", {"job_id": job_id, "wait_ms": 500})
        if output["status"] != "running":
            return output
    return output


def test_run_job_fast_output_is_not_lost():
    started = mcp_tools.call_tool("run_job", {"command": "echo fast-line"})
    output = mcp_tools.call_tool("job_output", {"job_id": started["job_id"], "wait_ms": 5000})
    assert output["status"] == "done"
    assert output["exit_code"] == 0
    assert "fast-line" in output["stdout"]


def test_run_job_output_and_list():
    command = py_command(
        "import sys, time; time.sleep(0.3); print('hi'); sys.stderr.write('err\\n')"
    )
    started = mcp_tools.call_tool("run_job", {"command": command})
    assert started["job_id"]
    assert isinstance(started["pid"], int)

    output = mcp_tools.call_tool("job_output", {"job_id": started["job_id"], "wait_ms": 5000})
    assert output["status"] == "done"
    assert output["exit_code"] == 0
    assert "hi" in output["stdout"]
    assert "err" in output["stderr"]
    assert output["duration"] is not None

    listing = mcp_tools.call_tool("job_list", {})
    assert listing["count"] >= 1
    assert any(j["job_id"] == started["job_id"] for j in listing["jobs"])


def test_run_job_missing_is_reported_and_kill():
    started = mcp_tools.call_tool("run_job", {"command": py_sleep(30)})
    job_id = started["job_id"]

    running = mcp_tools.call_tool("job_output", {"job_id": job_id, "wait_ms": 0})
    assert running["status"] == "running"

    killed = mcp_tools.call_tool("job_kill", {"job_id": job_id})
    assert killed == {"ok": True, "status": "killed"}

    output = _wait_status(job_id)
    assert output["status"] in ("killed", "done")

    assert "error" in mcp_tools.call_tool("job_output", {"job_id": "does-not-exist"})
    assert "error" in mcp_tools.call_tool("job_kill", {"job_id": "does-not-exist"})


def test_job_kill_tolerates_an_already_dead_process(monkeypatch):
    """taskkill exits non-zero ("process not found") when the tree is already gone.

    That is the normal race on Windows between a job finishing and job_kill
    arriving, and it must not be reported as a failure to stop the job.
    """
    class FakeCompleted(object):
        returncode = 128
        stdout = ""
        stderr = 'ERROR: The process "4242" not found.'

    monkeypatch.setattr(mcp_tools.subprocess, "run", lambda *a, **k: FakeCompleted())
    mcp_tools._kill_tree_windows(4242)  # must not raise

    class RealFailure(object):
        returncode = 1
        stdout = ""
        stderr = "Access is denied."

    monkeypatch.setattr(mcp_tools.subprocess, "run", lambda *a, **k: RealFailure())
    with pytest.raises(OSError):
        mcp_tools._kill_tree_windows(4242)


def test_run_job_read_only_still_runs_but_kill_blocked():
    started = mcp_tools.call_tool("run_job", {"command": py_sleep(2)})
    mcp_tools.configure(read_only=True)
    assert "error" in mcp_tools.call_tool("job_kill", {"job_id": started["job_id"]})
    assert mcp_tools.call_tool("bash_exec", {"command": "echo ok"})["stdout"].strip() == "ok"
    mcp_tools.configure(read_only=False)
    mcp_tools.call_tool("job_kill", {"job_id": started["job_id"]})


def test_job_output_stdout_pagination():
    started = mcp_tools.call_tool(
        "run_job", {"command": py_command("print('Z' * 5000)")}
    )
    first = mcp_tools.call_tool(
        "job_output", {"job_id": started["job_id"], "wait_ms": 5000, "max_chars": 500}
    )
    assert first["status"] == "done"
    assert first["truncated"] is True
    assert isinstance(first["next_cursor"], int)

    collected = first["stdout"]
    cursor = first["next_cursor"]
    guard = 0
    while cursor is not None and guard < 50:
        page = mcp_tools.call_tool(
            "job_output", {"job_id": started["job_id"], "max_chars": 500, "cursor": cursor}
        )
        collected += page["stdout"]
        cursor = page["next_cursor"]
        guard += 1
    assert collected == "Z" * 5000 + "\n"


# ---------------------------------------------------------------------------
# server integration
# ---------------------------------------------------------------------------

def test_server_exposes_all_tools_and_jsonrpc_list():
    from core.server import create_mcp_server

    server = create_mcp_server()
    assert server.name == "antigravity_mesh"
    for name in ("bash_exec", "system_vitals", "get_orchestration_skill", "write_file", "job_list"):
        assert name in server.tools

    status, response = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 200
    names = {tool["name"] for tool in response["result"]["tools"]}
    # Compare against the single source of truth so adding a tool cannot silently
    # desynchronise the HTTP surface from the handler registry.
    assert names == {t["name"] for t in mcp_tools.TOOLS}
    assert "mesh_status" in names

    status, response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write_file", "arguments": {}},
        }
    )
    assert status == 200
    assert response["error"]["code"] == -32602

    status, response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "job_list", "arguments": {}},
        }
    )
    assert status == 200
    payload = json.loads(response["result"]["content"][0]["text"])
    assert isinstance(payload["jobs"], list)


def test_glob_find_patterns_with_separators_are_deduplicated(tmp_path):
    """Patterns mixing '/' and '**' must not report the same file twice."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "sub" / "b.py").write_text("b")
    (tmp_path / "sub" / "deep" / "c.py").write_text("c")

    for pattern, expected in (("**/**/*.py", 3), ("**/*.py", 3), ("sub/**/*.py", 2)):
        result = mcp_tools.call_tool("glob_find", {"pattern": pattern, "path": str(tmp_path)})
        files = result["files"]
        assert len(files) == len(set(files)), (pattern, files)
        assert len(files) == expected, (pattern, files)

    # Patterns with a separator used to return nothing at all.
    assert len(mcp_tools.call_tool("glob_find", {"pattern": "sub/*.py", "path": str(tmp_path)})["files"]) == 1
    assert len(mcp_tools.call_tool("glob_find", {"pattern": "sub/b.py", "path": str(tmp_path)})["files"]) == 1
