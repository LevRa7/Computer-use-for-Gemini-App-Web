"""core/termux.py: the right command under the right situation, and nothing else.

The Termux:API adapter is the only place in the node that starts a ``termux-*``
process, so this file guards the three things that go wrong on a real phone:

* **the wrong command** - a caller's string must never become an executable, and
  a command that is not installed must be answered from ``shutil.which`` without
  starting anything;
* **the wrong options** - ``termux-sensor`` without ``-n 1`` streams forever and
  would hold a tool open until its timeout, so the sample invariant is asserted
  on the exact argv the adapter hands to the runner;
* **the wrong story** - Android freezes the Termux:API app (a hang), refuses
  permissions (a ``SecurityException``) and loses the API entirely (a missing
  command); each of those has to come back as a dict with a fix a user can act
  on, never as an exception;
* **one root cause, said once** - on a real phone with the ``termux-api`` package
  installed and the Termux:API *app* missing, every command exists and every one
  of them hangs; the adapter remembers that for a short while so a device report
  can name the single cause instead of five separate timeouts.

Nothing here touches PATH or starts a subprocess: the module's two seams,
``_which`` and ``_run``, are replaced with a fake that records ``(argv, timeout)``
and replays canned output. That keeps the suite honest on Windows and Linux, where
no ``termux-*`` command can exist.
"""

import os
import subprocess
import time

import pytest

from core import termux

#: A machine that really is a phone always answers yes to the local signal, so the
#: negative detection cases can only be asserted elsewhere (same rule as
#: tests/test_termux_support.py).
ON_ANDROID = os.path.isdir("/data/data/com.termux/files/usr/bin")

#: What the mandatory trio looks like when the API is properly installed.
FULL_TRIO = ("termux-battery-status", "termux-clipboard-get", "termux-notification")


class Phone:
    """A phone whose tools exist only in this process.

    ``which`` reports a command when it is in ``installed``, ``run`` records every
    call and answers from ``answers`` (a ``{command: (returncode, text)}`` map, or
    a replacement text for the permission tests). A command in ``hanging`` raises
    ``subprocess.TimeoutExpired`` instead, which is exactly what the real frozen
    Termux:API app does to ``subprocess.run``.
    """

    def __init__(self, installed=(), answers=None, hanging=()):
        self.installed = set(installed)
        self.answers = dict(answers or {})
        self.hanging = set(hanging)
        self.calls = []          # [(argv, timeout)]
        self.lookups = []        # every name _which was asked about

    def which(self, name):
        self.lookups.append(name)
        if name in self.installed:
            return "/data/data/com.termux/files/usr/bin/%s" % name
        return None

    def run(self, argv, timeout):
        argv = list(argv)
        self.calls.append((argv, timeout))
        if argv[0] in self.hanging:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        return self.answers.get(argv[0], (0, "{}"))

    @property
    def commands_run(self):
        return [argv[0] for argv, _timeout in self.calls]

    def install(self, monkeypatch):
        monkeypatch.setattr(termux, "_which", self.which)
        monkeypatch.setattr(termux, "_run", self.run)
        return self


@pytest.fixture(autouse=True)
def clean_adapter_state(monkeypatch):
    """No test may inherit a cache, a wake-lock count or a fake Termux environment."""
    termux.reset_state()
    for key in ("TERMUX_VERSION", "PREFIX"):
        monkeypatch.delenv(key, raising=False)
    yield
    termux.reset_state()


def not_a_phone(monkeypatch):
    """The local-path signal is false, so only the environment can say otherwise."""
    monkeypatch.setattr(termux.os.path, "isdir", lambda path: False)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, value", (
    ("TERMUX_VERSION", "0.118.0"),
    ("PREFIX", "/data/data/com.termux/files/usr"),
))
def test_a_termux_signal_names_a_phone(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert termux.is_android() is True


@pytest.mark.skipif(ON_ANDROID, reason="a real Termux always answers yes")
def test_the_application_data_directory_names_a_phone(monkeypatch):
    monkeypatch.setattr(termux.os.path, "isdir",
                        lambda path: path == "/data/data/com.termux/files/usr/bin")
    assert termux.is_android() is True


@pytest.mark.skipif(ON_ANDROID, reason="a real Termux always answers yes")
def test_a_build_system_prefix_is_not_a_phone(monkeypatch):
    """Many build systems export PREFIX; only one that points into com.termux counts."""
    monkeypatch.setenv("PREFIX", "/usr/local")
    not_a_phone(monkeypatch)
    assert termux.is_android() is False


@pytest.mark.skipif(ON_ANDROID, reason="a real Termux always answers yes")
def test_a_clean_environment_is_not_a_phone(monkeypatch):
    not_a_phone(monkeypatch)
    assert termux.is_android() is False


# ---------------------------------------------------------------------------
# The command inventory
# ---------------------------------------------------------------------------

def test_the_inventory_maps_found_and_missing_commands(monkeypatch):
    phone = Phone(installed=list(FULL_TRIO) + ["termux-sensor"]).install(monkeypatch)

    report = termux.api_installed()

    assert report["installed"] is True
    assert report["source"] == "shutil.which"
    assert report["fix"] == termux.TERMUX_API_FIX
    # Every command this project may use is reported, found or not: a model must
    # be able to see "termux-sensor yes, termux-usb no" in one answer.
    assert set(report["commands"]) == set(termux.REQUIRED_COMMANDS)
    assert report["commands"]["termux-sensor"] is True
    assert report["commands"]["termux-camera-photo"] is False
    assert "termux-camera-photo" in report["missing"]
    for name in termux.MANDATORY_COMMANDS:
        assert name not in report["missing"]
    assert "termux-sensor" not in report["missing"]
    assert phone.calls == [], "the inventory must be answered without executing anything"


def test_a_partial_install_is_not_installed(monkeypatch):
    """The trio is what "the API works" means; one command of it is not enough."""
    Phone(installed=["termux-battery-status", "termux-sensor"]).install(monkeypatch)

    report = termux.api_installed()

    assert report["installed"] is False
    assert "termux-clipboard-get" in report["missing"]
    assert "termux-notification" in report["missing"]


def test_the_inventory_is_cached_until_it_is_refreshed(monkeypatch):
    """A chatty model must not cause 44 PATH lookups per tool call."""
    phone = Phone(installed=list(FULL_TRIO)).install(monkeypatch)

    termux.api_installed()
    first = len(phone.lookups)
    termux.api_installed()
    assert len(phone.lookups) == first

    termux.api_installed(refresh=True)
    assert len(phone.lookups) == first * 2


# ---------------------------------------------------------------------------
# run_json / run_text
# ---------------------------------------------------------------------------

def test_a_json_answer_is_parsed_and_the_argv_is_reported(monkeypatch):
    phone = Phone(installed=["termux-battery-status"],
                  answers={"termux-battery-status": (0, '{"percentage": 42, "plugged": "UNPLUGGED"}')}
                  ).install(monkeypatch)

    result = termux.run_json("termux-battery-status", timeout=4.0)

    assert result["ok"] is True
    assert result["value"] == {"percentage": 42, "plugged": "UNPLUGGED"}
    assert result["raw"].startswith("{")
    assert result["command"] == "termux-battery-status"
    assert result["argv"] == ["termux-battery-status"]
    assert isinstance(result["duration_ms"], int)
    assert "shell" not in result
    assert phone.calls == [(["termux-battery-status"], 4.0)]


def test_arguments_are_stringified_and_never_split(monkeypatch):
    """An argument is one argv element: splitting text on spaces is the step that
    turns caller input into options."""
    phone = Phone(installed=["termux-toast"]).install(monkeypatch)

    termux.run_json("termux-toast", ["hello world", 42])
    termux.run_json("termux-toast", "-n 1")

    assert phone.calls == [
        (["termux-toast", "hello world", "42"], 6.0),
        (["termux-toast", "-n 1"], 6.0),
    ]


def test_non_json_output_is_still_a_success(monkeypatch):
    Phone(installed=["termux-clipboard-get"],
          answers={"termux-clipboard-get": (0, "hello from the clipboard")}).install(monkeypatch)

    result = termux.run_json("termux-clipboard-get")

    assert result["ok"] is True
    assert result["value"] == "hello from the clipboard"
    assert result["parsed"] is False
    assert result["raw"] == "hello from the clipboard"


def test_run_text_returns_stdout_and_normalises_line_endings(monkeypatch):
    Phone(installed=["termux-clipboard-get"],
          answers={"termux-clipboard-get": (0, "line one\r\nline two\r\n")}).install(monkeypatch)

    result = termux.run_text("termux-clipboard-get")

    assert result["ok"] is True
    assert result["value"] == "line one\nline two"
    assert result["raw"] == "line one\nline two"


# ---------------------------------------------------------------------------
# The allowlist, the missing command and the hang
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("refused", (
    "rm",
    "sh",
    "curl",
    "Termux-toast",          # the prefix is case-sensitive
    "/usr/bin/termux-toast",  # a path is not a command name
    "termux/../bin/sh",       # satisfies the prefix, escapes the allowlist
))
def test_anything_that_is_not_a_termux_command_is_refused(monkeypatch, refused):
    phone = Phone(installed=list(FULL_TRIO)).install(monkeypatch)

    result = termux.run_json(refused, ["-rf", "/"])

    assert result["ok"] is False
    assert result["reason"].startswith("refused:")
    assert result["argv"][0] == refused
    assert phone.calls == [], "a refused command must never reach the runner"
    assert phone.lookups == [], "a refused command must not even be looked up"


def test_refusal_covers_every_entry_point(monkeypatch):
    phone = Phone(installed=list(FULL_TRIO)).install(monkeypatch)

    assert termux.run_text("sh")["reason"].startswith("refused:")
    refused_wake = termux.wake_lock("take")
    assert refused_wake["reason"].startswith("refused:")
    assert refused_wake["command"] == ""
    assert phone.calls == []


def test_a_missing_command_fails_fast_with_the_fix(monkeypatch):
    phone = Phone(installed=[]).install(monkeypatch)

    result = termux.run_json("termux-location", ["--provider", "gps"])

    assert result["ok"] is False
    assert result["missing"] is True
    assert result["fix"] == termux.TERMUX_API_FIX
    assert "termux-location" in result["reason"]
    assert result["command"] == "termux-location"
    assert result["argv"] == ["termux-location", "--provider", "gps"]
    assert phone.calls == [], "a missing command must not be executed"
    assert phone.lookups == ["termux-location"]


def test_a_hanging_command_becomes_a_timeout_not_an_exception(monkeypatch):
    """The frozen Termux:API app: the call never returns and only the timeout ends it."""
    Phone(installed=["termux-location"], hanging={"termux-location"}).install(monkeypatch)

    result = termux.run_json("termux-location", timeout=4.0)

    assert result["ok"] is False
    assert result["timeout"] is True
    assert "termux-location timed out after 4s" == result["reason"]
    assert result["fix"] == termux.TERMUX_TIMEOUT_FIX
    assert "battery" in result["fix"].lower()


def test_a_timeout_is_also_a_dict_from_run_text_and_the_sensor(monkeypatch):
    Phone(installed=["termux-sensor"], hanging={"termux-sensor"}).install(monkeypatch)

    for result in (termux.run_text("termux-sensor"), termux.sensor_sample("acceleration")):
        assert result["ok"] is False
        assert result["timeout"] is True
        assert result["fix"]


def test_a_failing_command_reports_what_it_said(monkeypatch):
    Phone(installed=["termux-toast"], answers={"termux-toast": (1, "termux-toast: bad option -z")}
          ).install(monkeypatch)

    result = termux.run_text("termux-toast", ["-z"])

    assert result["ok"] is False
    assert result["reason"] == "termux-toast: bad option -z"
    assert result["raw"] == "termux-toast: bad option -z"
    assert "denied" not in result


# ---------------------------------------------------------------------------
# Failure text -> permission state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, command, expected_api", (
    ("Permission denied", "termux-location", "location"),
    ("android.permission.ACCESS_FINE_LOCATION not granted", "termux-location", "location"),
    ("this command requires permission", "termux-location", "location"),
    ("java.lang.SecurityException: uid 10123 does not have permission",
     "termux-location", "location"),
    ("Location services are disabled", "termux-location", "location"),
    ("User denied the request", "termux-location", "location"),
    ("open failed: No such file or directory: /dev/video0",
     "termux-camera-photo", "camera"),
    ("PERMISSION DENIED", "termux-sms-send", "sms"),
))
def test_android_refusals_map_to_denied_with_a_fix(monkeypatch, text, command, expected_api):
    Phone(installed=[command], answers={command: (1, text)}).install(monkeypatch)

    result = termux.run_json(command)

    assert result["ok"] is False
    assert result["denied"] is True
    assert result["reason"] == text
    assert expected_api in result["fix"]
    assert termux.capabilities()["permissions"][expected_api] == "denied"


@pytest.mark.parametrize("text", (
    "could not open camera: permission denied",
    "android.permission.CAMERA not granted",
))
def test_a_camera_permission_failure_is_attributed_to_the_camera(monkeypatch, text):
    Phone(installed=["termux-camera-photo"], answers={"termux-camera-photo": (1, text)}).install(monkeypatch)

    result = termux.run_json("termux-camera-photo")

    assert result["denied"] is True
    assert "camera" in result["fix"]
    assert termux.capabilities()["permissions"]["camera"] == "denied"


@pytest.mark.parametrize("text", (
    "microphone permission is required",
    "recording failed: user denied the microphone permission",
))
def test_a_microphone_permission_failure_is_attributed_to_the_microphone(monkeypatch, text):
    Phone(installed=["termux-microphone-record"],
          answers={"termux-microphone-record": (1, text)}).install(monkeypatch)

    result = termux.run_json("termux-microphone-record")

    assert result["denied"] is True
    assert termux.capabilities()["permissions"]["microphone"] == "denied"


@pytest.mark.parametrize("text", (
    "no camera found on this device",
    "camera devices: 0",
    "termux-camera-photo: unknown option",
))
def test_a_hardware_answer_is_not_a_permission_story(monkeypatch, text):
    """"No camera found" is hardware news; reporting "grant the permission" would
    send the user to a settings screen that cannot help."""
    Phone(installed=["termux-camera-photo"], answers={"termux-camera-photo": (1, text)}).install(monkeypatch)

    result = termux.run_json("termux-camera-photo")

    assert result["ok"] is False
    assert "denied" not in result


def test_the_denied_state_is_remembered_and_surfaces_in_capabilities(monkeypatch):
    Phone(installed=["termux-camera-photo"],
          answers={"termux-camera-photo": (1, "java.lang.SecurityException: Permission denied")}
          ).install(monkeypatch)

    # Populate the capabilities cache first: the observation must invalidate it,
    # or a caller keeps reading "unknown" for a minute after the phone said no.
    assert termux.capabilities()["permissions"]["camera"] == "unknown"

    result = termux.run_json("termux-camera-photo")

    assert result["denied"] is True
    capabilities = termux.capabilities()
    assert capabilities["permissions"]["camera"] == "denied"
    assert any("camera" in fix for fix in capabilities["fixes"])


def test_recording_a_permission_directly_is_validated():
    termux.record_permission_state("termux-sms-send", "granted")
    termux.record_permission_state("camera", "nonsense")

    permissions = termux.capabilities()["permissions"]

    assert permissions["sms"] == "granted", "a command name is accepted as a shorthand"
    assert permissions["camera"] == "unknown", "only the three known states are stored"


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

def test_a_sensor_sample_always_asks_for_exactly_one_value(monkeypatch):
    """``-n 1`` is the invariant: without it termux-sensor streams forever."""
    phone = Phone(installed=["termux-sensor"],
                  answers={"termux-sensor": (0, '{"acceleration": {"values": [0.0, 0.0, 9.8]}}')}
                  ).install(monkeypatch)

    result = termux.sensor_sample("acceleration")

    assert phone.calls == [(["termux-sensor", "-s", "acceleration", "-n", "1"], 8.0)]
    assert result["ok"] is True
    assert result["argv"] == ["termux-sensor", "-s", "acceleration", "-n", "1"]
    assert result["value"] == {"acceleration": {"values": [0.0, 0.0, 9.8]}}


def test_the_sensor_name_is_trimmed_but_never_reshaped(monkeypatch):
    phone = Phone(installed=["termux-sensor"]).install(monkeypatch)

    termux.sensor_sample("  acceleration  ")

    assert phone.calls[0][0] == ["termux-sensor", "-s", "acceleration", "-n", "1"]


# ---------------------------------------------------------------------------
# Wake lock
# ---------------------------------------------------------------------------

def test_the_wake_lock_is_reference_counted(monkeypatch):
    phone = Phone(installed=["termux-wake-lock", "termux-wake-unlock"]).install(monkeypatch)

    first = termux.wake_lock("acquire")
    assert (first["available"], first["held"], first["count"]) == (True, True, 1)
    second = termux.wake_lock("acquire")
    assert (second["held"], second["count"]) == (True, 2)

    status = termux.wake_lock("status")
    assert (status["held"], status["count"]) == (True, 2)
    assert status["command"] == "termux-wake-lock"
    assert phone.calls == [(["termux-wake-lock"], termux.WAKE_LOCK_TIMEOUT)], \
        "the second acquire and the status call must not touch Android"

    still_held = termux.wake_lock("release")
    assert (still_held["held"], still_held["count"]) == (True, 1), \
        "another holder is left, so the system lock must stay taken"
    assert phone.calls == [(["termux-wake-lock"], termux.WAKE_LOCK_TIMEOUT)], \
        "the unlock command belongs to the 1 -> 0 transition only"

    released = termux.wake_lock("release")
    assert (released["held"], released["count"]) == (False, 0)
    assert termux.wake_lock()["count"] == 0, "status is the default action"
    # A release with nothing held must never unlock a lock somebody else owns.
    again = termux.wake_lock("release")
    assert (again["available"], again["held"], again["count"]) == (True, False, 0)

    assert phone.calls == [
        (["termux-wake-lock"], termux.WAKE_LOCK_TIMEOUT),
        (["termux-wake-unlock"], termux.WAKE_LOCK_TIMEOUT),
    ], "one acquire and one release, exactly"
    assert termux.wake_lock("status")["count"] == 0, "the count never goes negative"


def test_a_missing_wake_lock_command_is_unknown_not_an_error(monkeypatch):
    phone = Phone(installed=[]).install(monkeypatch)

    acquired = termux.wake_lock("acquire")

    assert acquired["available"] is False
    assert acquired["held"] is None
    assert acquired["count"] == 0
    assert acquired["missing"] is True
    assert acquired["fix"] == termux.TERMUX_API_FIX
    assert termux.wake_lock("status")["held"] is None
    assert phone.calls == []


def test_a_wake_lock_that_hangs_or_fails_keeps_the_count_honest(monkeypatch):
    Phone(installed=["termux-wake-lock"], hanging={"termux-wake-lock"}).install(monkeypatch)

    hanging = termux.wake_lock("acquire")

    assert hanging["available"] is True
    assert hanging["held"] is False
    assert hanging["count"] == 0, "a lock that was never taken must not be counted"
    assert hanging["timeout"] is True
    assert hanging["fix"] == termux.TERMUX_TIMEOUT_FIX


def test_an_unknown_action_is_refused(monkeypatch):
    Phone(installed=["termux-wake-lock"]).install(monkeypatch)

    result = termux.wake_lock("take")

    assert result["available"] is False
    assert result["held"] is None
    assert result["reason"].startswith("refused:")


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------

def _storage_present(monkeypatch):
    """Pretend ``~/storage/shared`` exists, whatever platform this suite runs on."""
    monkeypatch.setattr(termux.os.path, "isdir",
                        lambda path: str(path).replace("\\", "/").endswith("storage/shared"))


def test_capabilities_merges_the_api_the_storage_and_the_wake_lock(monkeypatch):
    Phone(installed=list(FULL_TRIO) + ["termux-wake-lock", "termux-wake-unlock"]).install(monkeypatch)
    _storage_present(monkeypatch)

    capabilities = termux.capabilities()

    assert capabilities["api"]["installed"] is True
    assert capabilities["storage_permission"] is True
    assert capabilities["wake_lock"] == {"command": True, "held": False, "count": 0}
    assert set(capabilities["permissions"]) == set(termux.PERMISSION_APIS)
    assert all(state == "unknown" for state in capabilities["permissions"].values())
    assert capabilities["fixes"] == []


def test_capabilities_names_the_fixes_a_user_has_to_apply(monkeypatch):
    Phone(installed=[]).install(monkeypatch)
    monkeypatch.setattr(termux.os.path, "isdir", lambda path: False)

    capabilities = termux.capabilities()

    assert capabilities["api"]["installed"] is False
    assert capabilities["storage_permission"] is False
    assert capabilities["wake_lock"]["command"] is False
    assert termux.TERMUX_API_FIX in capabilities["fixes"]
    assert any("termux-setup-storage" in fix for fix in capabilities["fixes"])
    assert len(capabilities["fixes"]) == len(set(capabilities["fixes"])), "fixes are deduplicated"


def test_capabilities_is_cached_but_refresh_bypasses_the_cache(monkeypatch):
    phone = Phone(installed=list(FULL_TRIO)).install(monkeypatch)
    _storage_present(monkeypatch)

    termux.capabilities()
    lookups = len(phone.lookups)
    termux.capabilities()
    assert len(phone.lookups) == lookups

    termux.capabilities(refresh=True)
    assert len(phone.lookups) > lookups


def test_reset_state_forgets_everything(monkeypatch):
    Phone(installed=list(FULL_TRIO) + ["termux-wake-lock", "termux-wake-unlock"]).install(monkeypatch)
    _storage_present(monkeypatch)

    termux.capabilities()
    termux.record_permission_state("camera", "denied")
    termux.wake_lock("acquire")

    termux.reset_state()

    assert termux.capabilities()["permissions"]["camera"] == "unknown"
    assert termux.wake_lock("status")["count"] == 0


def test_a_broken_probe_never_escapes(monkeypatch):
    """The contract is "a public function returns a dict", even when a seam lies."""
    def explode(name):
        raise RuntimeError("shutil.which is having a bad day")

    monkeypatch.setattr(termux, "_which", explode)

    assert termux.api_installed(refresh=True)["installed"] is False
    assert termux.capabilities(refresh=True)["api"]["installed"] is False
    assert termux.wake_lock("acquire")["available"] is False


# ---------------------------------------------------------------------------
# "The app is not installed": one root cause, reported once
# ---------------------------------------------------------------------------
#
# Real-device evidence (OPPO PHY110, Android 16, termux-api 0.60.0): with the
# package installed and the Termux:API **app** missing, every termux-* command
# exists and every one of them hangs. device_info then pays one timeout per
# section and the model reads five unrelated "timed out" blocks. The adapter
# therefore remembers the hang and reports the shared cause once.

def test_a_fresh_adapter_does_not_claim_the_app_is_broken(monkeypatch):
    Phone(installed=list(FULL_TRIO)).install(monkeypatch)

    capabilities = termux.capabilities()

    assert capabilities["app_reachable"] is None, "no evidence is not the same as broken"
    assert "app_fix" not in capabilities


def test_one_hang_marks_the_app_unreachable_with_its_fix(monkeypatch):
    Phone(installed=["termux-location"], hanging={"termux-location"}).install(monkeypatch)

    result = termux.run_json("termux-location", timeout=1.0)

    assert result["timeout"] is True
    capabilities = termux.capabilities()
    assert capabilities["app_reachable"] is False
    assert capabilities["app_fix"] == termux.TERMUX_TIMEOUT_FIX
    # The keys the device report merges must survive the addition.
    for key in ("api", "storage_permission", "wake_lock", "permissions", "fixes"):
        assert key in capabilities


def test_an_answered_call_clears_the_hang_evidence(monkeypatch):
    """The operator opened (or installed) the app: the phone must stop saying "broken"."""
    Phone(installed=["termux-location", "termux-battery-status"],
          hanging={"termux-location"},
          answers={"termux-battery-status": (0, '{"percentage": 42}')}).install(monkeypatch)

    termux.run_json("termux-location", timeout=1.0)
    assert termux.capabilities()["app_reachable"] is False

    answered = termux.run_json("termux-battery-status")
    assert answered["ok"] is True

    capabilities = termux.capabilities()
    assert capabilities["app_reachable"] is True
    assert "app_fix" not in capabilities


def test_the_newest_evidence_wins(monkeypatch):
    Phone(installed=["termux-location", "termux-battery-status"],
          hanging={"termux-location"},
          answers={"termux-battery-status": (0, '{"percentage": 42}')}).install(monkeypatch)

    termux.run_json("termux-battery-status")
    assert termux.capabilities()["app_reachable"] is True

    termux.run_json("termux-location", timeout=1.0)

    capabilities = termux.capabilities()
    assert capabilities["app_reachable"] is False
    assert capabilities["app_fix"] == termux.TERMUX_TIMEOUT_FIX


def test_a_hang_older_than_the_ttl_is_no_evidence(monkeypatch):
    """Stale evidence is worse than none: the phone may have been fixed since."""
    Phone(installed=["termux-location"], hanging={"termux-location"}).install(monkeypatch)

    termux.run_json("termux-location", timeout=1.0)
    assert termux.capabilities()["app_reachable"] is False

    monkeypatch.setattr(termux, "_APP_TIMEOUT_AT",
                        time.monotonic() - termux.APP_EVIDENCE_TTL - 1.0)

    capabilities = termux.capabilities(refresh=True)
    assert capabilities["app_reachable"] is None
    assert "app_fix" not in capabilities


def test_only_an_answered_call_counts_as_evidence(monkeypatch):
    """A non-zero exit is not proof that the app is alive.

    "Permission denied" may well mean the app answered, but it is not the
    positive evidence this flag is defined by, so it must not silently clear a
    hang - the short TTL is what retires that record instead.
    """
    Phone(installed=["termux-location", "termux-camera-photo"],
          hanging={"termux-location"},
          answers={"termux-camera-photo": (1, "Permission denied")}).install(monkeypatch)

    termux.run_json("termux-location", timeout=1.0)
    termux.run_json("termux-camera-photo")

    assert termux.capabilities()["app_reachable"] is False


def test_reset_state_forgets_the_app_evidence(monkeypatch):
    Phone(installed=["termux-location", "termux-battery-status"],
          hanging={"termux-location"},
          answers={"termux-battery-status": (0, '{"percentage": 42}')}).install(monkeypatch)

    termux.run_json("termux-location", timeout=1.0)
    assert termux.capabilities()["app_reachable"] is False
    termux.reset_state()
    assert termux.capabilities()["app_reachable"] is None, "a hang must not survive a reset"

    termux.run_json("termux-battery-status")
    assert termux.capabilities()["app_reachable"] is True
    termux.reset_state()
    assert termux.capabilities()["app_reachable"] is None, "a success must not survive a reset"
    assert "app_fix" not in termux.capabilities()
