#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/termux.py - the one place in the node that runs ``termux-*`` commands.

Termux:API is a two-part install: the ``termux-api`` package supplies the shell
commands, and the Termux:API **app** answers them over an Android binder. Either
half missing produces the same silent nothing from the phone, so every
``termux-*`` call in this project goes through this module and every failure
comes back as a dict a model can act on instead of an exception nobody catches:

* ``missing`` - the command is not on ``PATH`` (the package or the app is absent);
* ``denied``  - Android refused the permission behind the call;
* ``timeout`` - Android froze the Termux:API app, which is the *normal* failure
  mode once the screen has been off for a while;
* ``refused`` - the caller asked for something that is not a ``termux-*``
  command, which must never reach an exec.

Contract:

* **nothing raises** - a public function always returns a dict;
* **no shell, ever** - every command is an argv list under a hard timeout, so a
  string that arrived from a model can never become shell syntax;
* **cheap** - a missing command is answered from ``shutil.which`` without
  starting a process, and the command inventory is cached for a minute;
* **one root cause, said once** - when every command hangs because the
  Termux:API *app* is missing, that is remembered (for a short while) instead of
  making each telemetry section report its own timeout;
* **silent** - no printing, no logging, no progress output.

The wake lock lives here for the same reason the rest does: Android freezes
background processes when the screen goes off, and the node's own boot script
takes the lock exactly once at boot with nobody left to release it.
"""

from __future__ import annotations

import json
import locale as _locale
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The one sentence a user needs when a ``termux-*`` command is missing. Both
#: halves matter: an app from a *different* store than the package fails to bind,
#: which looks exactly like "the command is not installed".
TERMUX_API_FIX = ("install the Termux:API app from the SAME store as Termux "
                  "(F-Droid or GitHub, never mixed) and run `pkg install termux-api`")

#: Told whenever a command exists but never answers. Android freezes the
#: Termux:API app in the background, and the binder call then waits forever.
TERMUX_TIMEOUT_FIX = ("open the Termux:API app once after installing it, and disable "
                      "battery optimisation for Termux and Termux:API")

#: Told when ``~/storage/shared`` is missing: that symlink is the only reason the
#: node can read or write the phone's own storage.
TERMUX_STORAGE_FIX = ("run `termux-setup-storage` in Termux and grant the storage "
                      "permission it asks for")

#: Every Termux:API command this project may use. Kept in one tuple so the
#: inventory a model receives and the allowlist the runner enforces cannot drift.
REQUIRED_COMMANDS: Tuple[str, ...] = (
    "termux-battery-status",
    "termux-brightness",
    "termux-call-log",
    "termux-camera-info",
    "termux-camera-photo",
    "termux-clipboard-get",
    "termux-clipboard-set",
    "termux-contact-list",
    "termux-dialog",
    "termux-download",
    "termux-fingerprint",
    "termux-infrared-frequencies",
    "termux-infrared-transmit",
    "termux-job-scheduler",
    "termux-location",
    "termux-media-player",
    "termux-media-scan",
    "termux-microphone-record",
    "termux-notification",
    "termux-notification-list",
    "termux-notification-remove",
    "termux-open",
    "termux-open-url",
    "termux-sensor",
    "termux-share",
    "termux-sms-list",
    "termux-sms-send",
    "termux-storage-get",
    "termux-telephony-call",
    "termux-telephony-cellinfo",
    "termux-telephony-deviceinfo",
    "termux-toast",
    "termux-torch",
    "termux-tts-engines",
    "termux-tts-speak",
    "termux-usb",
    "termux-vibrate",
    "termux-volume",
    "termux-wallpaper",
    "termux-wake-lock",
    "termux-wake-unlock",
    "termux-wifi-connectioninfo",
    "termux-wifi-enable",
    "termux-wifi-scaninfo",
)

#: Without these three a phone cannot even be described: battery, clipboard and
#: the notification channel. Their absence is what "the API is not installed"
#: means to a user, so a partial install is reported as not installed.
MANDATORY_COMMANDS: Tuple[str, ...] = (
    "termux-battery-status",
    "termux-clipboard-get",
    "termux-notification",
)

#: The Android permission groups behind the commands above. They start as
#: "unknown" and are only ever changed by a real observation - probing the camera
#: or the microphone just to learn a state would open hardware the caller never
#: asked for, which is a side effect a status call must not have.
PERMISSION_APIS: Tuple[str, ...] = (
    "biometric",
    "call_log",
    "camera",
    "clipboard",
    "contacts",
    "location",
    "microphone",
    "notification",
    "sensors",
    "sms",
    "storage",
    "telephony",
    "usb",
    "wifi",
)

#: Which permission a failing command was exercising. A command missing from this
#: map still reports ``denied``; it just cannot name the permission to remember.
_COMMAND_API: Dict[str, str] = {
    "termux-call-log": "call_log",
    "termux-camera-info": "camera",
    "termux-camera-photo": "camera",
    "termux-clipboard-get": "clipboard",
    "termux-clipboard-set": "clipboard",
    "termux-contact-list": "contacts",
    "termux-fingerprint": "biometric",
    "termux-location": "location",
    "termux-microphone-record": "microphone",
    "termux-notification": "notification",
    "termux-notification-list": "notification",
    "termux-notification-remove": "notification",
    "termux-sensor": "sensors",
    "termux-sms-list": "sms",
    "termux-sms-send": "sms",
    "termux-storage-get": "storage",
    "termux-telephony-call": "telephony",
    "termux-telephony-cellinfo": "telephony",
    "termux-telephony-deviceinfo": "telephony",
    "termux-usb": "usb",
    "termux-wifi-connectioninfo": "wifi",
    "termux-wifi-enable": "wifi",
    "termux-wifi-scaninfo": "wifi",
}

#: Failure text from the Termux:API app that always means "Android refused this
#: call", whichever API is behind it.
_PERMISSION_PHRASES: Tuple[str, ...] = (
    "permission denied",
    "not granted",
    "requires permission",
    "securityexception",
    "location services",
    "user denied",
    "no such file or directory: /dev/video",
    # The wording a real phone used (OPPO PHY110, Android 16), delivered as JSON on
    # stdout with exit code 0: {"error": "Please grant the following permission to
    # use this command: android.permission.ACCESS_COARSE_LOCATION"}.
    "please grant",
)

#: Words that turn a plain noun ("camera", "microphone") into a permission
#: failure. The nouns alone are not enough: "no camera found" is a hardware
#: answer, not a permission one, and must not be reported as "grant this".
_PERMISSION_CONTEXT: Tuple[str, ...] = (
    "permission",
    "denied",
    "not granted",
    "securityexception",
    "security exception",
    "not allowed",
    "forbidden",
    "unauthorized",
    "requires",
)

#: Where Termux really lives. ``$PREFIX`` is not trusted on its own - many build
#: systems export it - so the application data path is an independent signal.
_TERMUX_BIN_DIR = "/data/data/com.termux/files/usr/bin"

#: Default budget for one ``termux-*`` command, in seconds.
DEFAULT_TIMEOUT = 6.0

#: Wake-lock calls do not return data, so they get the same budget as anything else.
WAKE_LOCK_TIMEOUT = 6.0

#: The command inventory is a property of the install, not of the moment: a minute
#: of caching keeps a chatty model from running 44 PATH lookups per tool call.
_CACHE_TTL = 60.0

#: How long one timeout counts as evidence that the Termux:API **app** is
#: unreachable. Short on purpose: the operator may install the app, or Android may
#: unfreeze it, at any moment, and stale evidence is worse than no evidence.
APP_EVIDENCE_TTL = 120.0

_ACQUIRE = "termux-wake-lock"
_RELEASE = "termux-wake-unlock"
_WAKE_ACTIONS: Dict[str, str] = {"acquire": _ACQUIRE, "release": _RELEASE}

#: Windows: keep a console window from flashing for a phone command that cannot
#: exist there anyway (the flag is 0 everywhere else).
_POPEN_FLAGS = 0x08000000 if os.name == "nt" else 0

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()

#: ``api_installed``/``capabilities`` caches and the observation-backed permission
#: map are separate locks: a status call must not wait on a camera probe.
_PERMISSIONS: Dict[str, str] = {name: "unknown" for name in PERMISSION_APIS}
_PERMISSION_LOCK = threading.RLock()

#: Is the Termux:API *app* answering? The commands can all exist while the app is
#: missing, and then every one of them hangs: on a real phone that cost one
#: timeout per telemetry section, so the node has to remember the shared cause
#: instead of reporting five unrelated "timed out" blocks. Guarded by ``_CACHE_LOCK``
#: because it is capability evidence, exactly like the cached blocks beside it.
_APP_SUCCESS = False          #: a call has been answered since the last reset
_APP_TIMEOUT_COMMAND = ""     #: which command last hung (kept for diagnostics)
_APP_TIMEOUT_AT = 0.0         #: ``time.monotonic()`` of that hang

#: How many times *this process* has taken the wake lock. Held across the command
#: itself, because "exactly one acquire and one release" is the invariant Android
#: cares about and a race between two threads would break it.
_WAKE_COUNT = 0
_WAKE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Seams: the two things tests replace, plus decoding
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

    Order matters: every single-byte code page "succeeds" at decoding anything, so
    UTF-8 (what Termux writes) has to be tried before the Windows OEM page (what
    cmd.exe and the native console tools write) and before ANSI, the last resort.
    """
    global _DECODINGS
    if _DECODINGS is not None:
        return _DECODINGS
    candidates = ["utf-8"]
    if os.name == "nt":
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
    """Decode captured bytes without mojibake, with line endings normalised.

    A stray ``\\r`` would end up inside a path or a JSON key on the way back to a
    model, so ``\\r\\n`` and lone ``\\r`` both become ``\\n`` here rather than in
    every reader.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(data, (bytes, bytearray)):
        for encoding in _decodings():
            try:
                return (bytes(data).decode(encoding).replace("\r\n", "\n")
                        .replace("\r", "\n").strip())
            except (UnicodeDecodeError, LookupError):
                continue
        return bytes(data).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n").strip()
    return str(data).strip()


def _which(name: str) -> Optional[str]:
    """``shutil.which`` behind a seam, so tests never need a PATH shim."""
    try:
        return shutil.which(name)
    except Exception:
        return None


def _run(argv: Sequence[str], timeout: float) -> Tuple[int, str]:
    """Run one argv list: no shell, no stdin, and a hard timeout.

    ``subprocess.TimeoutExpired`` is deliberately **not** swallowed here: this
    module exists to explain a frozen Termux:API app, so the caller has to turn
    that exception into a timeout answer instead of a silent empty result. Any
    other failure to start the process is ``(-1, "")``, which the caller reports
    as a reason.
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
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        return -1, ""
    out = _decode(proc.stdout)
    if proc.returncode != 0:
        # A failing Termux:API command explains itself on stderr. On success stdout
        # is the answer - and an empty stdout is a legitimate empty clipboard, so
        # stderr is never substituted there.
        return int(proc.returncode), (_decode(proc.stderr) or out)
    return int(proc.returncode), out


# ---------------------------------------------------------------------------
# Platform detection and the command inventory
# ---------------------------------------------------------------------------

def is_android() -> bool:
    """True only inside Termux on Android.

    Three independent signals, any of which is enough: Termux exports
    ``TERMUX_VERSION``, its ``$PREFIX`` points inside ``com.termux``, and its
    application data directory has a fixed path. A generic ``PREFIX`` such as
    ``/usr/local`` is not a signal - build systems export it everywhere.
    """
    try:
        if os.environ.get("TERMUX_VERSION"):
            return True
        if "com.termux" in os.environ.get("PREFIX", ""):
            return True
        return os.path.isdir(_TERMUX_BIN_DIR)
    except Exception:
        return False


def api_installed(refresh: bool = False) -> Dict[str, Any]:
    """Which Termux:API commands this phone actually has, cached for a minute.

    ``installed`` answers the only question a user asks ("is the API working?"),
    which needs the mandatory trio; ``commands`` answers the one a model asks
    ("may I try termux-sensor?"). ``refresh=True`` is for the moment right after
    the installer ran ``pkg install termux-api``.
    """
    now = time.monotonic()
    if not refresh:
        with _CACHE_LOCK:
            hit = _CACHE.get("api_installed")
        if hit and hit[0] > now:
            return hit[1]
    try:
        commands = {name: bool(_which(name)) for name in REQUIRED_COMMANDS}
        missing = [name for name in REQUIRED_COMMANDS if not commands[name]]
        report: Dict[str, Any] = {
            "installed": all(commands.get(name, False) for name in MANDATORY_COMMANDS),
            "commands": commands,
            "missing": missing,
            "fix": TERMUX_API_FIX,
            "source": "shutil.which",
        }
    except Exception as exc:                     # a probe must never escape
        report = {
            "installed": False,
            "commands": {name: False for name in REQUIRED_COMMANDS},
            "missing": list(REQUIRED_COMMANDS),
            "fix": TERMUX_API_FIX,
            "source": "shutil.which",
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    with _CACHE_LOCK:
        _CACHE["api_installed"] = (now + _CACHE_TTL, report)
    return report


def _permission_snapshot() -> Dict[str, str]:
    """A copy of the permission map: a caller must not be able to edit our state."""
    with _PERMISSION_LOCK:
        return {name: _PERMISSIONS.get(name, "unknown") for name in sorted(_PERMISSIONS)}


def _command_exists(name: str) -> bool:
    try:
        return bool(_which(name))
    except Exception:
        return False


def _wake_state() -> Dict[str, Any]:
    """The in-process half of the wake-lock answer (``capabilities`` needs it)."""
    available = _command_exists(_ACQUIRE)
    with _WAKE_LOCK:
        return {"command": available,
                # None while the command is missing: a phone without the API
                # cannot tell us whether a lock is held, and guessing would be
                # worse than saying "unknown".
                "held": (bool(_WAKE_COUNT > 0) if available else None),
                "count": int(_WAKE_COUNT)}


def _record_app_timeout(command: str) -> None:
    """Remember that a ``termux-*`` call hung, which points at the app, not the package.

    The cached capabilities block is dropped with it: a reader that still sees
    ``app_reachable: True`` (or "unknown") would hide the observation that was just
    made, and telling the operator once is the whole point of remembering it.
    """
    global _APP_TIMEOUT_AT, _APP_TIMEOUT_COMMAND
    with _CACHE_LOCK:
        _APP_TIMEOUT_COMMAND = str(command)
        _APP_TIMEOUT_AT = time.monotonic()
        _CACHE.pop("capabilities", None)


def _record_app_success() -> None:
    """An answered call disproves every earlier timeout, so drop that evidence."""
    global _APP_SUCCESS, _APP_TIMEOUT_AT, _APP_TIMEOUT_COMMAND
    with _CACHE_LOCK:
        changed = (not _APP_SUCCESS) or bool(_APP_TIMEOUT_COMMAND)
        _APP_SUCCESS = True
        _APP_TIMEOUT_COMMAND = ""
        _APP_TIMEOUT_AT = 0.0
        if changed:
            # Only when the answer actually changes: a working phone must not
            # rebuild the capabilities block on every single successful call.
            _CACHE.pop("capabilities", None)


def _app_reachable() -> Optional[bool]:
    """Is the Termux:API app answering? ``None`` means "no evidence either way".

    A fresh node must not claim the app is broken, so the answer only becomes
    ``False`` when the newest evidence is a *recent* hang. A record older than
    :data:`APP_EVIDENCE_TTL` is no evidence at all, because the operator may have
    installed or opened the app in the meantime.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        succeeded = _APP_SUCCESS
        hung = bool(_APP_TIMEOUT_COMMAND)
        hung_at = _APP_TIMEOUT_AT
    if hung and (now - hung_at) <= APP_EVIDENCE_TTL:
        return False
    if succeeded:
        return True
    return None


def capabilities(refresh: bool = False) -> Dict[str, Any]:
    """A mergeable block for :mod:`core.device`'s capabilities section.

    It answers what a model needs before it tries something: is the API there,
    can the node reach shared storage, is a wake lock held, which permission a
    *previous* call proved to be missing, and - the one shared cause behind a
    phone whose every command hangs - whether the Termux:API **app** is
    answering at all (``app_reachable``: ``True``/``False``/``None`` for "no
    evidence", with ``app_fix`` present only when it is ``False``). Permission
    states are reported, never probed - opening the camera to learn whether the
    camera is allowed would be an unwanted side effect of a status call.
    """
    now = time.monotonic()
    if not refresh:
        with _CACHE_LOCK:
            hit = _CACHE.get("capabilities")
        if hit and hit[0] > now:
            return hit[1]
    value = _build_capabilities(refresh)
    with _CACHE_LOCK:
        _CACHE["capabilities"] = (now + _CACHE_TTL, value)
    return value


def _build_capabilities(refresh: bool) -> Dict[str, Any]:
    fixes: List[str] = []
    try:
        api = api_installed(refresh=refresh)
    except Exception as exc:
        api = {"installed": False, "commands": {}, "missing": list(REQUIRED_COMMANDS),
               "fix": TERMUX_API_FIX, "source": "shutil.which",
               "error": "%s: %s" % (type(exc).__name__, exc)}
    if not api.get("installed"):
        fixes.append(TERMUX_API_FIX)

    try:
        storage_permission = bool(os.path.isdir(os.path.expanduser("~/storage/shared")))
    except Exception:
        storage_permission = False
    if not storage_permission:
        fixes.append(TERMUX_STORAGE_FIX)

    wake = _wake_state()
    if wake.get("command") is not True:
        fixes.append(TERMUX_API_FIX)

    permissions = _permission_snapshot()
    for api_name in permissions:
        if permissions[api_name] == "denied":
            fixes.append(_permission_fix(api_name))

    unique: List[str] = []
    for fix in fixes:
        if fix and fix not in unique:
            unique.append(fix)
    app_reachable = _app_reachable()
    block: Dict[str, Any] = {
        "api": api,
        "storage_permission": storage_permission,
        "wake_lock": wake,
        "permissions": permissions,
        "fixes": unique,
        "app_reachable": app_reachable,
    }
    if app_reachable is False:
        # Deliberately a key of its own rather than another entry in "fixes": the
        # timeout fix is about the app, and a consumer merging this block into a
        # device report has to be able to tell the two kinds of fix apart.
        block["app_fix"] = TERMUX_TIMEOUT_FIX
    return block


# ---------------------------------------------------------------------------
# Running one command
# ---------------------------------------------------------------------------

def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000.0))


def _failure(command: str, argv: Sequence[str], started: float, reason: str,
             **fields: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "command": command,
        "argv": list(argv),
        "duration_ms": _elapsed_ms(started),
    }
    result.update(fields)
    return result


def _allowed(name: str) -> bool:
    """The security guard: only bare ``termux-*`` command names may be executed.

    A caller must never be able to turn a tool argument into an arbitrary
    execution, so the prefix is checked *and* a path separator is refused -
    ``termux-../../bin/sh`` satisfies the prefix while escaping the allowlist.
    """
    if not name.startswith("termux-"):
        return False
    return not any(separator in name for separator in ("/", "\\"))


def _bounded(timeout: Any) -> float:
    """A timeout that is always usable, whatever the caller passed."""
    try:
        return max(0.2, float(timeout))
    except Exception:
        return DEFAULT_TIMEOUT


def _seconds(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def run_json(command: str, args: Sequence[str] = (),
             timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Run one ``termux-*`` command and return its JSON answer as ``value``.

    Non-JSON stdout is **not** an error: several commands (``termux-open``,
    ``termux-toast``) answer with a sentence or with nothing at all, and a model
    is better served by the text plus ``parsed: False`` than by a failure.
    """
    return _execute(command, args, timeout, parse=True)


def run_text(command: str, args: Sequence[str] = (),
             timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Run one ``termux-*`` command and return its stdout as ``value``."""
    return _execute(command, args, timeout, parse=False)


def _execute(command: str, args: Sequence[str], timeout: float, parse: bool) -> Dict[str, Any]:
    started = time.monotonic()
    name = str(command or "")
    if isinstance(args, (str, bytes)):
        # A bare string is one argument, never a split on spaces: splitting is
        # exactly the step that would turn caller text into extra options.
        extra = [args if isinstance(args, str) else _decode(args)]
    else:
        try:
            extra = [str(item) for item in (args or ())]
        except Exception:
            extra = []
    argv = [name] + extra

    if not _allowed(name):
        return _failure(name, argv, started,
                        "refused: %r is not a termux-* command" % name)

    if not _command_exists(name):
        # Nothing is executed when the command is absent: the failure itself is
        # the answer, and running a "help" invocation would just be noise.
        return _failure(name, argv, started,
                        "the %s command is not installed" % name,
                        missing=True, fix=TERMUX_API_FIX)

    try:
        rc, text = _run(argv, _bounded(timeout))
    except subprocess.TimeoutExpired as exc:
        # One hang is evidence about the app, not about this command: record it so
        # the next capabilities() call can explain the whole phone at once.
        _record_app_timeout(name)
        fields: Dict[str, Any] = {"timeout": True, "fix": TERMUX_TIMEOUT_FIX}
        partial = _decode(getattr(exc, "stdout", None) or getattr(exc, "stderr", None))
        if partial:
            fields["raw"] = partial
        seconds = _seconds(getattr(exc, "timeout", None)) or _seconds(timeout)
        return _failure(name, argv, started,
                        "%s timed out after %gs" % (name, seconds), **fields)
    except Exception as exc:
        return _failure(name, argv, started,
                        "%s could not be run: %s" % (name, exc))

    # The seam may hand back text or bytes; normalising here keeps the contract of
    # the result dict the same whichever way it arrived.
    raw = _decode(text)
    if rc != 0:
        reason = raw.strip() or "%s exited with %d" % (name, rc)
        fields = _diagnose(raw, name)
        fields["raw"] = raw
        return _failure(name, argv, started, reason, **fields)

    value: Any = raw
    extra_fields: Dict[str, Any] = {}
    if parse:
        try:
            value = json.loads(raw)
        except Exception:
            value = raw
            extra_fields["parsed"] = False
    # A refused permission arrives as a SUCCESSFUL call: the app prints
    # {"error": "Please grant the following permission to use this command: ..."}
    # on stdout and exits 0. Reading only the exit code therefore turned "Android
    # said no" into an empty answer - the model saw a phone with no cell data
    # instead of a phone that needs one permission granted.
    if isinstance(value, dict):
        message = value.get("error")
        if isinstance(message, str) and message.strip():
            fields = _diagnose(message, name)
            fields["raw"] = raw
            return _failure(name, argv, started, message.strip(), **fields)
    # Only an answered call counts. A non-zero exit is not proof that the app is
    # alive - the honest reading of "the app is unreachable" is the one that
    # requires positive evidence before it is cleared.
    _record_app_success()
    result: Dict[str, Any] = {
        "ok": True,
        "value": value,
        "raw": raw,
        "command": name,
        "argv": argv,
        "duration_ms": _elapsed_ms(started),
    }
    result.update(extra_fields)
    return result


def sensor_sample(name: str, timeout: float = 8.0) -> Dict[str, Any]:
    """One value from one named sensor.

    ``-n 1`` is built here and can never come from the caller: without it
    ``termux-sensor`` streams samples until it is killed, so a model asking for a
    single reading would hold the tool open until its own timeout expires.
    """
    sensor = str(name or "").strip()
    return run_json("termux-sensor", ["-s", sensor, "-n", "1"], timeout)


# ---------------------------------------------------------------------------
# Failure text -> a fix and a remembered permission
# ---------------------------------------------------------------------------

def _permission_fix(api: str) -> str:
    return ("grant the %s permission to Termux:API in Android Settings > Apps > "
            "Termux:API > Permissions" % api)


def _api_name(api: str) -> str:
    """Normalise a permission name, accepting a command name as a shorthand."""
    name = str(api or "").strip().lower()
    if not name:
        return ""
    return _COMMAND_API.get(name, name)


def record_permission_state(api: str, state: str) -> None:
    """Remember what a real call proved about one Android permission.

    Only an observed call may call this: asking Android up front would mean
    opening the camera or the microphone to read a state, which is the side
    effect this module refuses to have. The cached capabilities block is dropped
    with it, so the next reader sees the new fact instead of a stale "unknown".
    """
    name = _api_name(api)
    wanted = str(state or "").strip().lower()
    if not name or wanted not in ("granted", "denied", "unknown"):
        return
    with _PERMISSION_LOCK:
        _PERMISSIONS[name] = wanted
    with _CACHE_LOCK:
        _CACHE.pop("capabilities", None)


def _diagnose(text: str, command: str = "") -> Dict[str, Any]:
    """Map a failing command's output to ``denied``/``fix`` and remember it.

    All of the "Android said no" vocabulary lives in this one function, so a new
    permission-backed command only has to be added to ``_COMMAND_API`` to be
    explained properly. An empty dict means "no permission story here" and lets
    the caller report the raw text unchanged.
    """
    lowered = (text or "").lower()
    if not lowered:
        return {}
    denied = any(phrase in lowered for phrase in _PERMISSION_PHRASES)
    if not denied and "camera" in lowered:
        denied = any(word in lowered for word in _PERMISSION_CONTEXT)
    if not denied and any(word in lowered for word in ("microphone", "audio record", "recording")):
        denied = any(word in lowered for word in _PERMISSION_CONTEXT)
    if not denied:
        return {}
    # The text is the more specific evidence, so it is read first: a command that
    # names a permission in its own message is believed over the command map.
    api = ""
    if "camera" in lowered or "/dev/video" in lowered:
        api = "camera"
    elif any(word in lowered for word in ("microphone", "audio record", "recording")):
        api = "microphone"
    elif "location" in lowered:
        api = "location"
    if not api:
        api = _COMMAND_API.get(str(command or ""), "")
    fields: Dict[str, Any] = {"denied": True, "fix": _permission_fix(api or "requested")}
    if api:
        record_permission_state(api, "denied")
    return fields


# ---------------------------------------------------------------------------
# Wake lock
# ---------------------------------------------------------------------------

def _wake_report(command: str, available: bool, reason: str = "",
                 **fields: Any) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "available": bool(available),
        # ``held`` is None while the command is missing: the honest answer is
        # "unknown", because a phone without the API cannot tell us either way.
        "held": (bool(_WAKE_COUNT > 0) if available else None),
        "count": int(_WAKE_COUNT),
        "command": command,
    }
    if reason:
        report["reason"] = reason
    report.update(fields)
    return report


def wake_lock(action: str = "status") -> Dict[str, Any]:
    """Take, drop or report the Android wake lock, reference counted in process.

    Android freezes a background process once the screen goes off, and the node's
    install-time boot script takes the lock exactly once with nobody left to
    release it. Counting the lock here means the system command runs only when the
    lock really changes state - once on the way up (0 -> 1) and once on the way
    down (1 -> 0) - so ``acquire`` twice and ``release`` twice still touch Android
    once each, and the count can never go below zero. There is no "is a lock
    held?" command in Termux:API, so ``held`` describes *this process's* count -
    the only truth available without root.
    """
    global _WAKE_COUNT
    wanted = str(action or "status").strip().lower()
    if wanted not in _WAKE_ACTIONS and wanted != "status":
        with _WAKE_LOCK:
            count = _WAKE_COUNT
        return {"available": False, "held": None, "count": int(count), "command": "",
                "reason": "refused: %r is not a wake-lock action" % (str(action),)}

    with _WAKE_LOCK:
        if wanted == "status":
            # No status command exists, so nothing may be executed here - running
            # the lock command to "check" would change the state it reports.
            return _wake_report(_ACQUIRE, _command_exists(_ACQUIRE))

        command = _WAKE_ACTIONS[wanted]
        if wanted == "acquire" and _WAKE_COUNT > 0:
            # Already held by this process: Android counts the lock, not the calls,
            # so a second acquire only moves our own counter.
            _WAKE_COUNT += 1
            return _wake_report(command, True)
        if wanted == "release":
            if _WAKE_COUNT == 0:
                # Nothing of ours is held: unlocking now would drop a lock some
                # other app took, and the ref-count is the only thing that can know.
                return _wake_report(command, _command_exists(command))
            if _WAKE_COUNT > 1:
                # Other holders remain, so the system lock must stay taken.
                _WAKE_COUNT -= 1
                return _wake_report(command, True)

        if not _command_exists(command):
            return _wake_report(command, False,
                                "the %s command is not installed" % command,
                                missing=True, fix=TERMUX_API_FIX)

        try:
            rc, text = _run([command], WAKE_LOCK_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            seconds = _seconds(getattr(exc, "timeout", None)) or WAKE_LOCK_TIMEOUT
            return _wake_report(command, True,
                                "%s timed out after %gs" % (command, seconds),
                                timeout=True, fix=TERMUX_TIMEOUT_FIX)
        except Exception as exc:
            return _wake_report(command, True, "%s could not be run: %s" % (command, exc))

        if rc != 0:
            fields = _diagnose(text, command)
            reason = (text or "").strip() or "%s exited with %d" % (command, rc)
            return _wake_report(command, True, reason, **fields)

        if wanted == "acquire":
            _WAKE_COUNT += 1
        else:
            # Only a command that really ran gives the count back; a failed unlock
            # leaves the lock held, which is what the phone still believes.
            _WAKE_COUNT = max(0, _WAKE_COUNT - 1)
        return _wake_report(command, True)


# ---------------------------------------------------------------------------
# Test seam
# ---------------------------------------------------------------------------

def reset_state() -> None:
    """Drop every in-process cache: the inventory, capabilities, the lock count,
    the permission map and the app evidence, as if this module had just been
    imported."""
    global _WAKE_COUNT, _APP_SUCCESS, _APP_TIMEOUT_AT, _APP_TIMEOUT_COMMAND
    with _CACHE_LOCK:
        _CACHE.clear()
        _APP_SUCCESS = False
        _APP_TIMEOUT_COMMAND = ""
        _APP_TIMEOUT_AT = 0.0
    with _PERMISSION_LOCK:
        _PERMISSIONS.clear()
        _PERMISSIONS.update({name: "unknown" for name in PERMISSION_APIS})
    with _WAKE_LOCK:
        _WAKE_COUNT = 0
