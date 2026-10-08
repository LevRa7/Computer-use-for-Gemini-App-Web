"""The node agent must leave evidence of what it was and how it ended.

The incident this covers: after a Windows Update reboot the autostart launcher ran
the Microsoft Store ``python.exe`` alias, which printed one torn line (``Python ``)
into ``agent.log`` and exited. Nothing named the interpreter that was expected,
nothing recorded that a process had died, and the node stayed offline for a day
while the MCP client showed an opaque frontend error.

The runtime banner, the fault log, the clean-stop marker and the heartbeat are what
turn that silence into a diagnosis: they are read by ``ops/windows/agent-watchdog.ps1``
and ``ops/doctor.ps1``.
"""

import json
import os
import sys
import threading
import time

import pytest

from core import agent


def test_module_import_starts_no_background_thread():
    """Importing the module (tests, harnesses) must not start the heartbeat."""
    assert [t for t in threading.enumerate() if t.name == "mesh-heartbeat"] == []


def test_heartbeat_round_trip(tmp_path):
    path = str(tmp_path / "agent.heartbeat")

    payload = agent.write_heartbeat(path)

    assert payload["pid"] == os.getpid()
    assert payload["interpreter"] == sys.executable
    assert payload["python"].startswith("%d.%d" % sys.version_info[:2])
    data = agent.read_heartbeat(path)
    assert data["ts"] == pytest.approx(payload["ts"], abs=1.0)
    assert data["gateway"] == agent.GATEWAY_HOST
    assert data["user"] == agent.USER


def test_heartbeat_write_is_atomic(tmp_path):
    """A reader must never see a half-written file, and no temp file is left."""
    path = str(tmp_path / "agent.heartbeat")

    agent.write_heartbeat(path)

    assert not os.path.exists(path + ".tmp")
    with open(path, "r", encoding="utf-8") as handle:
        json.load(handle)


def test_heartbeat_age_tracks_the_file(tmp_path):
    path = str(tmp_path / "agent.heartbeat")
    agent.write_heartbeat(path)

    assert agent.heartbeat_age(path) < 5.0
    assert agent.heartbeat_age(str(tmp_path / "absent")) == float("inf")


def test_read_heartbeat_tolerates_garbage(tmp_path):
    path = tmp_path / "agent.heartbeat"
    path.write_text("{not json", encoding="utf-8")

    assert agent.read_heartbeat(str(path)) == {}
    assert agent.heartbeat_age(str(path)) == float("inf")


def test_connection_state_reaches_the_heartbeat(tmp_path):
    path = str(tmp_path / "agent.heartbeat")

    agent.mark_connected(False, "ConnectionResetError: boom")
    agent.write_heartbeat(path)
    data = agent.read_heartbeat(path)
    assert data["connected"] is False
    assert "boom" in data["last_error"]

    agent.mark_connected(True)
    agent.write_heartbeat(path)
    data = agent.read_heartbeat(path)
    assert data["connected"] is True
    assert data["last_error"] == ""
    assert data["connects"] >= 1


def test_runtime_banner_reports_the_real_interpreter():
    facts = agent.log_runtime_banner()

    assert facts["interpreter"] == sys.executable
    assert facts["python"].startswith("%d.%d" % sys.version_info[:2])
    # Either a version string or the explicit MISSING marker: both are answers,
    # and MISSING is the one that explains a node that cannot start.
    assert facts["websockets"]


def test_banner_warns_about_the_microsoft_store_alias(monkeypatch, caplog):
    """The alias that caused the incident must be named in the log."""
    monkeypatch.setattr(agent, "_RUNTIME_FACTS",
                        {"python": "3.13.3", "interpreter": r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe",
                         "websockets": "17.2"})

    with caplog.at_level("ERROR"):
        agent.log_runtime_banner()

    assert "Microsoft Store" in caplog.text


def test_fault_log_is_created(tmp_path):
    target = str(tmp_path / "nested" / "agent.fault.log")

    assert agent.enable_fault_log(target) == target
    assert os.path.exists(target)


def test_stop_marker_records_pid_and_reason(tmp_path, monkeypatch):
    marker = tmp_path / "agent.stopped"
    monkeypatch.setattr(agent, "STOP_MARKER_FILE", str(marker))

    agent.write_stop_marker("test-reason")

    text = marker.read_text(encoding="utf-8")
    assert "pid=%d" % os.getpid() in text
    assert "reason=test-reason" in text


def test_heartbeat_thread_writes_and_is_a_daemon(tmp_path):
    path = str(tmp_path / "agent.heartbeat")

    thread = agent.start_heartbeat(path, interval=0.1)

    assert thread.daemon is True, "the heartbeat must never keep the process alive"
    assert thread.name == "mesh-heartbeat"
    assert os.path.exists(path), "the first heartbeat is written immediately"
    os.remove(path)
    deadline = time.time() + 5
    while time.time() < deadline and not os.path.exists(path):
        time.sleep(0.05)
    assert os.path.exists(path), "the thread stopped refreshing the heartbeat"
