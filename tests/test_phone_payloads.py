"""The phone chain, fed with the payloads ``termux-api`` really sends.

Why this file exists. Three other suites cover the device branch, and each of them
deliberately looks away from exactly one thing:

* ``tests/test_device_tools.py`` replaces ``core/device.py`` with a fake module, so
  the JSON parsers *inside* it never run;
* ``tests/test_device_scenarios.py`` replays the captured files from a real phone
  (``getprop``, ``/proc/meminfo``, thermal zones, CPU frequencies) - the file-backed
  half, which is all the captured phone could answer;
* ``tests/test_termux_adapter.py`` drives the adapter with a fake runner and checks
  *how* it runs a command, not what a device report makes of the answer.

That leaves one gap, and it is the one that matters most on a phone: a successful
``termux-api`` answer arriving as JSON and being turned into the battery, Wi-Fi,
cellular, camera and sensor blocks. The real phone cannot close it yet - the
Termux:API app is not installed, so every call hangs (see
``tests/fixtures/termux/README.md``) - so this file pins the *documented* shapes
instead, and it is where captured JSON will land once the app is there.

Every payload below is the shape termux-api documents, and every assertion is about
a decision ``core/device.py`` makes from it: which cell is the registered one, that
Android's ``<unknown ssid>``/``-1`` placeholders are not reported as a real network,
that the largest JPEG size is the one named, that a named sensor is sampled with
``-n 1`` (never as a stream), and that a phone whose app is silent is reported as
one cause rather than five timeouts.
"""

import json
import os
import subprocess

import pytest

from core import device, mcp_tools, termux, vitals

# ---------------------------------------------------------------------------
# Documented termux-api payloads
# ---------------------------------------------------------------------------

BATTERY = {
    "health": "GOOD",
    "percentage": 42,
    "plugged": "UNPLUGGED",
    "status": "DISCHARGING",
    "temperature": 31.5,
    "current": -412345,
}

WIFI = {
    "bssid": "e8:48:b8:af:f8:17",
    "frequency": 5180,
    "ip": "192.168.2.102",
    "link_speed_mbps": 433,
    "mac_address": "94:08:53:46:8c:88",
    "network_id": 7,
    "rssi": -52,
    "ssid": "Warmen5g",
    "supplicant_state": "COMPLETED",
}

#: What Android 10+ hands back when the location permission is missing: the SSID is
#: the literal string "<unknown ssid>" and the signal is a sentinel, not a number.
WIFI_WITHOUT_PERMISSION = dict(WIFI, ssid="<unknown ssid>", rssi=-1, bssid="02:00:00:00:00:00")

TELEPHONY_DEVICE = {
    "network_operator_name": "Magti",
    "network_type": "LTE",
    "network_roaming": False,
    "data_state": "CONNECTED",
    "sim_state": "READY",
    "phone_type": "GSM",
}

#: A neighbouring cell with a stronger signal than the serving one, plus the serving
#: cell. Only the registered one may be reported as "the" signal.
CELLS = [
    {"type": "lte", "registered": False, "dbm": -75, "level": 4, "cid": 11},
    {"type": "lte", "registered": True, "dbm": -101, "level": 2, "cid": 42, "rsrp": -108},
]

CAMERAS = [
    {
        "id": 0,
        "facing": "back",
        "focal_lengths": [4.44],
        "jpeg_output_sizes": [
            {"width": 1920, "height": 1080},
            {"width": 4032, "height": 3024},
        ],
    },
    {"id": 1, "facing": "front", "jpeg_output_sizes": [{"width": 3264, "height": 2448}]},
]

SENSOR_LIST = {"sensors": ["acceleration", "magnetic_field", "light"]}
SENSOR_SAMPLE = {"acceleration": {"values": [0.12, 9.71, 0.33]}}

#: getprop answers the phone really gives (see tests/fixtures/termux/).
GETPROP = {
    "ro.product.model": "PHY110",
    "ro.product.manufacturer": "OPPO",
    "ro.build.version.release": "16",
    "ro.build.version.sdk": "36",
    "ro.build.characteristics": "nosdcard",
    "persist.sys.locale": "ru-RU",
    "persist.sys.timezone": "Asia/Tbilisi",
}

PAYLOADS = {
    "termux-battery-status": BATTERY,
    "termux-wifi-connectioninfo": WIFI,
    "termux-telephony-deviceinfo": TELEPHONY_DEVICE,
    "termux-telephony-cellinfo": CELLS,
    "termux-camera-info": CAMERAS,
    "termux-sensor": SENSOR_LIST,
}


class Phone(object):
    """A simulated Termux node: the adapter's two seams plus the device readers."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.answers = dict(PAYLOADS)
        self.hang = False
        monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        # The scenario is pinned rather than inferred. On this host `os.name == "nt"`
        # wins over the Termux signals on purpose - a phone is never Windows - and
        # that precedence has its own suite (tests/test_device_scenarios.py). This
        # file is about what the parsers make of an answer, so it says which platform
        # it is pretending to be instead of trying to fool the resolver.
        monkeypatch.setattr(device, "scenario", lambda: "termux")
        termux.reset_state()
        device.clear_cache()
        monkeypatch.setattr(termux, "_which", self._which)
        monkeypatch.setattr(termux, "_run", self._run)
        monkeypatch.setattr(device, "_which", self._device_which)
        monkeypatch.setattr(device, "_run", self._device_run)
        # The filesystem half is not what this file tests; an empty answer keeps a
        # host /sys or /proc from leaking into an assertion.
        monkeypatch.setattr(device, "_read_text", lambda path: "")
        monkeypatch.setattr(device, "_read_int", lambda path: None)

    # -- adapter seams ------------------------------------------------------
    @staticmethod
    def _which(name):
        return name if str(name).startswith("termux-") else None

    def _run(self, argv, timeout=6.0):
        self.calls.append(list(argv))
        if self.hang:
            # The frozen-app failure, exactly as it reaches the adapter.
            raise subprocess.TimeoutExpired(list(argv), timeout)
        name = argv[0]
        if name == "termux-sensor" and "-l" not in argv:
            return 0, json.dumps(SENSOR_SAMPLE)
        if name in self.answers:
            return 0, json.dumps(self.answers[name])
        # Actions (torch, vibrate, notification, sms-send...) print nothing on
        # success, so an empty zero-exit answer is the realistic default.
        return 0, ""

    # -- device seams -------------------------------------------------------
    @staticmethod
    def _device_which(name):
        return name if name in ("getprop", "ip", "ifconfig") else None

    @staticmethod
    def _device_run(argv, timeout=5.0):
        if argv and str(argv[0]).endswith("getprop") and len(argv) > 1:
            return 0, GETPROP.get(argv[1], "")
        return -1, ""

    # -- helpers ------------------------------------------------------------
    def argv_for(self, command):
        return [call for call in self.calls if call and call[0] == command]


@pytest.fixture
def phone(monkeypatch):
    return Phone(monkeypatch)


# ---------------------------------------------------------------------------
# The scenario itself
# ---------------------------------------------------------------------------

def test_a_phone_is_recognised_and_reported_as_one(phone):
    assert device.scenario() == "termux", "the Termux signals must win over platform.system()"
    block = device.collect(sections=["device"], fresh=True)["device"]
    assert block["class"] == "phone", "nosdcard must not be read as a tablet"
    assert block["model"] == "PHY110" and block["manufacturer"] == "OPPO"
    assert block["android_release"] == "16"


# ---------------------------------------------------------------------------
# battery / network / signal
# ---------------------------------------------------------------------------

def test_battery_is_normalised_from_the_api_answer(phone):
    block = device.collect(sections=["battery"], fresh=True)["battery"]
    assert block["available"] is True
    assert block["source"] == "termux-battery-status"
    assert block["percent"] == 42
    assert block["status"] == "discharging"
    assert block["plugged"] == "unplugged"
    assert block["temperature_c"] == 31.5
    assert block["ac_online"] is False, "UNPLUGGED must not read as on the charger"


def test_wifi_signal_comes_from_the_api(phone):
    block = device.collect(sections=["network"], fresh=True)["network"]
    assert block["wifi"]["ssid"] == "Warmen5g"
    assert block["wifi"]["rssi_dbm"] == -52
    assert block["wifi"]["link_speed_mbps"] == 433
    assert block["connected_kind"] == "wifi"
    assert "Warmen5g" in block["signal"] and "-52 dBm" in block["signal"]


def test_android_placeholders_are_not_reported_as_a_network(phone):
    """Without the location permission Android answers "<unknown ssid>" and rssi -1."""
    phone.answers["termux-wifi-connectioninfo"] = WIFI_WITHOUT_PERMISSION
    block = device.collect(sections=["network"], fresh=True)["network"]
    assert block["wifi"]["ssid"] == "", "a placeholder is not a network name"
    assert block["wifi"]["rssi_dbm"] is None, "-1 is a sentinel, not a signal"
    assert block["signal"] != "wifi (hidden) -1 dBm"


def test_the_registered_cell_is_the_one_reported(phone):
    block = device.collect(sections=["network"], fresh=True)["network"]
    cellular = block["cellular"]
    assert cellular["operator"] == "Magti"
    assert cellular["network_type"] == "LTE"
    assert cellular["signal_dbm"] == -101, "a stronger neighbour is not the serving cell"
    assert cellular["level"] == 2
    assert cellular["cells_seen"] == 2
    # This phone is on Wi-Fi, so the one-line summary names the link that actually
    # carries traffic; the cellular detail above is not lost, and the next test
    # proves the summary falls back to it when there is no Wi-Fi.
    assert block["signal"] == "wifi Warmen5g -52 dBm"


def test_cellular_is_the_summary_when_there_is_no_wifi(phone):
    phone.answers.pop("termux-wifi-connectioninfo")
    block = device.collect(sections=["network"], fresh=True)["network"]
    assert block["wifi"] is None
    assert block["connected_kind"] == "cellular"
    assert block["signal"] == "Magti LTE -101 dBm (level 2)"


def test_an_empty_cell_list_is_explained_rather_than_invented(phone):
    phone.answers["termux-telephony-cellinfo"] = []
    block = device.collect(sections=["network"], fresh=True)["network"]
    cellular = block["cellular"] or {}
    assert cellular.get("signal_dbm") is None
    assert cellular.get("cells_seen") == 0


# ---------------------------------------------------------------------------
# cameras and sensors
# ---------------------------------------------------------------------------

def test_cameras_are_summarised_with_their_largest_mode(phone):
    block = device.collect(sections=["cameras"], fresh=True)["cameras"]
    assert block["count"] == 2
    back = block["cameras"][0]
    assert back["facing"] == "back"
    assert back["max_resolution"] == "4032x3024", "the biggest JPEG size is the one worth naming"


def test_sensors_are_listed_without_sampling_them(phone):
    """Listing is free; sampling costs battery, so a plain report must not sample."""
    block = device.collect(sections=["sensors"], fresh=True)["sensors"]
    assert block["sensors"] == ["acceleration", "magnetic_field", "light"]
    assert block["sampling"] == "names"
    assert phone.argv_for("termux-sensor") == [["termux-sensor", "-l"]], \
        "only the listing may run"


def test_a_named_sensor_is_sampled_once_and_never_streamed(phone):
    block = device.collect(sections=["sensors"], fresh=True, sensor="acceleration")["sensors"]
    assert block["sampling"] == "values"
    assert block["values"] == SENSOR_SAMPLE
    assert ["termux-sensor", "-s", "acceleration", "-n", "1"] in phone.argv_for("termux-sensor"), \
        "without -n 1 termux-sensor streams forever and would hang the tool"


# ---------------------------------------------------------------------------
# A phone whose Termux:API app is missing: one cause, not five failures
# ---------------------------------------------------------------------------

def test_a_silent_app_is_reported_once_and_not_as_five_broken_sections(phone):
    phone.hang = True
    device.clear_cache()
    result = mcp_tools.call_tool("device_info", {"section": "all", "fresh": True})
    assert result.get("hint"), "the model must be told the single root cause"
    assert "Termux:API" in result["hint"]
    for name in ("battery", "network", "cameras", "sensors"):
        block = result["sections"].get(name) or {}
        assert block.get("available") is False, "%s must not be fabricated" % name
        assert "percent" not in block and "count" not in block
    capabilities = termux.capabilities(refresh=True)
    assert capabilities["app_reachable"] is False
    assert "app_fix" in capabilities

def test_a_phone_without_the_package_says_what_to_install(phone, monkeypatch):
    monkeypatch.setattr(termux, "_which", lambda name: None)
    termux.reset_state()
    device.clear_cache()
    block = device.collect(sections=["battery"], fresh=True)["battery"]
    assert block["available"] is False
    assert block["fix"] == termux.TERMUX_API_FIX


# ---------------------------------------------------------------------------
# The same phone through the tools
# ---------------------------------------------------------------------------

def test_control_actions_reach_the_adapter_with_the_right_argv(phone):
    mcp_tools.configure(device="auto", device_actions=None, device_pim=False)
    torch = mcp_tools.call_tool("device_control", {"action": "torch", "on": True})
    assert torch["ok"] is True and torch["argv"] == ["on"]
    assert phone.argv_for("termux-torch") == [["termux-torch", "on"]]

    vibro = mcp_tools.call_tool("device_control", {"action": "vibrate", "value": 2500})
    assert vibro["argv"] == ["-d", "2500"]


def test_capture_writes_into_the_capture_directory(phone, tmp_path):
    mcp_tools.configure(device="auto", capture_dir=str(tmp_path / "caps"))
    photo = mcp_tools.call_tool("device_capture", {"action": "camera_photo", "camera_id": 1})
    assert photo["ok"] is True
    assert photo["argv"][0] == "-c" and photo["argv"][1] == "1"
    assert photo["path"].startswith(str(tmp_path / "caps"))
    assert (tmp_path / "caps").is_dir(), "the directory is created before the phone is asked"


def test_private_data_stays_off_until_the_operator_enables_it(phone):
    mcp_tools.configure(device="auto", device_pim=False)
    refused = mcp_tools.call_tool("device_messages", {"action": "sms_list"})
    assert refused["available"] is False
    assert "MESH_DEVICE_PIM=1" in refused["fix"]
    assert phone.argv_for("termux-sms-list") == [], "nothing may run while it is off"

    mcp_tools.configure(device_pim=True)
    allowed = mcp_tools.call_tool("device_messages", {"action": "sms_list", "limit": 3})
    assert allowed["ok"] is True
    # Every argv element is a string: the adapter normalises them before they reach
    # subprocess, which is part of "no shell, no type surprises".
    assert allowed["argv"] == ["-l", "3"]


# ---------------------------------------------------------------------------
# The payloads captured from a real phone (tests/fixtures/termux/)
# ---------------------------------------------------------------------------
# The synthetic payloads above pin behaviour; these pin FIELD NAMES, which is the
# part documentation gets wrong. Two bugs on the reference phone came from exactly
# this gap: the real answer names the Wi-Fi channel "frequency_mhz" (the parser read
# "frequency"), and a refused permission arrives as a SUCCESSFUL call whose stdout
# is {"error": "Please grant the following permission to use this command: ..."}
# with exit code 0 - which used to be read as an empty answer instead of a refusal.

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "termux")


def _fixture(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        pytest.skip("the captured fixture %s is not in the tree" % name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_captured_wifi_payload_names_its_frequency(phone):
    payload = _fixture("wifi-connectioninfo.json")
    assert "frequency_mhz" in payload, "the parser reads this exact key"
    phone.answers["termux-wifi-connectioninfo"] = payload

    block = device.collect(sections=["network"], fresh=True)["network"]
    assert block["wifi"]["frequency_mhz"] == payload["frequency_mhz"]
    assert block["wifi"]["link_speed_mbps"] == payload.get("link_speed_mbps")
    assert block["wifi"]["rssi_dbm"] == payload["rssi"]
    if payload.get("ssid") == "<unknown ssid>":
        # The capture was taken without the location permission, so Android masked
        # the network name: the placeholder must not become a network name.
        assert block["wifi"]["ssid"] == ""


def test_a_captured_permission_error_is_a_refusal_not_an_empty_answer(phone):
    """The exact body this phone sent before its location permission was granted.

    It arrived on stdout with exit code 0, so reading only the exit status turned
    "Android said no" into an empty answer. Kept as its own fixture because
    ``telephony-cellinfo.json`` now holds real cells: the refusal shape has to stay
    pinned even after the permission is granted, or the regression would come back
    silently on the next phone.
    """
    payload = _fixture("permission-denied.json")
    assert isinstance(payload, dict) and payload.get("error"), \
        "this fixture exists to pin the JSON-on-stdout refusal shape"
    phone.answers["termux-telephony-cellinfo"] = payload

    result = termux.run_json("termux-telephony-cellinfo", [], 5.0)
    assert result["ok"] is False, "exit code 0 with an error body is still a refusal"
    assert result["denied"] is True
    assert "permission" in result["fix"].lower()
    # The message names the permission, and the message wins over the command map:
    # ACCESS_COARSE_LOCATION is what has to be granted, whichever API asked for it.
    assert termux.capabilities(refresh=True)["permissions"]["location"] == "denied"


def test_the_captured_battery_payload_has_every_field_the_parser_uses(phone):
    payload = _fixture("battery-status.json")
    for key in ("percentage", "status", "plugged", "health", "temperature"):
        assert key in payload, "the battery parser reads %r" % key
    phone.answers["termux-battery-status"] = payload

    block = device.collect(sections=["battery"], fresh=True)["battery"]
    assert block["percent"] == payload["percentage"]
    assert block["temperature_c"] == payload["temperature"]
    assert block["status"] == payload["status"].lower()
    assert block["ac_online"] is False, "the capture was taken on battery power"


def test_the_captured_camera_and_sensor_payloads_match_the_parsers(phone):
    cameras = _fixture("camera-info.json")
    assert isinstance(cameras, list) and cameras
    assert "jpeg_output_sizes" in cameras[0], "the parser reads this exact key"
    phone.answers["termux-camera-info"] = cameras

    block = device.collect(sections=["cameras"], fresh=True)["cameras"]
    assert block["count"] == len(cameras)
    assert block["cameras"][0]["max_resolution"], "a mode must be named for every camera"

    sensors = _fixture("sensor-list.json")
    assert isinstance(sensors.get("sensors"), list), "the parser reads this exact key"
    phone.answers["termux-sensor"] = sensors

    block = device.collect(sections=["sensors"], fresh=True)["sensors"]
    assert block["sensors"][:2] == sensors["sensors"][:2]
    assert block["sampling"] == "names", "a plain report must never sample a sensor"


def test_the_captured_telephony_payloads_drive_the_cellular_block(phone):
    """The serving cell is the registered one, and it is the only one reported.

    The capture holds two cells: one registered (LTE, -102 dBm, level 2) and one
    that is not. Reporting the stronger neighbour would be a plausible-looking lie
    about the phone's actual signal, so the choice is pinned against real data.
    """
    device_info = _fixture("telephony-deviceinfo.json")
    cells = _fixture("telephony-cellinfo.json")
    assert {"network_operator_name", "network_type", "network_roaming",
            "data_state", "sim_state"} <= set(device_info), \
        "the cellular parser reads these exact keys"
    assert isinstance(cells, list) and any(c.get("registered") for c in cells)

    phone.answers["termux-telephony-deviceinfo"] = device_info
    phone.answers["termux-telephony-cellinfo"] = cells
    # Without Wi-Fi the cellular link is the one carrying traffic, which is also
    # what makes the one-line summary describe it.
    phone.answers.pop("termux-wifi-connectioninfo")

    block = device.collect(sections=["network"], fresh=True)["network"]
    cellular = block["cellular"]
    serving = next(cell for cell in cells if cell.get("registered"))
    assert cellular["operator"] == device_info["network_operator_name"]
    assert cellular["network_type"] == device_info["network_type"]
    assert cellular["signal_dbm"] == serving["dbm"], "a stronger neighbour is not the serving cell"
    assert cellular["level"] == serving["level"]
    assert cellular["cells_seen"] == len(cells)
    assert block["signal"].startswith(device_info["network_operator_name"])


# ---------------------------------------------------------------------------
# Two bugs the first real device report exposed
# ---------------------------------------------------------------------------
# The phone's own summary answered "cpu: 0" and "memory: 0.0/0.0 MB". Both are
# pinned here against the facts that produced them, so neither can come back.

#: What an Android 16 phone actually puts in /proc/cpuinfo: per-core entries only.
#: No "Hardware", no "model name" - verified on the OPPO PHY110.
ANDROID_CPUINFO = (
    "processor\t: 0\n"
    "BogoMIPS\t: 38.40\n"
    "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32\n"
    "CPU implementer\t: 0x41\n"
    "processor\t: 1\n"
    "BogoMIPS\t: 38.40\n"
    "CPU implementer\t: 0x41\n"
)


def test_a_phone_cpu_model_is_never_a_core_index(phone, monkeypatch):
    """`processor : 0` must not become the model, and props must supply the real one."""
    monkeypatch.setattr(device, "_read_text",
                        lambda path: ANDROID_CPUINFO if path == "/proc/cpuinfo" else "")
    monkeypatch.setattr(device, "_android_getprop",
                        lambda name: {"ro.soc.model": "SM8650-AB"}.get(name, ""))

    block = device.collect(sections=["hardware"], fresh=True)["hardware"]
    assert block["cpu"]["model"] == "SM8650-AB"
    assert block["cpu"]["model"] != "0", \
        "a core index is not a CPU model - the first phone report said 'cpu: 0'"


def test_a_phone_cpu_model_falls_back_to_the_board_when_ro_soc_is_absent(phone, monkeypatch):
    """Older Android builds answer ro.board.platform instead of ro.soc.model."""
    monkeypatch.setattr(device, "_read_text",
                        lambda path: ANDROID_CPUINFO if path == "/proc/cpuinfo" else "")
    monkeypatch.setattr(device, "_android_getprop",
                        lambda name: {"ro.board.platform": "kalama"}.get(name, ""))

    block = device.collect(sections=["hardware"], fresh=True)["hardware"]
    assert block["cpu"]["model"] == "kalama"


@pytest.mark.skipif(os.name == "nt", reason="reads the real /proc/meminfo")
def test_vitals_treat_android_as_linux(monkeypatch):
    """Python 3.13+ answers ``platform.system() == "Android"`` on a phone.

    The collector knew only Linux/Darwin/Windows, so on Android it filled neither
    branch and the phone reported 0 MB of RAM - which is what the first device report
    showed, next to a real 84 % battery.
    """
    monkeypatch.setattr(vitals.platform, "system", lambda: "Android")
    ram = vitals.get_host_vitals()["ram"]
    assert ram["total_mb"] > 0, "Android is Linux underneath: /proc/meminfo answers"
    assert ram["used_pct"] > 0
