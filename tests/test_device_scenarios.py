"""The device scenario matrix - the falsification suite for platform detection.

The claim under test is the one a phone user cares about: *"the tools detect the
system correctly and run the right code path for that system"*. A test that only
runs on the host it happens to sit on cannot falsify that, so every case here
fakes one of the four scenarios of ``core/device.py`` and then asserts both sides
of the branch:

* the **positive** side - the tools of that platform are the ones that ran;
* the **negative** side - no other platform's tools, and no other platform's
  private helpers, were touched at all.

The suite runs identically on Windows (where it is normally run) and on Linux.
The host's own platform is never the subject: the seams below already exist in
the module, and nothing here needs Android, the network, or ``core/termux.py``
(a parallel change may add that file; ``device._termux_adapter`` is patched
either way, so this suite passes whether it exists or not).

Seams used:

* ``device._run(argv, timeout)``  - replaced by a recorder with canned payloads;
* ``device._which(name)``         - replaced by a plausible install per platform;
* ``device._termux_adapter()``    - ``None``, or a fake adapter object;
* ``device._termux_data_dir()``   - the third Termux signal, decided by the test;
* ``device.scenario``             - forced to the scenario under test;
* ``device.clear_cache()``        - the per-section cache must not leak between
  scenarios;
* ``device._IS_WINDOWS`` / ``_IS_DARWIN`` - the host flags, forced to match.

The fake payloads are small inline strings on purpose: a fixture file would add
a path to the test for no gain (nothing here reads them from disk).
"""

import inspect
import json
import os
import platform
import re
import threading
import time

import pytest

from core import device

#: The Termux root, as Termux itself sets ``$PREFIX``.
TERMUX_PREFIX = "/data/data/com.termux/files/usr"
#: The application data directory Termux detection falls back to.
TERMUX_DATA_DIR = "/data/data/com.termux/files/usr/bin"
#: A machine that really is a phone detects Termux whatever the environment says,
#: so the signals cannot be faked from there.
ON_ANDROID = os.path.isdir(TERMUX_DATA_DIR)

TERMUX_COMMANDS = (
    "termux-battery-status",
    "termux-camera-info",
    "termux-sensor",
    "termux-telephony-cellinfo",
    "termux-telephony-deviceinfo",
    "termux-wifi-connectioninfo",
)

#: What this fake host has on ``PATH``, per scenario. This is a *plausible
#: install* of that platform (what ``which`` would answer), deliberately not the
#: allow-list: a collector that reaches for a foreign tool must really call it
#: and fail the allow-list assertion instead of being silently skipped.
FAKE_TOOLS = {
    "termux": ("getprop", "termux-wake-lock") + TERMUX_COMMANDS,
    "linux": ("ip", "ifconfig", "iw", "nmcli", "arecord"),
    "windows": ("powershell", "netsh"),
    "darwin": ("sysctl", "pmset", "ifconfig", "system_profiler", "airport"),
}

#: Commands a scenario must NEVER run, spelled out from "the right program for
#: the right system". The allow-list test proves the positive side; this proves
#: the negative side even if that list ever grows by mistake.
NEVER_RUNS = {
    "termux": ("pmset", "netsh", "powershell", "sysctl", "system_profiler", "airport",
               "iw", "nmcli", "arecord"),
    "linux": TERMUX_COMMANDS + ("pmset", "netsh", "powershell", "sysctl",
                                "system_profiler", "airport"),
    "windows": TERMUX_COMMANDS + ("pmset", "iw", "nmcli", "arecord", "ip", "ifconfig",
                                  "sysctl", "system_profiler", "airport"),
    "darwin": TERMUX_COMMANDS + ("netsh", "powershell", "iw", "nmcli", "arecord"),
}

#: The command whose source each scenario's battery block must name.
BATTERY_SOURCE = {
    "termux": "termux-battery-status",
    "windows": "GetSystemPowerStatus",
    "darwin": "pmset -g batt",
    "linux": "/sys/class/power_supply",
}


# ---------------------------------------------------------------------------
# Canned command output
# ---------------------------------------------------------------------------

GETPROP = {
    "ro.product.model": "Pixel 7 Pro",
    "ro.product.device": "panther",
    "ro.product.manufacturer": "Google",
    "ro.build.version.release": "14",
    "ro.build.version.sdk": "34",
    "ro.build.characteristics": "default",
    "ro.product.cpu.abi": "arm64-v8a",
    "persist.sys.locale": "en-GB",
    "persist.sys.timezone": "Europe/Berlin",
}

IP_ADDR = [{
    "ifname": "wlan0", "operstate": "up", "address": "aa:bb:cc:dd:ee:ff",
    "addr_info": [
        {"family": "inet", "local": "192.168.1.20", "prefixlen": 24},
        {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
    ],
}]

IFCONFIG = (
    "wlan0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n"
    "        ether aa:bb:cc:dd:ee:ff  txqueuelen 1000  (Ethernet)\n"
    "        inet 192.168.1.20  netmask 255.255.255.0  broadcast 192.168.1.255\n"
)

IW_LINK = (
    "Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"
    "\tSSID: home-wifi\n"
    "\tfreq: 2437\n"
    "\tsignal: -55 dBm\n"
    "\ttx bitrate: 72.2 MBit/s\n"
)

NMCLI = "yes:home-wifi:78:2437\n"

ARECORD = (
    "**** List of CAPTURE Hardware Devices ****\n"
    "card 0: PCH [HDA Intel PCH], device 0: ALC295 Analog [ALC295 Analog]\n"
)

PMSET_BATTERY = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=1234567)\t84%; discharging; 3:12 remaining present: true\n"
)

AIRPORT = (
    "     agrCtlRSSI: -52\n"
    "     SSID: home-wifi\n"
    "     BSSID: aa:bb:cc:dd:ee:ff\n"
    "     lastTxRate: 866\n"
    "     channel: 44,80\n"
)

NETSH = (
    "\n    Name                   : Wi-Fi\n"
    "    Description            : Intel(R) Wi-Fi 6 AX201 160MHz\n"
    "    GUID                   : 00000000-0000-0000-0000-000000000000\n"
    "    SSID                   : home-wifi\n"
    "    BSSID                  : aa:bb:cc:dd:ee:ff\n"
    "    Signal                 : 78%\n"
    "    Receive rate (Mbps)    : 300\n"
    "    Channel                : 6\n"
)

SYSTEM_PROFILER = (
    "Camera:\n"
    "      FaceTime HD Camera:\n"
    "\n"
    "Audio:\n"
    "      MacBook Pro Microphone Input:\n"
)


def _termux_battery(argv):
    return 0, json.dumps({"percentage": 42, "status": "DISCHARGING", "plugged": "UNPLUGGED",
                          "health": "GOOD", "temperature": 30.5, "current": -450000})


def _getprop(argv):
    name = argv[1] if len(argv) > 1 else ""
    return 0, GETPROP.get(name, "")


def _sysctl(argv):
    return 0, ("MacBookPro18,3" if "hw.model" in argv else "Apple M1 Pro")


def _powershell(argv):
    """One responder for the three PowerShell CIM queries of the Windows path."""
    script = " ".join(argv)
    if "Win32_NetworkAdapterConfiguration" in script:
        return 0, json.dumps([{
            "Description": "Intel(R) Wi-Fi 6 AX201 160MHz",
            "MACAddress": "AA:BB:CC:DD:EE:FF",
            "IPAddress": ["192.168.1.20", "fe80::1"],
            "DefaultIPGateway": ["192.168.1.1"],
        }])
    if "PNPClass='Camera'" in script:
        return 0, "Integrated Camera\n"
    if "AudioEndpoint" in script:
        return 0, "Microphone (Realtek(R) Audio)\n"
    return -1, ""


CANNED = {
    "getprop": _getprop,
    "sysctl": _sysctl,
    "powershell": _powershell,
    "termux-battery-status": _termux_battery,
    "termux-wifi-connectioninfo": (0, json.dumps({"ssid": "home-wifi",
                                                  "bssid": "aa:bb:cc:dd:ee:ff",
                                                  "rssi": -55, "frequency": 2437,
                                                  "link_speed_mbps": 72, "ip": "192.168.1.20"})),
    "termux-telephony-deviceinfo": (0, json.dumps({"network_operator_name": "Operator",
                                                   "network_type": "lte",
                                                   "network_roaming": False,
                                                   "data_state": "connected",
                                                   "sim_state": "ready"})),
    "termux-telephony-cellinfo": (0, json.dumps([{"type": "lte", "registered": True,
                                                  "dbm": -95, "level": 3}])),
    "termux-camera-info": (0, json.dumps([{"id": "0", "facing": "back",
                                           "jpeg_output_sizes": [{"width": 4000,
                                                                  "height": 3000}]}])),
    "termux-sensor": (0, json.dumps(["accelerometer", "gyroscope"])),
    "ip": (0, json.dumps(IP_ADDR)),
    "ifconfig": (0, IFCONFIG),
    "iw": (0, IW_LINK),
    "nmcli": (0, NMCLI),
    "arecord": (0, ARECORD),
    "pmset": (0, PMSET_BATTERY),
    "airport": (0, AIRPORT),
    "netsh": (0, NETSH),
    "system_profiler": (0, SYSTEM_PROFILER),
}


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------

class FakeRun:
    """``device._run`` replacement: records argv and answers canned payloads.

    ``responses`` maps a command name (``argv[0]``'s basename) to ``(rc, text)``
    or to a callable that receives the full argv. A command that is not in the
    map answers like a missing or broken tool: ``(-1, "")``.
    """

    def __init__(self, responses=None):
        self.calls = []
        self.responses = dict(responses if responses is not None else CANNED)

    def __call__(self, argv, timeout=device.DEFAULT_COMMAND_TIMEOUT):
        argv = [str(part) for part in argv]
        self.calls.append(argv)
        name = os.path.basename(argv[0]) if argv else ""
        answer = self.responses.get(name, (-1, ""))
        if callable(answer):
            answer = answer(argv)
        return answer

    @property
    def names(self):
        """The basenames of every command that was executed, in order."""
        return [os.path.basename(call[0]) for call in self.calls if call]

    def clear(self):
        self.calls = []


class FakeAdapter:
    """A stand-in for ``core/termux.py``: the seam that must work with or without it."""

    PAYLOADS = {
        "termux-battery-status": {"percentage": 55, "status": "CHARGING",
                                  "plugged": "PLUGGED_AC", "health": "GOOD",
                                  "temperature": 28.0, "current": 120000},
        "termux-wifi-connectioninfo": {"ssid": "adapter-wifi", "bssid": "11:22:33:44:55:66",
                                       "rssi": -60, "frequency": 5180},
        "termux-telephony-deviceinfo": {"network_operator_name": "Operator",
                                        "network_type": "nr", "network_roaming": False},
        "termux-telephony-cellinfo": [{"type": "nr", "registered": True, "dbm": -88}],
        "termux-camera-info": [{"id": "0", "facing": "back"}],
        "termux-sensor": ["accelerometer", "gyroscope"],
    }

    def __init__(self):
        self.commands = []
        self.samples = []

    def run_json(self, command, args=(), timeout=device.DEFAULT_COMMAND_TIMEOUT):
        self.commands.append((command, tuple(str(arg) for arg in args)))
        return {"ok": True, "value": self.PAYLOADS.get(command)}

    def api_installed(self):
        return {"installed": True, "commands": {"termux-battery-status": True}}

    def capabilities(self):
        # The real shape of core/termux.capabilities(): the inventory is filed
        # under "api" and the wake lock is a dict, both of which device.py must
        # translate/preserve rather than rename or overwrite.
        return {"api": {"installed": True, "commands": {"termux-battery-status": True}},
                "storage_permission": True,
                "wake_lock": {"command": True, "held": False, "count": 0},
                "permissions": {"camera": "unknown"},
                "fixes": []}

    def sensor_sample(self, name, timeout=8.0):
        self.samples.append(name)
        return {"ok": True, "value": {"accelerometer": {"values": [0.1, 0.2, 0.3]}}}


@pytest.fixture(autouse=True)
def isolated_device(monkeypatch):
    """No test may inherit another test's scenario, cache, environment or capture dir."""
    monkeypatch.setattr(device, "_termux_data_dir", lambda: False)
    for key in ("TERMUX_VERSION", "PREFIX", "MESH_DEVICE_CAPTURE_DIR",
                "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(key, raising=False)
    device.clear_cache()
    yield
    device.clear_cache()
    device.configure(None)


@pytest.fixture
def fake_platform(monkeypatch):
    """Install one scenario's fake host: resolver, tools, commands and adapter."""

    def install(case, adapter=None, responses=None, tools=None, clear=True):
        monkeypatch.setattr(device, "scenario", lambda: case)
        monkeypatch.setattr(device, "_IS_WINDOWS", case == "windows")
        monkeypatch.setattr(device, "_IS_DARWIN", case == "darwin")
        monkeypatch.setattr(device, "_termux_adapter", lambda: adapter)
        installed = set(FAKE_TOOLS[case] if tools is None else tools)
        monkeypatch.setattr(
            device, "_which",
            lambda name: ("/usr/bin/%s" % name) if name in installed else None)
        recorder = FakeRun(responses)
        monkeypatch.setattr(device, "_run", recorder)
        if clear:
            device.clear_cache()
        return recorder

    return install


def _commands_of(name, case):
    """The commands implementation ``name`` declares for ``case``.

    The very attribute ``scenario_commands()`` unions, so this is a view of the
    matrix, not a second hand-written list.
    """
    impl = device._SECTION_IMPL[name][case]
    return set(getattr(impl, "commands", ()) or ())


def _slow_commands(case):
    found = set()
    for name in device.SLOW_SECTIONS:
        found |= _commands_of(name, case)
    return found


def _fake_sysfs(monkeypatch, files):
    """Point the ``/sys/class/power_supply`` reads at ``files`` (paths -> text).

    A ``/sys`` tree is not needed: the Linux battery collector reads that one
    directory and a handful of files in it, so faking exactly those reads keeps
    the test deterministic on a host that has no battery (and on one that has).
    Paths are normalised because ``os.path.join`` uses the host's separator while
    the collector builds its paths from a POSIX literal.
    """
    directory = os.path.normpath("/sys/class/power_supply")
    normalized = {os.path.normpath(path): text for path, text in files.items()}
    real_isdir, real_listdir = os.path.isdir, os.listdir

    monkeypatch.setattr(device.os.path, "isdir",
                        lambda path: os.path.normpath(path) == directory or real_isdir(path))

    def fake_listdir(path):
        if os.path.normpath(path) != directory:
            return real_listdir(path)
        entries = set()
        for candidate in normalized:
            parent, _name = os.path.split(candidate)
            grandparent, child = os.path.split(parent)
            if grandparent == directory:
                entries.add(child)
        return sorted(entries)

    monkeypatch.setattr(device.os, "listdir", fake_listdir)
    monkeypatch.setattr(device, "_read_text",
                        lambda path: normalized.get(os.path.normpath(path), ""))


def _no_battery_host(fake_platform, case, monkeypatch):
    """Configure ``case`` so that this machine genuinely has no battery."""
    recorder = fake_platform(case, tools=() if case == "termux" else None)
    if case == "windows":
        monkeypatch.setattr(device, "_windows_power_status",
                            lambda: {"BatteryFlag": 128, "no_battery": True})
    elif case == "darwin":
        recorder.responses["pmset"] = (0, "Now drawing from 'AC Power'\n")
    elif case == "linux":
        _fake_sysfs(monkeypatch, {})
    return recorder


# ---------------------------------------------------------------------------
# (a)(b) The resolver: one place, four answers, and the order of the checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("os_name,system,env,expected", [
    ("nt", "Windows", {}, "windows"),
    # A Windows host that exports Termux variables is still Windows: the phone
    # checks must not be able to win over os.name.
    ("nt", "Windows", {"TERMUX_VERSION": "0.118.0"}, "windows"),
    ("nt", "Windows", {"PREFIX": TERMUX_PREFIX}, "windows"),
    ("posix", "Linux", {"TERMUX_VERSION": "0.118.0"}, "termux"),
    ("posix", "Linux", {"PREFIX": TERMUX_PREFIX}, "termux"),
    ("posix", "Darwin", {}, "darwin"),
    # Many build systems export PREFIX; only com.termux means Termux.
    ("posix", "Darwin", {"PREFIX": "/usr/local"}, "darwin"),
    ("posix", "Linux", {}, "linux"),
    ("posix", "Linux", {"PREFIX": "/usr/local"}, "linux"),
])
def test_scenario_is_decided_by_the_platform_signals(monkeypatch, os_name, system, env,
                                                    expected):
    monkeypatch.setattr(os, "name", os_name)
    monkeypatch.setattr(platform, "system", lambda: system)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert device.scenario() == expected
    assert device.is_termux() is (expected == "termux")


def test_the_termux_data_directory_is_a_signal_by_itself(monkeypatch):
    """The only signal that survives a scrubbed environment (su, Termux:Boot)."""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(device, "_termux_data_dir", lambda: True)
    assert device.scenario() == "termux"
    assert device.scenario_report()["signals"]["termux_data_dir"] is True


def test_a_phone_is_never_reported_as_windows(monkeypatch):
    """``platform.system()`` may say anything; ``os.name`` is the Windows fact."""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    assert device.scenario() == "termux"


def test_scenario_report_explains_the_decision(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    monkeypatch.setenv("PREFIX", TERMUX_PREFIX)
    assert device.scenario_report() == {
        "scenario": "termux",
        "platform": "Linux",
        "os_name": "posix",
        "signals": {"TERMUX_VERSION": "0.118.0", "PREFIX": TERMUX_PREFIX,
                    "termux_data_dir": False, "platform_system": "Linux"},
        "checked": ["windows", "termux", "darwin", "linux"],
    }


def test_scenario_report_works_on_the_real_host():
    report = device.scenario_report()
    assert report["scenario"] in device.SCENARIOS
    assert report["os_name"] == os.name
    assert report["platform"] == platform.system()
    assert report["checked"] == ["windows", "termux", "darwin", "linux"]
    assert set(report["signals"]) == {"TERMUX_VERSION", "PREFIX", "termux_data_dir",
                                      "platform_system"}


def test_the_resolver_is_not_cached(monkeypatch):
    """A cached answer would make a node restarted with a new environment lie."""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert device.scenario() == "linux"
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    assert device.scenario() == "termux"
    monkeypatch.delenv("TERMUX_VERSION")
    assert device.scenario() == "linux"


def test_is_termux_is_only_a_read_of_scenario(monkeypatch):
    monkeypatch.setattr(device, "scenario", lambda: "termux")
    assert device.is_termux() is True
    monkeypatch.setattr(device, "scenario", lambda: "linux")
    assert device.is_termux() is False


@pytest.mark.skipif(ON_ANDROID, reason="a real Termux is detected whatever the environment says")
@pytest.mark.parametrize("env", [{}, {"TERMUX_VERSION": "0.118.0"},
                                 {"PREFIX": TERMUX_PREFIX}, {"PREFIX": "/usr/local"}])
def test_the_two_termux_detectors_agree(monkeypatch, env):
    """core.domain answers the same question; the two must never disagree."""
    from core import domain

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert device.is_termux() is bool(domain.is_termux())


def test_the_resolver_is_the_only_decision_point():
    """A second place that asks "is this a phone?" is how the branches drift.

    The three signals are pinned to one reader each: ``"com.termux"`` appears
    once (in ``scenario``), the data directory constant is referenced only by its
    own function, and the Windows decision itself is made in exactly one place.
    """
    source = inspect.getsource(device)
    assert source.count('"com.termux"') == 1
    assert source.count("_TERMUX_DATA_DIR") == 2
    assert source.count('if os.name == "nt":') == 1


# ---------------------------------------------------------------------------
# (c) Every section x scenario pair exists
# ---------------------------------------------------------------------------

def test_every_section_has_an_implementation_for_every_scenario():
    assert device.matrix_problems() == []
    assert set(device._SECTION_IMPL) == set(device.ALL_SECTIONS)
    for name in device.ALL_SECTIONS:
        assert set(device._SECTION_IMPL[name]) == set(device.SCENARIOS), name
        for case in device.SCENARIOS:
            assert callable(device._SECTION_IMPL[name][case]), (name, case)


def test_matrix_problems_reports_a_missing_pair(monkeypatch):
    """The checker must fail loudly, not just always return an empty list."""
    monkeypatch.delitem(device._SECTION_IMPL["battery"], "darwin")
    problems = device.matrix_problems()
    assert any("battery" in problem and "darwin" in problem for problem in problems)


def test_matrix_problems_reports_a_foreign_name(monkeypatch):
    monkeypatch.setitem(device._SECTION_IMPL, "nonsense",
                        dict(device._SECTION_IMPL["time"]))
    assert any("nonsense" in problem for problem in device.matrix_problems())


def test_matrix_problems_reports_a_value_that_is_not_callable(monkeypatch):
    monkeypatch.setitem(device._SECTION_IMPL["sensors"], "linux", "not a function")
    assert any("sensors" in problem and "linux" in problem
               for problem in device.matrix_problems())


def test_a_missing_pair_is_a_reason_not_a_keyerror(monkeypatch):
    for case in device.SCENARIOS:
        device.clear_cache()
        monkeypatch.setattr(device, "scenario", lambda case=case: case)
        monkeypatch.setitem(device._SECTION_IMPL, "sensors", {case: None})
        blocks = device.collect(["sensors"], fresh=True)
        assert blocks["sensors"]["available"] is False
        assert "no implementation for scenario" in blocks["sensors"]["reason"]


def test_an_unknown_section_is_a_reason_too():
    block = device._collect_one("nonsense", {"fresh": True})
    assert block["available"] is False
    assert "unknown section" in block["reason"]


# ---------------------------------------------------------------------------
# (d) The command matrix: the right program for the right system
# ---------------------------------------------------------------------------

def test_the_allowed_tools_are_platform_tools():
    """The allow-list is data: no pmset on Windows, no netsh anywhere else."""
    assert set(device.scenario_commands("termux")) <= (
        {"getprop", "ip", "ifconfig", "arecord", "iw", "nmcli"} | set(TERMUX_COMMANDS))
    assert set(device.scenario_commands("windows")) == {"powershell", "netsh"}
    assert set(device.scenario_commands("linux")) <= {"ip", "ifconfig", "iw", "nmcli",
                                                     "arecord"}
    assert set(device.scenario_commands("darwin")) <= {"pmset", "sysctl", "airport",
                                                       "system_profiler", "ifconfig",
                                                       "netstat"}
    for case in device.SCENARIOS:
        allowed = set(device.scenario_commands(case))
        assert not (allowed & set(NEVER_RUNS[case])), case
    assert device.scenario_commands("not-a-scenario") == []


@pytest.mark.parametrize("case", device.SCENARIOS)
def test_a_full_collect_runs_only_this_scenarios_tools(case, fake_platform):
    recorder = fake_platform(case)
    blocks = device.collect(sections="all", fresh=True, deadline=12.0)

    assert set(blocks) == set(device.ALL_SECTIONS)
    assert recorder.names, "the fake host ran nothing, so this case proves nothing"

    allowed = set(device.scenario_commands(case))
    assert set(recorder.names) <= allowed, (
        "undeclared tool(s): %s" % sorted(set(recorder.names) - allowed))
    assert not (set(recorder.names) & set(NEVER_RUNS[case])), (
        "another platform's tool ran: %s" % sorted(set(recorder.names) & set(NEVER_RUNS[case])))

    essentials = {"termux": "getprop", "linux": "arecord",
                  "windows": "powershell", "darwin": "pmset"}
    assert essentials[case] in recorder.names


@pytest.mark.parametrize("case", ("termux", "linux", "darwin"))
def test_windows_only_mechanisms_stay_unreachable(case, fake_platform, monkeypatch):
    """ctypes/winreg/CIM are Windows code: no posix path may reach them."""
    fake_platform(case)
    touched = []
    monkeypatch.setattr(device, "_windows_power_status",
                        lambda: (touched.append("GetSystemPowerStatus"), {})[1])
    monkeypatch.setattr(device, "_windows_interfaces",
                        lambda: (touched.append("Win32_NetworkAdapterConfiguration"), [])[1])
    monkeypatch.setattr(device, "_windows_cpu_model",
                        lambda: (touched.append("winreg"), "")[1])

    blocks = device.collect(sections="all", fresh=True, deadline=12.0)

    assert set(blocks) == set(device.ALL_SECTIONS)
    assert touched == []


@pytest.mark.parametrize("case", ("termux", "linux", "darwin"))
@pytest.mark.parametrize("name", device.ALL_SECTIONS)
def test_posix_implementations_name_no_windows_mechanism(name, case):
    source = inspect.getsource(device._SECTION_IMPL[name][case])
    for forbidden in ("winreg", "ctypes", "netsh", "powershell"):
        assert forbidden not in source, "%s/%s names %s" % (name, case, forbidden)


# ---------------------------------------------------------------------------
# (e) Battery: the right source per scenario, and never a fabricated percent
# ---------------------------------------------------------------------------

def test_battery_on_a_phone_comes_from_termux_api(fake_platform):
    fake_platform("termux")
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is True
    assert block["source"] == BATTERY_SOURCE["termux"]
    assert block["percent"] == 42
    assert block["status"] == "discharging"
    assert block["ac_online"] is False


def test_battery_on_windows_comes_from_get_system_power_status(fake_platform, monkeypatch):
    fake_platform("windows")
    monkeypatch.setattr(device, "_windows_power_status", lambda: {
        "BatteryFlag": 8, "percent": 77, "ac_online": True, "charging": True,
        "no_battery": False, "time_remaining_s": None})
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is True
    assert block["source"] == BATTERY_SOURCE["windows"]
    assert block["percent"] == 77
    assert block["status"] == "charging"
    assert block["ac_online"] is True


def test_battery_on_macos_comes_from_pmset(fake_platform):
    fake_platform("darwin")
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is True
    assert block["source"] == BATTERY_SOURCE["darwin"]
    assert block["percent"] == 84
    assert block["status"] == "discharging"
    assert block["time_remaining_s"] == 3 * 3600 + 12 * 60


def test_battery_on_linux_comes_from_sysfs(fake_platform, monkeypatch):
    fake_platform("linux")
    base = os.path.join("/sys/class/power_supply", "BAT0")
    _fake_sysfs(monkeypatch, {
        os.path.join(base, "capacity"): "42",
        os.path.join(base, "status"): "Discharging",
        os.path.join(base, "health"): "Good",
        os.path.join(base, "temp"): "300",
    })
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is True
    assert block["source"] == BATTERY_SOURCE["linux"]
    assert block["percent"] == 42
    assert block["status"] == "discharging"
    assert block["temperature_c"] == 30.0


@pytest.mark.parametrize("case", device.SCENARIOS)
def test_a_machine_without_a_battery_never_fabricates_a_percent(case, fake_platform,
                                                               monkeypatch):
    _no_battery_host(fake_platform, case, monkeypatch)
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is False
    assert block["reason"]
    assert "percent" not in block


def test_a_missing_termux_api_says_how_to_fix_it(fake_platform):
    fake_platform("termux", tools=())
    block = device.collect(["battery"], fresh=True)["battery"]
    assert block["available"] is False
    assert "Termux:API" in block.get("fix", "")
    assert "percent" not in block


def test_battery_blocks_always_carry_available_and_source(fake_platform):
    for case in device.SCENARIOS:
        device.clear_cache()
        fake_platform(case, clear=True)
        block = device.collect(["battery"], fresh=True)["battery"]
        assert "available" in block and "source" in block, case


# ---------------------------------------------------------------------------
# The Termux:API adapter seam (works with or without core/termux.py)
# ---------------------------------------------------------------------------

def test_the_adapter_is_used_instead_of_shelling_out(fake_platform):
    adapter = FakeAdapter()
    recorder = fake_platform("termux", adapter=adapter)
    blocks = device.collect(sections="all", fresh=True, deadline=12.0)

    assert blocks["battery"]["source"] == "termux-battery-status"
    assert blocks["battery"]["percent"] == 55
    assert blocks["device"]["termux_api"] == {"installed": True,
                                              "commands": {"termux-battery-status": True}}
    capabilities = blocks["capabilities"]
    # One fact, one name: the adapter's "api" inventory is exposed as termux_api.
    assert capabilities["termux_api"] == {"installed": True,
                                         "commands": {"termux-battery-status": True}}
    assert "api" not in capabilities
    # The adapter's wake-lock dict survives: a bare bool used to overwrite it.
    assert capabilities["wake_lock"] == {"command": True, "held": False, "count": 0}
    assert "wake_lock_command" not in capabilities
    assert capabilities["storage_permission"] is True
    assert capabilities["permissions"] == {"camera": "unknown"}
    assert ("termux-battery-status", ()) in adapter.commands
    # The adapter owns its subprocesses: device must not run termux-* behind it.
    assert not [name for name in recorder.names if name.startswith("termux-")]


def test_the_fallback_inventory_uses_the_canonical_key(fake_platform):
    """Without the adapter the same key and the same wake-lock split apply."""
    fake_platform("termux")
    capabilities = device.collect(["capabilities"], fresh=True)["capabilities"]
    assert capabilities["termux_api"]["installed"] is True
    assert capabilities["termux_api"]["commands"]["termux-wake-lock"] is True
    assert capabilities["wake_lock_command"] is True
    assert "api" not in capabilities
    assert "wake_lock" not in capabilities      # never a bare bool under that name


def test_a_named_sensor_sample_goes_through_the_adapter_and_is_never_cached(fake_platform):
    adapter = FakeAdapter()
    fake_platform("termux", adapter=adapter)
    block = device.collect(["sensors"], fresh=True, sensor="accelerometer")["sensors"]
    assert block["sampling"] == "values"
    assert adapter.samples == ["accelerometer"]
    device.collect(["sensors"], sensor="accelerometer")
    assert adapter.samples == ["accelerometer", "accelerometer"]


def test_a_named_sensor_sample_falls_back_to_the_command(fake_platform):
    recorder = fake_platform("termux")
    block = device.collect(["sensors"], fresh=True, sensor="accelerometer")["sensors"]
    assert block["sampling"] == "values"
    sampled = [call for call in recorder.calls if "-s" in call and "accelerometer" in call]
    assert sampled, recorder.calls


def test_an_adapter_that_raises_is_a_reason_not_an_exception(fake_platform):
    class Broken:
        def run_json(self, *args, **kwargs):
            raise RuntimeError("the Termux:API app is not running")

        def api_installed(self):
            raise RuntimeError("the Termux:API app is not running")

        def capabilities(self):
            raise RuntimeError("the Termux:API app is not running")

    fake_platform("termux", adapter=Broken())
    blocks = device.collect(sections="all", fresh=True, deadline=12.0)
    assert set(blocks) == set(device.ALL_SECTIONS)
    assert blocks["battery"]["available"] is False
    assert "RuntimeError" in blocks["battery"]["reason"]
    assert blocks["capabilities"]["termux_api"]["error"].startswith("RuntimeError")


# ---------------------------------------------------------------------------
# (f) Nothing raises, and one broken source cannot take the call down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", device.SCENARIOS)
def test_every_block_survives_a_run_that_raises(case, fake_platform, monkeypatch):
    recorder = fake_platform(case)

    def boom(argv, timeout=device.DEFAULT_COMMAND_TIMEOUT):
        recorder.calls.append([str(part) for part in argv])
        raise OSError("the tool is not there any more")

    monkeypatch.setattr(device, "_run", boom)
    blocks = device.collect(sections="all", fresh=True, deadline=12.0)

    assert set(blocks) == set(device.ALL_SECTIONS)
    for name, block in blocks.items():
        assert isinstance(block, dict) and "available" in block, name


def test_a_source_that_hangs_times_out_inside_the_deadline(fake_platform, monkeypatch):
    recorder = fake_platform("linux")
    release = threading.Event()

    def hang(argv, timeout=device.DEFAULT_COMMAND_TIMEOUT):
        recorder.calls.append([str(part) for part in argv])
        release.wait(5.0)
        return -1, ""

    monkeypatch.setattr(device, "_run", hang)
    started = time.monotonic()
    try:
        blocks = device.collect(["network", "battery", "time"], deadline=0.8, fresh=True)
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert set(blocks) == {"network", "battery", "time"}
    assert blocks["network"]["available"] is False
    assert "timed out" in blocks["network"]["reason"]
    # The fast section still answered: one stuck probe cannot stall the call.
    assert blocks["time"]["available"] is True
    assert elapsed < 3.0


def test_a_collector_that_raises_is_a_block(monkeypatch):
    def boom():
        raise RuntimeError("collector exploded")

    for case in device.SCENARIOS:
        device.clear_cache()
        monkeypatch.setattr(device, "scenario", lambda case=case: case)
        monkeypatch.setitem(device._SECTION_IMPL, "hardware", {case: boom})
        block = device.collect(["hardware"], fresh=True)["hardware"]
        assert block["available"] is False
        assert "RuntimeError" in block["reason"]


def test_a_collector_that_returns_a_non_block_is_a_block(monkeypatch):
    monkeypatch.setattr(device, "scenario", lambda: "linux")
    monkeypatch.setitem(device._SECTION_IMPL, "time", {"linux": lambda: "not a block"})
    block = device.collect(["time"], fresh=True)["time"]
    assert block["available"] is False
    assert "returned 'str'" in block["reason"]


# ---------------------------------------------------------------------------
# (g) quick=True never touches a slow source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", device.SCENARIOS)
def test_quick_never_touches_a_slow_section(case, fake_platform):
    recorder = fake_platform(case)
    slow = _slow_commands(case)
    assert slow, "no slow-section command is declared for %s" % case

    device.collect(sections="all", fresh=True, deadline=12.0)
    assert set(recorder.names) & slow, (
        "the fake host recorded no slow command, so the quick case proves nothing")

    recorder.clear()
    device.clear_cache()
    blocks = device.collect(sections="all", quick=True, fresh=True, deadline=12.0)

    assert set(blocks) == set(device.ALL_SECTIONS) - set(device.SLOW_SECTIONS)
    assert not (set(recorder.names) & slow), (
        "quick collect ran %s" % sorted(set(recorder.names) & slow))


# ---------------------------------------------------------------------------
# (h) main() prints valid JSON for every section
# ---------------------------------------------------------------------------

def _offline(monkeypatch):
    """Make main() cheap and offline: no real probes, no subprocesses."""
    monkeypatch.setattr(device, "_run", lambda *args, **kwargs: (-1, ""))
    monkeypatch.setattr(device, "_which", lambda name: None)
    monkeypatch.setattr(device, "_termux_adapter", lambda: None)


@pytest.mark.parametrize("section", device.ALL_SECTIONS)
def test_main_prints_json_for_every_section(section, monkeypatch, capsys):
    _offline(monkeypatch)
    assert device.main([section]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert section in payload
    assert payload["_scenario"]["scenario"] in device.SCENARIOS
    assert payload["_scenario"]["checked"] == ["windows", "termux", "darwin", "linux"]
    assert payload["_meta"]["platform"]
    assert payload["_meta"]["termux"] is (device.scenario() == "termux")


def test_main_reports_every_section_at_once(monkeypatch, capsys):
    _offline(monkeypatch)
    assert device.main([]) == 0                     # no argument means "all"
    payload = json.loads(capsys.readouterr().out)
    assert set(device.ALL_SECTIONS) <= set(payload)
    assert isinstance(payload["_scenario"]["signals"]["termux_data_dir"], bool)


def test_main_survives_an_unknown_section(monkeypatch, capsys):
    _offline(monkeypatch)
    assert device.main(["nonsense"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "_scenario" in payload and "_meta" in payload


# ---------------------------------------------------------------------------
# (i) The cache: per section and per scenario
# ---------------------------------------------------------------------------

def test_the_cache_answers_again_without_running_the_source(fake_platform):
    recorder = fake_platform("termux")
    first = device.collect(["battery"])["battery"]
    calls = len(recorder.calls)

    second = device.collect(["battery"])["battery"]
    assert second == first
    assert len(recorder.calls) == calls, "the second call re-ran the source"

    device.collect(["battery"], fresh=True)
    assert len(recorder.calls) > calls, "fresh=True did not bypass the cache"


def test_a_block_cached_for_another_scenario_is_never_served(fake_platform):
    fake_platform("windows")
    windows_block = device.collect(["battery"])["battery"]
    assert windows_block["source"] == BATTERY_SOURCE["windows"]

    fake_platform("termux", clear=False)            # deliberately keep the cache
    termux_block = device.collect(["battery"])["battery"]

    assert termux_block["source"] == BATTERY_SOURCE["termux"]
    assert termux_block != windows_block


def test_clear_cache_drops_every_scenario(fake_platform):
    fake_platform("termux")
    device.collect(["battery"])
    assert device._CACHE
    device.clear_cache()
    assert device._CACHE == {}


# ---------------------------------------------------------------------------
# The real phone: fixtures captured from an OPPO PHY110 by tools/probe-node.py
#
# These replay the *captured* files through the parsers. They are not a second
# unit test of the parsers - they are the case where the documentation was wrong
# and the phone was right (thermal zones that are not temperatures, a missing
# storage grant, and a Termux:API app that hangs).
# ---------------------------------------------------------------------------

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "termux")


def _fixture(name):
    """One captured file, loaded from disk so the test proves it is committed."""
    path = os.path.join(FIXTURES, name)
    if not os.path.isfile(path):
        pytest.skip("fixture %s is not in the tree" % name)
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()


#: The captured probe for each ``termux-*`` command this module runs. The phone
#: was probed twice: the first time the commands were absent, the second time they
#: were on PATH but the Termux:API app never answered (exit 124). Both captures
#: are read from the fixture rather than assumed, so the suite survives a re-probe.
COMMAND_CAPTURES = {
    "termux-battery-status": "battery-status.txt",
    "termux-wifi-connectioninfo": "wifi-connectioninfo.txt",
    "termux-telephony-deviceinfo": "telephony-deviceinfo.txt",
    "termux-telephony-cellinfo": "telephony-cellinfo.txt",
    "termux-sensor": "sensor-list.txt",
    "termux-camera-info": "camera-info.txt",
}


def _captured_command(name):
    """``(returncode, output)`` as the phone's probe recorded it for one command.

    The capture files lead with ``[Exit code: N] ...``; a file without that header
    is the command's output from an ordinary run.
    """
    filename = COMMAND_CAPTURES.get(name)
    if not filename:
        return -1, ""
    body = _fixture(filename)
    head, _, rest = body.partition("\n")
    match = re.match(r"\[Exit code: (\d+)\]", head)
    if match:
        return int(match.group(1)), rest.strip()
    return 0, body


def _captured_inventory():
    """``{command: installed?}`` exactly as ``termux-api-installed.txt`` answered.

    Handles both capture generations: ``MISSING <name>`` lines (the command was
    not on ``PATH``) and resolved paths (it was).
    """
    inventory = {}
    for line in _fixture("termux-api-installed.txt").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("MISSING "):
            inventory[line.split(None, 1)[1].strip()] = False
        else:
            inventory[os.path.basename(line)] = True
    return inventory


def _captured_meminfo():
    """``{name: kB}`` for the two lines the memory collector reads."""
    values = {}
    for line in _fixture("proc-meminfo.txt").splitlines():
        key, _, rest = line.partition(":")
        if key.strip() in ("MemTotal", "MemAvailable") and rest.strip():
            values[key.strip()] = int(rest.split()[0])
    return values


def _captured_thermal():
    """``{zone type: millidegrees}`` in the phone's own zone order.

    A duplicate label (``usb-therm`` appears twice in one capture) keeps the last
    reading, which is all a report that names each zone once can show anyway.
    """
    values = {}
    for line in _fixture("thermal-zones.txt").splitlines():
        name, _, raw = line.partition("=")
        if raw.strip():
            values[name.strip()] = int(raw)
    return values


def _captured_cpu_khz():
    """``(current, maximum)`` cpu0 frequencies in kHz, the two captured lines."""
    current, _, maximum = _fixture("cpu-freq.txt").partition("\n")
    return int(current.strip()), int(maximum.strip())


class FakeGlob:
    """``glob`` where the fixture decides every result: no host path is consulted."""

    def __init__(self, mapping):
        self.mapping = mapping

    def glob(self, pattern):
        return list(self.mapping.get(pattern, ()))


def _replay_phone(monkeypatch):
    """Answer every probe with the real phone's captured output.

    The layout mirrors ``MANIFEST.json`` (one thermal-zone directory per captured
    line, the two cpu-frequency files) instead of a real ``/sys`` tree, so the
    assertions are about the phone and this test host is irrelevant.
    """
    monkeypatch.setattr(device, "scenario", lambda: "termux")
    monkeypatch.setattr(device, "_IS_WINDOWS", False)
    monkeypatch.setattr(device, "_IS_DARWIN", False)
    # The local fallback is what runs when core/termux.py cannot serve; that
    # module has its own tests. The replay therefore exercises the code that has
    # no adapter to lean on.
    monkeypatch.setattr(device, "_termux_adapter", lambda: None)
    monkeypatch.setenv("TERMUX_VERSION", "0.118.6")
    monkeypatch.setenv("PREFIX", TERMUX_PREFIX)

    files = {"/proc/meminfo": _fixture("proc-meminfo.txt")}
    zones = []
    for index, line in enumerate(_fixture("thermal-zones.txt").splitlines()):
        name, _, raw = line.partition("=")
        zone = "/sys/class/thermal/thermal_zone%d" % index
        zones.append(zone)
        files[os.path.join(zone, "type")] = name
        files[os.path.join(zone, "temp")] = raw

    current, _, maximum = _fixture("cpu-freq.txt").partition("\n")
    cpus = ["/sys/devices/system/cpu/cpu%d" % index for index in range(4)]
    current_paths = [os.path.join(cpu, "cpufreq", "scaling_cur_freq") for cpu in cpus]
    maximum_path = os.path.join(cpus[0], "cpufreq", "cpuinfo_max_freq")
    for path in current_paths:
        files[path] = current.strip()
    files[maximum_path] = maximum.strip()

    monkeypatch.setattr(device, "_read_text", lambda path: files.get(path, ""))
    monkeypatch.setattr(device, "_glob", FakeGlob({
        "/sys/class/thermal/thermal_zone*": zones,
        "/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq": current_paths,
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": [maximum_path],
        "/sys/devices/system/cpu/cpu[0-9]*": cpus,
        "/sys/class/hwmon/hwmon*": [],
        "/sys/class/power_supply/BAT*": [],
        "/sys/class/video4linux/video*": [],
    }))

    getprop = {
        "ro.product.model": _fixture("getprop-model.txt"),
        "ro.product.device": _fixture("getprop-model.txt"),
        "ro.product.manufacturer": _fixture("getprop-manufacturer.txt"),
        "ro.build.version.release": _fixture("getprop-android.txt"),
        "ro.build.version.sdk": _fixture("getprop-sdk.txt"),
        "ro.build.characteristics": _fixture("getprop-characteristics.txt"),
        # The capture has `uname -m`; `ro.product.cpu.abi` was not probed, so the
        # captured architecture answers that one field.
        "ro.product.cpu.abi": _fixture("uname.txt"),
        "persist.sys.locale": _fixture("getprop-locale.txt"),
        "persist.sys.timezone": _fixture("getprop-timezone.txt"),
    }

    # termux-api-installed.txt is the phone's own `command -v` inventory: it names
    # either the resolved path (the command is there) or "MISSING <name>".
    installed = {name for name, present in _captured_inventory().items() if present}

    def fake_which(name):
        if name in installed:
            return "/data/data/com.termux/files/usr/bin/%s" % name
        return None

    def fake_run(argv, timeout=device.DEFAULT_COMMAND_TIMEOUT):
        name = os.path.basename(argv[0])
        if name == "getprop" and len(argv) > 1:
            return 0, getprop.get(argv[1], "")
        if name in COMMAND_CAPTURES:
            # The captured answer, exit status included: a silent Termux:API app
            # is a timeout (124), not a fabricated reading.
            return _captured_command(name)
        return -1, ""

    monkeypatch.setattr(device, "_which", fake_which)
    monkeypatch.setattr(device, "_run", fake_run)
    return files


def test_device_identity_matches_the_real_phone(monkeypatch):
    _replay_phone(monkeypatch)
    block = device.collect(["device"], fresh=True)["device"]

    assert _fixture("getprop-model.txt") == "PHY110"
    assert block["model"] == "PHY110"
    assert block["manufacturer"] == _fixture("getprop-manufacturer.txt") == "OPPO"
    assert block["android_release"] == _fixture("getprop-android.txt") == "16"
    assert block["android_sdk"] == _fixture("getprop-sdk.txt") == "36"
    assert block["abi"] == _fixture("uname.txt")
    assert block["termux_version"] == "0.118.6"
    # The trap: "nosdcard" contains no "tablet", and a phone must stay a phone.
    assert block["characteristics"] == _fixture("getprop-characteristics.txt") == "nosdcard"
    assert block["class"] == "phone"


def test_locale_and_timezone_come_from_the_real_getprop(monkeypatch):
    _replay_phone(monkeypatch)

    locale_block = device.collect(["locale"], fresh=True)["locale"]
    assert _fixture("getprop-locale.txt") == "ru-RU"
    assert locale_block["language"] == "ru"
    assert locale_block["region"] == "RU"
    assert locale_block["source"] == "getprop + environment"

    time_block = device.collect(["time"], fresh=True)["time"]
    assert _fixture("getprop-timezone.txt") == "Asia/Tbilisi"
    assert time_block["timezone"] == "Asia/Tbilisi"


def test_ram_comes_from_the_real_proc_meminfo(monkeypatch):
    """The phone's 15.5 GB, parsed from its own ``/proc/meminfo``.

    ``core.vitals`` reads the same file on a real phone, so it is made to fail
    here: that forces the parser inside ``_memory_block``, which is what a
    checkout without ``core.vitals`` runs.
    """
    from core import vitals

    _replay_phone(monkeypatch)

    def no_vitals():
        raise RuntimeError("core.vitals is not available in this test")

    monkeypatch.setattr(vitals, "get_host_vitals", no_vitals)

    memory = device.collect(["hardware"], fresh=True)["hardware"]["memory"]
    assert memory["source"] == "/proc/meminfo"
    # The expected numbers are computed from the capture, not typed in: the node
    # was probed more than once and the phone's free memory moved between probes.
    kb = _captured_meminfo()
    total_mb = round(kb["MemTotal"] / 1024.0, 1)
    free_mb = round(kb["MemAvailable"] / 1024.0, 1)
    used_mb = round(total_mb - free_mb, 1)
    assert memory["total_mb"] == total_mb
    assert memory["free_mb"] == free_mb
    assert memory["used_mb"] == max(0.0, used_mb)
    assert memory["used_pct"] == round(used_mb / total_mb * 100.0, 1)
    assert 15000 < total_mb < 16000               # the phone's ~15.5 GB of RAM


def test_cpu_frequency_comes_from_the_real_phone(monkeypatch):
    _replay_phone(monkeypatch)
    current_khz, maximum_khz = _captured_cpu_khz()
    assert 400000 < current_khz < 3000000
    assert 400000 < maximum_khz < 4000000

    cpu = device.collect(["hardware"], fresh=True)["hardware"]["cpu"]
    assert set(cpu["freq_mhz_cur"]) == {round(current_khz / 1000.0, 1)}
    assert len(cpu["freq_mhz_cur"]) == 4
    assert cpu["freq_mhz_max"] == round(maximum_khz / 1000.0, 1)
    assert cpu["cores"] == 4


def test_thermal_zones_from_the_real_phone(monkeypatch):
    """The block a model reads: real temperatures, and no fabricated ones."""
    _replay_phone(monkeypatch)
    captured = _captured_thermal()

    hardware = device.collect(["hardware"], fresh=True)["hardware"]
    thermal = hardware["thermal"]
    assert thermal

    values = {item["name"]: item["value"] for item in thermal}
    # The exact numbers come from the capture: the phone was re-probed and its
    # temperatures moved by tens of millidegrees between probes.
    for name in ("cpuss-0", "battery", "usb"):
        assert values[name] == round(captured[name] / 1000.0, 1), name
    assert -273.0 not in values.values()                # mmw*, bcl_warn, epm*, sub1_*
    assert not [name for name in values if "lvl" in name or "bcl" in name]
    assert all(-100 < value < 200 for value in values.values())
    # The block is full of temperatures: the cut preferred the named ones.
    assert len(thermal) == device._MAX_LIST
    # The CPU temperature is the max of what was *reported*, not of the raw file.
    assert hardware["cpu"]["temp_c"] == max(values.values())


def test_the_thermal_filter_rules_over_all_107_zones(monkeypatch):
    """With every zone parsed, the filter - not the truncation - does the work."""
    _replay_phone(monkeypatch)
    monkeypatch.setattr(device, "_MAX_LIST", 500)
    captured = _captured_thermal()

    thermal = device._thermal_sensors()
    names = [item["name"] for item in thermal]
    values = {item["name"]: item["value"] for item in thermal}

    assert len(thermal) >= 70, "most of the phone's zones are real temperatures"
    assert values["cpuss-0"] == round(captured["cpuss-0"] / 1000.0, 1)
    assert values["battery"] == round(captured["battery"] / 1000.0, 1)

    # Zones the capture itself says are not temperatures: an unpopulated radio
    # (-273000) and a current-limit level (0, 93, 187).
    unpopulated = {name for name, milli in captured.items() if milli <= -100000}
    levels = {name for name in captured if "lvl" in name.lower() or "bcl" in name.lower()}
    assert unpopulated, "the capture has unpopulated zones"
    assert levels, "the capture has level zones"
    assert not (unpopulated & set(names))
    assert not (levels & set(names))
    assert -273.0 not in values.values()
    assert all(-100 < value < 200 for value in values.values())


def test_storage_reports_the_missing_shared_grant(monkeypatch):
    _replay_phone(monkeypatch)
    shared = os.path.expanduser("~/storage/shared")
    real_isdir = os.path.isdir
    monkeypatch.setattr(device.os.path, "isdir",
                        lambda path: False if path == shared else real_isdir(path))

    block = device.collect(["storage"], fresh=True)["storage"]
    assert _fixture("storage-shared.txt") == "absent"
    assert block["shared"] == "absent"
    assert "termux-setup-storage" in block["shared_fix"]
    assert block["prefix"] == TERMUX_PREFIX


def test_the_termux_inventory_matches_the_capture(monkeypatch):
    """What the node reports as installed is what the phone's own probe found."""
    _replay_phone(monkeypatch)
    captured = _captured_inventory()
    assert captured, "the capture lists the probed commands"

    observed = device.collect(["capabilities"], fresh=True)["capabilities"]["termux_api"]
    for name, present in captured.items():
        assert name in observed["commands"], name
        assert observed["commands"][name] is present, name


def test_a_silent_termux_api_is_never_a_fabricated_reading(monkeypatch):
    """The second capture: the commands exist, the Termux:API app never answers.

    An exit status of 124 is the app hanging in the background (Android freezes
    it); the block must say so. It must not report 0 %, and it must not invent a
    camera list either.
    """
    _replay_phone(monkeypatch)
    rc, _text = _captured_command("termux-battery-status")
    if rc == 0:
        pytest.skip("this capture carries an API answer; the case here is a silent app")

    blocks = device.collect(["battery", "cameras", "sensors"], fresh=True, deadline=8.0)
    for name in ("battery", "cameras", "sensors"):
        assert blocks[name]["available"] is False, name
        assert blocks[name]["reason"], name
        assert "percent" not in blocks[name], name

    assert str(rc) in blocks["battery"]["reason"]      # the exit status is surfaced
    # The commands are on PATH, so the inventory must say installed - the failure
    # is the app, not the package, and a model needs to be able to tell them apart.
    capabilities = device.collect(["capabilities"], fresh=True)["capabilities"]
    assert capabilities["termux_api"]["installed"] is True
    assert capabilities["wake_lock_command"] is True
