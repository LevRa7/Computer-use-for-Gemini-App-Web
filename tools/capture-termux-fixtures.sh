#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
#  Capture real Termux:API answers into tests/fixtures/termux/
#
#  Why this exists: the parsers in core/device.py and core/termux.py are written
#  against the *documented* JSON of termux-api, and Android changes it between
#  releases (a field is renamed, a list becomes an object, a permission turns a
#  value into null). A fixture captured on a real phone is the only way to prove
#  the node reads what the phone actually sends, and it lets the test suite keep
#  running on a laptop where no termux-* command exists at all.
#
#  Run it ON the phone, inside Termux, from the repository root:
#
#      bash tools/capture-termux-fixtures.sh
#
#  It writes one JSON file per command plus a small MANIFEST.json describing the
#  device and which commands answered. Nothing here needs root; a command that is
#  missing, refused or empty is recorded as such (that is a fixture too - the
#  "permission denied" shape is exactly what the error mapping must recognise).
#
#  Review the output before committing it: it is device telemetry. Contacts, SMS
#  and the call log are deliberately NOT captured by this script.
# ==============================================================================
set -u

OUT_DIR="${1:-tests/fixtures/termux}"
mkdir -p "$OUT_DIR" || exit 1

log() { printf '%s\n' "$*"; }

# name|command|arguments
COMMANDS="
battery-status|termux-battery-status|
wifi-connectioninfo|termux-wifi-connectioninfo|
wifi-scaninfo|termux-wifi-scaninfo|
telephony-deviceinfo|termux-telephony-deviceinfo|
telephony-cellinfo|termux-telephony-cellinfo|
camera-info|termux-camera-info|
sensor-list|termux-sensor|-l
sensor-sample|termux-sensor|-s acceleration -n 1
volume|termux-volume|
notification-list|termux-notification-list|
tts-engines|termux-tts-engines|
usb-list|termux-usb|-l
infrared-frequencies|termux-infrared-frequencies|
location|termux-location|-p gps
fingerprint|termux-fingerprint|
"

manifest="$OUT_DIR/MANIFEST.json"
{
  printf '{\n'
  printf '  "captured_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "termux_version": "%s",\n' "${TERMUX_VERSION:-unknown}"
  printf '  "prefix": "%s",\n' "${PREFIX:-unknown}"
  printf '  "model": "%s",\n' "$(getprop ro.product.model 2>/dev/null)"
  printf '  "manufacturer": "%s",\n' "$(getprop ro.product.manufacturer 2>/dev/null)"
  printf '  "android_release": "%s",\n' "$(getprop ro.build.version.release 2>/dev/null)"
  printf '  "android_sdk": "%s",\n' "$(getprop ro.build.version.sdk 2>/dev/null)"
  printf '  "characteristics": "%s",\n' "$(getprop ro.build.characteristics 2>/dev/null)"
  printf '  "locale": "%s",\n' "$(getprop persist.sys.locale 2>/dev/null)"
  printf '  "timezone": "%s",\n' "$(getprop persist.sys.timezone 2>/dev/null)"
  printf '  "storage_shared": %s,\n' "$([ -d "$HOME/storage/shared" ] && echo true || echo false)"
  printf '  "commands": {\n'
  first=1
} > "$manifest"

while IFS='|' read -r name command args; do
  [ -n "$name" ] || continue
  target="$OUT_DIR/$name.json"
  if ! command -v "$command" >/dev/null 2>&1; then
    printf '{"_capture_error": "command not found: %s"}\n' "$command" > "$target"
    status="missing"
    log "  - $name: $command is not installed"
  else
    # shellcheck disable=SC2086
    if output=$($command $args 2>"$target.err"); then
      if [ -n "$output" ]; then
        printf '%s\n' "$output" > "$target"
        status="ok"
      else
        printf '{"_capture_error": "empty answer"}\n' > "$target"
        status="empty"
      fi
    else
      # The failure text is the fixture: this is the shape the error mapping reads.
      printf '{"_capture_error": %s}\n' "$(cat "$target.err" | tr -d '\r' | head -c 400 | sed 's/\\/\\\\/g; s/"/\\"/g; s/$//' | awk '{printf "%s\\n", $0}' | sed 's/^/"/; s/\\n"$/" /' | tr -d '\n')" > "$target"
      status="failed"
      log "  - $name: $command refused (recorded)"
    fi
    rm -f "$target.err"
  fi
  if [ "$first" = 1 ]; then first=0; else printf ',\n' >> "$manifest"; fi
  printf '    "%s": "%s"' "$name" "$status" >> "$manifest"
done <<EOF
$(printf '%s\n' "$COMMANDS" | sed '/^[[:space:]]*$/d')
EOF

{
  printf '\n  }\n'
  printf '}\n'
} >> "$manifest"

log ""
log "Fixtures written to $OUT_DIR"
log "Next: review them (they are real device telemetry), then commit and run"
log "      python -m pytest -q tests/test_device_scenarios.py tests/test_termux_adapter.py"
