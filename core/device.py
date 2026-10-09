#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/device.py - telemetry of the device this node runs on, on every platform.

One collector API, several sources:

* **Android / Termux** - the ``termux-api`` commands (through :mod:`core.termux`
  when it is present), ``/proc``, ``/sys`` and ``getprop``;
* **Linux** - ``/sys/class/power_supply``, ``/proc``, ``/sys``, ``iw``/``nmcli``;
* **Windows** - ``ctypes`` and ``winreg`` where that is enough (battery, memory,
  locale, CPU name), PowerShell CIM only where it is not (network, cameras,
  microphones);
* **macOS** - ``pmset``, ``sysctl``, ``airport``, ``system_profiler``.

One resolver, four scenarios, one table:

* :func:`scenario` is the *only* place that decides which of the four code paths
  this node takes - ``os.name`` first (a phone is never Windows), then the
  Termux signals, then Darwin, then Linux as the fallback;
* ``_SECTION_IMPL`` maps every section to one implementation per scenario, so
  "what runs on a phone" is a lookup instead of a trail of ``if``s, and a
  scenario with no source for a section says *why* instead of borrowing another
  platform's answer;
* :func:`scenario_report`, :func:`matrix_problems` and :func:`scenario_commands`
  turn that matrix into something a tool, a bug report and CI can check: the
  tools a scenario is allowed to run are data attached to the implementations
  themselves, never a second hand-written list.

Contract (the whole point of this module):

* **never raises** - every collector returns a block, and a block that has no
  answer says ``{"available": False, "reason": ...}`` instead of a fabricated
  zero. A model must never read "0 %" where the truth is "this platform does not
  expose it";
* every block carries ``source`` - where the number came from;
* collectors run under **one deadline** in a thread pool, so one slow source
  (``system_profiler``, a frozen Termux:API app) cannot stall ``system_info``;
* results are cached for a few seconds; ``fresh=True`` bypasses the cache. The
  cache key is ``(section, scenario)``: a block collected for another scenario
  would be a lie about this machine;
* every section x scenario pair exists (``matrix_problems() == []``), and a
  scenario runs only the external tools its implementations declare
  (``scenario_commands()``).

Sections and their fields::

    device        class, model, manufacturer, os, release, arch, android_*,
                  termux_api, termux_version
    battery       percent, status, plugged, health, temperature_c, current_ua,
                  time_remaining_s, ac_online
    network       interfaces[{name, kind, up, macs, ipv4, ipv6}], default_route,
                  wifi{ssid, bssid, rssi_dbm, link_speed_mbps, frequency_mhz} | None,
                  cellular{operator, network_type, signal_dbm, level, signal_pct,
                           roaming, data_state, sim_state} | None,
                  signal (one-line summary for a model)
    locale        language, languages[], region, encoding
    time          iso_local, utc, epoch, utc_offset, utc_offset_minutes, timezone
    hardware      cpu{model, cores, logical, freq_mhz_cur[], freq_mhz_max, load,
                      temp_c}, memory{total_mb, used_mb, free_mb, used_pct, swap_*},
                  thermal[{name, value, unit}]
    storage       root, total_gb, free_gb, used_pct, shared, capture_dir
    cameras       [{id, facing, max_resolution, ...}], count
    microphones   [{name, kind}], count
    sensors       [{name, value?, unit?}], sampling ("names" | "values")
    capabilities  per-API availability on a phone, plus which sections answer

Consumers: :mod:`core.mcp_tools` (``system_info``, ``mesh_status``,
``system_vitals`` and the ``device_*`` tools). Nothing here imports
``mcp_tools`` - the dependency points one way only.
"""

from __future__ import annotations

import glob as _glob
import json
import locale as _locale
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Host mechanics, *not* the scenario decision: these say what this interpreter
#: runs on (which code page a child writes, whether a console window would flash),
#: while :func:`scenario` says which collector path to take.
_IS_WINDOWS = os.name == "nt"
_SYSTEM = platform.system()
_IS_DARWIN = _SYSTEM == "Darwin"
#: Windows: keep console windows from flashing for every probe.
_POPEN_FLAGS = 0x08000000 if _IS_WINDOWS else 0

#: Every section ``collect()`` knows about, in the order a report reads best.
SECTIONS: Tuple[str, ...] = (
    "device", "battery", "network", "locale", "time", "hardware",
    "storage", "cameras", "microphones", "sensors",
)

#: Sections that answer from local files in milliseconds on every platform.
#: ``system_info`` uses this set: it is the "cheap and always interesting" half,
#: while ``device_info`` can ask for everything.
CHEAP_SECTIONS: Tuple[str, ...] = (
    "device", "battery", "locale", "time", "hardware", "storage",
)

#: Sections that need an external tool, a permission or a slow profiler. They are
#: still part of ``system_info`` by default, but an operator who wants a status
#: call to stay instant sets ``MESH_DEVICE_QUICK=1`` and only these are skipped.
SLOW_SECTIONS: Tuple[str, ...] = ("network", "cameras", "microphones", "sensors")

#: The full report ``device_info(section="all")`` returns.
ALL_SECTIONS: Tuple[str, ...] = SECTIONS + ("capabilities",)

#: The four code paths this node can take. Exactly one is in effect per process,
#: and :func:`scenario` is the only function allowed to decide which one: a
#: second decision point is how a Linux box ends up answering like a phone.
SCENARIOS: Tuple[str, ...] = ("termux", "linux", "windows", "darwin")

#: Per-section cache lifetime in seconds. Battery and load move; a camera list
#: does not.
_CACHE_TTL: Dict[str, float] = {
    "device": 60.0,
    "battery": 5.0,
    "network": 5.0,
    "locale": 30.0,
    "time": 1.0,
    "hardware": 3.0,
    "storage": 10.0,
    "cameras": 60.0,
    "microphones": 60.0,
    "sensors": 30.0,
    "capabilities": 60.0,
}

#: Default wall-clock budget for one ``collect()`` call, in seconds. Both the
#: pool and each future respect it.
DEFAULT_DEADLINE = 8.0
#: Default budget for one external command.
DEFAULT_COMMAND_TIMEOUT = 5.0

#: Told to the model whenever a ``termux-*`` command is missing: the Android app
#: is a separate install from the package, and only both together answer.
TERMUX_API_FIX = ("install the Termux:API app (F-Droid or GitHub, the *same* store "
                  "as Termux) and run `pkg install termux-api` in Termux")

#: Termux's fixed application data directory: the third detection signal, and the
#: only one that still identifies a phone with a scrubbed environment (a ``su``
#: session, a service started by ``Termux:Boot``).
_TERMUX_DATA_DIR = "/data/data/com.termux/files/usr/bin"

_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()

_CAPTURE_DIR: Optional[str] = None
_MAX_LIST = 12


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_DECODINGS: Optional[List[str]] = None


def _windows_code_pages() -> List[str]:
    """Code pages a Windows child may write with: OEM console CP first, then ANSI."""
    pages: List[str] = []
    try:
        import ctypes

        for getter in ("GetOEMCP", "GetACP"):
            try:
                code_page = int(getattr(ctypes.windll.kernel32, getter)())
            except Exception:
                continue
            if code_page > 0:
                pages.append("cp%d" % code_page)
    except Exception:
        pass
    return pages


def _decodings() -> List[str]:
    """Encodings to try for captured child output, most likely first.

    Order matters, because every single-byte code page "succeeds" at decoding
    anything: UTF-8 is what Termux, git and PowerShell 7 write, the **OEM** code
    page is what cmd.exe, Windows PowerShell and the native console tools behind
    it write (cp866 on a Russian Windows - trying ANSI first turned every
    localized device name into mojibake), and ANSI is the last resort.
    """
    global _DECODINGS
    if _DECODINGS is not None:
        return _DECODINGS
    candidates = ["utf-8"]
    if _IS_WINDOWS:
        candidates.extend(_windows_code_pages())
    else:
        try:
            candidates.append(_locale.getpreferredencoding(False))
        except Exception:
            pass
    candidates.append("cp1252")
    unique: List[str] = []
    seen = set()
    for name in candidates:
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    _DECODINGS = unique
    return unique


def _decode(data: Any) -> str:
    """Decode captured bytes into text without mojibake (see :func:`_decodings`)."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, (bytes, bytearray)):
        for encoding in _decodings():
            try:
                # Line endings are normalised, so a Windows child's \r\n cannot
                # leave a stray \r that breaks a `$`-anchored regex further down.
                return (bytes(data).decode(encoding).replace("\r\n", "\n")
                        .replace("\r", "\n").strip())
            except (UnicodeDecodeError, LookupError):
                continue
        return bytes(data).decode("utf-8", "replace").replace("\r\n", "\n").strip()
    return str(data).strip()


def _run(argv: Sequence[str], timeout: float = DEFAULT_COMMAND_TIMEOUT) -> Tuple[int, str]:
    """Run one command with no shell, no stdin and a hard timeout.

    Returns ``(returncode, output)``; ``(-1, "")`` means "could not run at all".
    Nothing here raises: a missing tool is a normal answer on a phone.
    """
    try:
        proc = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.2, float(timeout)),
            creationflags=_POPEN_FLAGS,
        )
    except Exception:
        return -1, ""
    text = _decode(proc.stdout) or _decode(proc.stderr)
    return int(proc.returncode), text


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except Exception:
        return ""


def _read_int(path: str) -> Optional[int]:
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text.split()[0])
    except Exception:
        return None


def _which(name: str) -> Optional[str]:
    try:
        return shutil.which(name)
    except Exception:
        return None


def _block(source: str, **fields: Any) -> Dict[str, Any]:
    block: Dict[str, Any] = {"available": True, "source": source}
    block.update(fields)
    return block


def _unavailable(reason: str, source: str = "", fix: str = "", **fields: Any) -> Dict[str, Any]:
    block: Dict[str, Any] = {"available": False, "reason": reason}
    if source:
        block["source"] = source
    if fix:
        block["fix"] = fix
    block.update(fields)
    return block


def _runs(*commands: str):
    """Declare the external commands one implementation is allowed to execute.

    The list lives on the function object instead of in a second table, so
    :func:`scenario_commands` cannot drift from the code that actually runs: an
    implementation that starts calling a new tool without declaring it fails the
    scenario tests, and a scenario can never quietly inherit another platform's
    tools.
    """
    def decorate(func):
        existing = tuple(getattr(func, "commands", ()))
        func.commands = tuple(dict.fromkeys(existing + commands))  # type: ignore[attr-defined]
        return func
    return decorate


def _safe_call(producer, source: str = "") -> Dict[str, Any]:
    """Run one collector so that a broken one is still an answer.

    The contract is that nothing raises: a collector that throws, or that returns
    something which is not a block, becomes ``available: False`` with the cause
    named in ``reason``.
    """
    name = source or getattr(producer, "__name__", "collector")
    try:
        value = producer()
    except Exception as exc:                      # a collector must never escape
        return _unavailable("%s: %s" % (type(exc).__name__, exc), source=name)
    if not isinstance(value, dict):
        return _unavailable("collector %s returned %r" % (name, type(value).__name__),
                            source=name)
    return value


def _cached(name: str, producer, fresh: bool = False) -> Dict[str, Any]:
    """Memoize one collector for ``_CACHE_TTL[name]`` seconds, per scenario.

    The key is ``(section, scenario)``, not the section alone: the environment
    can change inside one process (a test, a wrapper that exports
    ``TERMUX_VERSION``, a node that re-reads its configuration), and a block
    collected for another scenario would be a lie about *this* machine.
    """
    ttl = _CACHE_TTL.get(name, 5.0)
    key = (name, scenario())
    now = time.monotonic()
    if not fresh:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = _safe_call(producer, source=name)
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def clear_cache() -> None:
    """Drop every cached block (used by tests and by ``fresh=True`` paths)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def configure(capture_dir: Optional[str] = None) -> None:
    """Set module state that comes from ``core.mcp_tools.configure()``."""
    global _CAPTURE_DIR
    _CAPTURE_DIR = str(capture_dir) if capture_dir else None


# ---------------------------------------------------------------------------
# Platform detection: the one place that decides which code path runs
# ---------------------------------------------------------------------------

def _termux_data_dir() -> bool:
    """True when Termux's fixed application data directory exists.

    The only Termux signal that survives a scrubbed environment, and the one a
    phone always has. It is a separate function so a test can decide the answer
    without depending on the host's filesystem or on running on Android.
    """
    try:
        return os.path.isdir(_TERMUX_DATA_DIR)
    except Exception:
        return False


def _platform_signals() -> Dict[str, Any]:
    """The raw evidence :func:`scenario` reads - no interpretation, no cache."""
    return {
        "TERMUX_VERSION": os.environ.get("TERMUX_VERSION", ""),
        "PREFIX": os.environ.get("PREFIX", ""),
        "termux_data_dir": _termux_data_dir(),
        "platform_system": platform.system(),
    }


def scenario() -> str:
    """The single place that decides which code path this node must take.

    The order is the contract, not a preference:

    1. ``os.name == "nt"`` wins outright. A phone is never Windows, and a
       Windows host that happens to export ``TERMUX_VERSION`` (a CI matrix, a
       wrapper script, a WSL environment leaking through) must still take the
       Windows path;
    2. Termux is checked next, because a phone reports ``Linux`` from
       ``platform.system()`` and would otherwise be mistaken for a desktop;
    3. Darwin, because macOS is POSIX too but owns its tools;
    4. Linux is the fallback, which is what a server, a container and an
       unrecognised POSIX host all are.

    Nothing is cached: the answer costs a few environment reads and one
    ``os.path.isdir``, and a cached answer would make a node that was restarted
    with a changed environment - and every scenario test - lie.
    """
    if os.name == "nt":
        return "windows"
    signals = _platform_signals()
    if (signals["TERMUX_VERSION"] or "com.termux" in signals["PREFIX"]
            or signals["termux_data_dir"]):
        return "termux"
    if signals["platform_system"] == "Darwin":
        return "darwin"
    return "linux"


def scenario_report() -> Dict[str, Any]:
    """Why the resolver answered what it answered, for tools and bug reports.

    ``signals`` is deliberately raw (the environment values as they are, the
    filesystem fact, the platform string) so a user can see which signal decided
    it, and ``checked`` names the branches in the order :func:`scenario` tried
    them.
    """
    return {
        "scenario": scenario(),
        "platform": platform.system(),
        "os_name": os.name,
        "signals": _platform_signals(),
        "checked": ["windows", "termux", "darwin", "linux"],
    }


def is_termux() -> bool:
    """True on an Android phone running Termux.

    A thin read of :func:`scenario`, kept because callers outside this module ask
    the yes/no question. It is deliberately *not* a second detection path: the
    signals are the same ones ``core.domain.is_termux()`` uses (``TERMUX_VERSION``,
    ``$PREFIX`` inside ``com.termux``, the application data directory), and
    ``tests/test_device_scenarios.py`` pins the two answers together.
    """
    return scenario() == "termux"


def _android_getprop(name: str) -> str:
    getprop = _which("getprop") or "/system/bin/getprop"
    rc, text = _run([getprop, name], timeout=2.0)
    return text if rc == 0 else ""


def _termux_adapter():
    """Return :mod:`core.termux` when this checkout has it, else ``None``."""
    try:
        from core import termux  # type: ignore
        return termux
    except Exception:
        return None


def _termux_json(command: str, args: Sequence[str] = (), timeout: float = DEFAULT_COMMAND_TIMEOUT,
                 adapter=None) -> Dict[str, Any]:
    """Run one ``termux-*`` command and return ``{"ok", "value"|"reason", ...}``.

    ``core.termux`` is the full adapter (capabilities, wake lock, permission
    mapping) and is preferred; the local path exists so this module still reports
    a phone correctly in a checkout that has not got that file.
    """
    adapter = adapter if adapter is not None else _termux_adapter()
    if adapter is not None:
        try:
            return adapter.run_json(command, args, timeout)
        except Exception as exc:
            return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    exe = _which(command)
    if not exe:
        return {"ok": False, "reason": "the %s command is not installed" % command,
                "fix": TERMUX_API_FIX, "missing": True}
    rc, text = _run([exe] + [str(a) for a in args], timeout=timeout)
    if rc != 0:
        return {"ok": False, "reason": text or ("%s exited with %d" % (command, rc))}
    if not text:
        return {"ok": False, "reason": "%s returned nothing" % command}
    try:
        return {"ok": True, "value": json.loads(text), "raw": text}
    except Exception:
        return {"ok": True, "value": text, "raw": text}


def _cmd_report(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a termux result into the fields a block needs (``fix`` included)."""
    if result.get("ok"):
        return {"value": result.get("value")}
    fields = {"reason": result.get("reason") or "the command did not answer"}
    if result.get("fix"):
        fields["fix"] = result["fix"]
    return fields


# ---------------------------------------------------------------------------
# device: what kind of machine is this
# ---------------------------------------------------------------------------

def _windows_registry(path: str, name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value or "").strip()
    except Exception:
        return ""


def _linux_dmi(name: str) -> str:
    return _read_text("/sys/devices/virtual/dmi/id/%s" % name)


def _classify(has_battery: bool) -> str:
    """phone | tablet | laptop | desktop | server - from cheap local evidence.

    Android answers this itself. Elsewhere a battery means a portable machine; a
    machine with no battery and no desktop session is a server, which is what a
    VPS is and what the model should assume before suggesting GUI work. The
    branch is on :func:`scenario` so a Windows box is never asked for a Linux
    chassis file, and a Mac without a battery is never called a server (macOS has
    no XDG desktop session to read).
    """
    case = scenario()
    if case == "termux":
        characteristics = (_android_getprop("ro.build.characteristics") or "").lower()
        return "tablet" if "tablet" in characteristics else "phone"
    if case in ("windows", "darwin"):
        return "laptop" if has_battery else "desktop"
    if has_battery:
        chassis = _read_int("/sys/class/dmi/id/chassis_type")
        if chassis in (30, 31):
            return "tablet"
        return "laptop"
    desktop_session = (os.environ.get("XDG_CURRENT_DESKTOP")
                       or os.environ.get("DESKTOP_SESSION")
                       or os.environ.get("DISPLAY")
                       or os.environ.get("WAYLAND_DISPLAY"))
    return "desktop" if desktop_session else "server"


def _device_base_fields() -> Dict[str, Any]:
    """The fields every platform answers without a subprocess."""
    return {
        "os": platform.platform(),
        "release": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
    }


@_runs("getprop")
def _device_termux() -> Dict[str, Any]:
    """A phone: the hardware names are Android system properties."""
    fields = _device_base_fields()
    fields.update({
        "model": _android_getprop("ro.product.model") or _android_getprop("ro.product.device"),
        "manufacturer": _android_getprop("ro.product.manufacturer"),
        "android_release": _android_getprop("ro.build.version.release"),
        "android_sdk": _android_getprop("ro.build.version.sdk"),
        "characteristics": _android_getprop("ro.build.characteristics"),
        "abi": _android_getprop("ro.product.cpu.abi") or platform.machine(),
        "termux_version": os.environ.get("TERMUX_VERSION", ""),
        "prefix": os.environ.get("PREFIX", ""),
    })
    adapter = _termux_adapter()
    if adapter is not None:
        try:
            fields["termux_api"] = adapter.api_installed()
        except Exception:
            fields["termux_api"] = {"installed": bool(_which("termux-battery-status"))}
    else:
        fields["termux_api"] = {"installed": bool(_which("termux-battery-status")),
                                "commands": {}}
    fields["class"] = _classify(True)              # a phone always has a battery
    return _block("getprop + termux-api", **fields)


def _device_windows() -> Dict[str, Any]:
    """A Windows box: the model lives in the registry, not in a file."""
    fields = _device_base_fields()
    fields.update({
        "model": _windows_registry(
            r"HARDWARE\DESCRIPTION\System\BIOS", "SystemProductName"),
        "manufacturer": _windows_registry(
            r"HARDWARE\DESCRIPTION\System\BIOS", "SystemManufacturer"),
    })
    fields["class"] = _classify(_battery_present())
    return _block("winreg + platform", **fields)


@_runs("sysctl", "pmset")
def _device_darwin() -> Dict[str, Any]:
    """A Mac: one sysctl names the hardware model, pmset says portable or not."""
    fields = _device_base_fields()
    _rc, model = _run(["sysctl", "-n", "hw.model"], timeout=2.0)
    fields.update({"model": model, "manufacturer": "Apple"})
    fields["class"] = _classify(_battery_present())
    return _block("sysctl + platform", **fields)


def _device_linux() -> Dict[str, Any]:
    """A PC or a server: the DMI tables under /sys name the hardware."""
    fields = _device_base_fields()
    fields.update({
        "model": _linux_dmi("product_name") or _linux_dmi("product_version"),
        "manufacturer": _linux_dmi("sys_vendor"),
    })
    fields["class"] = _classify(_battery_present())
    return _block("/sys/devices/virtual/dmi/id + platform", **fields)


def _battery_present() -> bool:
    """Cheaply answered question: does this machine have a battery at all?"""
    case = scenario()
    if case == "termux":
        return True
    if case == "windows":
        return _windows_power_status().get("BatteryFlag") not in (128, 255, None)
    if case == "darwin":
        _rc, text = _run(["pmset", "-g", "batt"], timeout=3.0)
        return "InternalBattery" in text
    return bool(_glob.glob("/sys/class/power_supply/BAT*"))


# ---------------------------------------------------------------------------
# battery
# ---------------------------------------------------------------------------

def _windows_power_status() -> Dict[str, Any]:
    """``GetSystemPowerStatus`` de-duplicated: one call for battery and AC.

    No PowerShell, no CIM, no console window - which matters because
    ``system_info`` runs on every node and this is the one value a phone user
    looks at first.
    """
    if scenario() != "windows":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return {}
        percent: Optional[int] = int(status.BatteryLifePercent)
        if percent == 255:
            percent = None
        ac: Optional[bool]
        if status.ACLineStatus == 1:
            ac = True
        elif status.ACLineStatus == 0:
            ac = False
        else:
            ac = None
        flags = int(status.BatteryFlag)
        return {
            "BatteryFlag": flags,
            "percent": percent,
            "ac_online": ac,
            "charging": bool(flags & 8) if flags not in (128, 255) else None,
            "no_battery": flags in (128, 255),
            "time_remaining_s": (int(status.BatteryLifeTime)
                                 if status.BatteryLifeTime not in (0xFFFFFFFF, 0) else None),
        }
    except Exception:
        return {}


def _linux_battery() -> Optional[Dict[str, Any]]:
    base = "/sys/class/power_supply"
    if not os.path.isdir(base):
        return None
    for entry in sorted(os.listdir(base)):
        if not entry.upper().startswith("BAT"):
            continue
        path = os.path.join(base, entry)
        percent = _read_int(os.path.join(path, "capacity"))
        if percent is None:
            # Some kernels expose only charge_now/charge_full.
            now = _read_int(os.path.join(path, "charge_now"))
            full = _read_int(os.path.join(path, "charge_full"))
            if now is not None and full:
                percent = int(round(100.0 * now / full))
        temp = _read_int(os.path.join(path, "temp"))
        current = _read_int(os.path.join(path, "current_now"))
        power = _read_int(os.path.join(path, "power_now"))
        ac_online = None
        for ac in sorted(os.listdir(base)):
            upper = ac.upper()
            if upper.startswith("AC") or upper.startswith("ADP") or upper == "ACAD":
                online = _read_int(os.path.join(base, ac, "online"))
                if online is not None:
                    ac_online = bool(online)
                    break
        return {
            "percent": percent,
            "status": _read_text(os.path.join(path, "status")).lower(),
            "health": _read_text(os.path.join(path, "health")).lower(),
            "temperature_c": round(temp / 10.0, 1) if temp is not None else None,
            "current_ua": current if current is not None else None,
            "power_uw": power if power is not None else None,
            "ac_online": ac_online,
            "name": entry,
        }
    return None


def _darwin_battery() -> Optional[Dict[str, Any]]:
    rc, text = _run(["pmset", "-g", "batt"], timeout=4.0)
    if rc != 0 or "InternalBattery" not in text:
        return None
    percent = None
    match = re.search(r"(\d{1,3})%", text)
    if match:
        percent = int(match.group(1))
    # The status is the word right after the percentage. Scanning for a bare word
    # is not enough: "discharging" contains "charging", so the shorter word won and
    # a Mac running on its battery was reported as "charging".
    status = ""
    match = re.search(r"\d{1,3}%;\s*([^;]+)", text)
    if match:
        status = match.group(1).strip().lower()
    if not status:
        for word in ("finishing charge", "discharging", "charging", "charged"):
            if word in text:
                status = word
                break
    remaining = None
    match = re.search(r"(\d+):(\d{2})\s+remaining", text)
    if match:
        remaining = int(match.group(1)) * 3600 + int(match.group(2)) * 60
    return {
        "percent": percent,
        "status": status,
        "health": "",
        "temperature_c": None,
        "ac_online": ("AC Power" in text) or None,
        "time_remaining_s": remaining,
    }


@_runs("termux-battery-status")
def _battery_termux() -> Dict[str, Any]:
    """A phone: the Termux:API app is the only battery source that exists."""
    result = _termux_json("termux-battery-status", timeout=4.0)
    if not result.get("ok"):
        return _unavailable(**{
            "reason": result.get("reason") or "termux-battery-status did not answer",
            **({"fix": result["fix"]} if result.get("fix") else {}),
        })
    value = result.get("value")
    if not isinstance(value, dict):
        return _unavailable("termux-battery-status answered %r" % (value,),
                            source="termux-battery-status")
    percent = value.get("percentage")
    plugged = str(value.get("plugged") or "").upper()
    return _block(
        "termux-battery-status",
        percent=int(percent) if isinstance(percent, (int, float)) else None,
        status=str(value.get("status") or "").lower(),
        plugged=plugged.lower(),
        health=str(value.get("health") or "").lower(),
        temperature_c=value.get("temperature"),
        current_ua=value.get("current"),
        ac_online=None if not plugged else plugged != "UNPLUGGED",
    )


def _battery_linux() -> Dict[str, Any]:
    """A Linux host: ``/sys/class/power_supply`` or an honest "no battery"."""
    value = _linux_battery()
    if value is None:
        return _unavailable("no BAT* device under /sys/class/power_supply",
                            source="/sys/class/power_supply")
    return _block("/sys/class/power_supply", **value)


def _battery_windows() -> Dict[str, Any]:
    """Windows: ``GetSystemPowerStatus``, and a reason when there is no battery."""
    power = _windows_power_status()
    if not power or power.get("no_battery"):
        return _unavailable("no system battery detected (desktop or virtual machine)",
                            source="GetSystemPowerStatus")
    return _block(
        "GetSystemPowerStatus",
        percent=power.get("percent"),
        # GetSystemPowerStatus has no "full" state: with the charger connected
        # and the charging flag clear, the honest answer is "not charging".
        status=("charging" if power.get("charging")
                else ("not charging" if power.get("ac_online") else "discharging")),
        plugged="ac" if power.get("ac_online") else "battery",
        health="",
        temperature_c=None,
        time_remaining_s=power.get("time_remaining_s"),
        ac_online=power.get("ac_online"),
    )


@_runs("pmset")
def _battery_darwin() -> Dict[str, Any]:
    """A Mac: ``pmset -g batt``, which reports InternalBattery or nothing."""
    value = _darwin_battery()
    if value is None:
        return _unavailable("no battery reported by pmset", source="pmset -g batt")
    return _block("pmset -g batt", **value)


# ---------------------------------------------------------------------------
# network: interfaces, default route, wifi, cellular
# ---------------------------------------------------------------------------

def _interface_kind(name: str) -> str:
    """Classify an interface from its name *or* its Windows description.

    Windows reports adapters by marketing name ("Intel(R) Wi-Fi 6 AX201 160MHz"),
    so prefix matching alone is not enough there; the substrings below cover both
    spellings without inventing a kind for an unknown adapter.
    """
    lowered = name.lower()
    if lowered == "lo" or lowered.startswith("lo:"):
        return "loopback"
    if lowered.startswith(("wlan", "wlp", "wifi", "wl")) or any(
            token in lowered for token in ("wi-fi", "wireless", "802.11")):
        return "wifi"
    if lowered.startswith(("rmnet", "ccmni", "pdp", "wwan", "wwp", "usb0", "rndis")) or any(
            token in lowered for token in ("mobile broadband", "cellular", "lte", "5g")):
        return "cellular"
    if lowered.startswith(("eth", "en", "eno", "enp", "ens")) or "ethernet" in lowered:
        return "ethernet"
    if lowered.startswith(("tun", "tap", "wg", "tailscale", "utun", "ppp")) or any(
            token in lowered for token in ("vpn", "wireguard", "openvpn")):
        return "vpn"
    if lowered.startswith(("docker", "veth", "br-", "virbr")) or any(
            token in lowered for token in ("virtual", "hyper-v", "vmware", "loopback")):
        return "virtual"
    return "other"


def _posix_interfaces() -> List[Dict[str, Any]]:
    """Linux and macOS interfaces: ``ip -j`` when it exists, else ``ifconfig -a``.

    ``ip`` is probed before it is run because macOS has no iproute2: calling it
    there would only add a failing process to every collect.
    """
    interfaces: List[Dict[str, Any]] = []
    rc, text = -1, ""
    if _which("ip"):
        rc, text = _run(["ip", "-j", "addr", "show"], timeout=4.0)
    if rc == 0 and text:
        try:
            for item in json.loads(text):
                if not isinstance(item, dict):
                    continue
                ipv4, ipv6 = [], []
                for addr in item.get("addr_info") or []:
                    if not isinstance(addr, dict):
                        continue
                    local = addr.get("local")
                    if not local:
                        continue
                    (ipv4 if addr.get("family") == "inet" else ipv6).append(
                        "%s/%s" % (local, addr.get("prefixlen")) if addr.get("prefixlen") is not None
                        else str(local))
                name = str(item.get("ifname") or "")
                interfaces.append({
                    "name": name,
                    "kind": _interface_kind(name),
                    "up": str(item.get("operstate") or "").lower() == "up",
                    "macs": [str(item.get("address") or "")] if item.get("address") else [],
                    "ipv4": ipv4,
                    "ipv6": ipv6[:_MAX_LIST // 4],
                })
            return interfaces
        except Exception:
            pass
    # Fallback: ifconfig -a (macOS, older Linux) - names only, plus addresses.
    rc, text = _run(["ifconfig", "-a"], timeout=4.0)
    if rc != 0 or not text:
        return []
    current: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        head = re.match(r"^([A-Za-z0-9_.:-]+):?\s", line)
        if head and not line.startswith((" ", "\t")):
            current = {"name": head.group(1), "kind": _interface_kind(head.group(1)),
                       "up": "UP" in line.upper(), "macs": [], "ipv4": [], "ipv6": []}
            mac = re.search(r"ether\s+([0-9a-f:]{17})", line, re.I)
            if mac:
                current["macs"] = [mac.group(1)]
            interfaces.append(current)
            continue
        if current is None:
            continue
        addr = re.search(r"inet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)(?:/\d+)?", line)
        if addr:
            current["ipv4"].append(addr.group(1))
    return interfaces


def _windows_interfaces() -> List[Dict[str, Any]]:
    script = (
        "try { Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE' | "
        "Select-Object Description,MACAddress,IPAddress,DefaultIPGateway | ConvertTo-Json -Compress -Depth 4 } "
        "catch { '' }"
    )
    rc, text = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=6.0)
    if rc != 0 or not text.strip():
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    interfaces: List[Dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Description") or "")
        ips = item.get("IPAddress") or []
        if isinstance(ips, str):
            ips = [ips]
        ipv4 = [str(ip) for ip in ips if re.match(r"^\d+\.\d+\.\d+\.\d+$", str(ip))]
        ipv6 = [str(ip) for ip in ips if ":" in str(ip)]
        gateways = item.get("DefaultIPGateway") or []
        if isinstance(gateways, str):
            gateways = [gateways]
        interfaces.append({
            "name": name,
            "kind": _interface_kind(name) if _interface_kind(name) != "other" else "ethernet",
            "up": bool(ipv4 or ipv6),
            "macs": [str(item.get("MACAddress"))] if item.get("MACAddress") else [],
            "ipv4": ipv4,
            "ipv6": ipv6[:_MAX_LIST // 4],
            "gateways": [str(g) for g in gateways],
        })
    return interfaces


def _default_route(interfaces: List[Dict[str, Any]]) -> str:
    if scenario() == "windows":
        # Windows has no /proc/net/route; the CIM query already returned the
        # per-adapter gateways.
        for item in interfaces:
            for gateway in item.get("gateways") or []:
                return "%s via %s" % (item.get("name", ""), gateway)
        return ""
    if os.path.isfile("/proc/net/route"):
        for line in _read_text("/proc/net/route").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "00000000":
                return parts[0]
    for item in interfaces:
        if item.get("ipv4") and item.get("kind") != "loopback":
            return str(item.get("name") or "")
    return ""


def _wifi_posix() -> Optional[Dict[str, Any]]:
    wireless = _read_text("/proc/net/wireless")
    candidates: List[str] = []
    for line in wireless.splitlines()[2:]:
        name = line.split(":")[0].strip()
        if name:
            candidates.append(name)
    for name in candidates:
        if _which("iw"):
            rc, text = _run(["iw", "dev", name, "link"], timeout=3.0)
            if rc == 0 and "Connected to" in text:
                ssid = re.search(r"SSID:\s*(.+)", text)
                signal = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
                freq = re.search(r"freq:\s*(\d+)", text)
                rate = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", text)
                bssid = re.search(r"Connected to\s+([0-9a-f:]{17})", text, re.I)
                return {
                    "ssid": ssid.group(1).strip() if ssid else "",
                    "bssid": bssid.group(1) if bssid else "",
                    "rssi_dbm": int(signal.group(1)) if signal else None,
                    "frequency_mhz": int(freq.group(1)) if freq else None,
                    "link_speed_mbps": float(rate.group(1)) if rate else None,
                }
    if _which("nmcli"):
        rc, text = _run(["nmcli", "-t", "-e", "no", "-f", "ACTIVE,SSID,SIGNAL,FREQ", "dev", "wifi"],
                        timeout=4.0)
        if rc == 0:
            for line in text.splitlines():
                parts = line.split(":")
                if parts and parts[0].strip() == "yes" and len(parts) >= 3:
                    try:
                        percent = int(parts[len(parts) - 2].strip())
                    except Exception:
                        percent = None
                    return {
                        "ssid": parts[1] if len(parts) > 1 else "",
                        "bssid": "",
                        "rssi_dbm": int(round(percent / 2.0 - 100)) if percent is not None else None,
                        "signal_pct": percent,
                        "frequency_mhz": None,
                        "link_speed_mbps": None,
                    }
    return None


def _wifi_windows() -> Optional[Dict[str, Any]]:
    rc, text = _run(["netsh", "wlan", "show", "interfaces"], timeout=5.0)
    if rc != 0 or not text:
        return None
    ssid = bssid = ""
    percent: Optional[int] = None
    rate: Optional[float] = None
    frequency: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if "BSSID" in stripped.upper():
            mac = re.search(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", stripped, re.I)
            if mac:
                bssid = mac.group(1)
            continue
        if re.match(r"^SSID\s*:", stripped, re.I):
            ssid = stripped.split(":", 1)[1].strip()
            continue
        percent_match = re.match(r"^[^:]+:\s*(\d{1,3})\s*%\s*$", stripped)
        if percent_match and percent is None:
            percent = int(percent_match.group(1))
            continue
        if "Mbps" in stripped:
            numbers = re.findall(r"(\d+(?:\.\d+)?)", stripped)
            if numbers and rate is None:
                rate = float(numbers[0])
            continue
        if "MHz" in stripped or "GHz" in stripped:
            numbers = re.findall(r"(\d+(?:\.\d+)?)", stripped)
            if numbers:
                value = float(numbers[0])
                frequency = int(value * 1000) if "GHz" in stripped else int(value)
    if not ssid and percent is None:
        return None
    return {
        "ssid": ssid,
        "bssid": bssid,
        "rssi_dbm": int(round(percent / 2.0 - 100)) if percent is not None else None,
        "signal_pct": percent,
        "frequency_mhz": frequency,
        "link_speed_mbps": rate,
    }


def _wifi_darwin() -> Optional[Dict[str, Any]]:
    airport = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
               "Versions/Current/Resources/airport")
    rc, text = _run([airport, "-I"], timeout=4.0)
    if rc != 0 or not text:
        return None
    if "AirPort: Off" in text:
        return None
    def field(name: str) -> Optional[str]:
        match = re.search(r"^\s*%s:\s*(.+)$" % re.escape(name), text, re.M)
        return match.group(1).strip() if match else None
    rssi = field("agrCtlRSSI")
    rate = field("lastTxRate")
    channel = field("channel")
    frequency = None
    if channel:
        try:
            frequency = int(float(channel.split(",")[0]) * 1000) or None
        except Exception:
            frequency = None
    return {
        "ssid": field("SSID") or "",
        "bssid": field("BSSID") or "",
        "rssi_dbm": int(rssi) if rssi and re.match(r"^-?\d+$", rssi) else None,
        "frequency_mhz": frequency,
        "link_speed_mbps": float(rate) if rate and re.match(r"^[\d.]+$", rate) else None,
    }


@_runs("termux-wifi-connectioninfo")
def _wifi_termux() -> Optional[Dict[str, Any]]:
    """A phone: Android exposes the associated network through Termux:API."""
    result = _termux_json("termux-wifi-connectioninfo", timeout=4.0)
    if not result.get("ok"):
        return None
    value = result.get("value")
    if not isinstance(value, dict):
        return None
    rssi = value.get("rssi")
    if isinstance(rssi, (int, float)) and rssi in (0, -1, -127):
        rssi = None
    ssid = str(value.get("ssid") or "")
    if ssid in ("<unknown ssid>", "unknown"):
        ssid = ""
    return {
        "ssid": ssid,
        "bssid": str(value.get("bssid") or ""),
        "rssi_dbm": int(rssi) if isinstance(rssi, (int, float)) else None,
        # The command answers "frequency_mhz" (verified on an OPPO PHY110, Android
        # 16); "frequency" stays as the fallback for older builds.
        "frequency_mhz": value.get("frequency_mhz", value.get("frequency")),
        "link_speed_mbps": value.get("link_speed_mbps"),
        "ip": value.get("ip"),
        "network_id": value.get("network_id"),
    }


def _cell_level(dbm: Optional[int]) -> Optional[int]:
    """Android's 0-4 bars, derived the way the framework does it for LTE."""
    if dbm is None:
        return None
    if dbm >= -85:
        return 4
    if dbm >= -95:
        return 3
    if dbm >= -105:
        return 2
    if dbm >= -115:
        return 1
    return 0


@_runs("termux-telephony-deviceinfo", "termux-telephony-cellinfo")
def _cellular_termux() -> Dict[str, Any]:
    """Cellular state, which only Android can report without root.

    A desktop has no such source at all, which is why no other scenario has an
    implementation that could answer this: their network blocks carry
    ``cellular: None``.
    """
    block: Dict[str, Any] = {}
    device = _termux_json("termux-telephony-deviceinfo", timeout=4.0)
    if device.get("ok") and isinstance(device.get("value"), dict):
        value = device["value"]
        block.update({
            "operator": value.get("network_operator_name") or value.get("sim_operator_name") or "",
            "network_type": value.get("network_type") or "",
            "roaming": value.get("network_roaming"),
            "data_state": value.get("data_state"),
            "sim_state": value.get("sim_state"),
            "phone_type": value.get("phone_type"),
        })
    cells = _termux_json("termux-telephony-cellinfo", timeout=5.0)
    if cells.get("ok"):
        value = cells.get("value")
        if isinstance(value, dict):
            value = [value]
        registered = [c for c in (value or []) if isinstance(c, dict) and c.get("registered")]
        pool = registered or [c for c in (value or []) if isinstance(c, dict)]
        best_dbm: Optional[int] = None
        best_level: Optional[int] = None
        for cell in pool:
            candidates = [cell.get("dbm")]
            for key in ("rsrp", "rssi", "level"):
                if isinstance(cell.get(key), (int, float)):
                    candidates.append(cell.get(key))
            for candidate in candidates:
                if isinstance(candidate, (int, float)) and -160 < candidate < 0:
                    dbm = int(candidate)
                    if best_dbm is None or dbm > best_dbm:
                        best_dbm = dbm
                    break
            if isinstance(cell.get("level"), (int, float)) and 0 <= cell["level"] <= 4:
                best_level = max(best_level or 0, int(cell["level"]))
        if best_dbm is not None:
            block["signal_dbm"] = best_dbm
        if best_level is not None:
            block["level"] = best_level
        elif best_dbm is not None:
            block["level"] = _cell_level(best_dbm)
        block["cells_seen"] = len(value or [])
        block["cell_type"] = (pool[0].get("type") if pool else "") or ""
    elif cells.get("reason"):
        block["cellinfo_reason"] = cells["reason"]
        if cells.get("fix"):
            block["cellinfo_fix"] = cells["fix"]
    return block


def _signal_summary(wifi: Optional[Dict[str, Any]], cellular: Dict[str, Any],
                    interfaces: Optional[List[Dict[str, Any]]] = None,
                    connected_kind: str = "") -> str:
    """One line a model can quote: the link that is actually carrying traffic.

    The primary link wins. A phone that is on Wi-Fi is not described by its LTE
    bars: reporting "Magti LTE -101 dBm" while ``connected_kind`` says ``wifi``
    made the two fields contradict each other, and the model quotes whichever it
    reads first. The other radio is not lost - it stays in the ``wifi`` and
    ``cellular`` blocks.
    """
    cellular_line = ""
    if cellular.get("signal_dbm") is not None:
        cellular_line = "%s %s %d dBm (level %s)" % (
            cellular.get("operator") or "cellular", cellular.get("network_type") or "",
            cellular["signal_dbm"], cellular.get("level"))
    wifi_line = ""
    if wifi and wifi.get("rssi_dbm") is not None:
        wifi_line = "wifi %s %d dBm" % (wifi.get("ssid") or "(hidden)", wifi["rssi_dbm"])
    elif wifi:
        wifi_line = "wifi %s (signal not reported by Android)" % (wifi.get("ssid") or "(hidden)")
    if connected_kind == "wifi" and wifi_line:
        return wifi_line
    if connected_kind == "cellular" and cellular_line:
        return cellular_line
    if wifi_line or cellular_line:
        return wifi_line or cellular_line
    for item in interfaces or []:
        if item.get("up") and item.get("kind") in ("ethernet", "vpn", "other"):
            return "%s (%s)" % (item.get("name", ""), item.get("kind", ""))
    return "unknown"


def _network_result(interfaces: List[Dict[str, Any]], wifi: Optional[Dict[str, Any]],
                    cellular: Dict[str, Any], source: str) -> Dict[str, Any]:
    """The block every scenario returns, so no scenario invents its own shape."""
    route = _default_route(interfaces)
    # A cellular dict that carries only ``cellinfo_reason``/``cellinfo_fix`` is an
    # explanation, not a network: counting it as one produced "available: true,
    # interface_count: 0" on a phone whose every radio call had timed out.
    has_cellular = bool(cellular.get("network_type") or cellular.get("signal_dbm"))
    if not interfaces and not wifi and not has_cellular:
        return _unavailable("no network interface could be inspected", source=source,
                            cellular=cellular or None)
    connected_kind = "wifi" if wifi else ("cellular" if has_cellular else "")
    if not connected_kind:
        for item in interfaces:
            if item.get("up") and item.get("kind") != "loopback":
                connected_kind = str(item.get("kind"))
                break
    return _block(
        source,
        interfaces=interfaces[:_MAX_LIST],
        interface_count=len(interfaces),
        default_route=route,
        connected_kind=connected_kind,
        wifi=wifi,
        cellular=cellular or None,
        signal=_signal_summary(wifi, cellular, interfaces, connected_kind),
    )


@_runs("ip", "ifconfig", "termux-wifi-connectioninfo", "termux-telephony-deviceinfo",
       "termux-telephony-cellinfo")
def _network_termux() -> Dict[str, Any]:
    """A phone: Termux:API for the radios, /proc and ip/ifconfig for the links."""
    return _network_result(_posix_interfaces(), _wifi_termux(), _cellular_termux(),
                           "termux-api + ip/ifconfig")


@_runs("ip", "ifconfig", "iw", "nmcli")
def _network_linux() -> Dict[str, Any]:
    """A Linux host: ip/ifconfig for the links, iw/nmcli for Wi-Fi. No cellular."""
    return _network_result(_posix_interfaces(), _wifi_posix(), {},
                           "ip/ifconfig + iw/nmcli")


@_runs("powershell", "netsh")
def _network_windows() -> Dict[str, Any]:
    """Windows: CIM for the adapters and gateways, netsh for the Wi-Fi link."""
    return _network_result(_windows_interfaces(), _wifi_windows(), {},
                           "Win32_NetworkAdapterConfiguration + netsh")


@_runs("ifconfig", "airport")
def _network_darwin() -> Dict[str, Any]:
    """macOS: ifconfig for the links, the Apple80211 airport helper for Wi-Fi."""
    return _network_result(_posix_interfaces(), _wifi_darwin(), {},
                           "ifconfig -a + airport")


# ---------------------------------------------------------------------------
# locale and time
# ---------------------------------------------------------------------------

def _split_locale(tag: str) -> Tuple[str, str]:
    """``ru-RU.UTF-8`` -> ``("ru", "RU")``; tolerant of ``C``/``POSIX``/empty."""
    if not tag:
        return "", ""
    tag = tag.split("@", 1)[0]
    tag = tag.split(".", 1)[0]
    if tag.upper() in ("C", "POSIX"):
        return "", ""
    parts = re.split(r"[-_]", tag)
    language = parts[0].lower() if parts and parts[0] else ""
    region = ""
    for part in parts[1:]:
        if len(part) in (2, 3) and part.isalpha():
            region = part.upper()
            break
    return language, region


def _windows_locale() -> str:
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
            return buffer.value
    except Exception:
        pass
    return ""


def _locale_block(platform_tag: str = "", platform_source: str = "") -> Dict[str, Any]:
    """Locale from the environment, with one platform-provided tag taking priority.

    Every scenario goes through here; only the tag the platform can add differs,
    so the parsing and the "no locale is configured" answer exist once.
    """
    tags: List[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(key)
        if value:
            tags.extend(part for part in str(value).split(":") if part)
    source = "environment"
    if platform_tag:
        tags.insert(0, platform_tag)
        source = "%s + environment" % platform_source
    language, region = "", ""
    for tag in tags:
        language, region = _split_locale(tag)
        if language:
            break
    python_locale: Tuple[Optional[str], Optional[str]] = (None, None)
    try:
        python_locale = _locale.getlocale()          # ('ru_RU', 'UTF-8') or (None, None)
    except Exception:
        python_locale = (None, None)
    if not language and python_locale[0]:
        language, region = _split_locale(python_locale[0] or "")
        source = "locale.getlocale"
    if not language:
        return _unavailable("no locale is configured (LANG/LC_* unset)",
                            source=source or "environment", languages=tags[:_MAX_LIST // 3])
    return _block(
        source,
        language=language,
        region=region,
        languages=tags[:_MAX_LIST // 3],
        encoding=python_locale[1] or os.environ.get("LC_CTYPE", "").split(".")[-1] or "",
    )


@_runs("getprop")
def _locale_termux() -> Dict[str, Any]:
    """A phone: Android keeps the locale in a system property, not in LANG."""
    prop = _android_getprop("persist.sys.locale") or _android_getprop("ro.product.locale")
    return _locale_block(prop, "getprop")


def _locale_windows() -> Dict[str, Any]:
    """Windows: the user's default locale name comes from the Win32 API."""
    return _locale_block(_windows_locale(), "GetUserDefaultLocaleName")


def _locale_linux() -> Dict[str, Any]:
    """A Linux host: LANG/LC_* are the whole answer."""
    return _locale_block("", "environment")


def _locale_darwin() -> Dict[str, Any]:
    """macOS: it keeps the locale in the same POSIX variables, so no probe.

    ``defaults read -g AppleLocale`` would be one more subprocess for a value
    LANG or ``locale.getlocale()`` already carries.
    """
    return _locale_block("", "environment")


def _posix_timezone_name() -> str:
    """The tz name on a Linux/macOS host, from the files the tz database updates.

    ``/etc/timezone`` is Debian's copy, ``/var/db/zoneinfo`` is macOS's, and the
    ``/etc/localtime`` symlink is what every other Linux distribution has.
    """
    for candidate in ("/etc/timezone", "/var/db/zoneinfo"):
        text = _read_text(candidate)
        if text and "/" in text:
            return text
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return ""


def _tz_name_fallback() -> str:
    """Last resort: the C library's own abbreviation (``UTC``, ``MSK``, ``MSD``)."""
    try:
        return str(time.tzname[0] or "")
    except Exception:
        return ""


@_runs("getprop")
def _time_termux() -> Dict[str, Any]:
    """A phone: Android keeps the zone in a system property; an app has no /etc."""
    return _time_block(_android_getprop("persist.sys.timezone") or _posix_timezone_name()
                       or _tz_name_fallback())


def _time_windows() -> Dict[str, Any]:
    """Windows: the IANA-ish key lives in the registry, not in a file."""
    name = _windows_registry(
        r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation", "TimeZoneKeyName")
    return _time_block(name or _tz_name_fallback())


def _time_linux() -> Dict[str, Any]:
    """A Linux host: /etc/timezone or the /etc/localtime symlink."""
    return _time_block(_posix_timezone_name() or _tz_name_fallback())


def _time_darwin() -> Dict[str, Any]:
    """macOS: /var/db/zoneinfo is the authoritative copy."""
    return _time_block(_posix_timezone_name() or _tz_name_fallback())


def _time_block(timezone_name: str = "") -> Dict[str, Any]:
    now = datetime.now()
    local = now.astimezone()
    offset = local.utcoffset()
    offset_seconds = int(offset.total_seconds()) if offset is not None else 0
    sign = "+" if offset_seconds >= 0 else "-"
    absolute = abs(offset_seconds)
    return _block(
        "datetime.astimezone + tz database",
        iso_local=local.isoformat(timespec="seconds"),
        utc=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        epoch=int(time.time()),
        utc_offset="%s%02d:%02d" % (sign, absolute // 3600, (absolute % 3600) // 60),
        utc_offset_minutes=offset_seconds // 60,
        timezone=timezone_name,
        abbreviation=local.tzname() or "",
        weekday=local.strftime("%A"),
        time_24h=local.strftime("%H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# hardware: cpu, memory, thermal
# ---------------------------------------------------------------------------

def _posix_cpu_model() -> str:
    info = _read_text("/proc/cpuinfo")
    for key in ("model name", "Hardware", "cpu model", "Processor", "Model"):
        match = re.search(r"^%s\s*:\s*(.+)$" % re.escape(key), info, re.M | re.I)
        if match:
            return match.group(1).strip()
    return platform.processor() or platform.machine()


def _windows_cpu_model() -> str:
    return _windows_registry(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")


def _cpu_block(model: str, source: str,
               sensors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The CPU fields every scenario shares; only the model and the source differ.

    ``sensors`` is the thermal list a CPU temperature is read from: Windows and
    macOS pass an empty list because neither has sysfs, and a temperature that
    does not exist must stay ``None`` instead of being guessed.
    """
    frequencies: List[float] = []
    for path in sorted(_glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"))[:_MAX_LIST]:
        value = _read_int(path)
        if value:
            frequencies.append(round(value / 1000.0, 1))     # kHz -> MHz
    max_freq = None
    for path in sorted(_glob.glob("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")):
        value = _read_int(path)
        if value:
            max_freq = round(value / 1000.0, 1)
    cores = len(_glob.glob("/sys/devices/system/cpu/cpu[0-9]*"))
    zones = _thermal_sensors() if sensors is None else sensors
    temp = max((item["value"] for item in zones if isinstance(item.get("value"), (int, float))),
               default=None)
    load: Dict[str, float] = {}
    try:
        if hasattr(os, "getloadavg"):
            values = os.getloadavg()
            load = {"1m": round(values[0], 2), "5m": round(values[1], 2), "15m": round(values[2], 2)}
    except Exception:
        load = {}
    if not load:
        try:
            from core.vitals import get_host_vitals
            load = dict(get_host_vitals().get("cpu_load") or {})
        except Exception:
            load = {}
    return _block(
        source,
        model=model,
        cores=cores or os.cpu_count() or 0,
        logical=os.cpu_count() or 0,
        freq_mhz_cur=frequencies,
        freq_mhz_max=max_freq,
        load=load,
        temp_c=temp,
    )


def _memory_block() -> Dict[str, Any]:
    """RAM and swap. ``core.vitals`` owns the per-OS maths; /proc is the fallback.

    A phone reports through the same Linux path, so reusing the existing
    collector keeps one implementation of the Windows/macOS branches instead of a
    second copy here.
    """
    fields: Dict[str, Any] = {}
    source = "core.vitals"
    try:
        from core.vitals import get_host_vitals
        ram = get_host_vitals().get("ram") or {}
        fields.update({
            "total_mb": ram.get("total_mb", 0.0),
            "used_mb": ram.get("used_mb", 0.0),
            "free_mb": ram.get("free_mb", 0.0),
            "used_pct": ram.get("used_pct", 0.0),
        })
    except Exception:
        source = "/proc/meminfo"
        total_kb = avail_kb = 0
        for line in _read_text("/proc/meminfo").splitlines():
            parts = line.split(":")
            if len(parts) < 2:
                continue
            key = parts[0].strip()
            raw = parts[1].strip().split()
            if not raw:
                continue
            try:
                value = int(raw[0])
            except Exception:
                continue
            if key == "MemTotal":
                total_kb = value
            elif key == "MemAvailable":
                avail_kb = value
        total_mb = round(total_kb / 1024.0, 1)
        free_mb = round(avail_kb / 1024.0, 1)
        used_mb = max(0.0, round(total_mb - free_mb, 1))
        fields.update({
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": free_mb,
            "used_pct": round(used_mb / total_mb * 100.0, 1) if total_mb else 0.0,
        })
    if os.path.isfile("/proc/meminfo"):
        swap_total_kb = swap_free_kb = 0
        for line in _read_text("/proc/meminfo").splitlines():
            if line.startswith("SwapTotal:"):
                swap_total_kb = int(line.split()[1])
            elif line.startswith("SwapFree:"):
                swap_free_kb = int(line.split()[1])
        fields["swap_total_mb"] = round(swap_total_kb / 1024.0, 1)
        fields["swap_free_mb"] = round(swap_free_kb / 1024.0, 1)
    return _block(source, **fields)


#: Substrings of a thermal-zone type that mean "this zone is a temperature".
#: A phone exposes far more zones than fit into a block, so when the list is cut
#: the named temperatures win over the odd ones (levels, unpopulated radios).
_THERMAL_NAME_HINTS = ("temp", "tsens", "cpuss", "cpu", "gpu", "soc", "battery",
                       "skin", "charger", "quiet", "therm", "pa", "usb")


def _thermal_priority(name: str) -> int:
    """0 for a name that looks like a temperature, 1 for everything else."""
    lowered = name.lower()
    return 0 if any(hint in lowered for hint in _THERMAL_NAME_HINTS) else 1


def _zone_index(path: str) -> int:
    """The number at the end of a ``thermal_zoneN`` path, for a natural order.

    ``sorted()`` on the raw paths puts ``thermal_zone10`` before
    ``thermal_zone9``; the kernel numbers them, and the report should read in
    that order.
    """
    match = re.search(r"(\d+)$", os.path.basename(path))
    return int(match.group(1)) if match else 0


def _looks_like_a_level(name: str) -> bool:
    """True for a zone that reports a *level*, not a temperature.

    ``pm8550-bcl-lvl0`` and ``pm8550b-ibat-lvl0`` on an OPPO PHY110 report ``0``
    and ``93``: battery current limits, not 0 °C and 0.093 °C. Reporting either as
    a temperature is exactly the kind of fabricated value this module exists to
    avoid.
    """
    lowered = name.lower()
    return "lvl" in lowered or "bcl" in lowered


def _celsius(millidegrees: Optional[int]) -> Optional[float]:
    """One sysfs reading as °C, or ``None`` when it cannot be a temperature.

    sysfs reports millidegrees on phones and most PCs, but whole degrees on a few
    drivers, and an unpopulated zone reports ``-273000``. ``None`` means "say
    nothing"; it never becomes the number 0.
    """
    if millidegrees is None:
        return None
    value = (round(millidegrees / 1000.0, 1) if abs(millidegrees) > 1000
             else float(millidegrees))
    if not -100.0 < value < 200.0:
        return None                      # -273: not populated; 200+ : not a phone
    return value


def _thermal_sensors() -> List[Dict[str, Any]]:
    """Thermal zones (then hwmon chips) as temperatures, in degrees Celsius.

    Three things a real OPPO PHY110 taught us that the sysfs documentation does
    not say:

    * some zones are populated with ``-273000`` (``mmw0``, the ``epm*`` family,
      ``bcl_warn``) - reporting that as a temperature poisons any ``max()`` a
      model computes, so an impossible reading is dropped, never converted;
    * ``pm8550-bcl-lvl*`` are battery current-limit *levels* whose ``0`` is not
      0 °C, so a zone whose type names a level is skipped;
    * the phone exposes 107 zones and a block holds :data:`_MAX_LIST`, so the
      zones whose names look like temperatures survive the cut, and two zones
      that share one label (``usb-therm`` twice) are reported once.
    """
    zones: List[Tuple[int, int, str, str]] = []
    for zone in _glob.glob("/sys/class/thermal/thermal_zone*"):
        name = _read_text(os.path.join(zone, "type")) or os.path.basename(zone)
        zones.append((_thermal_priority(name), _zone_index(zone), name, zone))
    # Stable within a priority, so the kernel's own zone order decides between
    # two temperatures instead of the alphabet.
    zones.sort(key=lambda item: (item[0], item[1]))

    selected: List[Tuple[str, str]] = []
    seen = set()
    for _priority, _index, name, zone in zones:
        if name in seen or _looks_like_a_level(name):
            continue            # a repeated label, or a "zone" that is a current limit
        seen.add(name)
        selected.append((name, zone))
        if len(selected) >= _MAX_LIST:
            break

    found: List[Dict[str, Any]] = []
    for name, zone in selected:
        value = _celsius(_read_int(os.path.join(zone, "temp")))
        if value is None:
            continue
        found.append({"name": name, "value": value, "unit": "C", "source": "sysfs"})
    if found:
        return found
    for hwmon in sorted(_glob.glob("/sys/class/hwmon/hwmon*"))[:4]:
        chip = _read_text(os.path.join(hwmon, "name")) or os.path.basename(hwmon)
        for path in sorted(_glob.glob(os.path.join(hwmon, "temp*_input")))[:4]:
            value = _celsius(_read_int(path))
            if value is None:
                continue
            found.append({"name": chip, "value": value, "unit": "C", "source": "hwmon"})
    return found[:_MAX_LIST]


def _hardware_termux() -> Dict[str, Any]:
    """A phone: Android is Linux underneath, so /proc, cpufreq and thermal answer."""
    sensors = _thermal_sensors()
    return _block(
        "/proc/cpuinfo + /proc/meminfo + thermal",
        cpu=_cpu_block(_posix_cpu_model(), "/proc/cpuinfo + cpufreq + thermal",
                       sensors=sensors),
        memory=_memory_block(),
        thermal=sensors,
    )


def _hardware_linux() -> Dict[str, Any]:
    """A Linux host: the same files as a phone, with a x86 model name."""
    sensors = _thermal_sensors()
    return _block(
        "/proc/cpuinfo + /proc/meminfo + thermal",
        cpu=_cpu_block(_posix_cpu_model(), "/proc/cpuinfo + cpufreq", sensors=sensors),
        memory=_memory_block(),
        thermal=sensors,
    )


def _hardware_windows() -> Dict[str, Any]:
    """Windows: the CPU name comes from the registry, and there is no sysfs."""
    cpu = _cpu_block(_windows_cpu_model() or platform.processor(), "winreg + vitals",
                     sensors=[])
    # Windows has no sysfs: a CPU temperature is not available without a vendor
    # driver, and guessing one would be worse than saying so.
    cpu["temp_c"] = None
    cpu["temp_note"] = "not exposed without a vendor driver"
    return _block("ctypes + winreg + core.vitals", cpu=cpu, memory=_memory_block(),
                  thermal=[])


@_runs("sysctl")
def _hardware_darwin() -> Dict[str, Any]:
    """macOS: sysctl names the CPU; a temperature would need root (powermetrics)."""
    _rc, model = _run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=2.0)
    cpu = _cpu_block(model or platform.processor(), "sysctl + vitals", sensors=[])
    cpu["temp_c"] = None
    cpu["temp_note"] = "powermetrics needs root"
    return _block("sysctl + core.vitals", cpu=cpu, memory=_memory_block(), thermal=[])


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def capture_dir() -> str:
    """Where camera/microphone captures are written.

    Explicit configuration wins, then shared storage on a phone (only after
    ``termux-setup-storage``), then the node's own cache - never ``/sdcard``
    before the operator has granted it.
    """
    if _CAPTURE_DIR:
        return _CAPTURE_DIR
    env = os.environ.get("MESH_DEVICE_CAPTURE_DIR")
    if env:
        return os.path.expanduser(env)
    if scenario() == "termux":
        shared = os.path.expanduser("~/storage/dcim")
        if os.path.isdir(shared):
            return os.path.join(shared, "antigravity-mesh")
        shared = os.path.expanduser("~/storage/shared")
        if os.path.isdir(shared):
            return os.path.join(shared, "AntigravityMesh")
    return os.path.join(os.path.expanduser("~"), ".cache", "antigravity-mesh", "captures")


def _storage_fields(root: str) -> Dict[str, Any]:
    """Disk usage of one filesystem root, plus the paths a capture is written to."""
    try:
        usage = shutil.disk_usage(root)
        total_gb = round(usage.total / (1024 ** 3), 2)
        free_gb = round(usage.free / (1024 ** 3), 2)
        used_pct = round(usage.used / usage.total * 100.0, 1) if usage.total else 0.0
    except Exception:
        total_gb = free_gb = used_pct = 0.0
    return {
        "root": root,
        "total_gb": total_gb,
        "free_gb": free_gb,
        "used_pct": used_pct,
        "home": os.path.expanduser("~"),
        "capture_dir": capture_dir(),
    }


def _storage_termux() -> Dict[str, Any]:
    """A phone: the same disk, plus whether the shared-storage grant exists."""
    fields = _storage_fields("/")
    shared = os.path.expanduser("~/storage/shared")
    granted = os.path.isdir(shared)
    fields["shared"] = shared if granted else "absent"
    fields["prefix"] = os.environ.get("PREFIX", "")
    if not granted:
        fields["shared_fix"] = ("run `termux-setup-storage` in Termux and grant the "
                                "permission to reach photos, downloads and documents")
    return _block("shutil.disk_usage + termux-setup-storage", **fields)


def _storage_linux() -> Dict[str, Any]:
    """A Linux host: one root filesystem and the per-user capture directory."""
    return _block("shutil.disk_usage", **_storage_fields("/"))


def _storage_windows() -> Dict[str, Any]:
    """Windows: the system drive, not whichever drive the process sits on."""
    return _block("shutil.disk_usage", **_storage_fields("C:\\"))


def _storage_darwin() -> Dict[str, Any]:
    """macOS: one root filesystem, like Linux."""
    return _block("shutil.disk_usage", **_storage_fields("/"))


# ---------------------------------------------------------------------------
# cameras, microphones, sensors
# ---------------------------------------------------------------------------

def _normalise_camera(item: Dict[str, Any]) -> Dict[str, Any]:
    sizes = item.get("jpeg_output_sizes") or []
    best = None
    for size in sizes if isinstance(sizes, list) else []:
        if not isinstance(size, dict):
            continue
        width, height = size.get("width"), size.get("height")
        if isinstance(width, int) and isinstance(height, int):
            if best is None or width * height > best[0] * best[1]:
                best = (width, height)
    camera: Dict[str, Any] = {
        "id": item.get("id"),
        "facing": item.get("facing") or "",
    }
    if best:
        camera["max_resolution"] = "%dx%d" % best
    if item.get("focal_lengths"):
        camera["focal_lengths"] = item.get("focal_lengths")
    if sizes:
        camera["modes"] = len(sizes) if isinstance(sizes, list) else None
    return camera


@_runs("termux-camera-info")
def _cameras_termux() -> Dict[str, Any]:
    """A phone: the Termux:API app lists the cameras and their modes."""
    result = _termux_json("termux-camera-info", timeout=5.0)
    if not result.get("ok"):
        fields = {"reason": result.get("reason") or "termux-camera-info did not answer"}
        if result.get("fix"):
            fields["fix"] = result["fix"]
        return _unavailable(source="termux-camera-info", **fields)
    value = result.get("value")
    items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    cameras = [_normalise_camera(item) for item in items if isinstance(item, dict)]
    return _block("termux-camera-info", cameras=cameras, count=len(cameras))


@_runs("powershell")
def _cameras_windows() -> Dict[str, Any]:
    """Windows: plug-and-play entities of class Camera/Image, via CIM."""
    script = ("try { Get-CimInstance Win32_PnPEntity -Filter \"PNPClass='Camera' or "
              "PNPClass='Image'\" | Select-Object -ExpandProperty Name } catch { '' }")
    rc, text = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    timeout=6.0)
    names = [line.strip() for line in text.splitlines() if line.strip()] if rc == 0 else []
    if not names:
        return _unavailable("no camera reported by Win32_PnPEntity (PNPClass Camera/Image)",
                            source="Get-CimInstance Win32_PnPEntity")
    return _block("Get-CimInstance Win32_PnPEntity",
                  cameras=[{"id": index, "name": name, "facing": ""}
                           for index, name in enumerate(names)],
                  count=len(names))


@_runs("system_profiler")
def _cameras_darwin() -> Dict[str, Any]:
    """macOS: system_profiler's camera section, one indented model line each."""
    rc, text = _run(["system_profiler", "SPCameraDataType", "-detailLevel", "mini"],
                    timeout=6.0)
    if rc != 0 or not text:
        return _unavailable("system_profiler did not answer",
                            source="system_profiler SPCameraDataType")
    names = []
    for line in text.splitlines():
        stripped = line.strip()
        # Model lines are indented under the "Camera"/"iSight" group and end
        # in a colon; the group headers themselves are unindented.
        if stripped.endswith(":") and (line.startswith("      ") or line.startswith("\t")):
            names.append(stripped.rstrip(":"))
    names = names[:4]
    if not names:
        return _unavailable("no camera found by system_profiler",
                            source="system_profiler SPCameraDataType")
    return _block("system_profiler SPCameraDataType",
                  cameras=[{"id": index, "name": name, "facing": ""}
                           for index, name in enumerate(names)],
                  count=len(names))


def _cameras_linux() -> Dict[str, Any]:
    """A Linux host: video4linux devices under /sys, no subprocess needed."""
    cameras: List[Dict[str, Any]] = []
    for path in sorted(_glob.glob("/sys/class/video4linux/video*"))[:_MAX_LIST]:
        name = _read_text(os.path.join(path, "name"))
        index = _read_int(os.path.join(path, "index"))
        cameras.append({"id": index, "device": os.path.basename(path), "name": name,
                        "facing": ""})
    if not cameras:
        return _unavailable("no /sys/class/video4linux device", source="/sys/class/video4linux")
    return _block("/sys/class/video4linux", cameras=cameras, count=len(cameras))


def _microphones_termux() -> Dict[str, Any]:
    """A phone: Android gives apps no capture-device enumeration at all.

    This is a real platform limit, not a missing probe - the fix points at the
    recording action, which does work - so the block says so instead of trying a
    Linux tool that does not exist on Android.
    """
    return _unavailable(
        "Android does not expose capture-device enumeration to apps",
        source="android",
        fix=("record through device_capture(action=mic_record_start): the Termux:API app "
             "must be installed and granted the microphone permission"))


@_runs("powershell")
def _microphones_windows() -> Dict[str, Any]:
    """Windows: capture endpoints are audio-endpoint plug-and-play entities."""
    script = ("try { Get-CimInstance Win32_PnPEntity -Filter \"PNPClass='AudioEndpoint'\" | "
              "Where-Object { $_.DeviceID -like '*0.0.1.*' } | "
              "Select-Object -ExpandProperty Name } catch { '' }")
    rc, text = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    timeout=6.0)
    names = [line.strip() for line in text.splitlines() if line.strip()] if rc == 0 else []
    if not names:
        return _unavailable("no capture endpoint reported (Win32_PnPEntity AudioEndpoint)",
                            source="Get-CimInstance Win32_PnPEntity")
    return _block("Get-CimInstance Win32_PnPEntity (capture endpoints)",
                  microphones=[{"name": name, "kind": "input"} for name in names],
                  count=len(names))


@_runs("system_profiler")
def _microphones_darwin() -> Dict[str, Any]:
    """macOS: system_profiler's audio section, one input device per line."""
    rc, text = _run(["system_profiler", "SPAudioDataType", "-detailLevel", "mini"],
                    timeout=6.0)
    if rc != 0 or not text:
        return _unavailable("system_profiler did not answer",
                            source="system_profiler SPAudioDataType")
    names = [line.strip() for line in text.splitlines()
             if "Input" in line and line.strip().endswith(":")]
    names = [name.rstrip(":") for name in names][:4]
    if not names:
        return _unavailable("no input device found by system_profiler",
                            source="system_profiler SPAudioDataType")
    return _block("system_profiler SPAudioDataType",
                  microphones=[{"name": name, "kind": "input"} for name in names],
                  count=len(names))


@_runs("arecord")
def _microphones_linux() -> Dict[str, Any]:
    """A Linux host: ALSA's card list, with ``arecord -l`` as the detailed one."""
    mics: List[Dict[str, Any]] = []
    rc, text = _run(["arecord", "-l"], timeout=3.0)
    if rc == 0 and text:
        for line in text.splitlines():
            match = re.match(r"^card\s+(\d+):\s*([^\[]+)\[([^\]]+)\],\s*device\s+(\d+):\s*(.+)$",
                             line)
            if match:
                mics.append({"card": int(match.group(1)), "device": int(match.group(4)),
                             "name": match.group(3).strip(), "kind": "input"})
    if not mics:
        for card in re.finditer(r"^\s*(\d+)\s*\[([^\]]+)\]:\s*(\S+)\s*-\s*(.+)$",
                                _read_text("/proc/asound/cards"), re.M):
            mics.append({"card": int(card.group(1)), "name": card.group(4).strip(),
                         "driver": card.group(3), "kind": "input"})
    if not mics:
        return _unavailable("no ALSA capture device (/proc/asound/cards empty)",
                            source="/proc/asound/cards")
    return _block("/proc/asound/cards + arecord -l", microphones=mics[:_MAX_LIST],
                  count=len(mics))


def _sensor_names(result: Dict[str, Any]) -> List[str]:
    """Sensor names out of a ``termux-sensor -l`` answer (list or object)."""
    value = result.get("value")
    if isinstance(value, dict):
        value = value.get("sensors") or value.get("list") or list(value.keys())
    names: List[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("type")
            if name:
                names.append(str(name))
    return names[:_MAX_LIST]


@_runs("termux-sensor")
def _sensors_termux(sample: Optional[str] = None) -> Dict[str, Any]:
    """A phone: Termux:API lists the hardware sensors, and samples one on request.

    Names are free; a value can cost battery, which is why nothing here samples
    unless the caller asked for exactly one sensor by name.
    """
    result = _termux_json("termux-sensor", ["-l"], timeout=6.0)
    if not result.get("ok"):
        fields = {"reason": result.get("reason") or "termux-sensor did not answer"}
        if result.get("fix"):
            fields["fix"] = result["fix"]
        return _unavailable(source="termux-sensor -l", **fields)
    names = _sensor_names(result)
    if sample:
        adapter = _termux_adapter()
        timed = (adapter.sensor_sample(sample) if adapter is not None
                 else _termux_json("termux-sensor", ["-s", sample, "-n", "1"], timeout=8.0))
        if isinstance(timed, dict) and timed.get("ok") is False:
            return _unavailable(timed.get("reason") or "sensor %s did not answer" % sample,
                                source="termux-sensor -s %s -n 1" % sample,
                                sensors=names, sampling="names")
        value = timed.get("value") if isinstance(timed, dict) else timed
        return _block("termux-sensor -s %s -n 1" % sample, sensors=names,
                      sampling="values", values=value)
    return _block("termux-sensor -l", sensors=names, count=len(names), sampling="names")


def _sensors_linux(sample: Optional[str] = None) -> Dict[str, Any]:
    """A Linux host: thermal zones and hwmon chips are the only sensor source.

    ``sample`` is accepted and ignored: a sysfs zone is already a live value, and
    there is no per-sensor sampling verb here.
    """
    thermal = _thermal_sensors()
    if thermal:
        return _block("sysfs thermal + hwmon", sensors=thermal, count=len(thermal),
                      sampling="values")
    return _unavailable("no thermal zone or hwmon device", source="/sys/class/thermal")


def _sensors_windows(sample: Optional[str] = None) -> Dict[str, Any]:
    """Windows: thermal and hwmon are Linux concepts - there is no equivalent."""
    return _unavailable("no sensor interface (thermal/hwmon is a Linux/Android concept)",
                        source="windows")


def _sensors_darwin(sample: Optional[str] = None) -> Dict[str, Any]:
    """macOS: no sysfs at all, and a temperature would need root (powermetrics)."""
    return _unavailable("macOS exposes no sensor source without root (powermetrics)",
                        source="macos")


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------

def _capabilities_common() -> Dict[str, Any]:
    """The two fields every scenario reports, before the platform's own list."""
    return {"platform": _SYSTEM, "device_class": _classify(_battery_present())}


def _wifi_tools() -> Dict[str, bool]:
    """Which Wi-Fi tools this host has. A PATH probe, never a command."""
    return {tool: bool(_which(tool)) for tool in ("iw", "nmcli", "netsh", "airport")}


def _sources_without_sysfs() -> Dict[str, Any]:
    """The source list for a platform that has no sysfs (Windows, macOS).

    ``None`` means "this platform has no such source", which is different from
    ``False`` ("the source exists and found nothing") - a distinction the model
    needs before it suggests a camera probe.
    """
    return {"battery": bool(_battery_present()), "cameras": None, "microphones": None,
            "thermal": False, "wifi_tools": _wifi_tools()}


def _capabilities_termux() -> Dict[str, Any]:
    """A phone: the ``termux-*`` commands that exist plus the permissions proven.

    The local inventory is what ``core.termux`` would report, kept here so a
    checkout without that file still tells the model which phone APIs it can
    call. **The canonical key is ``termux_api``** in this section and in
    :func:`_device_termux`; the adapter files the same fact under ``api``, and it
    is renamed right here - one fact, one name, so a model that reads
    ``termux_api`` can never be told "no termux-api" because the answer was put
    under the other key.
    """
    fields = _capabilities_common()
    adapter = _termux_adapter()
    if adapter is not None:
        try:
            fields.update(adapter.capabilities())
        except Exception as exc:
            fields["termux_api"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    if "api" in fields:
        fields["termux_api"] = fields.pop("api")
    if "termux_api" not in fields:
        # The subset of core/termux.REQUIRED_COMMANDS this module can use; the
        # full inventory stays in one place, the adapter.
        commands = ("termux-battery-status", "termux-wifi-connectioninfo",
                    "termux-telephony-deviceinfo", "termux-telephony-cellinfo",
                    "termux-sensor", "termux-camera-info",
                    "termux-microphone-record", "termux-location", "termux-notification",
                    "termux-clipboard-get", "termux-volume", "termux-tts-speak",
                    "termux-torch", "termux-vibrate", "termux-wake-lock")
        fields["termux_api"] = {
            "installed": bool(_which("termux-battery-status")),
            "commands": {name: bool(_which(name)) for name in commands},
            "fix": TERMUX_API_FIX,
        }
    if "storage_permission" not in fields:
        fields["storage_permission"] = os.path.isdir(os.path.expanduser("~/storage/shared"))
    if "wake_lock" not in fields:
        # The adapter owns ``wake_lock`` and it is a dict ({"command","held",
        # "count"}) - a plain bool here used to overwrite it and lose the detail.
        # Without the adapter the only honest wake-lock fact is whether the
        # command exists, so it gets a name of its own instead.
        fields["wake_lock_command"] = bool(_which("termux-wake-lock"))
    return _block("local probes", **fields)


def _capabilities_windows() -> Dict[str, Any]:
    """Windows: cameras and microphones come from CIM, thermal has no source."""
    fields = _capabilities_common()
    fields["sources"] = _sources_without_sysfs()
    return _block("local probes", **fields)


def _capabilities_linux() -> Dict[str, Any]:
    """A Linux host: every source is a file under ``/sys`` or ``/proc``."""
    fields = _capabilities_common()
    fields["sources"] = {
        "battery": bool(_battery_present()),
        "cameras": bool(_glob.glob("/sys/class/video4linux/video*")),
        "microphones": os.path.isfile("/proc/asound/cards"),
        "thermal": bool(_glob.glob("/sys/class/thermal/thermal_zone*")),
        "wifi_tools": _wifi_tools(),
    }
    return _block("local probes", **fields)


@_runs("pmset")
def _capabilities_darwin() -> Dict[str, Any]:
    """macOS: cameras and microphones come from system_profiler, thermal needs root.

    The ``pmset`` declaration is here for :func:`_battery_present`, which is the
    ``device_class`` probe inside this block.
    """
    fields = _capabilities_common()
    fields["sources"] = _sources_without_sysfs()
    return _block("local probes", **fields)


# ---------------------------------------------------------------------------
# The matrix: one implementation per section per scenario
# ---------------------------------------------------------------------------

#: Section -> scenario -> the implementation that runs. This is the platform
#: contract of the module in one place: reading ``_SECTION_IMPL["battery"]``
#: answers "what runs on a phone" without following a single ``if``, and a
#: scenario that has no source for a section gets an implementation that says so
#: (with the missing platform capability named) instead of borrowing another
#: platform's answer.
#:
#: Every entry is zero-argument except ``sensors``, which also accepts
#: ``sample=<name>`` when the caller asked for one live reading.
_SECTION_IMPL: Dict[str, Dict[str, Callable[..., Dict[str, Any]]]] = {
    "device": {"termux": _device_termux, "linux": _device_linux,
               "windows": _device_windows, "darwin": _device_darwin},
    "battery": {"termux": _battery_termux, "linux": _battery_linux,
                "windows": _battery_windows, "darwin": _battery_darwin},
    "network": {"termux": _network_termux, "linux": _network_linux,
                "windows": _network_windows, "darwin": _network_darwin},
    "locale": {"termux": _locale_termux, "linux": _locale_linux,
               "windows": _locale_windows, "darwin": _locale_darwin},
    "time": {"termux": _time_termux, "linux": _time_linux,
             "windows": _time_windows, "darwin": _time_darwin},
    "hardware": {"termux": _hardware_termux, "linux": _hardware_linux,
                 "windows": _hardware_windows, "darwin": _hardware_darwin},
    "storage": {"termux": _storage_termux, "linux": _storage_linux,
                "windows": _storage_windows, "darwin": _storage_darwin},
    "cameras": {"termux": _cameras_termux, "linux": _cameras_linux,
                "windows": _cameras_windows, "darwin": _cameras_darwin},
    "microphones": {"termux": _microphones_termux, "linux": _microphones_linux,
                    "windows": _microphones_windows, "darwin": _microphones_darwin},
    "sensors": {"termux": _sensors_termux, "linux": _sensors_linux,
                "windows": _sensors_windows, "darwin": _sensors_darwin},
    "capabilities": {"termux": _capabilities_termux, "linux": _capabilities_linux,
                     "windows": _capabilities_windows, "darwin": _capabilities_darwin},
}


def scenario_commands(case: str) -> List[str]:
    """The external commands scenario ``case`` is allowed to run for one collect().

    Derived from the implementations themselves (the ``@_runs`` declarations
    attached to the very functions in ``_SECTION_IMPL``), never from a second
    hand-written list, so it cannot drift from the code that runs: an
    implementation that starts calling a new tool without declaring it fails
    ``tests/test_device_scenarios.py``.
    """
    found: List[str] = []
    for name in ALL_SECTIONS:
        impl = _SECTION_IMPL.get(name, {}).get(case)
        for command in getattr(impl, "commands", ()) or ():
            if command not in found:
                found.append(command)
    return sorted(found)


def matrix_problems() -> List[str]:
    """Sections x scenarios with no implementation, or a name that is not a section.

    Cheap, side-effect free, and meant to be called from CI and from the tests: a
    section added without four implementations must fail the build, not a phone
    at 3 a.m.
    """
    problems: List[str] = []
    for name in sorted(_SECTION_IMPL):
        if name not in ALL_SECTIONS:
            problems.append("%s: not a section of this module" % name)
    for name in ALL_SECTIONS:
        row = _SECTION_IMPL.get(name) or {}
        for case in SCENARIOS:
            impl = row.get(case)
            if impl is None:
                problems.append("%s: no implementation for scenario %s" % (name, case))
            elif not callable(impl):
                problems.append("%s/%s: %r is not callable" % (name, case, impl))
    return problems


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _resolve_sections(sections: Optional[Iterable[str]]) -> List[str]:
    if sections is None:
        return list(SECTIONS)
    if isinstance(sections, str):
        sections = [sections]
    wanted: List[str] = []
    for name in sections:
        name = str(name).strip().lower()
        if name == "all":
            return list(ALL_SECTIONS)
        if name in ALL_SECTIONS and name not in wanted:
            wanted.append(name)
    return wanted


def _collect_one(name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """One section through the matrix: ``_SECTION_IMPL[name][scenario()]``."""
    case = scenario()
    row = _SECTION_IMPL.get(name)
    if row is None:
        return _unavailable("unknown section %r" % name, source="device.collect")
    impl = row.get(case)
    if impl is None:
        # An impossible pair is still an answer, never a KeyError: the contract
        # is that nothing in this module raises.
        return _unavailable("no implementation for scenario %r" % case,
                            source="device.%s" % name)
    sensor = kwargs.get("sensor")
    if name == "sensors" and sensor:
        # A named sample is never cached: the caller asked for a live value.
        return _safe_call(lambda: impl(sample=str(sensor)), source=name)
    return _cached(name, impl, kwargs.get("fresh", False))


def collect(sections: Optional[Iterable[str]] = None,
            deadline: float = DEFAULT_DEADLINE,
            fresh: bool = False,
            sensor: Optional[str] = None,
            quick: bool = False) -> Dict[str, Dict[str, Any]]:
    """Collect the requested sections, in parallel, under one deadline.

    ``quick=True`` drops the slow sections (network, cameras, microphones,
    sensors): that is what ``MESH_DEVICE_QUICK=1`` asks for, so a status call
    stays instant on a busy host. A section that misses the deadline comes back
    as ``available: False`` with ``reason: "timed out"`` - never as a zero.
    """
    names = _resolve_sections(sections)
    if quick:
        names = [name for name in names if name not in SLOW_SECTIONS]
    if not names:
        return {}
    kwargs = {"fresh": fresh, "sensor": sensor}
    deadline = max(0.2, float(deadline))
    started = time.monotonic()
    results: Dict[str, Dict[str, Any]] = {}
    if len(names) == 1 or deadline <= 0.5:
        for name in names:
            results[name] = _collect_one(name, kwargs)
        return results
    pool = ThreadPoolExecutor(max_workers=min(6, len(names)),
                              thread_name_prefix="device-collect")
    try:
        futures = {name: pool.submit(_collect_one, name, kwargs) for name in names}
        for name, future in futures.items():
            remaining = deadline - (time.monotonic() - started)
            try:
                results[name] = future.result(timeout=max(0.05, remaining))
            except Exception:
                results[name] = _unavailable(
                    "timed out after %.1fs" % deadline, source=name)
    finally:
        pool.shutdown(wait=False)
    return results


def summary(sections: Optional[Iterable[str]] = None, quick: bool = False) -> Dict[str, Any]:
    """A compact one-line-per-fact view, for models that read prose poorly.

    Kept deliberately small: it is derived from :func:`collect`, never a second
    set of probes.
    """
    blocks = collect(sections=sections, quick=quick)
    device = blocks.get("device") or {}
    battery = blocks.get("battery") or {}
    network = blocks.get("network") or {}
    time_block = blocks.get("time") or {}
    hardware = blocks.get("hardware") or {}
    lines: List[str] = []
    if device.get("available"):
        lines.append("device: %s %s (%s)" % (device.get("manufacturer", ""),
                                             device.get("model", ""),
                                             device.get("class", "")))
    if battery.get("available"):
        lines.append("battery: %s%% %s (%s)" % (battery.get("percent"),
                                                battery.get("status", ""),
                                                battery.get("plugged", "")))
    if network.get("available"):
        lines.append("network: %s; route %s" % (network.get("signal", "unknown"),
                                                network.get("default_route", "")))
    if time_block.get("available"):
        lines.append("time: %s %s (%s)" % (time_block.get("iso_local"), time_block.get("timezone"),
                                           time_block.get("utc_offset")))
    cpu = (hardware.get("cpu") or {}) if hardware.get("available") else {}
    memory = (hardware.get("memory") or {}) if hardware.get("available") else {}
    if cpu:
        lines.append("cpu: %s, %s cores, load %s, temp %s" % (
            cpu.get("model", ""), cpu.get("cores"), cpu.get("load"), cpu.get("temp_c")))
    if memory:
        lines.append("memory: %s/%s MB used (%s%%)" % (memory.get("used_mb"),
                                                       memory.get("total_mb"),
                                                       memory.get("used_pct")))
    return {"available": bool(lines), "source": "device.summary", "lines": lines,
            "device_class": device.get("class", ""),
            "sections": sorted(blocks.keys())}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m core.device [section ...]`` - the probe a phone debug run uses."""
    argv = list(argv if argv is not None else sys.argv[1:])
    sections = argv or ["all"]
    payload = collect(sections=sections, fresh=True)
    # The resolver's own answer travels with the report: a bug report from a
    # phone that "reports the wrong thing" is what this block is for.
    payload["_scenario"] = scenario_report()
    payload["_meta"] = {"collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "termux": is_termux(), "platform": _SYSTEM}
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":                                        # pragma: no cover
    raise SystemExit(main())
