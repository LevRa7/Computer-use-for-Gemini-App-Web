# Termux fixtures: what a real phone actually answers

These files were **captured from a real Android phone**, not written by hand:

| | |
| :--- | :--- |
| Device | OPPO **PHY110**, Android **16** (SDK 36), `aarch64`, 4 kB pages |
| Locale / timezone | `ru-RU` / `Asia/Tbilisi` |
| Termux | `0.118.3`, installed from **GitHub** (the add-on must be signed with the same key) |
| Termux:API app | `0.53.0+github.debug`, installed over adb wireless debugging |
| Node | `localhost-602`, reached through the live Mesh gateway |
| Tool | [`tools/probe-node.py`](../../../tools/probe-node.py) (or [`tools/capture-termux-fixtures.sh`](../../../tools/capture-termux-fixtures.sh) on the phone itself) |
| Captured | 2026-10-09, after the Android permissions were granted |

The point of keeping them is that the parsers in `core/device.py` and
`core/termux.py` are otherwise written against *documented* shapes, and the real
answers differ from the documentation. Two bugs came from exactly this gap:

* `termux-wifi-connectioninfo` names the channel **`frequency_mhz`** (the parser read
  `frequency`, so the frequency was silently lost);
* a refused permission arrives as a **successful call** whose stdout is
  `{"error": "Please grant the following permission ..."}` with exit code 0, which
  used to be read as an empty answer instead of a refusal.

Both are now pinned by `tests/test_phone_payloads.py`, which loads these files.

## What each file is

* **Real answers** — everything else in this directory: `battery-status.json`
  (94 %, Li-ion, 33 °C, `DISCHARGING`), `wifi-connectioninfo.json` (SSID, BSSID,
  RSSI −65 dBm, 5220 MHz, 234 Mbps), `telephony-deviceinfo.json` (operator,
  `lte`, roaming, data/SIM state), `telephony-cellinfo.json` (two cells; the
  registered one is `dbm: -102, level: 2`), `camera-info.json` (4 cameras with
  `jpeg_output_sizes`), `sensor-list.json` (46 sensors), `volume.json`,
  `tts-engines.json`, `usb-list.json`, `infrared-frequencies.json`, plus the
  file-backed sources (`getprop-*.txt`, `proc-meminfo.txt`, `thermal-zones.txt`,
  `cpu-freq.txt`, `storage-shared.txt`, `system_info.txt`).
* **A captured refusal** — `permission-denied.json`: the exact body
  `termux-telephony-cellinfo` sent *before* the location permission was granted.
  It is kept on purpose: `telephony-cellinfo.json` now holds real cells, so without
  this file the refusal shape would stop being tested and the regression would come
  back silently on the next phone.
* `notification-list.txt` — captured while the phone had no active notifications
  (the node reports an empty successful command, which is why it is not JSON).
* `termux-api-installed.txt` — which commands the phone had at capture time.
* `MANIFEST.json` — the probe's own record: every command, its file, and whether it
  answered cleanly.

## Deliberately not captured

Camera frames, microphone recordings, the clipboard, the location fix, SMS, the
call log and contacts. Producing a fixture is not a reason to read the operator's
private data; those code paths are covered by the fake-adapter tests in
`tests/test_device_tools.py` instead.

## Redacted before publishing

The shapes are real, the identities are not. Committed values that would describe a
real place or a real subscriber were replaced **after** capture, without touching
anything the parsers are tested on:

* `wifi-connectioninfo.json` — the home SSID, BSSID, MAC and LAN address became
  `HomeNetwork`, `02:00:00:00:00:01`, `02:00:00:00:00:02`, `192.168.1.100`; the
  measured `rssi`, `frequency_mhz` and `link_speed_mbps` are untouched.
* `wifi-scaninfo.json` — all fifteen neighbouring networks were renamed
  `Neighbour-01…15` with placeholder BSSIDs; their signal levels and channels stay.
* `telephony-deviceinfo.json` — operator name/code and country became `Example`,
  `00000`, `zz` (`network_type`, roaming and the data/SIM states stay real).
* `telephony-cellinfo.json` — `ci`, `pci`, `tac`, `mcc`, `mnc` became `0` (or stayed
  `null` where the phone reported none); `dbm`, `level`, `rsrp`, `rsrq`, `rssi`,
  `asu`, `bands` and `registered` stay real, which is what the parser tests assert.
* `getprop-*`, `system_info.txt`, `proc-meminfo.txt`, `thermal-zones.txt`,
  `cpu-freq.txt` — device facts only (model, Android release, locale, timezone,
  memory, thermals), no personal identifiers.

If you re-capture on your own phone, redact the same fields before committing.


## Re-capturing

```bash
python tools/probe-node.py --url https://<gateway> --user <node> --token <secret>
```

Two things worth knowing before you run it:

1. **No output redirection.** A node may enforce a command policy that reads
   `2>/dev/null` as "writes into a system path" and refuses the whole call (this
   phone does). The probe never uses redirections for that reason.
2. **A phone whose Termux:API app is missing or frozen hangs instead of failing.**
   The probe wraps each `termux-*` call in `timeout 8`, and the captured exit code
   124 is itself a useful fixture — it is what the error mapping must recognise.
