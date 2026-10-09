"""The device branch of core/mcp_tools.py, and the wake lock around jobs.

``device_info``, ``device_control``, ``device_capture`` and ``device_messages``
are the only tools that touch the *physical* device the node runs on: the torch,
the clipboard, the camera, the microphone, the SMS inbox. Two failure modes are
far worse here than a wrong number, and this file exists to make both impossible
to reintroduce.

**A phone command must never run on a machine that is not a phone.**  The gateway
advertises the four tools for *every* node, so a Windows laptop, a Linux server
and a Mac reach exactly the same code path as an Android phone. If the platform
guard is dropped, reordered or bypassed, "take a photo" on a desktop stops being
an explanation and becomes an attempt to exec ``termux-camera-photo`` (a missing
binary at best, and on a machine where the operator happens to have termux-api in
PATH, a real action against a device nobody asked about). Every test that is not
about Android therefore asserts the fake adapter's call log stayed *empty*, not
merely that the answer looked like a refusal.

**A command must never be assembled from model input.**  ``tts_speak``, ``toast``,
``notify``, ``clipboard_set``, ``sms_send`` and the dialog body carry text that
comes straight from the model. The only safe shape is one argv element per
argument with no shell anywhere, so the tests assert the exact
``(command, argv, timeout)`` triple the adapter saw: "ok, something ran" would
still pass while a ``;``-laden string was being split on whitespace.

The two seams come from the module's optional imports: ``mcp_tools._device`` is
the telemetry layer and ``mcp_tools._termux`` the Termux:API adapter. Both are
replaced with fake module objects that record every call, so the suite needs no
Android phone, no ``termux-*`` binary and no network - and no test can leak a
``MESH_DEVICE*`` switch into the next one, because every test states its whole
device configuration explicitly and the fixtures restore the module globals.
"""

import json
import os
import sys
import time
import types

import pytest

from core import device as real_device
from core import mcp_tools


IS_WINDOWS = os.name == "nt"

#: The action tables from the code. The tests read them from the module (never
#: a copy) so a tool that gains an action cannot silently go untested.
ACTION_TUPLES = {
    "device_control": mcp_tools.DEVICE_CONTROL_ACTIONS,
    "device_capture": mcp_tools.DEVICE_CAPTURE_ACTIONS,
    "device_messages": mcp_tools.DEVICE_MESSAGES_ACTIONS,
}

#: The device branch, seen from the outside: the two tools that only observe and
#: the tools that act. ``device_info`` has no action argument.
DEVICE_TOOL_NAMES = ("device_info", "device_control", "device_capture", "device_messages")

#: "Not a phone" is three different platforms with one shared rule.
OTHER_SCENARIOS = ("linux", "windows", "darwin")

#: Workable arguments for every ``device_control`` action - the arguments a model
#: would pass when it really wants the action to happen. The platform tests use
#: them so that "the adapter was never called" cannot be a coincidence of a
#: missing argument being refused first.
CONTROL_INVOCATION = {
    "torch": {"on": True},
    "vibrate": {},
    "volume": {"value": 7},
    "volume_get": {},
    "brightness": {"value": 128},
    "tts_speak": {"text": "hello"},
    "toast": {"text": "hello"},
    "notify": {"text": "hello"},
    "notify_list": {},
    "notify_remove": {"id": "42"},
    "clipboard_get": {},
    "clipboard_set": {"text": "hello"},
    "media": {},
    "media_scan": {"path": "/sdcard/a.mp3"},
    "wakelock": {"state": "status"},
    "download": {"url": "https://example.com/file"},
    "open": {"url": "https://example.com/file"},
    "share": {"path": "/sdcard/file.pdf"},
    "dialog": {},
    "wallpaper": {"url": "https://example.com/wall.jpg"},
}

CAPTURE_INVOCATION = {
    "camera_list": {},
    "camera_photo": {},
    "mic_record_start": {},
    "mic_record_stop": {},
    "mic_record_status": {},
    "location": {},
    "fingerprint": {},
    "usb_list": {},
    "usb_access": {"path": "/dev/bus/usb/001/002"},
    "infrared_frequencies": {},
    "infrared_transmit": {"frequency": 38000, "pattern": "1000,2000"},
}

MESSAGE_INVOCATION = {
    "sms_list": {},
    "sms_send": {"number": "+15551234567", "text": "hello"},
    "call_log": {},
    "contacts": {},
    "call": {"number": "+15551234567"},
}

#: The device-branch module globals, captured before any test can touch them.
#: Re-installing them at the start of every test makes "which switch is set?"
#: a property of the test, not of whatever ran before it in the same process.
_PRISTINE = {
    name: getattr(mcp_tools, name)
    for name in ("_DEVICE_ENABLED", "_DEVICE_ACTIONS", "_DEVICE_PIM", "_DEVICE_QUICK",
                 "_READ_ONLY", "_WRITE_ROOTS", "_JOBS_DIR", "_WORKSPACE")
}


# ---------------------------------------------------------------------------
# Host shell helpers (same shape as tests/test_mcp_tools.py)
# ---------------------------------------------------------------------------

def py_command(code):
    """A command that runs *code* with this interpreter in the host's own shell.

    ``run_job`` runs PowerShell or cmd.exe on Windows and bash on POSIX, so a
    hardcoded ``sleep 30`` would test the host, not the wake lock.
    """
    exe = sys.executable
    if IS_WINDOWS:
        return '& "%s" -c "%s"' % (exe.replace('"', '`"'), code)
    return "'%s' -c \"%s\"" % (exe.replace("'", "'\\''"), code)


def py_sleep(seconds):
    return py_command("import time; time.sleep(%r)" % seconds)


# ---------------------------------------------------------------------------
# The two seams
# ---------------------------------------------------------------------------

class _TermuxStub(object):
    """State behind the fake ``core/termux.py`` module object.

    ``mcp_tools`` only ever reads attributes off ``_termux``, so a
    ``types.SimpleNamespace`` holding these bound methods is a drop-in module.
    """

    def __init__(self):
        #: Every ``run_json``/``run_text`` call: (command, argv, timeout, mode).
        #: This is *the* thing "no phone command ran" means, so it is recorded
        #: exactly as the adapter receives it - after mcp_tools stringified argv.
        self.calls = []
        #: Every ``wake_lock()`` action, in order, including the releases that
        #: the ref-counted adapter answers without touching Android.
        self.wake_calls = []
        #: The commands the lock really ran. core/termux runs termux-wake-lock
        #: only on the 0 -> 1 transition and termux-wake-unlock only on 1 -> 0,
        #: so this list is what "acquired once, released once" means.
        self.wake_commands = []
        self.state = {"count": 0, "installed": True}
        self.replies = {}
        self.default = {"ok": True, "value": {}, "duration_ms": 1}

    # -- the run_* seam ----------------------------------------------------

    def _run(self, mode, command, args, timeout):
        argv = [str(item) for item in args]      # the real adapter stringifies
        self.calls.append((command, argv, timeout, mode))
        reply = self.replies.get(command, self.default)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(command, argv, timeout)
        return dict(reply)

    def run_json(self, command, args=(), timeout=6.0):
        return self._run("json", command, args, timeout)

    def run_text(self, command, args=(), timeout=6.0):
        return self._run("text", command, args, timeout)

    # -- the platform probes core/device.py and the CLI ask for ------------

    def is_android(self):
        return True

    def api_installed(self, refresh=False):
        return {"installed": True}

    def capabilities(self, refresh=False):
        return {"api": {"installed": True}}

    # -- the wake lock -----------------------------------------------------

    def wake_lock(self, action="status"):
        """The ref-counted lock of core/termux.py, silence at zero included.

        Modelling the count (not the call) is the whole point: a second
        ``release`` at zero must not run a command, so a job that releases what
        it never took cannot steal another job's lock.
        """
        wanted = str(action or "status").strip().lower()
        self.wake_calls.append(wanted)
        if wanted not in ("acquire", "release", "status"):
            return {"available": False, "held": None, "count": self.state["count"],
                    "command": "", "reason": "refused: %r is not a wake-lock action" % wanted}
        if not self.state["installed"]:
            # What a desktop really looks like: the command is not on PATH, so
            # the adapter answers "unknown" and nothing is executed.
            return {"available": False, "held": None, "count": self.state["count"],
                    "command": "", "missing": True,
                    "reason": "the termux-wake-lock command is not installed"}
        if wanted == "status":
            return self._wake_report("termux-wake-lock")
        if wanted == "acquire" and self.state["count"] > 0:
            self.state["count"] += 1                 # already held: no command
            return self._wake_report("termux-wake-lock")
        if wanted == "release":
            if self.state["count"] == 0:
                return self._wake_report("termux-wake-unlock")   # nothing to drop
            if self.state["count"] > 1:
                self.state["count"] -= 1             # other holders remain
                return self._wake_report("termux-wake-unlock")
        command = "termux-wake-lock" if wanted == "acquire" else "termux-wake-unlock"
        self.wake_commands.append(command)
        if wanted == "acquire":
            self.state["count"] += 1
        else:
            self.state["count"] = max(0, self.state["count"] - 1)
        return self._wake_report(command)

    def _wake_report(self, command):
        installed = bool(self.state["installed"])
        return {"available": installed,
                "held": (self.state["count"] > 0) if installed else None,
                "count": int(self.state["count"]),
                "command": command}


class _DeviceStub(object):
    """State behind the fake ``core/device.py`` module object."""

    #: The real tuples. ``device_info`` validates its ``section`` argument
    #: against ``ALL_SECTIONS``, so the fake has to carry the real list - and the
    #: schema test below can then compare the advertised enum with it.
    SECTIONS = real_device.SECTIONS
    ALL_SECTIONS = real_device.ALL_SECTIONS

    def __init__(self, capture_dir, scenario="termux"):
        self._scenario = scenario
        self._capture_dir = str(capture_dir)
        self.summary_calls = []
        self.collect_calls = []
        self.configure_calls = []
        self.blocks = {
            "device": {"available": True, "class": "phone", "model": "Fake One",
                       "source": "fake"},
            "battery": {"available": True, "percent": 42, "source": "fake"},
        }

    def configure(self, capture_dir=None):
        self.configure_calls.append({"capture_dir": capture_dir})
        if capture_dir:
            self._capture_dir = str(capture_dir)

    def scenario(self):
        return self._scenario

    def set_scenario(self, name):
        self._scenario = name

    def is_termux(self):
        return self._scenario == "termux"

    def capture_dir(self):
        return self._capture_dir

    def summary(self, **kwargs):
        self.summary_calls.append(kwargs)
        return {"available": True, "source": "fake.summary", "lines": ["device: fake"],
                "device_class": "phone", "sections": ["device", "battery"]}

    def collect(self, **kwargs):
        self.collect_calls.append(kwargs)
        return dict(self.blocks)


@pytest.fixture(autouse=True)
def host_shell_is_the_default(monkeypatch):
    """The job tests build a command for the host's *default* shell.

    Another file (tests/test_agent_config.py) sets ``MESH_SHELL=cmd`` to exercise
    agent.env, and a value left behind would make the PowerShell-shaped command
    below invalid - turning a wake-lock test into an unrelated shell test.
    ``command_shell()`` re-resolves as soon as the value changes, so deleting the
    variable is enough to get the default back.
    """
    monkeypatch.delenv("MESH_SHELL", raising=False)


@pytest.fixture
def device_tools(monkeypatch, tmp_path):
    """Install both seams and state the whole device configuration.

    The module globals are re-installed from :data:`_PRISTINE` *before*
    ``configure`` runs, so a value another test left behind cannot decide what
    this test exercises; monkeypatch restores the module afterwards.
    """
    for name, value in _PRISTINE.items():
        monkeypatch.setattr(mcp_tools, name, value)

    device_stub = _DeviceStub(tmp_path / "captures", scenario="termux")
    termux_stub = _TermuxStub()
    device_module = types.SimpleNamespace(
        scenario=device_stub.scenario,
        is_termux=device_stub.is_termux,
        capture_dir=device_stub.capture_dir,
        configure=device_stub.configure,
        summary=device_stub.summary,
        collect=device_stub.collect,
        set_scenario=device_stub.set_scenario,
        SECTIONS=device_stub.SECTIONS,
        ALL_SECTIONS=device_stub.ALL_SECTIONS,
        summary_calls=device_stub.summary_calls,
        collect_calls=device_stub.collect_calls,
        configure_calls=device_stub.configure_calls,
        blocks=device_stub.blocks,
    )
    termux_module = types.SimpleNamespace(
        run_json=termux_stub.run_json,
        run_text=termux_stub.run_text,
        wake_lock=termux_stub.wake_lock,
        is_android=termux_stub.is_android,
        api_installed=termux_stub.api_installed,
        capabilities=termux_stub.capabilities,
        calls=termux_stub.calls,
        wake_calls=termux_stub.wake_calls,
        wake_commands=termux_stub.wake_commands,
        replies=termux_stub.replies,
        state=termux_stub.state,
    )
    monkeypatch.setattr(mcp_tools, "_device", device_module)
    monkeypatch.setattr(mcp_tools, "_termux", termux_module)

    env = types.SimpleNamespace(device=device_module, termux=termux_module,
                                tmp=tmp_path, captures=tmp_path / "captures")
    configure_device(env)
    return env


def configure_device(env, **overrides):
    """State every device switch explicitly, as the branch's contract requires.

    ``configure`` keeps whatever it is not told about, so a test that named only
    ``read_only`` would inherit the previous test's allowlist and PIM flag.
    """
    options = {
        "device": "auto",
        "device_actions": None,
        "device_pim": False,
        "device_quick": False,
        "capture_dir": str(env.tmp / "captures"),
        "read_only": False,
    }
    options.update(overrides)
    mcp_tools.configure(
        workspace=str(env.tmp),
        jobs_dir=str(env.tmp / "jobs"),
        write_roots=None,
        **options
    )


# ---------------------------------------------------------------------------
# small assertion helpers
# ---------------------------------------------------------------------------

def tool_spec(name):
    return next(spec for spec in mcp_tools.TOOLS if spec["name"] == name)


def assert_explained(result, scenario, action=None):
    """A phone action off a phone must be an explanation, not a failure."""
    assert result.get("available") is False, "expected an explanation, got %r" % (result,)
    assert result.get("scenario") == scenario, \
        "the answer must name the platform it is running on, not blame the caller"
    assert scenario in result.get("reason", ""), \
        "the reason has to say which platform this is: %r" % (result.get("reason"),)
    assert result.get("fix"), "an explanation without a fix leaves the model with nowhere to go"
    if action is not None:
        assert result.get("action") == action


def only_call(env):
    """The single adapter call a test expects; fails loudly on none or several."""
    assert len(env.termux.calls) == 1, \
        "expected exactly one adapter call, saw %r" % (env.termux.calls,)
    return env.termux.calls[0]


# ---------------------------------------------------------------------------
# guard rails: the tool surface and the action tables
# ---------------------------------------------------------------------------

def test_the_four_device_tools_are_dispatchable_and_advertised():
    """A tool that is not in _HANDLERS is not reachable; one not in TOOLS is invisible."""
    for name in DEVICE_TOOL_NAMES:
        assert name in mcp_tools._HANDLERS, "%s must be dispatchable" % name
        spec = tool_spec(name)
        assert spec["inputSchema"]["type"] == "object"
        assert spec["description"], "%s needs a description the model can read" % name
    # The action tools declare a required "action"; device_info does not.
    for name in ("device_control", "device_capture", "device_messages"):
        assert tool_spec(name)["inputSchema"]["required"] == ["action"]


@pytest.mark.parametrize("tool", sorted(ACTION_TUPLES))
def test_the_advertised_action_enum_is_exactly_the_code_table(tool):
    """The enum is the contract the client validates against before calling.

    Drift in either direction is a real bug: an action the code implements but
    does not advertise can never be called, and one advertised but not
    implemented comes back as "unknown action" to a model that was told it exists.
    """
    advertised = tool_spec(tool)["inputSchema"]["properties"]["action"]["enum"]
    assert advertised == list(ACTION_TUPLES[tool])


@pytest.mark.parametrize("tool", sorted(ACTION_TUPLES))
def test_no_advertised_action_is_ever_an_unknown_action(device_tools, tool):
    """Every name in the tuple must reach an action body, not the error return."""
    configure_device(device_tools, device_pim=True)
    for action in ACTION_TUPLES[tool]:
        result = mcp_tools.call_tool(tool, {"action": action})
        rendered = json.dumps(result)
        assert "unknown action" not in rendered, \
            "%s advertises %r but refuses it: %s" % (tool, action, rendered)
        if "error" in result:
            # The only acceptable refusal here is a missing argument: the action
            # name itself was understood.
            assert "needs" in result["error"], rendered


@pytest.mark.parametrize("tool", sorted(ACTION_TUPLES))
def test_an_unknown_action_lists_the_valid_ones(device_tools, tool):
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool(tool, {"action": "definitely-not-an-action"})
    assert "error" in result
    assert "unknown action" in result["error"]
    for action in ACTION_TUPLES[tool]:
        assert action in result["error"], "the message must list %r" % action
    assert device_tools.termux.calls == [], "a refused action must execute nothing"


def test_the_invocation_tables_cover_every_action():
    """Keeps the platform tables honest: a new action must get an invocation.

    Without this, adding an action to the tuple would silently exclude it from
    the "no phone command off a phone" sweep - the one test that must never have
    a hole.
    """
    assert set(CONTROL_INVOCATION) == set(mcp_tools.DEVICE_CONTROL_ACTIONS)
    assert set(CAPTURE_INVOCATION) == set(mcp_tools.DEVICE_CAPTURE_ACTIONS)
    assert set(MESSAGE_INVOCATION) == set(mcp_tools.DEVICE_MESSAGES_ACTIONS)


def test_the_advertised_device_info_sections_match_the_device_layer():
    """device_info validates ``section`` against core/device.py's own tuple."""
    advertised = tool_spec("device_info")["inputSchema"]["properties"]["section"]["enum"]
    assert advertised == ["summary", "all"] + list(real_device.ALL_SECTIONS)


def test_the_branch_explains_itself_when_a_collaborator_is_missing(device_tools, monkeypatch):
    """A partial checkout must answer, not raise: the tools are advertised anyway."""
    monkeypatch.setattr(mcp_tools, "_device", None)
    result = mcp_tools.call_tool("device_info", {"section": "summary"})
    assert result["available"] is False
    assert "core/device.py" in result["reason"]

    monkeypatch.setattr(mcp_tools, "_device", device_tools.device)
    monkeypatch.setattr(mcp_tools, "_termux", None)
    result = mcp_tools.call_tool("device_control", {"action": "torch", "on": True})
    assert result["available"] is False
    assert "core/termux.py" in result["reason"]
    assert device_tools.termux.calls == []


# ---------------------------------------------------------------------------
# Scenario: the phone-only guarantee
#
# This is the most important section of the file. The gateway advertises the four
# tools on every node, so the only thing standing between a desktop and an
# attempt to drive a phone is the scenario check inside _android_only().
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ("termux", "linux", "windows", "darwin"))
def test_android_only_returns_none_exactly_on_a_phone(device_tools, scenario):
    device_tools.device.set_scenario(scenario)
    guard = mcp_tools._android_only("camera_list")
    if scenario == "termux":
        assert guard is None, "on the phone the action must be allowed through"
        return
    assert guard["available"] is False
    assert guard["action"] == "camera_list"
    assert guard["scenario"] == scenario, "the answer must name the platform it is on"
    assert scenario in guard["reason"]
    assert guard["fix"], "the model needs to be told what to do instead"


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action", sorted(CONTROL_INVOCATION))
def test_no_control_action_runs_a_phone_command_off_a_phone(device_tools, scenario, action):
    """Even with every argument a working call needs, the adapter stays untouched."""
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools)
    mcp_tools.call_tool("device_control", dict(CONTROL_INVOCATION[action], action=action))
    assert device_tools.termux.calls == [], \
        "%s must not reach the Termux:API adapter on %s" % (action, scenario)


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action",
                         sorted(a for a in CONTROL_INVOCATION if a != "wakelock"))
def test_a_control_action_off_a_phone_answers_an_explanation(device_tools, scenario, action):
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_control",
                                 dict(CONTROL_INVOCATION[action], action=action))
    assert_explained(result, scenario, action=action)
    assert result["command"], "the answer must still name the command it would have run"
    assert device_tools.termux.calls == []


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
def test_wakelock_off_a_phone_runs_no_command_and_says_it_is_unavailable(device_tools, scenario):
    """The wake-lock action is the one control action that consults the adapter.

    Unlike the other nineteen it is not short-circuited by ``_android_only``: it
    asks the adapter, which on a real desktop finds no ``termux-wake-lock`` on
    PATH and answers ``available: false`` without executing anything. That is
    still safe, and this test pins the part that matters - no command ran and the
    answer does not pretend the lock was taken.
    """
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools)
    device_tools.termux.state["installed"] = False    # what a desktop looks like

    result = mcp_tools.call_tool("device_control", {"action": "wakelock", "state": "acquire"})

    assert device_tools.termux.calls == []
    assert device_tools.termux.wake_commands == []
    assert result["available"] is False
    assert result["reason"]
    assert result.get("held") is None, "no lock exists off a phone, so 'held' is unknown"


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action", sorted(CAPTURE_INVOCATION))
def test_no_capture_action_runs_a_phone_command_off_a_phone(device_tools, scenario, action):
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", dict(CAPTURE_INVOCATION[action], action=action))
    assert device_tools.termux.calls == [], \
        "%s must not reach the Termux:API adapter on %s" % (action, scenario)


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action", sorted(CAPTURE_INVOCATION))
def test_a_capture_action_off_a_phone_answers_an_explanation(device_tools, scenario, action):
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture",
                                 dict(CAPTURE_INVOCATION[action], action=action))
    assert_explained(result, scenario, action=action)


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action", sorted(MESSAGE_INVOCATION))
def test_no_message_action_runs_a_phone_command_off_a_phone(device_tools, scenario, action):
    """PIM is on here: the refusal must come from the platform, not from the flag."""
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools, device_pim=True)
    mcp_tools.call_tool("device_messages", dict(MESSAGE_INVOCATION[action], action=action))
    assert device_tools.termux.calls == [], \
        "%s must not reach the Termux:API adapter on %s" % (action, scenario)


@pytest.mark.parametrize("scenario", OTHER_SCENARIOS)
@pytest.mark.parametrize("action", sorted(MESSAGE_INVOCATION))
def test_a_message_action_off_a_phone_answers_an_explanation(device_tools, scenario, action):
    device_tools.device.set_scenario(scenario)
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool("device_messages",
                                 dict(MESSAGE_INVOCATION[action], action=action))
    assert_explained(result, scenario, action=action)


# ---------------------------------------------------------------------------
# device_info: which collector was asked, with which arguments
# ---------------------------------------------------------------------------

def test_device_info_summary_asks_for_a_summary_and_not_for_blocks(device_tools):
    """``summary`` is the cheap view: it must not fan out into the collectors."""
    result = mcp_tools.call_tool("device_info", {"section": "summary"})
    assert device_tools.device.summary_calls == [{"quick": False}]
    assert device_tools.device.collect_calls == []
    assert result["available"] is True


def test_device_info_summary_passes_quick_through(device_tools):
    mcp_tools.call_tool("device_info", {"section": "summary", "quick": True})
    assert device_tools.device.summary_calls == [{"quick": True}]


@pytest.mark.parametrize("section,asked", [
    ("all", ["all"]),
    ("battery", ["battery"]),
    ("cameras", ["cameras"]),
])
def test_device_info_collects_exactly_the_section_that_was_asked_for(device_tools, section, asked):
    result = mcp_tools.call_tool("device_info", {"section": section})
    # The exact keyword call matters: a tool that always collected everything
    # would answer the same thing here while hammering the phone's sensors.
    assert device_tools.device.collect_calls == [
        {"sections": asked, "fresh": False, "quick": False, "sensor": None}]
    assert device_tools.device.summary_calls == []
    assert result["scenario"] == "termux"
    assert result["device_class"] == "phone"
    assert set(result["sections"]) == {"device", "battery"}


def test_device_info_defaults_to_all_sections(device_tools):
    mcp_tools.call_tool("device_info", {})
    assert device_tools.device.collect_calls == [
        {"sections": ["all"], "fresh": False, "quick": False, "sensor": None}]


def test_device_info_passes_fresh_quick_and_sensor_through(device_tools):
    mcp_tools.call_tool("device_info", {"section": "sensors", "fresh": True,
                                        "quick": True, "sensor": "accelerometer"})
    assert device_tools.device.collect_calls == [
        {"sections": ["sensors"], "fresh": True, "quick": True, "sensor": "accelerometer"}]


def test_device_info_refuses_an_unknown_section_without_collecting_anything(device_tools):
    result = mcp_tools.call_tool("device_info", {"section": "nonsense"})
    assert "error" in result
    assert "nonsense" in result["error"]
    for name in real_device.ALL_SECTIONS:
        assert name in result["error"], "the error must list %r" % name
    assert "summary" in result["error"] and "all" in result["error"]
    assert device_tools.device.collect_calls == []
    assert device_tools.device.summary_calls == []


def test_device_info_reports_a_collector_that_raises(device_tools):
    """Telemetry that dies must surface as an error, never as a traceback."""
    def explode(**kwargs):
        raise RuntimeError("probe broke")

    device_tools.device.collect = explode
    result = mcp_tools.call_tool("device_info", {"section": "battery"})
    assert "error" in result
    assert "RuntimeError" in result["error"]


# ---------------------------------------------------------------------------
# MESH_DEVICE: the operator's off switch
# ---------------------------------------------------------------------------

DEVICE_SWITCH_CALLS = [
    ("device_info", {"section": "summary"}),
    ("device_control", {"action": "torch", "on": True}),
    ("device_capture", {"action": "camera_list"}),
    ("device_messages", {"action": "sms_list"}),
]


@pytest.mark.parametrize("tool,args", DEVICE_SWITCH_CALLS)
def test_device_zero_turns_every_device_tool_off(device_tools, tool, args):
    """One switch must silence the whole branch, on every entry point."""
    configure_device(device_tools, device="0", device_pim=True)
    result = mcp_tools.call_tool(tool, args)
    assert result["available"] is False
    assert "MESH_DEVICE=0" in json.dumps(result), \
        "the refusal has to name the switch that caused it"
    assert device_tools.termux.calls == []
    assert device_tools.device.collect_calls == []
    assert device_tools.device.summary_calls == []


@pytest.mark.parametrize("tool,args", DEVICE_SWITCH_CALLS)
@pytest.mark.parametrize("device", ("auto", "1"))
def test_device_auto_and_one_keep_the_tools_answering(device_tools, tool, args, device):
    """The default must not look like the switch: auto answers everywhere.

    A phone-only tool that vanishes on a desktop would break the node/gateway
    contract, because the gateway holds one static tool list for every node.
    """
    configure_device(device_tools, device=device, device_pim=True)
    result = mcp_tools.call_tool(tool, args)
    assert "MESH_DEVICE=0" not in json.dumps(result)


# ---------------------------------------------------------------------------
# MESH_DEVICE_ACTIONS: the operator's allowlist
# ---------------------------------------------------------------------------

def test_the_allowlist_allows_only_the_named_action(device_tools):
    configure_device(device_tools, device_actions="torch")

    allowed = mcp_tools.call_tool("device_control", {"action": "torch", "on": True})
    assert allowed["ok"] is True
    assert only_call(device_tools) == ("termux-torch", ["on"], 8.0, "json")

    refused = mcp_tools.call_tool("device_control", {"action": "vibrate"})
    assert refused["available"] is False
    assert refused["action"] == "vibrate"
    assert "torch" in refused["reason"], "the reason must name the allowlist that refused it"
    assert "MESH_DEVICE_ACTIONS" in refused["fix"]
    assert len(device_tools.termux.calls) == 1, "a refused action must execute nothing"


@pytest.mark.parametrize("tool,args", [
    ("device_capture", {"action": "camera_list"}),
    ("device_messages", {"action": "sms_list"}),
])
def test_the_allowlist_applies_to_capture_and_messages_too(device_tools, tool, args):
    """One allowlist covers the three action tools, not just device_control."""
    configure_device(device_tools, device_actions="torch", device_pim=True)
    result = mcp_tools.call_tool(tool, args)
    assert result["available"] is False
    assert "torch" in result["reason"]
    assert device_tools.termux.calls == []


def test_the_allowlist_accepts_a_spaced_comma_list(device_tools):
    configure_device(device_tools, device_actions="torch, vibrate")
    assert mcp_tools.call_tool("device_control", {"action": "torch"})["ok"] is True
    assert mcp_tools.call_tool("device_control", {"action": "vibrate"})["ok"] is True
    assert device_tools.termux.calls == [
        ("termux-torch", ["off"], 8.0, "json"),
        ("termux-vibrate", ["-d", "500"], 8.0, "json"),
    ]
    refused = mcp_tools.call_tool("device_control", {"action": "toast", "text": "hi"})
    assert refused["available"] is False
    assert "torch, vibrate" in refused["reason"]

    # A semicolon is accepted as a separator too: agent.env values are often
    # written that way, and a silently-ignored allowlist is an open door.
    configure_device(device_tools, device_actions="torch;vibrate;")
    assert mcp_tools.call_tool("device_control", {"action": "vibrate"})["ok"] is True


@pytest.mark.parametrize("empty", ("", "   ", None))
def test_an_empty_allowlist_means_every_action(device_tools, empty):
    configure_device(device_tools, device_actions=empty)
    assert mcp_tools.call_tool("device_control", {"action": "torch"})["ok"] is True
    assert mcp_tools.call_tool("device_control", {"action": "toast", "text": "hi"})["ok"] is True


# ---------------------------------------------------------------------------
# MESH_READ_ONLY: observation stays, mutation stops
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", sorted(CONTROL_INVOCATION))
def test_read_only_refuses_every_control_action_without_executing_it(device_tools, action):
    configure_device(device_tools, read_only=True)
    result = mcp_tools.call_tool("device_control",
                                 dict(CONTROL_INVOCATION[action], action=action))
    assert result["available"] is False
    assert "MESH_READ_ONLY" in result["reason"]
    assert device_tools.termux.calls == []
    assert device_tools.termux.wake_calls == []


@pytest.mark.parametrize("action", sorted(CAPTURE_INVOCATION))
def test_read_only_refuses_every_capture_action_without_executing_it(device_tools, action):
    """Even the listings: they enumerate sensors of a device nobody is holding."""
    configure_device(device_tools, read_only=True)
    result = mcp_tools.call_tool("device_capture",
                                 dict(CAPTURE_INVOCATION[action], action=action))
    assert result["available"] is False
    assert "MESH_READ_ONLY" in result["reason"]
    assert device_tools.termux.calls == []


def test_read_only_still_answers_device_info(device_tools):
    """Read-only is about mutations; telemetry has to keep working."""
    configure_device(device_tools, read_only=True)
    result = mcp_tools.call_tool("device_info", {"section": "summary"})
    assert result["available"] is True
    assert device_tools.device.summary_calls == [{"quick": False}]


@pytest.mark.parametrize("action,args", [
    ("sms_send", {"number": "+15551234567", "text": "hi"}),
    ("call", {"number": "+15551234567"}),
])
def test_read_only_refuses_sending_and_calling(device_tools, action, args):
    configure_device(device_tools, device_pim=True, read_only=True)
    result = mcp_tools.call_tool("device_messages", dict(args, action=action))
    assert result["available"] is False
    assert "MESH_READ_ONLY" in result["reason"]
    assert device_tools.termux.calls == []


@pytest.mark.parametrize("action,command,argv,timeout", [
    ("sms_list", "termux-sms-list", ["-l", "10"], 15.0),
    ("call_log", "termux-call-log", ["-l", "10"], 15.0),
    ("contacts", "termux-contact-list", [], 20.0),
])
def test_read_only_still_reads_messages_when_pim_is_on(device_tools, action, command, argv, timeout):
    """Reading the inbox changes nothing, so read-only must not block it."""
    configure_device(device_tools, device_pim=True, read_only=True)
    result = mcp_tools.call_tool("device_messages", {"action": action})
    assert result["ok"] is True
    assert only_call(device_tools) == (command, argv, timeout, "json")


# ---------------------------------------------------------------------------
# device_control: the exact argv of every action
# ---------------------------------------------------------------------------

#: (id, args, command, argv, timeout, runner). The runner is asserted because
#: clipboard reads must go through run_text: a JSON parse of a clipboard string
#: would either fail or, worse, silently reinterpret the user's text.
CONTROL_CASES = [
    ("torch-on", {"action": "torch", "on": True}, "termux-torch", ["on"], 8.0, "json"),
    ("torch-off", {"action": "torch"}, "termux-torch", ["off"], 8.0, "json"),
    ("torch-off-explicit", {"action": "torch", "on": False}, "termux-torch", ["off"], 8.0, "json"),
    ("vibrate-default", {"action": "vibrate"}, "termux-vibrate", ["-d", "500"], 8.0, "json"),
    ("vibrate-2500", {"action": "vibrate", "value": 2500},
     "termux-vibrate", ["-d", "2500"], 8.0, "json"),
    ("volume-music", {"action": "volume", "value": 7},
     "termux-volume", ["-s", "music", "7"], 8.0, "json"),
    ("volume-alarm", {"action": "volume", "value": 3, "stream": "alarm"},
     "termux-volume", ["-s", "alarm", "3"], 8.0, "json"),
    ("volume-get", {"action": "volume_get"}, "termux-volume", [], 8.0, "json"),
    ("brightness", {"action": "brightness", "value": 255},
     "termux-brightness", ["255"], 8.0, "json"),
    ("tts-speak", {"action": "tts_speak", "text": "hello world"},
     "termux-tts-speak", ["hello world"], 15.0, "json"),
    ("toast", {"action": "toast", "text": "hello world"},
     "termux-toast", ["hello world"], 15.0, "json"),
    ("notify-default-title", {"action": "notify", "text": "body"},
     "termux-notification", ["--title", "Antigravity Mesh", "--content", "body"], 10.0, "json"),
    ("notify-titled-id", {"action": "notify", "title": "T", "text": "body", "id": "7"},
     "termux-notification", ["--title", "T", "--content", "body", "--id", "7"], 10.0, "json"),
    ("notify-list", {"action": "notify_list"}, "termux-notification-list", [], 10.0, "json"),
    ("notify-remove", {"action": "notify_remove", "id": "7"},
     "termux-notification-remove", ["7"], 8.0, "json"),
    ("clipboard-get", {"action": "clipboard_get"},
     "termux-clipboard-get", [], 8.0, "text"),
    ("clipboard-set", {"action": "clipboard_set", "text": "copied"},
     "termux-clipboard-set", ["copied"], 8.0, "text"),
    ("media-info", {"action": "media"}, "termux-media-player", ["info"], 10.0, "json"),
    ("media-play", {"action": "media", "text": "play", "path": "/sdcard/a.mp3"},
     "termux-media-player", ["play", "/sdcard/a.mp3"], 10.0, "json"),
    ("media-pause", {"action": "media", "text": "pause"},
     "termux-media-player", ["pause"], 10.0, "json"),
    ("media-scan", {"action": "media_scan", "path": "/sdcard/a.mp3"},
     "termux-media-scan", ["/sdcard/a.mp3"], 15.0, "json"),
    ("download", {"action": "download", "url": "https://example.com/f"},
     "termux-download", ["https://example.com/f"], 15.0, "json"),
    ("open-path", {"action": "open", "path": "/sdcard/f.pdf"},
     "termux-open", ["/sdcard/f.pdf"], 10.0, "json"),
    ("open-url", {"action": "open", "url": "https://example.com/f"},
     "termux-open-url", ["https://example.com/f"], 10.0, "json"),
    ("share", {"action": "share", "path": "/sdcard/f.pdf"},
     "termux-share", ["/sdcard/f.pdf"], 20.0, "json"),
    ("wallpaper-file", {"action": "wallpaper", "path": "/sdcard/p.jpg"},
     "termux-wallpaper", ["-f", "/sdcard/p.jpg"], 20.0, "json"),
    ("wallpaper-url", {"action": "wallpaper", "url": "https://example.com/p.jpg"},
     "termux-wallpaper", ["-u", "https://example.com/p.jpg"], 20.0, "json"),
    ("dialog-defaults", {"action": "dialog"},
     "termux-dialog", ["text", "-t", "Antigravity Mesh", "-i", ""], 35.0, "json"),
    ("dialog-custom", {"action": "dialog", "title": "T", "text": "body", "timeout_sec": 10},
     "termux-dialog", ["text", "-t", "T", "-i", "body"], 15.0, "json"),
]


@pytest.mark.parametrize("args,command,argv,timeout,runner", [case[1:] for case in CONTROL_CASES],
                         ids=[case[0] for case in CONTROL_CASES])
def test_device_control_runs_the_exact_argv(device_tools, args, command, argv, timeout, runner):
    """The triple (command, argv, timeout) is the security boundary.

    "ok: true, something ran" would pass while an argument was dropped, split on
    whitespace or sent to the wrong command - so the whole triple is asserted.
    """
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_control", args)
    assert only_call(device_tools) == (command, argv, timeout, runner)
    assert result["command"] == command
    assert result["argv"] == argv, "the answer must report the argv it really used"
    assert result["ok"] is True


def test_dialog_waits_longer_than_the_timeout_it_was_given(device_tools):
    """The dialog blocks on the person holding the phone, so the adapter's own
    timeout has to outlive the wait the caller asked for."""
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_control",
                                 {"action": "dialog", "timeout_sec": 40, "text": "hi"})
    command, argv, timeout, runner = only_call(device_tools)
    assert command == "termux-dialog"
    assert timeout > 40, "the adapter must not time out before the person can answer"
    assert "40s" in result["note"], "the answer has to say how long it waited"


@pytest.mark.parametrize("text", [
    'hello; rm -rf / && echo "oops"',
    "line1\nline2",
    "$(reboot)",
    "`reboot`",
    'a"b\'c d',
])
def test_text_arguments_stay_one_argv_element(device_tools, text):
    """No shell, ever: text with metacharacters must arrive as a single element.

    Every surface that carries model text is checked, because one of them
    forgetting is enough: tts_speak, toast, clipboard_set, a notification body
    and an SMS body.
    """
    configure_device(device_tools, device_pim=True)

    for action, command in (("tts_speak", "termux-tts-speak"), ("toast", "termux-toast")):
        result = mcp_tools.call_tool("device_control", {"action": action, "text": text})
        assert device_tools.termux.calls[-1] == (command, [text], 15.0, "json"), \
            "%s must pass the text as one argv element" % action
        assert result["argv"] == [text]

    mcp_tools.call_tool("device_control", {"action": "clipboard_set", "text": text})
    assert device_tools.termux.calls[-1] == ("termux-clipboard-set", [text], 8.0, "text")

    mcp_tools.call_tool("device_control", {"action": "notify", "text": text})
    assert device_tools.termux.calls[-1] == (
        "termux-notification", ["--title", "Antigravity Mesh", "--content", text], 10.0, "json")

    mcp_tools.call_tool("device_messages", {"action": "sms_send", "number": "+15551234567",
                                            "text": text})
    assert device_tools.termux.calls[-1] == (
        "termux-sms-send", ["-n", "+15551234567", text], 20.0, "json")

    assert len(device_tools.termux.calls) == 5


def test_clipboard_get_uses_the_text_runner_and_returns_the_text(device_tools):
    """A clipboard holds arbitrary text: JSON-parsing it would corrupt or fail."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-clipboard-get"] = {
        "ok": True, "value": "line1\nline2", "parsed": False, "duration_ms": 4}
    result = mcp_tools.call_tool("device_control", {"action": "clipboard_get"})
    assert only_call(device_tools) == ("termux-clipboard-get", [], 8.0, "text")
    assert result["result"] == "line1\nline2"
    assert "plain text" in result["note"]


def test_clipboard_set_passes_the_text_as_one_element(device_tools):
    configure_device(device_tools)
    text = 'a; b "c"  d'
    result = mcp_tools.call_tool("device_control", {"action": "clipboard_set", "text": text})
    assert only_call(device_tools) == ("termux-clipboard-set", [text], 8.0, "text")
    assert result["argv"] == [text]


def test_wakelock_reports_the_ref_counted_lock(device_tools):
    configure_device(device_tools)

    status = mcp_tools.call_tool("device_control", {"action": "wakelock"})
    assert device_tools.termux.wake_calls == ["status"]
    assert status["action"] == "wakelock"
    assert "ref-counted" in status["note"]
    assert device_tools.termux.calls == [], \
        "status is answered from the in-process count: Android cannot report a lock"

    acquired = mcp_tools.call_tool("device_control", {"action": "wakelock", "state": "acquire"})
    assert acquired["held"] is True and acquired["count"] == 1
    released = mcp_tools.call_tool("device_control", {"action": "wakelock", "state": "release"})
    assert released["held"] is False and released["count"] == 0
    assert device_tools.termux.wake_commands == ["termux-wake-lock", "termux-wake-unlock"]


# --- refusals that must happen before anything is executed ------------------

@pytest.mark.parametrize("args,expected", [
    ({"action": "volume"}, "needs value"),
    ({"action": "volume", "value": "loud"}, "needs value"),
    ({"action": "volume", "value": True}, "needs value"),
    ({"action": "volume", "value": 5, "stream": "bass"}, "stream must be one of"),
    ({"action": "brightness"}, "needs value"),
    ({"action": "brightness", "value": None}, "needs value"),
    ({"action": "tts_speak"}, "needs text"),
    ({"action": "toast", "text": "   "}, "needs text"),
    ({"action": "notify"}, "needs text"),
    ({"action": "notify_remove"}, "needs id"),
    ({"action": "clipboard_set"}, "needs text"),
    ({"action": "media", "text": "rewind"}, "media: pass text=play|pause|stop|info"),
    ({"action": "media", "text": "play"}, "needs path"),
    ({"action": "media_scan"}, "needs path"),
    ({"action": "download"}, "needs url"),
    ({"action": "open"}, "needs path or url"),
    ({"action": "share"}, "needs path"),
    ({"action": "wallpaper"}, "needs path or url"),
    ({"action": "wakelock", "state": "hold"}, "wakelock: state must be"),
])
def test_control_arguments_are_validated_before_anything_runs(device_tools, args, expected):
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_control", args)
    assert "error" in result, "expected a refusal, got %r" % (result,)
    assert expected in result["error"]
    assert device_tools.termux.calls == []
    assert device_tools.termux.wake_calls == []


@pytest.mark.parametrize("value,expected", [
    (0, "1"),           # a "0 ms" vibration would be invisible: clamped up
    (-5, "1"),
    (999999, "60000"),
    (2500, "2500"),
])
def test_vibrate_is_clamped_to_a_useful_range(device_tools, value, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_control", {"action": "vibrate", "value": value})
    assert only_call(device_tools) == ("termux-vibrate", ["-d", expected], 8.0, "json")


@pytest.mark.parametrize("value,expected", [(999, "255"), (-3, "0"), (128, "128")])
def test_brightness_is_clamped_to_the_hardware_range(device_tools, value, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_control", {"action": "brightness", "value": value})
    assert only_call(device_tools) == ("termux-brightness", [expected], 8.0, "json")


def test_a_control_call_failure_survives_unchanged(device_tools):
    """The adapter's diagnosis (fix, denied, missing) must reach the model."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-torch"] = {
        "ok": False, "reason": "Android denied the permission",
        "fix": "grant the Camera permission to Termux:API", "denied": True}
    result = mcp_tools.call_tool("device_control", {"action": "torch", "on": True})
    assert result["ok"] is False
    assert result["reason"] == "Android denied the permission"
    assert result["fix"] == "grant the Camera permission to Termux:API"
    assert result["denied"] is True
    assert result["command"] == "termux-torch"


def test_an_adapter_that_raises_is_reported_not_propagated(device_tools):
    """call_tool never raises: a broken adapter is an answer, not a traceback."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-toast"] = RuntimeError("binder died")
    result = mcp_tools.call_tool("device_control", {"action": "toast", "text": "hi"})
    assert result["ok"] is False
    assert "RuntimeError" in result["reason"]


# ---------------------------------------------------------------------------
# device_capture
# ---------------------------------------------------------------------------

CAPTURE_CASES = [
    ("camera-list", {"action": "camera_list"}, "termux-camera-info", [], 8.0),
    ("mic-stop", {"action": "mic_record_stop"},
     "termux-microphone-record", ["-q"], 8.0),
    ("mic-status", {"action": "mic_record_status"},
     "termux-microphone-record", ["-i"], 8.0),
    ("location-default", {"action": "location"}, "termux-location", ["-p", "gps"], 25.0),
    ("location-network", {"action": "location", "provider": "network"},
     "termux-location", ["-p", "network"], 25.0),
    ("fingerprint", {"action": "fingerprint"}, "termux-fingerprint", [], 60.0),
    ("usb-list", {"action": "usb_list"}, "termux-usb", ["-l"], 10.0),
    ("usb-access", {"action": "usb_access", "path": "/dev/bus/usb/001/002"},
     "termux-usb", ["-r", "/dev/bus/usb/001/002"], 30.0),
    ("infrared-frequencies", {"action": "infrared_frequencies"},
     "termux-infrared-frequencies", [], 8.0),
    ("infrared-transmit", {"action": "infrared_transmit", "frequency": 38000,
                           "pattern": "1000,2000"},
     "termux-infrared-transmit", ["-f", "38000", "1000,2000"], 10.0),
]


@pytest.mark.parametrize("args,command,argv,timeout", [case[1:] for case in CAPTURE_CASES],
                         ids=[case[0] for case in CAPTURE_CASES])
def test_device_capture_runs_the_exact_argv(device_tools, args, command, argv, timeout):
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture", args)
    assert only_call(device_tools) == (command, argv, timeout, "json")
    assert result["command"] == command
    assert result["ok"] is True


def test_camera_photo_defaults_to_the_capture_directory_and_creates_it(device_tools):
    """A capture the model cannot find again is a lost capture.

    The default path must land in the configured capture directory and its parent
    must already exist, because the Termux:API app does not create directories.
    """
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture", {"action": "camera_photo"})

    command, argv, timeout, runner = only_call(device_tools)
    assert command == "termux-camera-photo"
    assert argv[0:2] == ["-c", "0"], "camera id defaults to the first camera"
    path = argv[2]
    assert os.path.dirname(path) == str(device_tools.captures)
    assert os.path.basename(path).startswith("photo-") and path.endswith(".jpg")
    assert os.path.isdir(os.path.dirname(path)), \
        "the capture directory has to exist before the camera writes into it"
    assert result["path"] == path
    assert result["note"].startswith("the file stays on the node")
    assert device_tools.device.configure_calls[-1] == {"capture_dir": str(device_tools.captures)}


def test_camera_photo_honours_an_explicit_path_and_camera_id(device_tools):
    configure_device(device_tools)
    target = device_tools.tmp / "shots" / "front.jpg"
    result = mcp_tools.call_tool("device_capture",
                                 {"action": "camera_photo", "path": str(target),
                                  "camera_id": 3})
    assert only_call(device_tools) == (
        "termux-camera-photo", ["-c", "3", os.path.realpath(str(target))], 30.0, "json")
    assert os.path.isdir(os.path.dirname(str(target))), "the parent directory is created"
    assert result["path"] == os.path.realpath(str(target))


def test_camera_photo_failure_survives_unchanged(device_tools):
    """A missing camera is an instruction ("install the app"), not a swallowed failure."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-camera-photo"] = {
        "ok": False, "reason": "the termux-camera-photo command is not installed",
        "fix": "install the Termux:API app", "missing": True}
    result = mcp_tools.call_tool("device_capture", {"action": "camera_photo"})
    assert result["ok"] is False
    assert result["missing"] is True
    assert result["reason"] == "the termux-camera-photo command is not installed"
    assert result["fix"] == "install the Termux:API app"
    assert result["command"] == "termux-camera-photo"
    assert "bytes" not in result, "no file was written, so no size may be reported"


def test_camera_photo_says_where_it_would_have_written_when_the_camera_fails(device_tools):
    """The generated file name is the model's only handle on a failed shot.

    It is what a retry needs (do not overwrite the half-written file) and what a
    human needs to look for the picture the phone may still have produced. The
    code adds the path when the *branch* refuses (a desktop gets one), but not
    when the adapter reports a real phone failure, because it tests
    ``result["available"] is False`` and an adapter failure carries no
    ``available`` key at all. Kept as a failing test on purpose: see the report.
    """
    configure_device(device_tools)
    device_tools.termux.replies["termux-camera-photo"] = {
        "ok": False, "reason": "the termux-camera-photo command is not installed",
        "fix": "install the Termux:API app", "missing": True}
    result = mcp_tools.call_tool("device_capture", {"action": "camera_photo"})
    assert result["ok"] is False
    assert result["path"].endswith(".jpg"), \
        "the answer must still say which file the capture was aimed at"
    assert os.path.isdir(os.path.dirname(result["path"])), \
        "and that directory was already created for it"


def test_mic_record_start_names_the_file_and_only_adds_a_length_when_asked(device_tools):
    configure_device(device_tools)

    first = mcp_tools.call_tool("device_capture", {"action": "mic_record_start"})
    command, argv, timeout, runner = only_call(device_tools)
    assert command == "termux-microphone-record"
    assert argv[0] == "-f"
    assert os.path.dirname(argv[1]) == str(device_tools.captures)
    assert os.path.basename(argv[1]).startswith("recording-")
    assert argv[1].endswith(".m4a")
    assert "-l" not in argv, "an unbounded recording is what the phone understands by default"
    assert os.path.isdir(os.path.dirname(argv[1]))
    assert first["path"] == argv[1]

    device_tools.termux.calls[:] = []
    target = device_tools.tmp / "rec.m4a"
    second = mcp_tools.call_tool("device_capture",
                                 {"action": "mic_record_start", "path": str(target),
                                  "seconds": 30})
    assert only_call(device_tools) == (
        "termux-microphone-record",
        ["-f", os.path.realpath(str(target)), "-l", "30"], 15.0, "json")
    assert second["path"] == os.path.realpath(str(target))


@pytest.mark.parametrize("seconds,expected", [(0, "1"), (99999, "3600"), (12, "12")])
def test_mic_record_seconds_is_clamped(device_tools, seconds, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", {"action": "mic_record_start", "seconds": seconds})
    argv = only_call(device_tools)[1]
    assert argv[-2:] == ["-l", expected]


def test_mic_record_ignores_a_non_numeric_length(device_tools):
    """A string length is a client bug; the phone must not see a bogus -l."""
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", {"action": "mic_record_start", "seconds": "30"})
    assert "-l" not in only_call(device_tools)[1]


def test_fingerprint_returns_only_a_verdict(device_tools):
    """Biometric data must never travel: the answer is a yes/no, nothing else."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-fingerprint"] = {
        "ok": True, "value": {"authentication": "success"}}
    result = mcp_tools.call_tool("device_capture", {"action": "fingerprint"})
    assert only_call(device_tools) == ("termux-fingerprint", [], 60.0, "json")
    assert result["result"] == {"authentication": "success"}
    assert "only the verdict is returned" in result["note"]
    assert "no fingerprint data" in result["note"]


def test_usb_access_explains_that_it_will_not_run_a_program(device_tools):
    """``termux-usb -e <program>`` would be arbitrary execution by argument name."""
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture",
                                 {"action": "usb_access", "path": "/dev/bus/usb/001/002"})
    assert only_call(device_tools) == (
        "termux-usb", ["-r", "/dev/bus/usb/001/002"], 30.0, "json")
    assert "permission" in result["note"]
    assert "deliberately" in result["note"], \
        "the answer must say running a program is deliberately not exposed"


@pytest.mark.parametrize("provider,expected", [("network", "network"), ("passive", "passive")])
def test_location_forwards_the_provider_it_was_given(device_tools, provider, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", {"action": "location", "provider": provider})
    assert only_call(device_tools) == ("termux-location", ["-p", expected], 25.0, "json")


def test_location_refuses_an_unknown_provider_before_any_call(device_tools):
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture", {"action": "location", "provider": "cell"})
    assert "error" in result
    assert "provider" in result["error"]
    assert device_tools.termux.calls == []


@pytest.mark.parametrize("pattern,expected", [
    ("1000,2000", "1000,2000"),
    ("1000, 2000, 1000", "1000,2000,1000"),   # spaces inside a list are cosmetic
    ("  500  ", "500"),
])
def test_infrared_patterns_are_normalised_to_durations(device_tools, pattern, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", {"action": "infrared_transmit", "frequency": 38000,
                                           "pattern": pattern})
    assert only_call(device_tools) == (
        "termux-infrared-transmit", ["-f", "38000", expected], 10.0, "json")


@pytest.mark.parametrize("pattern", [
    "1000; rm -rf /",
    "1000,2000; reboot",
    "$(reboot)",
    "1000;rm -rf / #",
    "1e3",
    "0x10",
    "1000,",
])
def test_infrared_refuses_anything_that_is_not_durations(device_tools, pattern):
    """The pattern reaches a phone API: nothing but digits and commas may pass."""
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture", {"action": "infrared_transmit",
                                                    "frequency": 38000, "pattern": pattern})
    assert "error" in result, "%r must be refused" % pattern
    assert "comma-separated integers" in result["error"]
    assert device_tools.termux.calls == [], "a refused pattern must execute nothing"


@pytest.mark.parametrize("args,expected", [
    ({"action": "infrared_transmit", "pattern": "1000"}, "needs frequency"),
    ({"action": "infrared_transmit", "frequency": "fast", "pattern": "1000"}, "needs frequency"),
    ({"action": "infrared_transmit", "frequency": True, "pattern": "1000"}, "needs frequency"),
    ({"action": "infrared_transmit", "frequency": 38000}, "needs pattern"),
    ({"action": "usb_access"}, "needs path"),
])
def test_capture_arguments_are_validated_before_anything_runs(device_tools, args, expected):
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_capture", args)
    assert "error" in result
    assert expected in result["error"]
    assert device_tools.termux.calls == []


@pytest.mark.parametrize("frequency,expected", [(0, "1"), (5000000, "1000000"), (38000, "38000")])
def test_infrared_frequency_is_clamped(device_tools, frequency, expected):
    configure_device(device_tools)
    mcp_tools.call_tool("device_capture", {"action": "infrared_transmit",
                                           "frequency": frequency, "pattern": "1000"})
    assert only_call(device_tools)[1] == ["-f", expected, "1000"]


def test_a_capture_failure_survives_unchanged(device_tools):
    """A denied location fix is an instruction, not an empty answer."""
    configure_device(device_tools)
    device_tools.termux.replies["termux-location"] = {
        "ok": False, "reason": "Android denied the permission", "denied": True,
        "fix": "grant the Location permission to Termux:API"}
    result = mcp_tools.call_tool("device_capture", {"action": "location"})
    assert result["ok"] is False
    assert result["reason"] == "Android denied the permission"
    assert result["denied"] is True
    assert result["fix"] == "grant the Location permission to Termux:API"
    assert result["argv"] == ["-p", "gps"]
    assert "note" not in result, "a failed fix has nothing to annotate"


# ---------------------------------------------------------------------------
# device_messages: private data behind MESH_DEVICE_PIM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", sorted(MESSAGE_INVOCATION))
def test_pim_off_refuses_every_message_action(device_tools, action):
    """The refusal has to be an instruction: which flag, which permissions."""
    configure_device(device_tools)
    result = mcp_tools.call_tool("device_messages", dict(MESSAGE_INVOCATION[action],
                                                        action=action))
    assert result["available"] is False
    assert result["action"] == action
    assert "private" in result["reason"]
    assert "MESH_DEVICE_PIM=1" in result["fix"]
    assert device_tools.termux.calls == [], "nothing may be read while PIM is off"


@pytest.mark.parametrize("tool,args", [
    ("device_control", {"action": "toast", "text": "hi"}),
    ("device_capture", {"action": "camera_list"}),
])
def test_pim_does_not_gate_the_other_device_tools(device_tools, tool, args):
    """PIM is about the operator's messages, not about the torch."""
    configure_device(device_tools, device_pim=False)
    result = mcp_tools.call_tool(tool, args)
    assert result.get("ok") is True


@pytest.mark.parametrize("action,command,argv,timeout", [
    ("sms_list", "termux-sms-list", ["-l", "10"], 15.0),
    ("call_log", "termux-call-log", ["-l", "10"], 15.0),
])
def test_sms_and_call_log_default_to_a_small_page(device_tools, action, command, argv, timeout):
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool("device_messages", {"action": action})
    assert only_call(device_tools) == (command, argv, timeout, "json")
    assert result["ok"] is True


@pytest.mark.parametrize("args,argv", [
    ({"limit": 5}, ["-l", "5"]),
    ({"limit": 999}, ["-l", "50"]),               # clamped to the documented maximum
    ({"offset": 2}, ["-l", "10", "-o", "2"]),
    ({"type": "inbox"}, ["-l", "10", "-t", "inbox"]),
    ({"limit": 5, "offset": 3, "type": "sent"}, ["-l", "5", "-o", "3", "-t", "sent"]),
])
def test_sms_list_passes_only_the_arguments_that_were_given(device_tools, args, argv):
    """An absent filter must not become an empty argument: -t "" means something else."""
    configure_device(device_tools, device_pim=True)
    mcp_tools.call_tool("device_messages", dict(args, action="sms_list"))
    assert only_call(device_tools) == ("termux-sms-list", argv, 15.0, "json")


def test_sms_send_passes_the_number_and_body_as_arguments(device_tools):
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool("device_messages", {"action": "sms_send",
                                                     "number": "+15551234567",
                                                     "text": "see you at 8"})
    assert only_call(device_tools) == (
        "termux-sms-send", ["-n", "+15551234567", "see you at 8"], 20.0, "json")
    assert result["ok"] is True


@pytest.mark.parametrize("args,expected", [
    ({"action": "sms_send", "number": "+15551234567"}, "needs text"),
    ({"action": "sms_send", "text": "hi"}, "needs number"),
    ({"action": "call"}, "needs number"),
])
def test_message_arguments_are_validated_before_anything_runs(device_tools, args, expected):
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool("device_messages", args)
    assert "error" in result
    assert expected in result["error"]
    assert device_tools.termux.calls == []


def test_call_dials_the_number_as_one_argument(device_tools):
    configure_device(device_tools, device_pim=True)
    result = mcp_tools.call_tool("device_messages", {"action": "call",
                                                     "number": "+15551234567"})
    assert only_call(device_tools) == ("termux-telephony-call", ["+15551234567"], 15.0, "json")
    assert result["ok"] is True


CONTACTS = [
    {"name": "Ada Lovelace", "number": "+15550000001"},
    {"name": "Grace Hopper", "number": "+15550000002"},
    {"name": "Alan Turing", "number": "+15550000003"},
    {"name": "Ada Byron", "number": "+15550000004"},
]


def test_contacts_are_filtered_and_limited_on_the_node(device_tools):
    """termux-contact-list has no filter: the whole address book would otherwise
    travel to the client on every lookup."""
    configure_device(device_tools, device_pim=True)
    device_tools.termux.replies["termux-contact-list"] = {"ok": True, "value": list(CONTACTS)}

    result = mcp_tools.call_tool("device_messages",
                                 {"action": "contacts", "query": "ada", "limit": 1})
    assert only_call(device_tools) == ("termux-contact-list", [], 20.0, "json")
    assert result["result"] == [CONTACTS[0]]
    assert result["total_matches"] == 2, \
        "the model needs to know how many matched, not just how many it got"

    # No query: only the limit applies, and the count still reflects everything.
    device_tools.termux.calls[:] = []
    unfiltered = mcp_tools.call_tool("device_messages", {"action": "contacts"})
    assert unfiltered["result"] == CONTACTS
    assert unfiltered["total_matches"] == len(CONTACTS)


def test_contacts_filtering_matches_the_number_too(device_tools):
    configure_device(device_tools, device_pim=True)
    device_tools.termux.replies["termux-contact-list"] = {"ok": True, "value": list(CONTACTS)}
    result = mcp_tools.call_tool("device_messages",
                                 {"action": "contacts", "query": "0003"})
    assert result["result"] == [CONTACTS[2]]
    assert result["total_matches"] == 1


def test_contacts_passes_an_adapter_failure_straight_through(device_tools):
    configure_device(device_tools, device_pim=True)
    device_tools.termux.replies["termux-contact-list"] = {
        "ok": False, "reason": "Android denied the permission", "denied": True,
        "fix": "grant the Contacts permission to Termux:API"}
    result = mcp_tools.call_tool("device_messages", {"action": "contacts"})
    assert result["ok"] is False
    assert result["denied"] is True
    assert "total_matches" not in result


# ---------------------------------------------------------------------------
# The Android wake lock around background jobs
#
# Android freezes a background app the moment the screen goes off, so run_job on
# a phone takes a ref-counted wake lock. The lock has to be held for exactly as
# long as the job runs: released too late and the phone stays awake forever,
# released too early (or twice) and a *different*, still-running job loses its
# protection.
# ---------------------------------------------------------------------------

def wait_for_final_status(job_id, timeout=20.0):
    """Poll job_output until the job leaves "running"; never returns a stale view."""
    deadline = time.time() + timeout
    output = {}
    while time.time() < deadline:
        output = mcp_tools.call_tool("job_output", {"job_id": job_id, "wait_ms": 500})
        if output.get("status") != "running":
            return output
    return output


def wait_for_wake_release(env, timeout=20.0):
    """Wait until the lock's ref count is back to zero, or report failure."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if env.termux.state["count"] == 0:
            return True
        time.sleep(0.05)
    return False


def join_reaper(job_id, timeout=20.0):
    """Wait for the job's reaper thread: makes the release ordering deterministic."""
    reaper = (mcp_tools._JOBS.get(job_id) or {}).get("reaper")
    if reaper is not None:
        reaper.join(timeout=timeout)


@pytest.fixture
def job_tools(device_tools):
    """A device environment whose jobs live in tmp_path and are always cleaned up.

    A leaked child would keep running for as long as the test process lives, so
    every job started in a test is killed here even when an assertion fails.
    """
    device_tools.started_jobs = []
    yield device_tools
    for job_id in device_tools.started_jobs:
        mcp_tools.call_tool("job_kill", {"job_id": job_id})
        wait_for_final_status(job_id, timeout=10.0)


def start_job(env, command):
    started = mcp_tools.call_tool("run_job", {"command": command})
    assert started.get("job_id"), "run_job failed: %r" % (started,)
    env.started_jobs.append(started["job_id"])
    return started


def test_run_job_takes_the_wake_lock_once_and_releases_it_when_the_job_ends(job_tools):
    configure_device(job_tools)                       # scenario: termux
    started = start_job(job_tools, py_command("print('hello')"))

    assert started["wake_lock"] is True, \
        "a job on a phone must say whether it is protected from Android's freezer"
    assert job_tools.termux.wake_calls[0] == "acquire"
    assert job_tools.termux.wake_calls.count("acquire") == 1, \
        "the lock is ref-counted, so a job takes exactly one hold"
    assert "termux-wake-lock" in job_tools.termux.wake_commands

    output = wait_for_final_status(started["job_id"])
    assert output["status"] == "done"
    assert output["exit_code"] == 0
    assert wait_for_wake_release(job_tools), \
        "a finished job that keeps the wake lock awake drains the battery"

    assert job_tools.termux.wake_calls.count("acquire") == 1
    assert job_tools.termux.wake_commands == ["termux-wake-lock", "termux-wake-unlock"], \
        "one acquire command and one release command, exactly"
    assert job_tools.termux.state["count"] == 0


def test_job_list_reports_whether_a_job_holds_the_wake_lock(job_tools):
    """The listing answers "is my long job protected right now?".

    The README says `job_list` reports "whether each one holds a wake_lock", so the
    field tracks the CURRENT state: True while the job runs, and False once the job
    is finished and the hold has been released. A stale True would let a model
    believe a phone is being kept awake for a job that ended minutes ago.
    """
    configure_device(job_tools)
    started = start_job(job_tools, py_sleep(30))
    job_id = started["job_id"]

    listing = mcp_tools.call_tool("job_list", {})
    entry = next(job for job in listing["jobs"] if job["job_id"] == job_id)
    assert entry["wake_lock"] is True, \
        "the listing is how a model checks whether a long job is protected"

    mcp_tools.call_tool("job_kill", {"job_id": job_id})
    wait_for_final_status(job_id)
    wait_for_wake_release(job_tools)
    join_reaper(job_id)

    listing = mcp_tools.call_tool("job_list", {})
    entry = next(job for job in listing["jobs"] if job["job_id"] == job_id)
    assert entry["wake_lock"] is False, \
        "a finished job no longer holds the lock, and the listing must not pretend it does"


def test_job_kill_releases_the_wake_lock_of_a_long_job(job_tools):
    configure_device(job_tools)
    started = start_job(job_tools, py_sleep(30))
    job_id = started["job_id"]

    assert started["wake_lock"] is True
    assert mcp_tools.call_tool("job_output", {"job_id": job_id, "wait_ms": 0})["status"] \
        == "running"
    assert job_tools.termux.state["count"] == 1

    killed = mcp_tools.call_tool("job_kill", {"job_id": job_id})
    assert killed == {"ok": True, "status": "killed"}

    assert wait_for_final_status(job_id)["status"] in ("killed", "done")
    assert wait_for_wake_release(job_tools), \
        "a killed job must not leave the phone's wake lock held"
    assert job_tools.termux.wake_calls.count("acquire") == 1
    # job_kill and the reaper thread both try to finish this job, and the release is
    # claimed once per job inside mcp_tools (_WAKE_LOCK_RELEASED), so the ref-counted
    # adapter is asked exactly once. With two jobs a double request would drop the
    # *surviving* job's protection - see
    # test_killing_one_job_does_not_release_another_jobs_wake_lock.
    assert job_tools.termux.wake_commands.count("termux-wake-unlock") == 1
    assert job_tools.termux.state["count"] == 0


def test_killing_one_job_does_not_release_another_jobs_wake_lock(job_tools):
    """_release_job_wake_lock promises exactly this, and the ref count is why.

    "The lock is ref-counted inside core.termux, so releasing it here cannot
    unlock a job that is still running" - so with two jobs holding the lock,
    ending one of them must leave the other one's hold intact. Ending a job goes
    through two paths (job_kill and the reaper thread), and each of them releases
    whatever the meta file still claims, which is the failure this test watches.
    """
    configure_device(job_tools)
    first = start_job(job_tools, py_sleep(30))
    second = start_job(job_tools, py_sleep(30))

    try:
        assert job_tools.termux.state["count"] == 2, "both jobs hold the lock"

        assert mcp_tools.call_tool("job_kill", {"job_id": first["job_id"]})["status"] == "killed"
        wait_for_final_status(first["job_id"])
        # Make every release for the first job land before looking at the count:
        # without this the assertion would race the reaper thread.
        join_reaper(first["job_id"])

        assert job_tools.termux.state["count"] == 1, (
            "the second job is still running, so its hold on the wake lock must "
            "survive the first job finishing; %d release requests (%r) dropped it"
            % (job_tools.termux.wake_calls.count("release"), job_tools.termux.wake_calls))
    finally:
        for job in (first, second):
            mcp_tools.call_tool("job_kill", {"job_id": job["job_id"]})
            wait_for_final_status(job["job_id"])


def test_run_job_off_a_phone_never_touches_the_wake_lock(job_tools):
    """The lock is an Android cure; asking for it elsewhere must be a no-op."""
    job_tools.device.set_scenario("linux")
    configure_device(job_tools)
    started = start_job(job_tools, py_command("print('hello')"))

    assert "wake_lock" not in started, \
        "off a phone the question does not apply, so no flag is reported"
    assert wait_for_final_status(started["job_id"])["status"] == "done"
    assert job_tools.termux.wake_calls == []
    assert job_tools.termux.wake_commands == []

    listing = mcp_tools.call_tool("job_list", {})
    entry = next(job for job in listing["jobs"] if job["job_id"] == started["job_id"])
    assert entry["wake_lock"] is False
