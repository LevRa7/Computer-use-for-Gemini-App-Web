#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/probe-node.py - talk to a live node through the Mesh gateway.

Two jobs, both about not guessing:

1. **Capture fixtures.** The parsers in ``core/device.py`` and ``core/termux.py``
   are written against the *documented* JSON of termux-api, and Android changes
   it between releases. This script asks a real phone for the real answers and
   writes them to ``tests/fixtures/termux/``, so the test suite runs on a laptop
   against what the phone actually sends - including the failure shapes
   ("command not found", "permission denied"), which are the ones the error
   mapping must recognise.

2. **Verify a live node.** ``--check`` runs the same commands and prints what the
   node answered, which is how a new release is confirmed on a real device before
   anyone trusts it.

It is deliberately read-only: telemetry and device identity only. Camera,
microphone, location, clipboard, SMS, contacts and the call log are NOT touched -
a fixture is worth nothing if producing it means spying on the operator.

Usage::

    python tools/probe-node.py --url https://mesh.example.com \
        --user pixel7pro --token <secret> [--out tests/fixtures/termux] [--check]

    # or point it at the same variables the node itself uses
    MESH_PUBLIC_URL=https://mesh.example.com MESH_USER=pixel7pro MESH_TOKEN=... \
        python tools/probe-node.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

#: name -> the command run on the node. Read-only by construction: nothing here
#: writes a file, sets a volume, posts a notification or opens a capture device.
PROBES: Tuple[Tuple[str, str, str], ...] = (
    ("system_info", "system_info", "tool"),
    ("getprop-model", "getprop ro.product.model", "text"),
    ("getprop-manufacturer", "getprop ro.product.manufacturer", "text"),
    ("getprop-android", "getprop ro.build.version.release", "text"),
    ("getprop-sdk", "getprop ro.build.version.sdk", "text"),
    ("getprop-characteristics", "getprop ro.build.characteristics", "text"),
    ("getprop-locale", "getprop persist.sys.locale", "text"),
    ("getprop-timezone", "getprop persist.sys.timezone", "text"),
    ("uname", "uname -m", "text"),
    ("termux-api-installed", "for c in termux-battery-status termux-wifi-connectioninfo "
                             "termux-telephony-deviceinfo termux-telephony-cellinfo "
                             "termux-sensor termux-camera-info termux-microphone-record "
                             "termux-location termux-notification termux-clipboard-get "
                             "termux-volume termux-tts-speak termux-torch termux-vibrate "
                             "termux-wake-lock; do command -v \"$c\" || echo \"MISSING $c\"; done",
     "text"),    ("battery-status", "termux-battery-status", "json"),
    ("wifi-connectioninfo", "termux-wifi-connectioninfo", "json"),
    ("wifi-scaninfo", "termux-wifi-scaninfo", "json"),
    ("telephony-deviceinfo", "termux-telephony-deviceinfo", "json"),
    ("telephony-cellinfo", "termux-telephony-cellinfo", "json"),
    ("sensor-list", "termux-sensor -l", "json"),
    ("camera-info", "termux-camera-info", "json"),
    ("tts-engines", "termux-tts-engines", "json"),
    ("volume", "termux-volume", "json"),
    ("notification-list", "termux-notification-list", "json"),
    ("usb-list", "termux-usb -l", "json"),
    ("infrared-frequencies", "termux-infrared-frequencies", "json"),
    ("storage-shared", "test -d \"$HOME/storage/shared\" && echo present || echo absent", "text"),
    ("proc-meminfo", "head -5 /proc/meminfo", "text"),
    ("thermal-zones", "for z in /sys/class/thermal/thermal_zone*; do "
                      "echo \"$(cat $z/type)=$(cat $z/temp)\"; done", "text"),
    ("cpu-freq", "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; "
                 "cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "text"),
)


def _post(url: str, payload: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    """One JSON-RPC POST to the gateway, returning the parsed body.

    The gateway answers in the HTTP body (never only on the caller's stream), so a
    plain request is enough - no SSE reader, no session bookkeeping.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit("gateway answered HTTP %s: %s" % (exc.code, body[:400]))
    if body.lstrip().startswith("event:") or "\ndata: " in body:
        # Legacy SSE framing: take the first data line and parse that.
        for line in body.splitlines():
            if line.startswith("data: "):
                body = line[len("data: "):]
                break
    try:
        return json.loads(body)
    except Exception:
        raise SystemExit("gateway answered something that is not JSON: %r" % body[:400])


def _guard(command: str) -> str:
    """Wrap a command so a frozen Termux:API app cannot hang the probe.

    The known failure mode of termux-api without its app is a call that never
    returns; ``timeout`` (coreutils, present in Termux) turns that into exit code
    124 and a line of output, which is a fixture worth having.

    Deliberately NO output redirection: a node may run a command policy that treats
    ``2>/dev/null`` as "writes into a system path" and refuses the whole call (this
    happened on the reference phone - the same command without the redirect was
    allowed). The probe asks for telemetry, so it must not look like a write.
    """
    if not command.startswith("termux-"):
        return command
    return "timeout 8 %s" % command


def call_tool(url: str, name: str, arguments: Optional[Dict[str, Any]] = None,
              timeout: float = 90.0) -> Tuple[str, bool]:
    """Run one tool on the node; return ``(text, is_error)``."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments or {}}}
    body = _post(url, payload, timeout=timeout)
    if "error" in body:
        return json.dumps(body["error"], ensure_ascii=False), True
    result = body.get("result") or {}
    parts: List[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts), bool(result.get("isError"))


def _classify(name: str, text: str, kind: str) -> Tuple[str, str]:
    """Return ``(extension, status)`` for a captured answer."""
    if not text.strip():
        return "txt", "empty"
    if text.startswith("[Error]") or text.startswith("ERROR"):
        return "txt", "error"
    if "not found" in text.lower() and "command" in text.lower():
        return "txt", "missing"
    # `timeout 8 termux-...` was killed: on Android that is the frozen-Termux:API-app
    # failure, and it is a fixture in its own right - it is what the error mapping
    # has to recognise on a phone whose app was never opened.
    if "[Exit code: 124]" in text or "exit code: 124" in text.lower():
        return "txt", "timeout"
    # A node-side command policy refused the call. Never record that as an answer
    # from the device: it says nothing about the phone.
    if "[Confirmation required]" in text:
        return "txt", "blocked"
    if kind == "json":
        try:
            json.loads(text)
            return "json", "ok"
        except Exception:
            # termux-sensor -l and friends answer one JSON object per line.
            lines = [line for line in text.splitlines() if line.strip()]
            if lines and all(_is_json(line) for line in lines):
                return "jsonl", "ok"
            return "txt", "unparsed"
    return "txt", "ok"


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def _safe(text: str) -> str:
    """Strip anything that looks like a secret before it lands in a fixture."""
    return re.sub(r"(token=)[A-Za-z0-9._-]+", r"\1<redacted>", text)


def capture(url: str, out_dir: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    manifest: Dict[str, Any] = {"source": "tools/probe-node.py", "probes": {}}
    failures = 0
    for name, command, kind in PROBES:
        if kind == "tool":
            text, is_error = call_tool(url, name, {})
        else:
            text, is_error = call_tool(url, "bash_exec", {"command": _guard(command),
                                                          "timeout_sec": 25})
        extension, status = _classify(name, text, kind)
        if is_error:
            status = "error"
        path = os.path.join(out_dir, "%s.%s" % (name, extension))
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_safe(text).rstrip() + "\n")
        manifest["probes"][name] = {"command": command if kind != "tool" else "tools/call",
                                    "file": os.path.basename(path), "status": status}
        if status not in ("ok",):
            failures += 1
        marker = "ok" if status == "ok" else status.upper()
        print("  %-24s %s" % (name, marker))
    manifest["failed_or_empty"] = sorted(
        name for name, item in manifest["probes"].items() if item["status"] != "ok")
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("\nfixtures written to %s (%d probes, %d without a clean answer)"
          % (out_dir, len(PROBES), failures))
    return 0


def check(url: str) -> int:
    text, is_error = call_tool(url, "mesh_status", {})
    print("--- mesh_status (isError=%s)" % is_error)
    print(text)
    text, is_error = call_tool(url, "system_info", {})
    print("\n--- system_info (isError=%s)" % is_error)
    print(text)
    for name, command, _kind in PROBES:
        if name in ("system_info",):
            continue
        text, is_error = call_tool(url, "bash_exec", {"command": _guard(command),
                                                      "timeout_sec": 25})
        first = text.strip().splitlines()[0] if text.strip() else "(no output)"
        print("  %-24s %s" % (name, first[:110]))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESH_PUBLIC_URL")
                        or os.environ.get("AGY_PUBLIC_BASE_URL") or "",
                        help="gateway base URL, e.g. https://mesh.example.com")
    parser.add_argument("--user", default=os.environ.get("MESH_USER") or "",
                        help="node name (the user= part of the MCP link)")
    parser.add_argument("--token", default=os.environ.get("MESH_TOKEN") or "",
                        help="node token (the token= part of the MCP link)")
    parser.add_argument("--out", default=os.path.join("tests", "fixtures", "termux"),
                        help="where captured fixtures are written")
    parser.add_argument("--check", action="store_true",
                        help="print what the node answers instead of writing fixtures")
    parser.add_argument("--cmd", default="",
                        help="run one shell command on the node and print its answer")
    parser.add_argument("--timeout-sec", type=int, default=25,
                        help="timeout for --cmd (1-120, default 25)")
    args = parser.parse_args(argv)

    if not args.url or not args.user or not args.token:
        parser.error("--url, --user and --token are required "
                     "(or set MESH_PUBLIC_URL, MESH_USER and MESH_TOKEN)")
    base = args.url.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    url = "%s/mcp?user=%s&token=%s" % (base, args.user, args.token)
    print("node: %s via %s" % (args.user, base))
    if args.cmd:
        text, is_error = call_tool(url, "bash_exec",
                                   {"command": args.cmd, "timeout_sec": args.timeout_sec})
        print(text)
        return 1 if is_error else 0
    if args.check:
        return check(url)
    return capture(url, args.out)


if __name__ == "__main__":                                        # pragma: no cover
    raise SystemExit(main())
