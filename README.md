# рџ“± Gemini Computer Use вЂ” control any PC from Gemini Spark (App & Web)

> **An MCP server that turns Google Gemini from a chatbot into a full-fledged mobile agent.**
> Type a task in the Gemini app on your phone вЂ” your PC, laptop or server executes it, and Gemini reports back with real results.

[English](README.md) | [рџ‡·рџ‡є Р СѓСЃСЃРєР°СЏ РІРµСЂСЃРёСЏ](#-СЂСѓСЃСЃРєР°СЏ-РІРµСЂСЃРёСЏ)

---

## рџ’Ў The idea

Gemini in a browser or on a phone can talk, but it cannot *do* anything on your computer.
**Gemini Computer Use** closes that gap. It is a free, open-source **Model Context Protocol (MCP)** server that you connect to **Gemini Spark** ([gemini.google.com/spark/apps](https://gemini.google.com/spark/apps) or the Gemini mobile app) with a single link. After that, Gemini gets hands:

- runs shell commands, builds and tests code, manages git;
- reads, writes and edits files;
- searches the file system;
- starts long-running jobs and checks on them later;
- watches CPU, RAM and disk.

```text
 рџ“± Gemini app / рџЊђ gemini.google.com (Spark)
              в”‚  MCP (SSE / Streamable HTTP)
              в–ј
     вЃпёЏ  Gateway  racknerd-5a24bf9.merino-carob.ts.net   в†ђ relay only, TLS
              в”‚  outbound WebSocket tunnel (no open ports)
              в–ј
   рџ’» Your PC В· laptop В· VPS В· home server  в†’  executes the task
```

**Result:** your phone becomes a remote control for every machine you own вЂ” and Gemini is the agent that operates them.

---

## рџ”Ґ What you can do with it

Just ask Gemini in plain language, from anywhere:

- *"Check why my home server is slow and show the top processes"*
- *"Pull the latest changes in ~/projects/api, run the tests and tell me what failed"*
- *"Fix the typo in config.yaml and restart the service"*
- *"Start a full backup in the background and tell me when it's done"*
- *"How much free disk space is left on my laptop?"*
- *"Turn on the phone's torch, and tell me its battery level and signal"*

No SSH client, no terminal on your phone, no VPN вЂ” only a chat.

---

## вњЁ Why it works this way

| | |
| :--- | :--- |
| рџ“± **Mobile-first agent** | Works in the official Gemini mobile app and on the web through Gemini Spark. |
| рџ–ҐпёЏ **Any PC** | Linux, macOS, Windows вЂ” desktops, laptops, VPS, containers. **Android phones and tablets** run the same node inside Termux. |
| рџЊђ **Behind any NAT** | The node opens an *outbound* WebSocket tunnel. No public IP, no port forwarding, no router setup. |
| вљЎ **One-line install** | Detects OS, architecture and device type; installs autostart (`systemd` / `launchd` / Windows task / runit + Termux:Boot on Android); copies your connection link to the clipboard. |
| рџ›ЎпёЏ **Token-gated** | Every call needs a personal 128-bit token (`?token=вЂ¦` or `Authorization: Bearer`). Everything else gets HTTP 401. |
| рџ”Ѓ **Many machines, one gateway** | Each node is addressed by `?user=<node-name>` on one shared domain. |
| рџ†“ **Free & open source** | MIT license, works with the free Gemini tier. |

---

## рџ› пёЏ What Gemini can do on your machine (MCP tools)

**26 tools.** Every one of them runs on your machine under your own user account вЂ” the gateway only carries the calls.

### Which calls ask for your confirmation

Gemini Spark decides whether to stop and ask *"confirm this action?"* from the **MCP annotation hints** each tool advertises (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). A tool advertised with no hints at all is treated as destructive by the spec's defaults вЂ” that is why a server that declares none gets a confirmation prompt on *every single call*. Here the hints are explicit:

| Class | Tools | Confirmation |
| :--- | :--- | :--- |
| Read-only | `mesh_status`, `system_info`, `system_vitals`, `get_orchestration_skill`, `list_dir`, `read_file`, `grep_search`, `glob_find`, `job_output`, `job_list`, `share_list`, `device_info` | never asked |
| Local work вЂ” commands, builds, file writes, jobs, shares, device actions | `bash_exec`, `run_job`, `write_file`, `edit_file`, `job_kill`, `share_file`, `serve_dir`, `device_control` | not asked |
| **Sensitive вЂ” observes the physical world or a person's private data** | `device_capture` (camera, microphone, location, fingerprint, USB, infrared), `device_messages` (SMS, call log, contacts, calls) | **asked first** |
| **Confirmed вЂ” install/delete, `sudo`, system paths** | `system_change` (installs/removes software, deletes data, runs `sudo`, edits system paths by shell), `system_write` (writes file content into a system path), `mesh_update` (installs a release), `unshare` (revokes and deletes the published copy) | **asked first** |

`device_capture` and `device_messages` delete nothing, yet they are advertised with `destructiveHint`. That hint is the only one clients such as Gemini Spark reliably turn into a *"confirm this action?"* prompt, and a camera, a microphone or an SMS list that fires silently is worse than an honest over-classification. They were put in that class deliberately. `device_control` stays ordinary local work for the same reason: marking a torch or a vibrate as destructive would only train you to click through the prompts that matter.

Four classes are gated, and the gateway вЂ” not the hint вЂ” enforces them, because a hint is static per tool and cannot tell `ls` from `apt install`:

1. **Installing or removing software** вЂ” package managers, uninstallers, `msiexec`, `dpkg`/`rpm`, `curl вЂ¦ | bash`, `iwr вЂ¦ | iex`.
2. **Deleting data** вЂ” `rm`, `Remove-Item`, `del`, `rd`, `shred`, `dd`, `mkfs`, `git clean -fd`, `docker rm`/`prune`, `truncate -s 0`.
3. **Running as root** вЂ” `sudo`, `doas`, `pkexec`, `runas`, `Start-Process -Verb RunAs`.
4. **System paths** вЂ” `/etc`, `/usr`, `/boot`, `/var/lib`, `/opt`, `/dev`, `C:\Windows`, `C:\Program Files`, `ProgramData`, the registry. Reads of those paths (`cat /etc/os-release`, `systemctl status`) are *not* gated, and neither is ordinary work in your own directories.

`bash_exec`/`run_job` refuse such commands and tell the model to re-issue them as `system_change`; `write_file`/`edit_file` refuse a system path and point at `system_write` (which takes the exact content, so there is no shell quoting to get wrong). The gate is moved, not removed вЂ” inspection, builds, tests, config edits in your projects and the like still run without a dialog.

The tool surface and the confirmation policy live in [gateway.py](gateway.py) (annotations, `INSTALL_COMMAND_PATTERNS` / `DELETE_COMMAND_PATTERNS`, `SYSTEM_PATH_RE`, `classify_command()`) and in [core/mcp_tools.py](core/mcp_tools.py) (`TOOL_ANNOTATIONS` for the node's own surface, which also carries the device branch's four tools); tests compare the two surfaces and pin the classifier, so a gated command cannot slip through.

> [!NOTE]
> The classifier matches a verb at the start of a command segment (after `sudo`/`env`/`timeout`/`powershell -Command` prefixes), so `echo "rm -rf /"` or `grep rm notes.txt` still run, while `curl вЂ¦ | bash` counts as an install. It is a UX gate, not a sandbox: for hard guarantees independent of any prompt use the node's own switches вЂ” `MESH_READ_ONLY=1` and `MESH_WRITE_ROOTS` ([core/agent.py](core/agent.py)).

### Status and host information

| Tool | What it does |
| :--- | :--- |
| `mesh_status()` | Confirms the node is reachable, with live evidence from the host itself: what kind of machine answered (`device_class`, `scenario`) and its `battery_percent`. Call it first if the machine looks offline. |
| `system_info()` | One-call host summary: OS, desktop, user, home, disks, memory, load, top processes, the current wallpaper, `command_shell` вЂ” the shell `bash_exec` will actually use вЂ” and the device blocks: battery and charging, network and signal, language, time and timezone, CPU, RAM, storage, cameras, microphones and sensors. |
| `system_vitals()` | CPU, RAM and disk metrics, plus the battery block and the thermal sensors the platform exposes. |

### Shell

| Tool | What it does |
| :--- | :--- |
| `bash_exec(command, timeout_sec, max_chars, cursor)` | Runs a shell command. The shell matches the **host**, not the tool's name вЂ” `bash` on Linux/macOS, PowerShell or `cmd.exe` on Windows; check `command_shell` from `system_info()` first. Output is paginated: when it is cut, call again with `cursor=next_cursor`, nothing is dropped; output above 2 MB is spooled to a file returned in `saved_to`. `timeout_sec` is 1вЂ“120 (default 25). Installs/removals, `sudo` and system paths are **refused here** and have to go through `system_change`. |
| `system_change(command, timeout_sec, background, cwd, max_chars, cursor)` | Installs, removes or deletes on the host, runs privileged commands and edits system paths вЂ” the confirmed twin of `bash_exec`, and the only tool allowed to run `apt`/`dnf`/`pacman`/`pip`/`npm`/`winget`/`msiexec`/`rm`/`Remove-Item`/`sudo` or disk tools. It is advertised as destructive, so Gemini asks you first. `background=true` runs it as a job (for long installs) and returns a `job_id` for `job_output`; `cwd` applies to that mode. |

### Files

| Tool | What it does |
| :--- | :--- |
| `list_dir(path)` | Lists files and directories (workspace by default). |
| `read_file(path, start_line, end_line, max_chars, cursor)` | Reads a text file with line numbers, optionally restricted to a line range. Paginated like `bash_exec`. |
| `write_file(path, content, create_dirs, mode)` | Atomically creates or overwrites a file (temp file + `os.replace`). `mode` is POSIX-only: on Windows it is not honoured, and the result says so instead of pretending. A path inside the system is **refused** and has to go through `system_write`. |
| `edit_file(path, old_string, new_string, expected_sha256, replace_all)` | Replaces an exact substring. `old_string` must match exactly once unless `replace_all` is set; `expected_sha256` guards against overwriting a file that changed since it was read. System paths are refused вЂ” read the file and send the whole new content to `system_write`. |
| `system_write(path, content, create_dirs, mode)` | Writes a file inside a system path (`/etc`, `/usr`, `/boot`, `/var/lib`, `C:\Windows`, `C:\Program Files`, `ProgramData`, the registry) вЂ” the confirmed twin of `write_file` and the only way to write there. Gemini asks you first. Content is exact, so there is no shell quoting to get wrong. |

### Search

| Tool | What it does |
| :--- | :--- |
| `grep_search(pattern, path, glob, limit, ignore_case, fixed, context)` | Recursively searches file contents, skipping binary files and heavy directories. `fixed` treats the pattern as literal text, `context` adds surrounding lines, `limit` is 1вЂ“1000 (default 200). |
| `glob_find(pattern, path)` | Finds files by glob pattern (`*` and `**`). |

### Long-running work

| Tool | What it does |
| :--- | :--- |
| `run_job(command, cwd)` | Starts a command in the background and returns a `job_id`. On an Android node it takes the wake lock for the lifetime of the job, so Android does not freeze it when the screen goes off. |
| `job_output(job_id, wait_ms, max_chars, cursor)` | Reads a job's output, optionally waiting up to 20 s for completion. Paginated. |
| `job_kill(job_id, signal)` | Terminates a job. `signal` is `TERM` (default), `KILL`, `INT`, `HUP` or `QUIT`. |
| `job_list(limit)` | Lists recent jobs, newest first (20 by default, max 50), including whether each one holds a `wake_lock`. |

### Device (phone, laptop or server)

These four tools read and act on the device the node itself runs on. They are advertised on **every** node вЂ” a phone-only action on a laptop answers with `available: false`, the reason and, where one exists, the fix, instead of failing.

| Tool | What it does |
| :--- | :--- |
| `device_info(section, sensor, fresh, quick)` | One-call device report: battery and charging, network interfaces with Wi-Fi and cellular signal, language, time and timezone, CPU, RAM, storage, cameras, microphones, sensors and what the platform can actually reach. `section` is `summary`, `all` (default) or one of `device`, `battery`, `network`, `locale`, `time`, `hardware`, `storage`, `cameras`, `microphones`, `sensors`, `capabilities`. `sensor` takes one live sample from a named sensor (Android; the name comes from the `sensors` block), `fresh` bypasses the few-second cache and `quick` skips the slow sections. Every block carries `available` and `source`; a block the platform cannot answer says why instead of showing a zero. |
| `device_control(action, on, value, stream, text, title, id, url, path, state, timeout_sec)` | Twenty reversible actions: `torch`, `vibrate`, `volume`, `volume_get`, `brightness`, `tts_speak`, `toast`, `notify`, `notify_list`, `notify_remove`, `clipboard_get`, `clipboard_set`, `media`, `media_scan`, `wakelock`, `download`, `open`, `share`, `dialog`, `wallpaper`. `value` is milliseconds for `vibrate`, a 0вЂ“15 level for `volume` and 0вЂ“255 for `brightness`; `stream` picks the audio stream (`music` by default); `text` is the body for TTS, toast, notification, clipboard and dialog, and `play\|pause\|stop\|info` for `media`. Nothing here is destructive. |
| `device_capture(action, camera_id, path, seconds, provider, frequency, pattern)` | Eleven actions: `camera_list`, `camera_photo`, `mic_record_start`, `mic_record_stop`, `mic_record_status`, `location`, `fingerprint`, `usb_list`, `usb_access`, `infrared_frequencies`, `infrared_transmit`. **Asks first.** `path` is the output file for `camera_photo` and `mic_record_start` (default: the node's capture directory) and the device path from `usb_list` for `usb_access`; `camera_id` comes from `camera_list`, `seconds` limits a recording, `provider` is `gps`, `network` or `passive`, and `frequency`/`pattern` drive `infrared_transmit`. A photo or a recording stays in the capture directory and is never uploaded by itself вЂ” `share_file` publishes it only when you ask for that. `fingerprint` returns only the verdict; `usb_access` asks Android for permission and returns the descriptor but deliberately refuses to run a program for you. |
| `device_messages(action, number, text, limit, offset, type, query)` | Five actions: `sms_list`, `sms_send`, `call_log`, `contacts`, `call`. **Asks first, and is off until the operator enables it with `MESH_DEVICE_PIM=1`** вЂ” it is the private data of whoever holds the phone. `limit` is 1вЂ“50 (default 10), `offset` pages through the list, `type` filters it and `query` filters contacts on the node. |

Example:

```text
"Check the phone's battery and signal."
  в†’ device_info(section="summary")

"Turn on the torch."
  в†’ device_control(action="torch", on=true)

"Take a photo and share it with me."
  в†’ device_capture(action="camera_photo")   # asks for confirmation
  в†’ share_file(path=<the path it returned>)
```

### Device switches (operator)

Read from `agent.env` at startup ([core/agent.py](core/agent.py), applied in [core/mcp_tools.py](core/mcp_tools.py) and [core/device.py](core/device.py)):

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `MESH_DEVICE` | `auto` | `auto` keeps the branch answering everywhere (only the answer differs per platform), `0` switches the branch off, `1` forces it on. |
| `MESH_DEVICE_ACTIONS` | empty | Comma-separated allowlist of action names for `device_control`, `device_capture` and `device_messages`; empty means every action the build implements. |
| `MESH_DEVICE_CAPTURE_DIR` | shared storage on a phone once `termux-setup-storage` has been granted (`~/storage/dcim/antigravity-mesh` or `~/storage/shared/AntigravityMesh`), otherwise `~/.cache/antigravity-mesh/captures` | Where photos and recordings are written. |
| `MESH_DEVICE_PIM` | `0` | `1` enables SMS, the call log, contacts and placing a call. |
| `MESH_DEVICE_QUICK` | `0` | `1` keeps `system_info` instant by skipping network, cameras, microphones and sensors. |
| `MESH_READ_ONLY` | `0` | `1` refuses every `device_control` and `device_capture` action, and blocks `sms_send` and `call`. This is the node-side guarantee that does not depend on any client prompt. |

### Publishing files and web pages

These four make something on your machine readable from the internet. The link is served by the gateway on the shared domain, so **the node has to stay connected for it to work**.

| Tool | What it does |
| :--- | :--- |
| `share_file(path, name, overwrite)` | Publishes **one file**: it is copied into the node's share root and you get `https://<shared-domain>/<node>/<name>-<random>/<filename>`. `overwrite` replaces an existing share with the same name. |
| `serve_dir(path, name)` | Serves a **directory** in place вЂ” no copy is made. `index.html` is used when present, otherwise a directory listing is shown. Read-only (GET/HEAD only). Returns `https://<shared-domain>/<node>/<name>-<random>/`. |
| `share_list()` | Lists active shares with their URLs, their roots, and whether the local server is running. |
| `unshare(name)` | Stops a share and revokes its link. Accepts the share name, its slug or the full URL. A file share also deletes the copy in the share root; a served directory is left untouched on disk. |

> [!IMPORTANT]
> The random part of the path **is** the credential вЂ” anyone who has the link can read the file or browse the directory. Treat a share link like a password, and `unshare` it when you are done. Size limits: 32 MiB per file by default, 64 MiB ceiling via `MESH_WEB_MAX_BYTES`.

### Agent behaviour

| Tool | What it does |
| :--- | :--- |
| `get_orchestration_skill()` | Loads the agent's current operating rules and orchestration skill. |

### Keeping the node up to date

| Tool | What it does |
| :--- | :--- |
| `mesh_update(action, force, offline, restart)` | Reports, checks for or installs a newer release of the node's own code. `status` reads local state (no network), `check` asks GitHub, `apply` downloads the payload, verifies its published SHA-256, installs it with a rollback backup and restarts the agent. |

> [!TIP]
> Nodes update themselves: a running agent checks for a newer release in the background (every 6 hours by default) and installs what it finds, and on Windows the `AntigravityMeshUpdater` task checks once a day even when no agent is running. `MESH_UPDATE_AUTO=0` switches that to "report only". Details, environment variables and rollback: [docs/UPDATES.md](docs/UPDATES.md).

> [!TIP]
> Gemini gives a single tool call roughly 30 seconds. For anything longer (builds, backups, downloads) the agent uses `run_job` and polls `job_output`, so tasks never get cut off.

---

## рџљЂ Quick start вЂ” 2 minutes

### 1. Install the node on the PC you want to control

**рџђ§ Linux / рџЌЋ macOS**
```bash
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

**рџ“± Android (Termux)** вЂ” no root, works from F-Droid's Termux
```bash
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```
Autostart on a phone is a runit service plus the Termux:Boot app, and the node is
named after the device model вЂ” see [docs/TERMUX.md](docs/TERMUX.md).

**рџЄџ Windows (PowerShell)**
```powershell
irm https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1 | iex
```

**рџЄџ Windows вЂ” visual installer** (from a clone or a release)

```powershell
.\install-gui.cmd
```

A WinForms wizard that mirrors the console installer's variants, switches language at
runtime and shows the MCP link at the end вЂ” see [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

**рџ“¦ Node.js (any OS)**
```bash
npx gemini-computer-use
```

**рџ› пёЏ From source**
```bash
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick
```

At the end the installer prints your personal MCP link and copies it to the clipboard:
```text
https://racknerd-5a24bf9.merino-carob.ts.net/sse?user=<your-node-name>&token=<your_secret_token>
```

### 2. Connect it to Gemini Spark

1. Open **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** in a browser or in the Gemini mobile app.
2. Tap **Add App** (or **Settings вљ™пёЏ в†’ Tools / Extensions (MCP)**).
3. Paste the link (**Ctrl+V**) and save.

### 3. Give it a task

> *"Show system_vitals and list the 5 biggest folders in my home directory."*

That's it вЂ” Gemini is now an agent running on your machine. Repeat step 1 on other computers to control all of them from the same phone.

---

## рџ–ҐпёЏ Deployment modes

| Mode | Command | When to use |
| :--- | :--- | :--- |
| **Gateway + tunnel** (recommended) | `./install.sh --quick` | Home PCs and laptops behind NAT вЂ” the mobile-agent scenario. |
| **Remote over SSH** | `./install.sh --ssh=user@host` | Deploy a node onto a remote Linux server from your terminal. |
| **Local standalone** | `./install.sh --mode=standalone --port=8096` | Pure localhost FastMCP server at `http://localhost:8096/sse`, no cloud relay. |

On Windows the same variants are available in the visual installer (`.\install-gui.cmd`); local standalone stays console-only there (`.\install.ps1 -Mode standalone -Port 8096`).

On **Android/Termux** the same `./install.sh` variants work; autostart is a runit service plus the Termux:Boot app instead of systemd, and standalone stays reachable from that phone alone вЂ” see [docs/TERMUX.md](docs/TERMUX.md).

---

## рџЄџ Windows installation

Windows already ships PowerShell 5.1, so there is nothing to prepare вЂ” no Python, no Node.js, no administrator rights. Both installers finish by putting the same MCP link on the clipboard.

|  | Console installer | Visual installer |
| :--- | :--- | :--- |
| Start | `irm https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1 \| iex` | `.\install-gui.cmd` |
| Needs | nothing but PowerShell | a clone or an unpacked release вЂ” it drives `install.ps1`, `core/` and `install.sh` |
| Variants | `-Mode tunnel` (default), `-Mode standalone`, `-User`, `-Gateway`, `-Token`, `-Port`, `-DryRun` | Quick setup, Custom setup, Remote over SSH |
| Language | `-Lang en` / `-Lang ru` | switch in the window header, or `-Lang` |

### Automatic dependencies and architecture

Nothing has to be prepared by hand first. Both installers obtain what they need, and if
they cannot, they stop with an explicit message instead of writing an autostart entry that
can never work.

| | Windows (`install.ps1`) | Linux / macOS (`install.sh`) |
| :--- | :--- | :--- |
| Python | `winget` в†’ the python.org installer for **this** architecture в†’ `uv` | `apt` / `dnf` / `yum` / `zypper` / `pacman` / `apk` / `xbps` / `brew` в†’ `uv` |
| `websockets` | `pip` в†’ `pip --user` в†’ `ensurepip` в†’ `uv` в†’ a venv | `pip` в†’ `pip --break-system-packages` в†’ `pip --user` в†’ `ensurepip` в†’ `uv` в†’ a venv |
| If all of that fails | stops, and prints the exact command to run | stops, and prints the exact command to run |

`websockets` is the only external Python requirement: `core/mcp_tools.py` and
`core/server.py` are standard library only. `uv` is fetched only when the cheaper paths
have already failed.

**The architecture decides which build is downloaded.** The installer asks the *OS*, not
the process, because a 32-bit PowerShell on 64-bit Windows reports `x86` and hides the
real value in `PROCESSOR_ARCHITEW6432`, and an emulated x64 process on ARM64 reports
`AMD64`:

| Machine | Python build | `uv` archive |
| :--- | :--- | :--- |
| Windows x64 | `python-3.12.5-amd64.exe` | `x86_64-pc-windows-msvc` |
| Windows ARM64 | `python-3.12.5-arm64.exe` | `aarch64-pc-windows-msvc` |
| Windows 32-bit | `python-3.12.5.exe` | вЂ” (uv ships no 32-bit Windows build) |
| Linux `x86_64` | the package manager's own build | `x86_64-unknown-linux-gnu` |
| Linux `aarch64` | the package manager's own build | `aarch64-unknown-linux-gnu` |
| Linux `armv7l` / `i686` | the package manager's own build | `armv7-unknown-linux-gnueabihf` / `i686-unknown-linux-gnu` |
| macOS `arm64` / `x86_64` | `brew`, or `uv` | `aarch64-apple-darwin` / `x86_64-apple-darwin` |

The interpreter that ends up pinned is the one that actually has `websockets` вЂ” which can
be a venv or a `uv`-managed CPython, both deliberately outside `PATH`. `install.ps1 -DryRun`
and `install.sh --dry-run` report the architecture without changing anything, and the visual
installer shows it in its preflight.

### Download the setup executable

The release also ships a single compiled installer, for machines where you would rather
not clone or download anything else вЂ” `AntigravityMesh-Setup-<version>.exe`:

```powershell
.\AntigravityMesh-Setup-0.4.1.exe            # open the visual installer
.\AntigravityMesh-Setup-0.4.1.exe -Lang ru   # start in Russian
.\AntigravityMesh-Setup-0.4.1.exe -SelfTest  # headless self-check, prints JSON
.\AntigravityMesh-Setup-0.4.1.exe --version  # print the version
```

It carries the wizard, `install.ps1`, `core/` and `install.sh` inside itself, unpacks them
to `%LOCALAPPDATA%\AntigravityMesh\setup\<version>` (override with `MESH_SETUP_DIR`) and
runs the wizard from there. It needs nothing but the .NET Framework and PowerShell that
Windows already has, and it is built from this repository by
`.\build-installer-exe.ps1` вЂ” no SDK, no NuGet, no network.

It is **not code-signed**, so SmartScreen may ask for confirmation the first time you run
it.

### Visual installer

![Windows visual installer](docs/images/gui-welcome-en.png)

- **Quick setup** (recommended) вЂ” node name = PC name, shared gateway domain, Windows autostart. The one to pick on a home PC or laptop.
- **Custom setup** вЂ” your own node name, your own shared domain, optional token.
- **Remote over SSH** вЂ” copy the node code to a Linux host and run `install.sh` there. Needs the Windows *OpenSSH Client* feature and key-based authentication; interactive password prompts are not supported.

The first screen runs `install.ps1 -DryRun` and reports what it found вЂ” Python, `websockets`, the resolved gateway domain, the config directory, the autostart path вЂ” without changing anything. Before starting, the window shows the exact command it will run; during the run it streams the installer output; at the end it shows the MCP link with a copy button, the three-step Gemini instructions and the paths it created.

Full details, screenshots and the SSH notes: [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

### What a Windows installation creates

| Path | What it is |
| :--- | :--- |
| `%USERPROFILE%\.config\antigravity-mesh\agent.env` | `MESH_GATEWAY`, `MESH_USER`, `MESH_TOKEN` |
| `%USERPROFILE%\.config\antigravity-mesh\domain.env` | `MESH_PUBLIC_URL` вЂ” the resolved shared domain, so the node's share links, its tunnel and the gateway name the same host (not written when the built-in default is all that is configured) |
| `вЂ¦\Start Menu\Programs\Startup\antigravity-agent.vbs` | autostart entry, so the node comes back after a reboot |
| `%USERPROFILE%\.config\antigravity-mesh\agent.log` | agent output, for when a silent autostart fails |

### Language

The visual installer starts in the language of your Windows language settings вЂ” the display language and the preferred-languages list are read from the registry вЂ” and falls back to English. `-Lang ru` / `-Lang en` overrides the detection. The switch in the window header changes every label immediately.

### Troubleshooting

- **"No gateway domain configured"** вЂ” this copy was not published with a domain. Pass one: use *Custom setup* in the wizard, or `.\install.ps1 -Gateway <shared-domain>`, or set `MESH_PUBLIC_URL`.
- **Registration fails** вЂ” the installer stops *before* writing anything when the gateway cannot be reached. Check that the domain resolves, then retry with an explicit `-Gateway`.
- **`python` opens the Microsoft Store** вЂ” that is the App Execution Alias, not an interpreter. Both installers resolve a real `python.exe` by full path and ignore the alias; the wizard never pins an alias into the autostart entry.
- **The node does not come back after a reboot** вЂ” run `.\ops\doctor.ps1`: it prints the autostart interpreter, the heartbeat age, the last `agent.log` lines (including a torn final line) and the gateway's own view of this node. `.\ops\windows\agent-watchdog.ps1` performs that check once and restarts the agent; the scheduled task `AntigravityMeshWatchdog` runs it every five minutes, so a dead agent comes back on its own.

### Standalone on Windows

The wizard does not offer it; use the console installer:

```powershell
.\install.ps1 -Mode standalone -Port 8096
```

That starts a local-only FastMCP server at `http://localhost:8096/sse` and puts that URL on the clipboard.

---

## рџ“± Android installation (Termux)

The same `install.sh` installs a node on an unrooted phone or tablet, and the phone
then shows up in Gemini Spark like any other machine. Full guide:
**[docs/TERMUX.md](docs/TERMUX.md)**.

```bash
# 1. Termux from F-Droid (not Google Play); optionally Termux:Boot and Termux:API
pkg update -y
# 2. the usual one-liner
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

What a phone changes, and how the installer handles it:

| | |
| :--- | :--- |
| **Packages** | `pkg` instead of `apt`, `python`/`python-pip` instead of `python3-pip`/`python3-venv` вЂ” a phone is never root and has no `sudo`. |
| **Node name** | The device model (`Pixel 7 Pro` в†’ `pixel7pro`): Android answers `localhost` to every app, and the gateway keeps one tunnel per name. |
| **Domain file** | `~/.config/antigravity-mesh/domain.env` вЂ” there is no `/etc` to write to. The installer records the resolved shared domain there, so the phone's share links, its tunnel and the gateway name the same host (the built-in default is never recorded). |
| **Autostart** | A **runit** service (`termux-services`, the `Restart=always` equivalent) plus a **Termux:Boot** script that takes the wake lock after a reboot. |
| **Clipboard** | `termux-clipboard-set` (Termux:API), so the MCP link goes straight into the Gemini app. |

> [!IMPORTANT]
> Two Android-side switches are yours to flip: install **Termux:Boot** and open it
> once, and set battery optimisation to *Unrestricted* for Termux. Without them
> Android unloads the node with the screen off.

The phone must also be able to **resolve the gateway name**. A gateway that lives on
a private network (Tailscale, a VPN, a DNS override on your laptop) resolves there and
nowhere else вЂ” mobile data resolves nothing private. Put the phone on that network, or
give the gateway a publicly resolvable domain; the installer detects a name it cannot
resolve and says so before writing anything: see
[docs/TERMUX.md](docs/TERMUX.md#private-gateway-tailscale--vpn).

```bash
sv status agy-agent                       # is it up?
sv restart agy-agent                      # restart now
tail -f $PREFIX/var/log/sv/agy-agent/current
```

`--mode=standalone` also works, but it serves `127.0.0.1` only вЂ” reachable from
inside that phone alone. Use the default tunnel mode to drive the phone from Gemini.

---

## рџ”„ Automatic updates

A node installs itself from a GitHub release and then keeps itself current the same
way. You do not re-install anything by hand, and nothing unverified is installed.

- **It checks by itself.** A running agent asks the GitHub Releases API every 6
  hours (`MESH_UPDATE_CHECK_INTERVAL`), compares the tag with the version in
  `core/version.py` and installs what it finds. On Windows the daily
  `AntigravityMeshUpdater` task does the same for a machine whose agent is not
  running. `MESH_UPDATE_AUTO=0` turns installing off and keeps reporting.
- **It verifies before it installs.** The payload is downloaded, its SHA-256 is
  compared with the published checksum, and only then are `core/`, `skills/` and
  `ops/` replaced. No checksum, no update.
- **It can always go back.** The replaced directories are saved under
  `%USERPROFILE%\.config\antigravity-mesh\backups\<version>-<timestamp>\`, and an
  update interrupted by a kill or a power cut is rolled back automatically on the
  next start.
- **The node comes back new.** The agent is restarted through whatever supervises
  it (the watchdog task on Windows, `systemctl --user restart agy-agent.service`,
  `launchctl kickstart -k`) вЂ” or started directly when nothing supervises it.

By hand, any time:

```powershell
gemini-computer-use update -Check      # is a newer release published?
gemini-computer-use update             # install it now and restart
gemini-computer-use update -Check -Json
```

From Gemini: ask the node to run `mesh_update` (`status`, then `check`, then
`apply`).

The state of the last check is in the heartbeat, so `ops/doctor.ps1` shows it, and
the full history is in `%USERPROFILE%\.config\antigravity-mesh\update.log`.
Configuration, exit codes, rollback and troubleshooting: [docs/UPDATES.md](docs/UPDATES.md).

---

## рџ”— One shared domain (canonical URL contract)

A node is **never** addressed by its own hostname. The gateway publishes a single shared domain, and the node name travels as a query parameter:

| Purpose | Canonical URL |
| :--- | :--- |
| MCP over SSE | `https://<shared-domain>/sse?user=<node-name>&token=<token>` |
| MCP Streamable HTTP | `https://<shared-domain>/mcp?user=<node-name>&token=<token>` |
| Reverse tunnel (agent) | `wss://<shared-domain>/ws/tunnel?user=<node-name>&token=<token>` |

Why: every extra hostname would need its own DNS record *and* its own SAN in the TLS certificate. A node whose name is missing from the certificate fails the TLS handshake, and the Gemini client then reports an opaque "cannot connect to host". With one shared domain the certificate covers every node forever, and adding a node is only a registration call.

- The shared domain defaults to `smart-server.online`; override it with `./install.sh --domain=<shared-domain>` (node side) or `MESH_PUBLIC_URL` (gateway side).
- Legacy per-device subdomain URLs still resolve for backwards compatibility and log a deprecation warning; set `MESH_LEGACY_SUBDOMAIN=0` on the gateway to reject them outright.
- The installer now records the domain it resolved in the domain file, so the node's share links, its tunnel and the gateway all name the same host: `MESH_PUBLIC_URL=https://<shared-domain>` in `domain.env`, written in both install branches (standalone, and cloud-gateway/tunnel right after `agent.env`). The value is normalised first, the `__MESH_DOMAIN__` placeholder and an empty value are never written, the write is skipped when the file already names that host, and a file that cannot be written is not fatal вЂ” the installer prints the exact command to run by hand. The built-in default is never recorded at all: it is a fallback, not a configuration, and pinning it would stop `core/domain.py` from consulting the legacy `MESH_GATEWAY` at all, so a later change in `agent.env` would be silently ignored вЂ” nothing is lost, because with no file that same value is already the resolver's last fallback. `--dry-run` reports the file and the value it would write вЂ” including `not written (the built-in default is a fallback, not a configuration)` вЂ” and still changes nothing on disk.
- Why this matters: share links are built by `core/domain.py`, whose chain is `MESH_PUBLIC_URL` в†’ `AGY_PUBLIC_BASE_URL` в†’ the domain file в†’ the built-in default, and which deliberately never reads the legacy `MESH_GATEWAY` that `agent.env` carries (only `gateway_host()`, the tunnel host, does). A node installed the normal way вЂ” `agent.env` with `MESH_GATEWAY`/`MESH_USER`/`MESH_TOKEN` and no domain file вЂ” therefore dialled the right gateway while minting links on the built-in default. On an already-installed node, re-run the installer or write the one line by hand: `mkdir -p ~/.config/antigravity-mesh && echo 'MESH_PUBLIC_URL=https://<domain>' > ~/.config/antigravity-mesh/domain.env`, or the same with `sudo tee /etc/antigravity-mesh/domain.env` on Linux. Existing share links keep working; new ones use the configured domain.
- `MESH_DOMAIN_FILE` only overrides the *path*, and it belongs to the node's own configuration rather than to the installer invocation: keep it in `agent.env` (every `MESH_*` key there is exported to the node), or the domain is written into a file the node never reads. The installer creates the parent directory of whichever file is in effect. Over `--ssh=<host>` the domain resolved here is handed to the remote `install.sh` as `--domain=`, so the target records that host instead of re-resolving it (a repository copy still carrying the `__MESH_DOMAIN__` placeholder would otherwise fall back to the built-in default).

---

## рџ”’ Security

- **Token-gated endpoints** вЂ” every request must carry the node's secret token.
- **Outbound-only tunnel** вЂ” nodes never listen on public ports; the agent dials out to the gateway.
- **Gateway is a relay** вЂ” it terminates TLS and forwards calls; commands run only on your node.
- **Unprivileged by default** вЂ” the agent runs in user space, without root.

> [!WARNING]
> Whoever has your MCP link can run commands on that machine. Treat it like a password: don't publish it, and reinstall the node to rotate the token if it leaks.

---

## рџљў Self-hosting the gateway

The gateway is a single host serving every node. Publish it only through the deploy script, so the running gateway, the served installers and the node bootstrap payload cannot drift apart:

```bash
MESH_GATEWAY_SSH=root@<gateway-ip> ./deploy_gateway.sh              # deploy
MESH_GATEWAY_SSH=root@<gateway-ip> ./deploy_gateway.sh --dry-run    # preview
MESH_GATEWAY_SSH=root@<gateway-ip> MESH_GATEWAY_SSH_PASS_FILE=~/.ssh/gw-pass \
    ./deploy_gateway.sh                                            # password auth
```
It verifies every upload with an sha256 manifest on the host, takes a timestamped backup, installs with the right owner, restarts the service and finishes with a public health check. Nothing is installed if verification fails.

> [!TIP]
> **One place for the domain.** `MESH_PUBLIC_URL` in `domain.env`
> (`/etc/antigravity-mesh/domain.env` on Linux, `%USERPROFILE%\.config\antigravity-mesh\domain.env`
> on Windows) is the only setting that names it: the node, the gateway, the installers, the public
> file links and the nginx vhosts all resolve it through `core/domain.py`. Switching domains is one
> value plus `ops/nginx/render-domain.sh --apply` вЂ” see [docs/DOMAIN.md](docs/DOMAIN.md).

> [!IMPORTANT]
> **`install.ps1` encoding вЂ” one file, two representations.** The repository copy is UTF-8 *with* a BOM: Windows PowerShell 5.1 decodes a BOM-less script with the ANSI code page, the Russian strings then turn into smart quotes and the parser rejects the whole file, so `.\install.ps1` from a clone would not run. The copy the gateway serves is written *without* the BOM by `deploy_gateway.sh`, because `irm вЂ¦ | iex` receives the BOM as part of the first token and `param(...)` then stops being the first statement. nginx declares `charset utf-8` for that location, so the BOM-less copy still decodes correctly. Please keep both properties when editing the file.

**TLS certificate policy вЂ” one name, no per-device SANs.** The certificate must cover the shared domain only; a node is selected by `?user=`, never by a hostname:

```bash
# what the certificate currently covers
sudo certbot certificates | grep -A1 'Certificate Name: smart-server.online'

# canonical renewal check (staging, does not touch the live certificate)
sudo certbot renew --dry-run --cert-name smart-server.online
```
Legacy per-device SANs may still be present from before this contract. They are harmless and keep old bookmarked URLs working; drop them at the next renewal once no client uses subdomain URLs any more (see the `domains =` line in `/etc/letsencrypt/renewal/<name>.conf`, or re-issue with a single `-d`).

---

## рџ“„ License

**MIT** вЂ” see [LICENSE](LICENSE).

---
---

# рџ‡·рџ‡є Р СѓСЃСЃРєР°СЏ РІРµСЂСЃРёСЏ

> **MCP-СЃРµСЂРІРµСЂ, РєРѕС‚РѕСЂС‹Р№ РїСЂРµРІСЂР°С‰Р°РµС‚ Google Gemini РёР· С‡Р°С‚-Р±РѕС‚Р° РІ РїРѕР»РЅРѕС†РµРЅРЅРѕРіРѕ РјРѕР±РёР»СЊРЅРѕРіРѕ Р°РіРµРЅС‚Р°.**
> РќР°РїРёС€РёС‚Рµ Р·Р°РґР°С‡Сѓ РІ РїСЂРёР»РѕР¶РµРЅРёРё Gemini РЅР° С‚РµР»РµС„РѕРЅРµ вЂ” РІР°С€ РџРљ, РЅРѕСѓС‚Р±СѓРє РёР»Рё СЃРµСЂРІРµСЂ РІС‹РїРѕР»РЅРёС‚ РµС‘, Р° Gemini РІРµСЂРЅС‘С‚СЃСЏ СЃ СЂРµР°Р»СЊРЅС‹Рј СЂРµР·СѓР»СЊС‚Р°С‚РѕРј.

---

## рџ’Ў РРґРµСЏ

Gemini РІ Р±СЂР°СѓР·РµСЂРµ РёР»Рё РЅР° С‚РµР»РµС„РѕРЅРµ СѓРјРµРµС‚ СЂР°Р·РіРѕРІР°СЂРёРІР°С‚СЊ, РЅРѕ РЅРёС‡РµРіРѕ РЅРµ РјРѕР¶РµС‚ *СЃРґРµР»Р°С‚СЊ* РЅР° РІР°С€РµРј РєРѕРјРїСЊСЋС‚РµСЂРµ.
**Gemini Computer Use** СѓСЃС‚СЂР°РЅСЏРµС‚ СЌС‚РѕС‚ СЂР°Р·СЂС‹РІ. Р­С‚Рѕ Р±РµСЃРїР»Р°С‚РЅС‹Р№ open-source СЃРµСЂРІРµСЂ **Model Context Protocol (MCP)**, РєРѕС‚РѕСЂС‹Р№ РїРѕРґРєР»СЋС‡Р°РµС‚СЃСЏ Рє **Gemini Spark** ([gemini.google.com/spark/apps](https://gemini.google.com/spark/apps) РёР»Рё РјРѕР±РёР»СЊРЅРѕРµ РїСЂРёР»РѕР¶РµРЅРёРµ Gemini) РѕРґРЅРѕР№ СЃСЃС‹Р»РєРѕР№. РџРѕСЃР»Рµ СЌС‚РѕРіРѕ Сѓ Gemini РїРѕСЏРІР»СЏСЋС‚СЃСЏ В«СЂСѓРєРёВ»:

- РІС‹РїРѕР»РЅСЏРµС‚ РєРѕРјР°РЅРґС‹ С‚РµСЂРјРёРЅР°Р»Р°, СЃРѕР±РёСЂР°РµС‚ Рё С‚РµСЃС‚РёСЂСѓРµС‚ РєРѕРґ, СЂР°Р±РѕС‚Р°РµС‚ СЃ git;
- С‡РёС‚Р°РµС‚, СЃРѕР·РґР°С‘С‚ Рё СЂРµРґР°РєС‚РёСЂСѓРµС‚ С„Р°Р№Р»С‹;
- РёС‰РµС‚ РїРѕ С„Р°Р№Р»РѕРІРѕР№ СЃРёСЃС‚РµРјРµ;
- Р·Р°РїСѓСЃРєР°РµС‚ РґРѕР»РіРёРµ Р·Р°РґР°С‡Рё РІ С„РѕРЅРµ Рё РїСЂРѕРІРµСЂСЏРµС‚ РёС… РїРѕР·Р¶Рµ;
- СЃР»РµРґРёС‚ Р·Р° CPU, RAM Рё РґРёСЃРєРѕРј.

```text
 рџ“± РџСЂРёР»РѕР¶РµРЅРёРµ Gemini / рџЊђ gemini.google.com (Spark)
              в”‚  MCP (SSE / Streamable HTTP)
              в–ј
     вЃпёЏ  РЁР»СЋР·  racknerd-5a24bf9.merino-carob.ts.net   в†ђ С‚РѕР»СЊРєРѕ СЂРµР»РµР№, TLS
              в”‚  РёСЃС…РѕРґСЏС‰РёР№ WebSocket-С‚СѓРЅРЅРµР»СЊ (Р±РµР· РѕС‚РєСЂС‹С‚С‹С… РїРѕСЂС‚РѕРІ)
              в–ј
   рџ’» Р’Р°С€ РџРљ В· РЅРѕСѓС‚Р±СѓРє В· VPS В· РґРѕРјР°С€РЅРёР№ СЃРµСЂРІРµСЂ  в†’  РІС‹РїРѕР»РЅСЏРµС‚ Р·Р°РґР°С‡Сѓ
```

**РС‚РѕРі:** С‚РµР»РµС„РѕРЅ СЃС‚Р°РЅРѕРІРёС‚СЃСЏ РїСѓР»СЊС‚РѕРј СѓРїСЂР°РІР»РµРЅРёСЏ РІСЃРµРјРё РІР°С€РёРјРё РјР°С€РёРЅР°РјРё, Р° Gemini вЂ” Р°РіРµРЅС‚РѕРј, РєРѕС‚РѕСЂС‹Р№ РёРјРё СѓРїСЂР°РІР»СЏРµС‚.

---

## рџ”Ґ Р§С‚Рѕ РјРѕР¶РЅРѕ РґРµР»Р°С‚СЊ

РџСЂРѕСЃС‚Рѕ РїРѕРїСЂРѕСЃРёС‚Рµ Gemini РѕР±С‹С‡РЅС‹Рј СЏР·С‹РєРѕРј, РѕС‚РєСѓРґР° СѓРіРѕРґРЅРѕ:

- *В«РџРѕСЃРјРѕС‚СЂРё, РїРѕС‡РµРјСѓ С‚РѕСЂРјРѕР·РёС‚ РґРѕРјР°С€РЅРёР№ СЃРµСЂРІРµСЂ, Рё РїРѕРєР°Р¶Рё С‚РѕРї РїСЂРѕС†РµСЃСЃРѕРІВ»*
- *В«РџРѕРґС‚СЏРЅРё РїРѕСЃР»РµРґРЅРёРµ РёР·РјРµРЅРµРЅРёСЏ РІ ~/projects/api, РїСЂРѕРіРѕРЅРё С‚РµСЃС‚С‹ Рё СЃРєР°Р¶Рё, С‡С‚Рѕ СѓРїР°Р»РѕВ»*
- *В«РСЃРїСЂР°РІСЊ РѕРїРµС‡Р°С‚РєСѓ РІ config.yaml Рё РїРµСЂРµР·Р°РїСѓСЃС‚Рё СЃРµСЂРІРёСЃВ»*
- *В«Р—Р°РїСѓСЃС‚Рё РїРѕР»РЅС‹Р№ Р±СЌРєР°Рї РІ С„РѕРЅРµ Рё СЃРѕРѕР±С‰Рё, РєРѕРіРґР° Р·Р°РєРѕРЅС‡РёС‚СЃСЏВ»*
- *В«РЎРєРѕР»СЊРєРѕ СЃРІРѕР±РѕРґРЅРѕРіРѕ РјРµСЃС‚Р° РѕСЃС‚Р°Р»РѕСЃСЊ РЅР° РЅРѕСѓС‚Р±СѓРєРµ?В»*
- *В«Р’РєР»СЋС‡Рё С„РѕРЅР°СЂРёРє РЅР° С‚РµР»РµС„РѕРЅРµ Рё СЃРєР°Р¶Рё СѓСЂРѕРІРµРЅСЊ Р·Р°СЂСЏРґР° Рё СЃРёРіРЅР°Р»В»*

Р‘РµР· SSH-РєР»РёРµРЅС‚Р°, Р±РµР· С‚РµСЂРјРёРЅР°Р»Р° РЅР° С‚РµР»РµС„РѕРЅРµ, Р±РµР· VPN вЂ” С‚РѕР»СЊРєРѕ С‡Р°С‚.

---

## вњЁ РџРѕС‡РµРјСѓ СЌС‚Рѕ СѓРґРѕР±РЅРѕ

| | |
| :--- | :--- |
| рџ“± **РњРѕР±РёР»СЊРЅС‹Р№ Р°РіРµРЅС‚** | Р Р°Р±РѕС‚Р°РµС‚ РІ РѕС„РёС†РёР°Р»СЊРЅРѕРј РїСЂРёР»РѕР¶РµРЅРёРё Gemini Рё РІ РІРµР±Рµ С‡РµСЂРµР· Gemini Spark. |
| рџ–ҐпёЏ **Р›СЋР±РѕР№ РџРљ** | Linux, macOS, Windows вЂ” РґРµСЃРєС‚РѕРїС‹, РЅРѕСѓС‚Р±СѓРєРё, VPS, РєРѕРЅС‚РµР№РЅРµСЂС‹. |
| рџЊђ **Р—Р° Р»СЋР±С‹Рј NAT** | РЈР·РµР» СЃР°Рј РѕС‚РєСЂС‹РІР°РµС‚ *РёСЃС…РѕРґСЏС‰РёР№* WebSocket-С‚СѓРЅРЅРµР»СЊ. РќРµ РЅСѓР¶РЅС‹ Р±РµР»С‹Р№ IP, РїСЂРѕР±СЂРѕСЃ РїРѕСЂС‚РѕРІ Рё РЅР°СЃС‚СЂРѕР№РєР° СЂРѕСѓС‚РµСЂР°. |
| вљЎ **РЈСЃС‚Р°РЅРѕРІРєР° РѕРґРЅРѕР№ РєРѕРјР°РЅРґРѕР№** | РћРїСЂРµРґРµР»СЏРµС‚ РћРЎ, Р°СЂС…РёС‚РµРєС‚СѓСЂСѓ Рё С‚РёРї СѓСЃС‚СЂРѕР№СЃС‚РІР°; СЃС‚Р°РІРёС‚ Р°РІС‚РѕР·Р°РїСѓСЃРє (`systemd` / `launchd` / Р·Р°РґР°С‡Р° Windows); РєРѕРїРёСЂСѓРµС‚ СЃСЃС‹Р»РєСѓ РїРѕРґРєР»СЋС‡РµРЅРёСЏ РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°. |
| рџ›ЎпёЏ **Р”РѕСЃС‚СѓРї РїРѕ С‚РѕРєРµРЅСѓ** | РљР°Р¶РґС‹Р№ РІС‹Р·РѕРІ С‚СЂРµР±СѓРµС‚ Р»РёС‡РЅС‹Р№ 128-Р±РёС‚РЅС‹Р№ С‚РѕРєРµРЅ (`?token=вЂ¦` РёР»Рё `Authorization: Bearer`). РћСЃС‚Р°Р»СЊРЅС‹Рј вЂ” HTTP 401. |
| рџ”Ѓ **РњРЅРѕРіРѕ РјР°С€РёРЅ, РѕРґРёРЅ С€Р»СЋР·** | РљР°Р¶РґС‹Р№ СѓР·РµР» РІС‹Р±РёСЂР°РµС‚СЃСЏ РїР°СЂР°РјРµС‚СЂРѕРј `?user=<РёРјСЏ-СѓР·Р»Р°>` РЅР° РѕРґРЅРѕРј РѕР±С‰РµРј РґРѕРјРµРЅРµ. |
| рџ†“ **Р‘РµСЃРїР»Р°С‚РЅРѕ Рё РѕС‚РєСЂС‹С‚Рѕ** | Р›РёС†РµРЅР·РёСЏ MIT, СЂР°Р±РѕС‚Р°РµС‚ РЅР° Р±РµСЃРїР»Р°С‚РЅРѕРј С‚Р°СЂРёС„Рµ Gemini. |

---

## рџ› пёЏ Р§С‚Рѕ Gemini РјРѕР¶РµС‚ РґРµР»Р°С‚СЊ РЅР° РІР°С€РµР№ РјР°С€РёРЅРµ (MCP-РёРЅСЃС‚СЂСѓРјРµРЅС‚С‹)

**26 РёРЅСЃС‚СЂСѓРјРµРЅС‚РѕРІ.** Р’СЃРµ РѕРЅРё РІС‹РїРѕР»РЅСЏСЋС‚СЃСЏ РЅР° РІР°С€РµР№ РјР°С€РёРЅРµ РїРѕРґ РІР°С€РµР№ СѓС‡С‘С‚РЅРѕР№ Р·Р°РїРёСЃСЊСЋ вЂ” С€Р»СЋР· С‚РѕР»СЊРєРѕ РїРµСЂРµРґР°С‘С‚ РІС‹Р·РѕРІС‹.

### РќР° РєР°РєРёРµ РІС‹Р·РѕРІС‹ Gemini СЃРїСЂРѕСЃРёС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ

Gemini Spark СЂРµС€Р°РµС‚, РѕСЃС‚Р°РЅР°РІР»РёРІР°С‚СЊСЃСЏ Р»Рё СЃ РІРѕРїСЂРѕСЃРѕРј *В«РїРѕРґС‚РІРµСЂРґРёС‚СЊ РґРµР№СЃС‚РІРёРµ?В»*, РїРѕ **РїРѕРґСЃРєР°Р·РєР°Рј MCP-Р°РЅРЅРѕС‚Р°С†РёР№** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`), РєРѕС‚РѕСЂС‹Рµ РёРЅСЃС‚СЂСѓРјРµРЅС‚ РѕР±СЉСЏРІР»СЏРµС‚ РІ `tools/list`. РРЅСЃС‚СЂСѓРјРµРЅС‚ Р±РµР· Р°РЅРЅРѕС‚Р°С†РёР№ СЃС‡РёС‚Р°РµС‚СЃСЏ РґРµСЃС‚СЂСѓРєС‚РёРІРЅС‹Рј РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ СЃРїРµС†РёС„РёРєР°С†РёРё вЂ” РїРѕСЌС‚РѕРјСѓ СЃРµСЂРІРµСЂ, РєРѕС‚РѕСЂС‹Р№ РёС… РЅРµ РѕР±СЉСЏРІР»СЏРµС‚, РїРѕР»СѓС‡Р°РµС‚ Р·Р°РїСЂРѕСЃ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ РЅР° **РєР°Р¶РґРѕРј** РІС‹Р·РѕРІРµ. Р—РґРµСЃСЊ РїРѕРґСЃРєР°Р·РєРё Р·Р°РґР°РЅС‹ СЏРІРЅРѕ:

| РљР»Р°СЃСЃ | РРЅСЃС‚СЂСѓРјРµРЅС‚С‹ | РџРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ |
| :--- | :--- | :--- |
| РўРѕР»СЊРєРѕ С‡С‚РµРЅРёРµ | `mesh_status`, `system_info`, `system_vitals`, `get_orchestration_skill`, `list_dir`, `read_file`, `grep_search`, `glob_find`, `job_output`, `job_list`, `share_list`, `device_info` | РЅРµ Р·Р°РїСЂР°С€РёРІР°РµС‚СЃСЏ |
| Р›РѕРєР°Р»СЊРЅР°СЏ СЂР°Р±РѕС‚Р° вЂ” РєРѕРјР°РЅРґС‹, СЃР±РѕСЂРєРё, Р·Р°РїРёСЃСЊ С„Р°Р№Р»РѕРІ, Р·Р°РґР°С‡Рё, РїСѓР±Р»РёРєР°С†РёРё, РґРµР№СЃС‚РІРёСЏ СЃ СѓСЃС‚СЂРѕР№СЃС‚РІРѕРј | `bash_exec`, `run_job`, `write_file`, `edit_file`, `job_kill`, `share_file`, `serve_dir`, `device_control` | РЅРµ Р·Р°РїСЂР°С€РёРІР°РµС‚СЃСЏ |
| **Р§СѓРІСЃС‚РІРёС‚РµР»СЊРЅС‹Рµ вЂ” РЅР°Р±Р»СЋРґР°СЋС‚ Р·Р° С„РёР·РёС‡РµСЃРєРёРј РјРёСЂРѕРј РёР»Рё Р»РёС‡РЅС‹РјРё РґР°РЅРЅС‹РјРё** | `device_capture` (РєР°РјРµСЂР°, РјРёРєСЂРѕС„РѕРЅ, РјРµСЃС‚РѕРїРѕР»РѕР¶РµРЅРёРµ, РѕС‚РїРµС‡Р°С‚РѕРє, USB, РРљ-РїРѕСЂС‚), `device_messages` (SMS, Р¶СѓСЂРЅР°Р» РІС‹Р·РѕРІРѕРІ, РєРѕРЅС‚Р°РєС‚С‹, Р·РІРѕРЅРєРё) | **Р·Р°РїСЂР°С€РёРІР°РµС‚СЃСЏ** |
| **РџРѕРґС‚РІРµСЂР¶РґР°РµРјС‹Рµ вЂ” СѓСЃС‚Р°РЅРѕРІРєР°/СѓРґР°Р»РµРЅРёРµ, `sudo`, СЃРёСЃС‚РµРјРЅС‹Рµ РїСѓС‚Рё** | `system_change` (СѓСЃС‚Р°РЅРѕРІРєР°/СѓРґР°Р»РµРЅРёРµ РџРћ, СѓРґР°Р»РµРЅРёРµ РґР°РЅРЅС‹С…, `sudo`, РїСЂР°РІРєР° СЃРёСЃС‚РµРјРЅС‹С… РїСѓС‚РµР№ С‡РµСЂРµР· РѕР±РѕР»РѕС‡РєСѓ), `system_write` (Р·Р°РїРёСЃСЊ С„Р°Р№Р»Р° РІ СЃРёСЃС‚РµРјРЅС‹Р№ РїСѓС‚СЊ), `mesh_update` (СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ СЂРµР»РёР·), `unshare` (РѕС‚Р·С‹РІР°РµС‚ Рё СѓРґР°Р»СЏРµС‚ РѕРїСѓР±Р»РёРєРѕРІР°РЅРЅСѓСЋ РєРѕРїРёСЋ) | **Р·Р°РїСЂР°С€РёРІР°РµС‚СЃСЏ** |

`device_capture` Рё `device_messages` РЅРёС‡РµРіРѕ РЅРµ СѓРґР°Р»СЏСЋС‚, РЅРѕ РѕР±СЉСЏРІР»РµРЅС‹ СЃ `destructiveHint`. Р­С‚Р° РїРѕРґСЃРєР°Р·РєР° вЂ” РµРґРёРЅСЃС‚РІРµРЅРЅР°СЏ, РєРѕС‚РѕСЂСѓСЋ РєР»РёРµРЅС‚С‹ РІСЂРѕРґРµ Gemini Spark РЅР°РґС‘Р¶РЅРѕ РїСЂРµРІСЂР°С‰Р°СЋС‚ РІ РІРѕРїСЂРѕСЃ *В«РїРѕРґС‚РІРµСЂРґРёС‚СЊ РґРµР№СЃС‚РІРёРµ?В»*, Р° РјРѕР»С‡Р° СЃСЂР°Р±РѕС‚Р°РІС€Р°СЏ РєР°РјРµСЂР°, РјРёРєСЂРѕС„РѕРЅ РёР»Рё СЃРїРёСЃРѕРє SMS С…СѓР¶Рµ С‡РµСЃС‚РЅРѕР№ РїРµСЂРµСЃС‚СЂР°С…РѕРІРєРё. Р’ СЌС‚РѕС‚ РєР»Р°СЃСЃ РѕРЅРё РѕС‚РЅРµСЃРµРЅС‹ РЅР°РјРµСЂРµРЅРЅРѕ. `device_control` РїРѕ С‚РѕР№ Р¶Рµ РїСЂРёС‡РёРЅРµ РѕСЃС‚Р°С‘С‚СЃСЏ РѕР±С‹С‡РЅРѕР№ Р»РѕРєР°Р»СЊРЅРѕР№ СЂР°Р±РѕС‚РѕР№: РїРѕРјРµС‚РёС‚СЊ С‚Р°Рє С„РѕРЅР°СЂРёРє РёР»Рё РІРёР±СЂР°С†РёСЋ вЂ” Р·РЅР°С‡РёС‚ РїСЂРёСѓС‡РёС‚СЊ РІР°СЃ Р·Р°РєСЂС‹РІР°С‚СЊ С‚Рµ РґРёР°Р»РѕРіРё, РєРѕС‚РѕСЂС‹Рµ РґРµР№СЃС‚РІРёС‚РµР»СЊРЅРѕ РІР°Р¶РЅС‹.

РџРѕРґ Р±Р°СЂСЊРµСЂРѕРј С‡РµС‚С‹СЂРµ РєР»Р°СЃСЃР°, Рё СЃР»РµРґРёС‚ Р·Р° РЅРёРјРё С€Р»СЋР·, Р° РЅРµ РїРѕРґСЃРєР°Р·РєР°: РїРѕРґСЃРєР°Р·РєР° СЃС‚Р°С‚РёС‡РЅР° РЅР° РёРЅСЃС‚СЂСѓРјРµРЅС‚ Рё РЅРµ РѕС‚Р»РёС‡Р°РµС‚ `ls` РѕС‚ `apt install`.

1. **РЈСЃС‚Р°РЅРѕРІРєР° Рё СѓРґР°Р»РµРЅРёРµ РџРћ** вЂ” РїР°РєРµС‚РЅС‹Рµ РјРµРЅРµРґР¶РµСЂС‹, РґРµРёРЅСЃС‚Р°Р»Р»СЏС‚РѕСЂС‹, `msiexec`, `dpkg`/`rpm`, `curl вЂ¦ | bash`, `iwr вЂ¦ | iex`.
2. **РЈРґР°Р»РµРЅРёРµ РґР°РЅРЅС‹С…** вЂ” `rm`, `Remove-Item`, `del`, `rd`, `shred`, `dd`, `mkfs`, `git clean -fd`, `docker rm`/`prune`, `truncate -s 0`.
3. **Р Р°Р±РѕС‚Р° РѕС‚ root** вЂ” `sudo`, `doas`, `pkexec`, `runas`, `Start-Process -Verb RunAs`.
4. **РЎРёСЃС‚РµРјРЅС‹Рµ СЂР°Р·РґРµР»С‹** вЂ” `/etc`, `/usr`, `/boot`, `/var/lib`, `/opt`, `/dev`, `C:\Windows`, `C:\Program Files`, `ProgramData`, СЂРµРµСЃС‚СЂ. **Р§С‚РµРЅРёРµ** СЌС‚РёС… РїСѓС‚РµР№ (`cat /etc/os-release`, `systemctl status`) РЅРµ Р±Р»РѕРєРёСЂСѓРµС‚СЃСЏ, РєР°Рє Рё РѕР±С‹С‡РЅР°СЏ СЂР°Р±РѕС‚Р° РІ РІР°С€РёС… РєР°С‚Р°Р»РѕРіР°С….

`bash_exec`/`run_job` РѕС‚РєР»РѕРЅСЏСЋС‚ С‚Р°РєРёРµ РєРѕРјР°РЅРґС‹ Рё РїСЂРµРґР»Р°РіР°СЋС‚ РїРµСЂРµРѕС„РѕСЂРјРёС‚СЊ РёС… РєР°Рє `system_change`; `write_file`/`edit_file` РѕС‚РєР»РѕРЅСЏСЋС‚ СЃРёСЃС‚РµРјРЅС‹Р№ РїСѓС‚СЊ Рё РЅР°РїСЂР°РІР»СЏСЋС‚ РІ `system_write` (РѕРЅ РїСЂРёРЅРёРјР°РµС‚ С‚РѕС‡РЅРѕРµ СЃРѕРґРµСЂР¶РёРјРѕРµ, РїРѕСЌС‚РѕРјСѓ СЌРєСЂР°РЅРёСЂРѕРІР°С‚СЊ РЅРёС‡РµРіРѕ РЅРµ РЅСѓР¶РЅРѕ). Р‘Р°СЂСЊРµСЂ РЅРµ СѓР±СЂР°РЅ, Р° РїРµСЂРµРЅРµСЃС‘РЅ: РѕСЃРјРѕС‚СЂ, СЃР±РѕСЂРєРё, С‚РµСЃС‚С‹, РїСЂР°РІРєР° РєРѕРЅС„РёРіРѕРІ РІ РІР°С€РёС… РїСЂРѕРµРєС‚Р°С… РёРґСѓС‚ Р±РµР· РґРёР°Р»РѕРіР°.

РџРѕРІРµСЂС…РЅРѕСЃС‚СЊ РёРЅСЃС‚СЂСѓРјРµРЅС‚РѕРІ Рё РїРѕР»РёС‚РёРєР° РїРѕРґС‚РІРµСЂР¶РґРµРЅРёР№ Р¶РёРІСѓС‚ РІ [gateway.py](gateway.py) (Р°РЅРЅРѕС‚Р°С†РёРё, `INSTALL_COMMAND_PATTERNS` / `DELETE_COMMAND_PATTERNS`, `SYSTEM_PATH_RE`, `classify_command()`) Рё РІ [core/mcp_tools.py](core/mcp_tools.py) (`TOOL_ANNOTATIONS` РґР»СЏ СЃРѕР±СЃС‚РІРµРЅРЅРѕР№ РїРѕРІРµСЂС…РЅРѕСЃС‚Рё СѓР·Р»Р°, РІРєР»СЋС‡Р°СЏ С‡РµС‚С‹СЂРµ РёРЅСЃС‚СЂСѓРјРµРЅС‚Р° РІРµС‚РєРё СѓСЃС‚СЂРѕР№СЃС‚РІР°); С‚РµСЃС‚С‹ СЃСЂР°РІРЅРёРІР°СЋС‚ РѕР±Рµ РїРѕРІРµСЂС…РЅРѕСЃС‚Рё Рё С„РёРєСЃРёСЂСѓСЋС‚ РєР»Р°СЃСЃРёС„РёРєР°С‚РѕСЂ, С‡С‚РѕР±С‹ Р·Р°РєСЂС‹С‚Р°СЏ РєРѕРјР°РЅРґР° РЅРµ РїСЂРѕСЃРѕС‡РёР»Р°СЃСЊ.

> [!NOTE]
> РљР»Р°СЃСЃРёС„РёРєР°С‚РѕСЂ РёС‰РµС‚ РіР»Р°РіРѕР» РІ РЅР°С‡Р°Р»Рµ СЃРµРіРјРµРЅС‚Р° РєРѕРјР°РЅРґС‹ (РїРѕСЃР»Рµ РїСЂРµС„РёРєСЃРѕРІ `sudo`/`env`/`timeout`/`powershell -Command`), РїРѕСЌС‚РѕРјСѓ `echo "rm -rf /"` РёР»Рё `grep rm notes.txt` РІС‹РїРѕР»РЅСЏСЋС‚СЃСЏ РєР°Рє СЂР°РЅСЊС€Рµ, Р° `curl вЂ¦ | bash` СЃС‡РёС‚Р°РµС‚СЃСЏ СѓСЃС‚Р°РЅРѕРІРєРѕР№. Р­С‚Рѕ UX-Р±Р°СЂСЊРµСЂ, Р° РЅРµ РїРµСЃРѕС‡РЅРёС†Р°: Р¶С‘СЃС‚РєР°СЏ РіР°СЂР°РЅС‚РёСЏ вЂ” РїРµСЂРµРєР»СЋС‡Р°С‚РµР»Рё СѓР·Р»Р° `MESH_READ_ONLY` Рё `MESH_WRITE_ROOTS` ([core/agent.py](core/agent.py)).

### РЎРѕСЃС‚РѕСЏРЅРёРµ Рё СЃРІРµРґРµРЅРёСЏ Рѕ С…РѕСЃС‚Рµ

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `mesh_status()` | РџРѕРґС‚РІРµСЂР¶РґР°РµС‚, С‡С‚Рѕ СѓР·РµР» РґРѕСЃС‚СѓРїРµРЅ, Р¶РёРІС‹РјРё РґР°РЅРЅС‹РјРё СЃ СЃР°РјРѕРіРѕ С…РѕСЃС‚Р°: С‡С‚Рѕ Р·Р° РјР°С€РёРЅР° РѕС‚РІРµС‚РёР»Р° (`device_class`, `scenario`) Рё РµС‘ `battery_percent`. Р’С‹Р·С‹РІР°С‚СЊ РїРµСЂРІС‹Рј, РµСЃР»Рё РјР°С€РёРЅР° РєР°Р¶РµС‚СЃСЏ offline. |
| `system_info()` | РЎРІРѕРґРєР° Рѕ С…РѕСЃС‚Рµ РѕРґРЅРёРј РІС‹Р·РѕРІРѕРј: РћРЎ, СЂР°Р±РѕС‡РёР№ СЃС‚РѕР», РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ, РґРѕРјР°С€РЅРёР№ РєР°С‚Р°Р»РѕРі, РґРёСЃРєРё, РїР°РјСЏС‚СЊ, Р·Р°РіСЂСѓР·РєР°, С‚РѕРї РїСЂРѕС†РµСЃСЃРѕРІ, С‚РµРєСѓС‰РёРµ РѕР±РѕРё, `command_shell` вЂ” С‚Р° РѕР±РѕР»РѕС‡РєР°, РєРѕС‚РѕСЂСѓСЋ СЂРµР°Р»СЊРЅРѕ РёСЃРїРѕР»СЊР·СѓРµС‚ `bash_exec`, вЂ” Рё Р±Р»РѕРєРё СѓСЃС‚СЂРѕР№СЃС‚РІР°: Р±Р°С‚Р°СЂРµСЏ Рё Р·Р°СЂСЏРґРєР°, СЃРµС‚СЊ Рё СЃРёРіРЅР°Р», СЏР·С‹Рє, РІСЂРµРјСЏ Рё С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ, CPU, РћР—РЈ, РЅР°РєРѕРїРёС‚РµР»СЊ, РєР°РјРµСЂС‹, РјРёРєСЂРѕС„РѕРЅС‹ Рё РґР°С‚С‡РёРєРё. |
| `system_vitals()` | РњРµС‚СЂРёРєРё CPU, РћР—РЈ Рё РґРёСЃРєРѕРІ, РїР»СЋСЃ Р±Р»РѕРє Р±Р°С‚Р°СЂРµРё Рё С‚РµСЂРјРёС‡РµСЃРєРёРµ РґР°С‚С‡РёРєРё, РєРѕС‚РѕСЂС‹Рµ РѕС‚РґР°С‘С‚ РїР»Р°С‚С„РѕСЂРјР°. |

### РћР±РѕР»РѕС‡РєР°

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `bash_exec(command, timeout_sec, max_chars, cursor)` | Р’С‹РїРѕР»РЅСЏРµС‚ РєРѕРјР°РЅРґСѓ РѕР±РѕР»РѕС‡РєРё. РћР±РѕР»РѕС‡РєР° СЃРѕРѕС‚РІРµС‚СЃС‚РІСѓРµС‚ **С…РѕСЃС‚Сѓ**, Р° РЅРµ РЅР°Р·РІР°РЅРёСЋ РёРЅСЃС‚СЂСѓРјРµРЅС‚Р° вЂ” `bash` РЅР° Linux/macOS, PowerShell РёР»Рё `cmd.exe` РЅР° Windows; СЃРЅР°С‡Р°Р»Р° РїРѕСЃРјРѕС‚СЂРёС‚Рµ `command_shell` РёР· `system_info()`. Р’С‹РІРѕРґ РїРѕСЃС‚СЂР°РЅРёС‡РЅС‹Р№: РµСЃР»Рё РѕР±СЂРµР·Р°РЅ, РІС‹Р·РѕРІРёС‚Рµ СЃРЅРѕРІР° СЃ `cursor=next_cursor`, РЅРёС‡РµРіРѕ РЅРµ С‚РµСЂСЏРµС‚СЃСЏ; РІС‹РІРѕРґ Р±РѕР»СЊС€Рµ 2 РњР‘ СЃРѕС…СЂР°РЅСЏРµС‚СЃСЏ РІ С„Р°Р№Р», РїСѓС‚СЊ РІ `saved_to`. `timeout_sec` вЂ” 1вЂ“120 (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 25). РЈСЃС‚Р°РЅРѕРІРєР°/СѓРґР°Р»РµРЅРёРµ, `sudo` Рё СЃРёСЃС‚РµРјРЅС‹Рµ РїСѓС‚Рё Р·РґРµСЃСЊ **РѕС‚РєР»РѕРЅСЏСЋС‚СЃСЏ** вЂ” РёС… РЅР°РґРѕ РІС‹РїРѕР»РЅСЏС‚СЊ С‡РµСЂРµР· `system_change`. |
| `system_change(command, timeout_sec, background, cwd, max_chars, cursor)` | РЈСЃС‚Р°РЅРѕРІРєР°, СѓРґР°Р»РµРЅРёРµ РџРћ Рё РґР°РЅРЅС‹С…, РїСЂРёРІРёР»РµРіРёСЂРѕРІР°РЅРЅС‹Рµ РєРѕРјР°РЅРґС‹ Рё РїСЂР°РІРєР° СЃРёСЃС‚РµРјРЅС‹С… РїСѓС‚РµР№ вЂ” В«РїРѕРґС‚РІРµСЂР¶РґР°РµРјС‹Р№В» РґРІРѕР№РЅРёРє `bash_exec` Рё РµРґРёРЅСЃС‚РІРµРЅРЅС‹Р№ РёРЅСЃС‚СЂСѓРјРµРЅС‚, РєРѕС‚РѕСЂРѕРјСѓ СЂР°Р·СЂРµС€РµРЅС‹ `apt`/`dnf`/`pacman`/`pip`/`npm`/`winget`/`msiexec`/`rm`/`Remove-Item`/`sudo` Рё СЂР°Р±РѕС‚Р° СЃ РґРёСЃРєР°РјРё. РћР±СЉСЏРІР»РµРЅ РґРµСЃС‚СЂСѓРєС‚РёРІРЅС‹Рј, РїРѕСЌС‚РѕРјСѓ Gemini СЃРїСЂР°С€РёРІР°РµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ. `background=true` Р·Р°РїСѓСЃРєР°РµС‚ РµРіРѕ РєР°Рє С„РѕРЅРѕРІСѓСЋ Р·Р°РґР°С‡Сѓ (РґР»СЏ РґРѕР»РіРёС… СѓСЃС‚Р°РЅРѕРІРѕРє) Рё РІРѕР·РІСЂР°С‰Р°РµС‚ `job_id` РґР»СЏ `job_output`; `cwd` РґРµР№СЃС‚РІСѓРµС‚ С‚РѕР»СЊРєРѕ РІ СЌС‚РѕРј СЂРµР¶РёРјРµ. |

### Р¤Р°Р№Р»С‹

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `list_dir(path)` | РЎРїРёСЃРѕРє С„Р°Р№Р»РѕРІ Рё РєР°С‚Р°Р»РѕРіРѕРІ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ вЂ” СЂР°Р±РѕС‡РёР№ РєР°С‚Р°Р»РѕРі). |
| `read_file(path, start_line, end_line, max_chars, cursor)` | Р§РёС‚Р°РµС‚ С‚РµРєСЃС‚РѕРІС‹Р№ С„Р°Р№Р» СЃ РЅРѕРјРµСЂР°РјРё СЃС‚СЂРѕРє, РїСЂРё Р¶РµР»Р°РЅРёРё вЂ” РґРёР°РїР°Р·РѕРЅ СЃС‚СЂРѕРє. РџРѕСЃС‚СЂР°РЅРёС‡РЅРѕ, РєР°Рє `bash_exec`. |
| `write_file(path, content, create_dirs, mode)` | РђС‚РѕРјР°СЂРЅРѕ СЃРѕР·РґР°С‘С‚ РёР»Рё РїРµСЂРµР·Р°РїРёСЃС‹РІР°РµС‚ С„Р°Р№Р» (РІСЂРµРјРµРЅРЅС‹Р№ С„Р°Р№Р» + `os.replace`). `mode` вЂ” С‚РѕР»СЊРєРѕ РґР»СЏ POSIX: РЅР° Windows РѕРЅ РЅРµ РїСЂРёРјРµРЅСЏРµС‚СЃСЏ, Рё СЂРµР·СѓР»СЊС‚Р°С‚ РѕР± СЌС‚РѕРј С‡РµСЃС‚РЅРѕ СЃРѕРѕР±С‰Р°РµС‚. РџСѓС‚СЊ РІРЅСѓС‚СЂРё СЃРёСЃС‚РµРјС‹ **РѕС‚РєР»РѕРЅСЏРµС‚СЃСЏ** вЂ” РґР»СЏ РЅРµРіРѕ РµСЃС‚СЊ `system_write`. |
| `edit_file(path, old_string, new_string, expected_sha256, replace_all)` | Р—Р°РјРµРЅСЏРµС‚ С‚РѕС‡РЅСѓСЋ РїРѕРґСЃС‚СЂРѕРєСѓ. `old_string` РґРѕР»Р¶РµРЅ РІСЃС‚СЂРµС‡Р°С‚СЊСЃСЏ СЂРѕРІРЅРѕ РѕРґРёРЅ СЂР°Р·, РµСЃР»Рё РЅРµ Р·Р°РґР°РЅ `replace_all`; `expected_sha256` Р·Р°С‰РёС‰Р°РµС‚ РѕС‚ РїРµСЂРµР·Р°РїРёСЃРё С„Р°Р№Р»Р°, РёР·РјРµРЅРёРІС€РµРіРѕСЃСЏ РїРѕСЃР»Рµ С‡С‚РµРЅРёСЏ. РЎРёСЃС‚РµРјРЅС‹Рµ РїСѓС‚Рё РѕС‚РєР»РѕРЅСЏСЋС‚СЃСЏ вЂ” РїСЂРѕС‡РёС‚Р°Р№С‚Рµ С„Р°Р№Р» Рё РѕС‚РїСЂР°РІСЊС‚Рµ РЅРѕРІРѕРµ СЃРѕРґРµСЂР¶РёРјРѕРµ С†РµР»РёРєРѕРј РІ `system_write`. |
| `system_write(path, content, create_dirs, mode)` | Р—Р°РїРёСЃСЊ С„Р°Р№Р»Р° РІ СЃРёСЃС‚РµРјРЅС‹Р№ РїСѓС‚СЊ (`/etc`, `/usr`, `/boot`, `/var/lib`, `C:\Windows`, `C:\Program Files`, `ProgramData`, СЂРµРµСЃС‚СЂ) вЂ” В«РїРѕРґС‚РІРµСЂР¶РґР°РµРјС‹Р№В» РґРІРѕР№РЅРёРє `write_file` Рё РµРґРёРЅСЃС‚РІРµРЅРЅС‹Р№ СЃРїРѕСЃРѕР± С‚СѓРґР° РїРёСЃР°С‚СЊ. Gemini СЃРїСЂР°С€РёРІР°РµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ. РЎРѕРґРµСЂР¶РёРјРѕРµ РїРµСЂРµРґР°С‘С‚СЃСЏ С‚РѕС‡РЅРѕ, СЌРєСЂР°РЅРёСЂРѕРІР°С‚СЊ РґР»СЏ РѕР±РѕР»РѕС‡РєРё РЅРёС‡РµРіРѕ РЅРµ РЅСѓР¶РЅРѕ. |

### РџРѕРёСЃРє

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `grep_search(pattern, path, glob, limit, ignore_case, fixed, context)` | Р РµРєСѓСЂСЃРёРІРЅРѕ РёС‰РµС‚ РїРѕ СЃРѕРґРµСЂР¶РёРјРѕРјСѓ С„Р°Р№Р»РѕРІ, РїСЂРѕРїСѓСЃРєР°СЏ РґРІРѕРёС‡РЅС‹Рµ С„Р°Р№Р»С‹ Рё С‚СЏР¶С‘Р»С‹Рµ РєР°С‚Р°Р»РѕРіРё. `fixed` вЂ” РїРѕРёСЃРє РєР°Рє РїРѕ РѕР±С‹С‡РЅРѕРјСѓ С‚РµРєСЃС‚Сѓ, `context` вЂ” СЃС‚СЂРѕРєРё РІРѕРєСЂСѓРі СЃРѕРІРїР°РґРµРЅРёСЏ, `limit` вЂ” 1вЂ“1000 (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 200). |
| `glob_find(pattern, path)` | РС‰РµС‚ С„Р°Р№Р»С‹ РїРѕ РјР°СЃРєРµ (`*` Рё `**`). |

### Р”РѕР»РіРёРµ Р·Р°РґР°С‡Рё

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `run_job(command, cwd)` | Р—Р°РїСѓСЃРєР°РµС‚ РєРѕРјР°РЅРґСѓ РІ С„РѕРЅРµ Рё РІРѕР·РІСЂР°С‰Р°РµС‚ `job_id`. РќР° Android-СѓР·Р»Рµ Р±РµСЂС‘С‚ wake lock РЅР° РІСЃС‘ РІСЂРµРјСЏ Р·Р°РґР°С‡Рё, С‡С‚РѕР±С‹ Android РЅРµ Р·Р°РјРѕСЂРѕР·РёР» РµС‘ РїСЂРё РІС‹РєР»СЋС‡РµРЅРЅРѕРј СЌРєСЂР°РЅРµ. |
| `job_output(job_id, wait_ms, max_chars, cursor)` | Р§РёС‚Р°РµС‚ РІС‹РІРѕРґ Р·Р°РґР°С‡Рё, РїСЂРё Р¶РµР»Р°РЅРёРё РѕР¶РёРґР°СЏ Р·Р°РІРµСЂС€РµРЅРёСЏ РґРѕ 20 СЃ. РџРѕСЃС‚СЂР°РЅРёС‡РЅРѕ. |
| `job_kill(job_id, signal)` | Р—Р°РІРµСЂС€Р°РµС‚ Р·Р°РґР°С‡Сѓ. `signal` вЂ” `TERM` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ), `KILL`, `INT`, `HUP` РёР»Рё `QUIT`. |
| `job_list(limit)` | РЎРїРёСЃРѕРє РїРѕСЃР»РµРґРЅРёС… Р·Р°РґР°С‡, РЅРѕРІС‹Рµ СЃРІРµСЂС…Сѓ (20 РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ, РјР°РєСЃРёРјСѓРј 50), СЃ РїСЂРёР·РЅР°РєРѕРј `wake_lock` Сѓ РєР°Р¶РґРѕР№. |

### РЈСЃС‚СЂРѕР№СЃС‚РІРѕ (С‚РµР»РµС„РѕРЅ, РЅРѕСѓС‚Р±СѓРє РёР»Рё СЃРµСЂРІРµСЂ)

Р­С‚Рё С‡РµС‚С‹СЂРµ РёРЅСЃС‚СЂСѓРјРµРЅС‚Р° С‡РёС‚Р°СЋС‚ СЃР°РјРѕ СѓСЃС‚СЂРѕР№СЃС‚РІРѕ, РЅР° РєРѕС‚РѕСЂРѕРј СЂР°Р±РѕС‚Р°РµС‚ СѓР·РµР», Рё СѓРїСЂР°РІР»СЏСЋС‚ РёРј. РћРЅРё РѕР±СЉСЏРІР»РµРЅС‹ РЅР° **РєР°Р¶РґРѕРј** СѓР·Р»Рµ вЂ” РґРµР№СЃС‚РІРёРµ РґР»СЏ С‚РµР»РµС„РѕРЅР° РЅР° РЅРѕСѓС‚Р±СѓРєРµ РѕС‚РІРµС‡Р°РµС‚ `available: false`, РїСЂРёС‡РёРЅРѕР№ Рё, РіРґРµ РѕРЅР° РµСЃС‚СЊ, РїРѕРґСЃРєР°Р·РєРѕР№ `fix`, Р° РЅРµ РѕС‚РєР°Р·РѕРј.

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `device_info(section, sensor, fresh, quick)` | РћС‚С‡С‘С‚ РѕР± СѓСЃС‚СЂРѕР№СЃС‚РІРµ РѕРґРЅРёРј РІС‹Р·РѕРІРѕРј: Р±Р°С‚Р°СЂРµСЏ Рё Р·Р°СЂСЏРґРєР°, СЃРµС‚РµРІС‹Рµ РёРЅС‚РµСЂС„РµР№СЃС‹ СЃ СѓСЂРѕРІРЅРµРј Wi-Fi Рё СЃРѕС‚РѕРІРѕРіРѕ СЃРёРіРЅР°Р»Р°, СЏР·С‹Рє, РІСЂРµРјСЏ Рё С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ, CPU, РћР—РЈ, РЅР°РєРѕРїРёС‚РµР»СЊ, РєР°РјРµСЂС‹, РјРёРєСЂРѕС„РѕРЅС‹, РґР°С‚С‡РёРєРё Рё С‚Рѕ, РґРѕ С‡РµРіРѕ РїР»Р°С‚С„РѕСЂРјР° СЂРµР°Р»СЊРЅРѕ РґРѕС‚СЏРіРёРІР°РµС‚СЃСЏ. `section` вЂ” `summary`, `all` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ) РёР»Рё РѕРґРёРЅ РёР· `device`, `battery`, `network`, `locale`, `time`, `hardware`, `storage`, `cameras`, `microphones`, `sensors`, `capabilities`. `sensor` Р±РµСЂС‘С‚ РѕРґРёРЅ Р¶РёРІРѕР№ Р·Р°РјРµСЂ СЃ РґР°С‚С‡РёРєР° РїРѕ РёРјРµРЅРё (Android; РёРјСЏ вЂ” РёР· Р±Р»РѕРєР° `sensors`), `fresh` РѕР±С…РѕРґРёС‚ РєРѕСЂРѕС‚РєРёР№ РєСЌС€, `quick` РїСЂРѕРїСѓСЃРєР°РµС‚ РјРµРґР»РµРЅРЅС‹Рµ СЂР°Р·РґРµР»С‹. РЈ РєР°Р¶РґРѕРіРѕ Р±Р»РѕРєР° РµСЃС‚СЊ `available` Рё `source`; Р±Р»РѕРє, РЅР° РєРѕС‚РѕСЂС‹Р№ Сѓ РїР»Р°С‚С„РѕСЂРјС‹ РЅРµС‚ РѕС‚РІРµС‚Р°, РіРѕРІРѕСЂРёС‚ РїСЂРёС‡РёРЅСѓ, Р° РЅРµ РїРѕРєР°Р·С‹РІР°РµС‚ РЅРѕР»СЊ. |
| `device_control(action, on, value, stream, text, title, id, url, path, state, timeout_sec)` | Р”РІР°РґС†Р°С‚СЊ РѕР±СЂР°С‚РёРјС‹С… РґРµР№СЃС‚РІРёР№: `torch`, `vibrate`, `volume`, `volume_get`, `brightness`, `tts_speak`, `toast`, `notify`, `notify_list`, `notify_remove`, `clipboard_get`, `clipboard_set`, `media`, `media_scan`, `wakelock`, `download`, `open`, `share`, `dialog`, `wallpaper`. `value` вЂ” РјРёР»Р»РёСЃРµРєСѓРЅРґС‹ РґР»СЏ `vibrate`, СѓСЂРѕРІРµРЅСЊ 0вЂ“15 РґР»СЏ `volume` Рё 0вЂ“255 РґР»СЏ `brightness`; `stream` РІС‹Р±РёСЂР°РµС‚ Р°СѓРґРёРѕРїРѕС‚РѕРє (`music` РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ); `text` вЂ” С‚РµР»Рѕ РґР»СЏ TTS, toast, СѓРІРµРґРѕРјР»РµРЅРёСЏ, Р±СѓС„РµСЂР° РѕР±РјРµРЅР° Рё РґРёР°Р»РѕРіР°, Р° РґР»СЏ `media` вЂ” `play\|pause\|stop\|info`. РќРёС‡РµРіРѕ РґРµСЃС‚СЂСѓРєС‚РёРІРЅРѕРіРѕ Р·РґРµСЃСЊ РЅРµС‚. |
| `device_capture(action, camera_id, path, seconds, provider, frequency, pattern)` | РћРґРёРЅРЅР°РґС†Р°С‚СЊ РґРµР№СЃС‚РІРёР№: `camera_list`, `camera_photo`, `mic_record_start`, `mic_record_stop`, `mic_record_status`, `location`, `fingerprint`, `usb_list`, `usb_access`, `infrared_frequencies`, `infrared_transmit`. **РЎРїСЂР°С€РёРІР°РµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ.** `path` Р·Р°РґР°С‘С‚ РІС‹С…РѕРґРЅРѕР№ С„Р°Р№Р» РґР»СЏ `camera_photo` Рё `mic_record_start` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ вЂ” РєР°С‚Р°Р»РѕРі СЃСЉС‘РјРєРё СѓР·Р»Р°) Рё РїСѓС‚СЊ СѓСЃС‚СЂРѕР№СЃС‚РІР° РёР· `usb_list` РґР»СЏ `usb_access`; `camera_id` Р±РµСЂС‘С‚СЃСЏ РёР· `camera_list`, `seconds` РѕРіСЂР°РЅРёС‡РёРІР°РµС‚ Р·Р°РїРёСЃСЊ, `provider` вЂ” `gps`, `network` РёР»Рё `passive`, Р° `frequency`/`pattern` вЂ” РґР»СЏ `infrared_transmit`. Р¤РѕС‚Рѕ Рё Р·Р°РїРёСЃСЊ РѕСЃС‚Р°СЋС‚СЃСЏ РІ РєР°С‚Р°Р»РѕРіРµ СЃСЉС‘РјРєРё Рё СЃР°РјРё РЅРёРєСѓРґР° РЅРµ РѕС‚РїСЂР°РІР»СЏСЋС‚СЃСЏ вЂ” `share_file` РїСѓР±Р»РёРєСѓРµС‚ С„Р°Р№Р» С‚РѕР»СЊРєРѕ РїРѕ РѕС‚РґРµР»СЊРЅРѕР№ РїСЂРѕСЃСЊР±Рµ. `fingerprint` РІРѕР·РІСЂР°С‰Р°РµС‚ С‚РѕР»СЊРєРѕ РІРµСЂРґРёРєС‚; `usb_access` Р·Р°РїСЂР°С€РёРІР°РµС‚ Сѓ Android СЂР°Р·СЂРµС€РµРЅРёРµ Рё РѕС‚РґР°С‘С‚ РґРµСЃРєСЂРёРїС‚РѕСЂ, РЅРѕ РЅР°РјРµСЂРµРЅРЅРѕ РѕС‚РєР°Р·С‹РІР°РµС‚СЃСЏ Р·Р°РїСѓСЃРєР°С‚СЊ РїСЂРѕРіСЂР°РјРјСѓ Р·Р° РІР°СЃ. |
| `device_messages(action, number, text, limit, offset, type, query)` | РџСЏС‚СЊ РґРµР№СЃС‚РІРёР№: `sms_list`, `sms_send`, `call_log`, `contacts`, `call`. **РЎРїСЂР°С€РёРІР°РµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ Рё РІС‹РєР»СЋС‡РµРЅ, РїРѕРєР° РѕРїРµСЂР°С‚РѕСЂ РЅРµ РІРєР»СЋС‡РёС‚ РµРіРѕ С‡РµСЂРµР· `MESH_DEVICE_PIM=1`** вЂ” СЌС‚Рѕ Р»РёС‡РЅС‹Рµ РґР°РЅРЅС‹Рµ С‚РѕРіРѕ, Сѓ РєРѕРіРѕ С‚РµР»РµС„РѕРЅ РІ СЂСѓРєР°С…. `limit` вЂ” 1вЂ“50 (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 10), `offset` Р»РёСЃС‚Р°РµС‚ СЃРїРёСЃРѕРє, `type` С„РёР»СЊС‚СЂСѓРµС‚ РµРіРѕ, Р° `query` С„РёР»СЊС‚СЂСѓРµС‚ РєРѕРЅС‚Р°РєС‚С‹ РЅР° СѓР·Р»Рµ. |

РџСЂРёРјРµСЂ:

```text
В«РџСЂРѕРІРµСЂСЊ Р±Р°С‚Р°СЂРµСЋ Рё СЃРёРіРЅР°Р» РЅР° С‚РµР»РµС„РѕРЅРµВ».
  в†’ device_info(section="summary")

В«Р’РєР»СЋС‡Рё С„РѕРЅР°СЂРёРєВ».
  в†’ device_control(action="torch", on=true)

В«РЎРґРµР»Р°Р№ С„РѕС‚Рѕ Рё РїРѕРґРµР»РёСЃСЊ РёРј СЃРѕ РјРЅРѕР№В».
  в†’ device_capture(action="camera_photo")   # СЃРїСЂР°С€РёРІР°РµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ
  в†’ share_file(path=<РїСѓС‚СЊ, РєРѕС‚РѕСЂС‹Р№ РѕРЅ РІРµСЂРЅСѓР»>)
```

### РџРµСЂРµРєР»СЋС‡Р°С‚РµР»Рё СѓСЃС‚СЂРѕР№СЃС‚РІР° (РѕРїРµСЂР°С‚РѕСЂ)

Р§РёС‚Р°СЋС‚СЃСЏ РёР· `agent.env` РїСЂРё Р·Р°РїСѓСЃРєРµ ([core/agent.py](core/agent.py), РїСЂРёРјРµРЅСЏСЋС‚СЃСЏ РІ [core/mcp_tools.py](core/mcp_tools.py) Рё [core/device.py](core/device.py)):

| РџРµСЂРµРјРµРЅРЅР°СЏ | РџРѕ СѓРјРѕР»С‡Р°РЅРёСЋ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- | :--- |
| `MESH_DEVICE` | `auto` | `auto` вЂ” РІРµС‚РєР° РѕС‚РІРµС‡Р°РµС‚ РІРµР·РґРµ (СЂР°Р·Р»РёС‡Р°РµС‚СЃСЏ С‚РѕР»СЊРєРѕ РѕС‚РІРµС‚), `0` вЂ” РІС‹РєР»СЋС‡Р°РµС‚ РІРµС‚РєСѓ, `1` вЂ” РІРєР»СЋС‡Р°РµС‚ РїСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕ. |
| `MESH_DEVICE_ACTIONS` | РїСѓСЃС‚Рѕ | РЎРїРёСЃРѕРє СЂР°Р·СЂРµС€С‘РЅРЅС‹С… РёРјС‘РЅ РґРµР№СЃС‚РІРёР№ С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ РґР»СЏ `device_control`, `device_capture` Рё `device_messages`; РїСѓСЃС‚Рѕ вЂ” РІСЃРµ РґРµР№СЃС‚РІРёСЏ, СЂРµР°Р»РёР·РѕРІР°РЅРЅС‹Рµ СЃР±РѕСЂРєРѕР№. |
| `MESH_DEVICE_CAPTURE_DIR` | РѕР±С‰РµРµ С…СЂР°РЅРёР»РёС‰Рµ С‚РµР»РµС„РѕРЅР° РїРѕСЃР»Рµ `termux-setup-storage` (`~/storage/dcim/antigravity-mesh` РёР»Рё `~/storage/shared/AntigravityMesh`), РёРЅР°С‡Рµ `~/.cache/antigravity-mesh/captures` | РљСѓРґР° РїРёС€СѓС‚СЃСЏ С„РѕС‚Рѕ Рё Р·Р°РїРёСЃРё. |
| `MESH_DEVICE_PIM` | `0` | `1` РІРєР»СЋС‡Р°РµС‚ SMS, Р¶СѓСЂРЅР°Р» РІС‹Р·РѕРІРѕРІ, РєРѕРЅС‚Р°РєС‚С‹ Рё Р·РІРѕРЅРѕРє. |
| `MESH_DEVICE_QUICK` | `0` | `1` РѕСЃС‚Р°РІР»СЏРµС‚ `system_info` РјРіРЅРѕРІРµРЅРЅС‹Рј, РїСЂРѕРїСѓСЃРєР°СЏ СЃРµС‚СЊ, РєР°РјРµСЂС‹, РјРёРєСЂРѕС„РѕРЅС‹ Рё РґР°С‚С‡РёРєРё. |
| `MESH_READ_ONLY` | `0` | `1` РѕС‚РєР°Р·С‹РІР°РµС‚ РІ РєР°Р¶РґРѕРј РґРµР№СЃС‚РІРёРё `device_control` Рё `device_capture`, Р° С‚Р°РєР¶Рµ Р±Р»РѕРєРёСЂСѓРµС‚ `sms_send` Рё `call`. Р­С‚Рѕ РіР°СЂР°РЅС‚РёСЏ РЅР° СЃС‚РѕСЂРѕРЅРµ СѓР·Р»Р°, РЅРµ Р·Р°РІРёСЃСЏС‰Р°СЏ РѕС‚ РґРёР°Р»РѕРіР° РєР»РёРµРЅС‚Р°. |

### РџСѓР±Р»РёРєР°С†РёСЏ С„Р°Р№Р»РѕРІ Рё РІРµР±-СЃС‚СЂР°РЅРёС†

Р­С‚Рё С‡РµС‚С‹СЂРµ РґРµР»Р°СЋС‚ С‡С‚Рѕ-С‚Рѕ РЅР° РІР°С€РµР№ РјР°С€РёРЅРµ РґРѕСЃС‚СѓРїРЅС‹Рј РёР· РёРЅС‚РµСЂРЅРµС‚Р°. РЎСЃС‹Р»РєСѓ РѕС‚РґР°С‘С‚ С€Р»СЋР· РЅР° РѕР±С‰РµРј РґРѕРјРµРЅРµ, РїРѕСЌС‚РѕРјСѓ **СѓР·РµР» РґРѕР»Р¶РµРЅ РѕСЃС‚Р°РІР°С‚СЊСЃСЏ РїРѕРґРєР»СЋС‡С‘РЅРЅС‹Рј**.

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `share_file(path, name, overwrite)` | РџСѓР±Р»РёРєСѓРµС‚ **РѕРґРёРЅ С„Р°Р№Р»**: РѕРЅ РєРѕРїРёСЂСѓРµС‚СЃСЏ РІ РєР°С‚Р°Р»РѕРі РїСѓР±Р»РёРєР°С†РёР№ СѓР·Р»Р°, Рё РІС‹ РїРѕР»СѓС‡Р°РµС‚Рµ `https://<РѕР±С‰РёР№-РґРѕРјРµРЅ>/<СѓР·РµР»>/<РёРјСЏ>-<СЃР»СѓС‡Р°Р№РЅРѕРµ>/<С„Р°Р№Р»>`. `overwrite` Р·Р°РјРµРЅСЏРµС‚ РїСѓР±Р»РёРєР°С†РёСЋ СЃ С‚РµРј Р¶Рµ РёРјРµРЅРµРј. |
| `serve_dir(path, name)` | РћС‚РґР°С‘С‚ **РєР°С‚Р°Р»РѕРі** РЅР° РјРµСЃС‚Рµ вЂ” РєРѕРїРёСЏ РЅРµ СЃРѕР·РґР°С‘С‚СЃСЏ. Р•СЃР»Рё РµСЃС‚СЊ `index.html`, РѕС‚РґР°С‘С‚СЃСЏ РѕРЅ, РёРЅР°С‡Рµ РїРѕРєР°Р·С‹РІР°РµС‚СЃСЏ СЃРїРёСЃРѕРє С„Р°Р№Р»РѕРІ. РўРѕР»СЊРєРѕ С‡С‚РµРЅРёРµ (GET/HEAD). Р’РѕР·РІСЂР°С‰Р°РµС‚ `https://<РѕР±С‰РёР№-РґРѕРјРµРЅ>/<СѓР·РµР»>/<РёРјСЏ>-<СЃР»СѓС‡Р°Р№РЅРѕРµ>/`. |
| `share_list()` | РЎРїРёСЃРѕРє Р°РєС‚РёРІРЅС‹С… РїСѓР±Р»РёРєР°С†РёР№: СЃСЃС‹Р»РєРё, РєРѕСЂРЅРё Рё Р·Р°РїСѓС‰РµРЅ Р»Рё Р»РѕРєР°Р»СЊРЅС‹Р№ СЃРµСЂРІРµСЂ. |
| `unshare(name)` | РћСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РїСѓР±Р»РёРєР°С†РёСЋ Рё РѕС‚Р·С‹РІР°РµС‚ СЃСЃС‹Р»РєСѓ. РџСЂРёРЅРёРјР°РµС‚ РёРјСЏ, slug РёР»Рё РїРѕР»РЅСѓСЋ СЃСЃС‹Р»РєСѓ. Р”Р»СЏ С„Р°Р№Р»Р° СѓРґР°Р»СЏРµС‚СЃСЏ Рё РєРѕРїРёСЏ РІ РєР°С‚Р°Р»РѕРіРµ РїСѓР±Р»РёРєР°С†РёР№; РѕС‚РґР°РІР°РµРјС‹Р№ РєР°С‚Р°Р»РѕРі РЅР° РґРёСЃРєРµ РЅРµ С‚СЂРѕРіР°РµС‚СЃСЏ. |

> [!IMPORTANT]
> РЎР»СѓС‡Р°Р№РЅР°СЏ С‡Р°СЃС‚СЊ РїСѓС‚Рё **Рё РµСЃС‚СЊ** РїР°СЂРѕР»СЊ вЂ” Р»СЋР±РѕР№, Сѓ РєРѕРіРѕ РµСЃС‚СЊ СЃСЃС‹Р»РєР°, РїСЂРѕС‡РёС‚Р°РµС‚ С„Р°Р№Р» РёР»Рё РїСЂРѕСЃРјРѕС‚СЂРёС‚ РєР°С‚Р°Р»РѕРі. РћС‚РЅРѕСЃРёС‚РµСЃСЊ Рє СЃСЃС‹Р»РєРµ РєР°Рє Рє РїР°СЂРѕР»СЋ Рё СЃРЅРёРјР°Р№С‚Рµ РїСѓР±Р»РёРєР°С†РёСЋ С‡РµСЂРµР· `unshare`, РєРѕРіРґР° РѕРЅР° Р±РѕР»СЊС€Рµ РЅРµ РЅСѓР¶РЅР°. РћРіСЂР°РЅРёС‡РµРЅРёСЏ: 32 РњРёР‘ РЅР° С„Р°Р№Р» РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ, РїРѕС‚РѕР»РѕРє 64 РњРёР‘ С‡РµСЂРµР· `MESH_WEB_MAX_BYTES`.

### РџРѕРІРµРґРµРЅРёРµ Р°РіРµРЅС‚Р°

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `get_orchestration_skill()` | Р—Р°РіСЂСѓР¶Р°РµС‚ С‚РµРєСѓС‰РёРµ СЂР°Р±РѕС‡РёРµ РїСЂР°РІРёР»Р° Р°РіРµРЅС‚Р° Рё РЅР°РІС‹Рє РѕСЂРєРµСЃС‚СЂР°С†РёРё. |

### РџРѕРґРґРµСЂР¶Р°РЅРёРµ СѓР·Р»Р° РІ Р°РєС‚СѓР°Р»СЊРЅРѕРј СЃРѕСЃС‚РѕСЏРЅРёРё

| РРЅСЃС‚СЂСѓРјРµРЅС‚ | Р§С‚Рѕ РґРµР»Р°РµС‚ |
| :--- | :--- |
| `mesh_update(action, force, offline, restart)` | РЎРѕРѕР±С‰Р°РµС‚, РїСЂРѕРІРµСЂСЏРµС‚ РёР»Рё СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РЅРѕРІСѓСЋ РІРµСЂСЃРёСЋ РєРѕРґР° СЃР°РјРѕРіРѕ СѓР·Р»Р°. `status` вЂ” Р»РѕРєР°Р»СЊРЅРѕРµ СЃРѕСЃС‚РѕСЏРЅРёРµ (Р±РµР· СЃРµС‚Рё), `check` вЂ” Р·Р°РїСЂРѕСЃ Рє GitHub, `apply` вЂ” СЃРєР°С‡РёРІР°РµС‚ payload, РїСЂРѕРІРµСЂСЏРµС‚ РѕРїСѓР±Р»РёРєРѕРІР°РЅРЅС‹Р№ SHA-256, СЃС‚Р°РІРёС‚ СЃ Р±СЌРєР°РїРѕРј РґР»СЏ РѕС‚РєР°С‚Р° Рё РїРµСЂРµР·Р°РїСѓСЃРєР°РµС‚ Р°РіРµРЅС‚. |

> [!TIP]
> РЈР·Р»С‹ РѕР±РЅРѕРІР»СЏСЋС‚СЃСЏ СЃР°РјРё: СЂР°Р±РѕС‚Р°СЋС‰РёР№ Р°РіРµРЅС‚ РїСЂРѕРІРµСЂСЏРµС‚ РЅРѕРІС‹Рµ СЂРµР»РёР·С‹ РІ С„РѕРЅРµ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ СЂР°Р· РІ 6 С‡Р°СЃРѕРІ) Рё СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РЅР°Р№РґРµРЅРЅРѕРµ, Р° РЅР° Windows Р·Р°РґР°С‡Р° `AntigravityMeshUpdater` РїСЂРѕРІРµСЂСЏРµС‚ СЂР°Р· РІ СЃСѓС‚РєРё РґР°Р¶Рµ РєРѕРіРґР° Р°РіРµРЅС‚ РЅРµ Р·Р°РїСѓС‰РµРЅ. `MESH_UPDATE_AUTO=0` РїРµСЂРµРІРѕРґРёС‚ СЌС‚Рѕ РІ СЂРµР¶РёРј В«С‚РѕР»СЊРєРѕ СЃРѕРѕР±С‰Р°С‚СЊВ». РџРѕРґСЂРѕР±РЅРѕСЃС‚Рё, РїРµСЂРµРјРµРЅРЅС‹Рµ РѕРєСЂСѓР¶РµРЅРёСЏ Рё РѕС‚РєР°С‚: [docs/UPDATES.md](docs/UPDATES.md).

> [!TIP]
> Gemini РґР°С‘С‚ РѕРґРЅРѕРјСѓ РІС‹Р·РѕРІСѓ РёРЅСЃС‚СЂСѓРјРµРЅС‚Р° РѕРєРѕР»Рѕ 30 СЃРµРєСѓРЅРґ. Р’СЃС‘, С‡С‚Рѕ РґРѕР»СЊС€Рµ (СЃР±РѕСЂРєРё, Р±СЌРєР°РїС‹, Р·Р°РіСЂСѓР·РєРё), Р°РіРµРЅС‚ Р·Р°РїСѓСЃРєР°РµС‚ С‡РµСЂРµР· `run_job` Рё Р·Р°Р±РёСЂР°РµС‚ СЂРµР·СѓР»СЊС‚Р°С‚ С‡РµСЂРµР· `job_output` вЂ” Р·Р°РґР°С‡Рё РЅРµ РѕР±СЂС‹РІР°СЋС‚СЃСЏ.

---

## рџљЂ Р‘С‹СЃС‚СЂС‹Р№ СЃС‚Р°СЂС‚ вЂ” 2 РјРёРЅСѓС‚С‹

### 1. РЈСЃС‚Р°РЅРѕРІРёС‚Рµ СѓР·РµР» РЅР° РџРљ, РєРѕС‚РѕСЂС‹Рј С…РѕС‚РёС‚Рµ СѓРїСЂР°РІР»СЏС‚СЊ

**рџђ§ Linux / рџЌЋ macOS**
```bash
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

**рџ“± Android (Termux)** вЂ” Р±РµР· root, Termux РёР· F-Droid
```bash
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```
РђРІС‚РѕР·Р°РїСѓСЃРє РЅР° С‚РµР»РµС„РѕРЅРµ вЂ” СЃР»СѓР¶Р±Р° runit РїР»СЋСЃ РїСЂРёР»РѕР¶РµРЅРёРµ Termux:Boot, Р° РёРјСЏ СѓР·Р»Р°
Р±РµСЂС‘С‚СЃСЏ РёР· РјРѕРґРµР»Рё СѓСЃС‚СЂРѕР№СЃС‚РІР°: СЃРј. [docs/TERMUX.md](docs/TERMUX.md).

**рџЄџ Windows (PowerShell)**
```powershell
irm https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1 | iex
```

**рџЄџ Windows вЂ” РІРёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє** (РёР· РєР»РѕРЅР° РёР»Рё СЂРµР»РёР·Р°)

```powershell
.\install-gui.cmd
```

РњР°СЃС‚РµСЂ РЅР° WinForms: С‚Рµ Р¶Рµ РІР°СЂРёР°РЅС‚С‹, С‡С‚Рѕ Рё РІ РєРѕРЅСЃРѕР»СЊРЅРѕРј СѓСЃС‚Р°РЅРѕРІС‰РёРєРµ, РїРµСЂРµРєР»СЋС‡РµРЅРёРµ СЏР·С‹РєР°
РЅР° С…РѕРґСѓ Рё РіРѕС‚РѕРІР°СЏ СЃСЃС‹Р»РєР° MCP РІ РєРѕРЅС†Рµ вЂ” СЃРј. [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

**рџ“¦ Node.js (Р»СЋР±Р°СЏ РћРЎ)**
```bash
npx gemini-computer-use
```

**рџ› пёЏ РР· РёСЃС…РѕРґРЅРёРєРѕРІ**
```bash
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick
```

Р’ РєРѕРЅС†Рµ СѓСЃС‚Р°РЅРѕРІС‰РёРє РІС‹РІРµРґРµС‚ РІР°С€Сѓ Р»РёС‡РЅСѓСЋ MCP-СЃСЃС‹Р»РєСѓ Рё СЃРєРѕРїРёСЂСѓРµС‚ РµС‘ РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°:
```text
https://racknerd-5a24bf9.merino-carob.ts.net/sse?user=<РёРјСЏ-РІР°С€РµРіРѕ-СѓР·Р»Р°>&token=<РІР°С€_СЃРµРєСЂРµС‚РЅС‹Р№_С‚РѕРєРµРЅ>
```

### 2. РџРѕРґРєР»СЋС‡РёС‚Рµ РµС‘ Рє Gemini Spark

1. РћС‚РєСЂРѕР№С‚Рµ **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)** РІ Р±СЂР°СѓР·РµСЂРµ РёР»Рё РІ РјРѕР±РёР»СЊРЅРѕРј РїСЂРёР»РѕР¶РµРЅРёРё Gemini.
2. РќР°Р¶РјРёС‚Рµ **Р”РѕР±Р°РІРёС‚СЊ РїСЂРёР»РѕР¶РµРЅРёРµ** (РёР»Рё **РќР°СЃС‚СЂРѕР№РєРё вљ™пёЏ в†’ РРЅСЃС‚СЂСѓРјРµРЅС‚С‹ / Р Р°СЃС€РёСЂРµРЅРёСЏ (MCP)**).
3. Р’СЃС‚Р°РІСЊС‚Рµ СЃСЃС‹Р»РєСѓ (**Ctrl+V**) Рё СЃРѕС…СЂР°РЅРёС‚Рµ.

### 3. Р”Р°Р№С‚Рµ Р·Р°РґР°С‡Сѓ

> *В«РџРѕРєР°Р¶Рё system_vitals Рё 5 СЃР°РјС‹С… Р±РѕР»СЊС€РёС… РїР°РїРѕРє РІ РґРѕРјР°С€РЅРµРј РєР°С‚Р°Р»РѕРіРµ.В»*

Р“РѕС‚РѕРІРѕ вЂ” Gemini С‚РµРїРµСЂСЊ Р°РіРµРЅС‚, СЂР°Р±РѕС‚Р°СЋС‰РёР№ РЅР° РІР°С€РµР№ РјР°С€РёРЅРµ. РџРѕРІС‚РѕСЂРёС‚Рµ С€Р°Рі 1 РЅР° РґСЂСѓРіРёС… РєРѕРјРїСЊСЋС‚РµСЂР°С…, С‡С‚РѕР±С‹ СѓРїСЂР°РІР»СЏС‚СЊ РёРјРё РІСЃРµРјРё СЃ РѕРґРЅРѕРіРѕ С‚РµР»РµС„РѕРЅР°.

---

## рџ–ҐпёЏ Р РµР¶РёРјС‹ СЂР°Р·РІС‘СЂС‚С‹РІР°РЅРёСЏ

| Р РµР¶РёРј | РљРѕРјР°РЅРґР° | РљРѕРіРґР° РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ |
| :--- | :--- | :--- |
| **РЁР»СЋР· + С‚СѓРЅРЅРµР»СЊ** (СЂРµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ) | `./install.sh --quick` | Р”РѕРјР°С€РЅРёРµ РџРљ Рё РЅРѕСѓС‚Р±СѓРєРё Р·Р° NAT вЂ” СЃС†РµРЅР°СЂРёР№ РјРѕР±РёР»СЊРЅРѕРіРѕ Р°РіРµРЅС‚Р°. |
| **РЈРґР°Р»С‘РЅРЅРѕ С‡РµСЂРµР· SSH** | `./install.sh --ssh=user@host` | Р Р°Р·РІРµСЂРЅСѓС‚СЊ СѓР·РµР» РЅР° СѓРґР°Р»С‘РЅРЅРѕРј Linux-СЃРµСЂРІРµСЂРµ РїСЂСЏРјРѕ РёР· С‚РµСЂРјРёРЅР°Р»Р°. |
| **Р›РѕРєР°Р»СЊРЅС‹Р№ Р°РІС‚РѕРЅРѕРјРЅС‹Р№** | `./install.sh --mode=standalone --port=8096` | Р§РёСЃС‚Рѕ Р»РѕРєР°Р»СЊРЅС‹Р№ FastMCP-СЃРµСЂРІРµСЂ РЅР° `http://localhost:8096/sse`, Р±РµР· РѕР±Р»Р°С‡РЅРѕРіРѕ СЂРµР»РµСЏ. |

РќР° Windows С‚Рµ Р¶Рµ РІР°СЂРёР°РЅС‚С‹ РµСЃС‚СЊ РІ РІРёР·СѓР°Р»СЊРЅРѕРј СѓСЃС‚Р°РЅРѕРІС‰РёРєРµ (`.\install-gui.cmd`); Р»РѕРєР°Р»СЊРЅС‹Р№ Р°РІС‚РѕРЅРѕРјРЅС‹Р№ СЂРµР¶РёРј С‚Р°Рј РѕСЃС‚Р°С‘С‚СЃСЏ РєРѕРЅСЃРѕР»СЊРЅС‹Рј (`.\install.ps1 -Mode standalone -Port 8096`).

РќР° **Android/Termux** СЂР°Р±РѕС‚Р°СЋС‚ С‚Рµ Р¶Рµ РІР°СЂРёР°РЅС‚С‹ `./install.sh`; Р°РІС‚РѕР·Р°РїСѓСЃРє вЂ” СЃР»СѓР¶Р±Р° runit РїР»СЋСЃ РїСЂРёР»РѕР¶РµРЅРёРµ Termux:Boot РІРјРµСЃС‚Рѕ systemd, Р° Р°РІС‚РѕРЅРѕРјРЅС‹Р№ СЂРµР¶РёРј РґРѕСЃС‚СѓРїРµРЅ С‚РѕР»СЊРєРѕ РІРЅСѓС‚СЂРё СЃР°РјРѕРіРѕ С‚РµР»РµС„РѕРЅР°: СЃРј. [docs/TERMUX.md](docs/TERMUX.md).

---

## рџЄџ РЈСЃС‚Р°РЅРѕРІРєР° РЅР° Windows

Р’ Windows СѓР¶Рµ РµСЃС‚СЊ PowerShell 5.1, РїРѕСЌС‚РѕРјСѓ РіРѕС‚РѕРІРёС‚СЊ РЅРёС‡РµРіРѕ РЅРµ РЅСѓР¶РЅРѕ вЂ” РЅРё Python, РЅРё Node.js, РЅРё РїСЂР°РІР° Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°. РћР±Р° СѓСЃС‚Р°РЅРѕРІС‰РёРєР° Р·Р°РєР°РЅС‡РёРІР°СЋС‚ РѕРґРёРЅР°РєРѕРІРѕ: РєР»Р°РґСѓС‚ СЃСЃС‹Р»РєСѓ MCP РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°.

|  | РљРѕРЅСЃРѕР»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє | Р’РёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє |
| :--- | :--- | :--- |
| Р—Р°РїСѓСЃРє | `irm https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1 \| iex` | `.\install-gui.cmd` |
| Р§С‚Рѕ РЅСѓР¶РЅРѕ | С‚РѕР»СЊРєРѕ PowerShell | РєР»РѕРЅ РёР»Рё СЂР°СЃРїР°РєРѕРІР°РЅРЅС‹Р№ СЂРµР»РёР· вЂ” РѕРЅ РІС‹Р·С‹РІР°РµС‚ `install.ps1`, `core/` Рё `install.sh` |
| Р’Р°СЂРёР°РЅС‚С‹ | `-Mode tunnel` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ), `-Mode standalone`, `-User`, `-Gateway`, `-Token`, `-Port`, `-DryRun` | Р‘С‹СЃС‚СЂР°СЏ РЅР°СЃС‚СЂРѕР№РєР°, РљР°СЃС‚РѕРјРЅР°СЏ РЅР°СЃС‚СЂРѕР№РєР°, РЈРґР°Р»С‘РЅРЅРѕ РїРѕ SSH |
| РЇР·С‹Рє | `-Lang en` / `-Lang ru` | РїРµСЂРµРєР»СЋС‡Р°С‚РµР»СЊ РІ С€Р°РїРєРµ РѕРєРЅР° РёР»Рё `-Lang` |

### РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРёРµ Р·Р°РІРёСЃРёРјРѕСЃС‚Рё Рё Р°СЂС…РёС‚РµРєС‚СѓСЂР°

Р—Р°СЂР°РЅРµРµ РіРѕС‚РѕРІРёС‚СЊ РЅРёС‡РµРіРѕ РЅРµ РЅСѓР¶РЅРѕ. РћР±Р° СѓСЃС‚Р°РЅРѕРІС‰РёРєР° СЃР°РјРё РґРѕР±С‹РІР°СЋС‚ РІСЃС‘ РЅРµРѕР±С…РѕРґРёРјРѕРµ, Р° РµСЃР»Рё
РЅРµ СЃРјРѕРіР»Рё вЂ” РѕСЃС‚Р°РЅР°РІР»РёРІР°СЋС‚СЃСЏ СЃ СЏРІРЅС‹Рј СЃРѕРѕР±С‰РµРЅРёРµРј, РІРјРµСЃС‚Рѕ С‚РѕРіРѕ С‡С‚РѕР±С‹ РїСЂРѕРїРёСЃР°С‚СЊ Р°РІС‚РѕР·Р°РїСѓСЃРє,
РєРѕС‚РѕСЂС‹Р№ Р·Р°РІРµРґРѕРјРѕ РЅРµ Р·Р°СЂР°Р±РѕС‚Р°РµС‚.

| | Windows (`install.ps1`) | Linux / macOS (`install.sh`) |
| :--- | :--- | :--- |
| Python | `winget` в†’ СѓСЃС‚Р°РЅРѕРІС‰РёРє python.org РґР»СЏ **СЌС‚РѕР№** Р°СЂС…РёС‚РµРєС‚СѓСЂС‹ в†’ `uv` | `apt` / `dnf` / `yum` / `zypper` / `pacman` / `apk` / `xbps` / `brew` в†’ `uv` |
| `websockets` | `pip` в†’ `pip --user` в†’ `ensurepip` в†’ `uv` в†’ venv | `pip` в†’ `pip --break-system-packages` в†’ `pip --user` в†’ `ensurepip` в†’ `uv` в†’ venv |
| Р•СЃР»Рё РЅРµ РІС‹С€Р»Рѕ РЅРёС‡РµРіРѕ | РѕСЃС‚Р°РЅРѕРІРєР° Рё С‚РѕС‡РЅР°СЏ РєРѕРјР°РЅРґР° РґР»СЏ СЂСѓС‡РЅРѕРіРѕ Р·Р°РїСѓСЃРєР° | РѕСЃС‚Р°РЅРѕРІРєР° Рё С‚РѕС‡РЅР°СЏ РєРѕРјР°РЅРґР° РґР»СЏ СЂСѓС‡РЅРѕРіРѕ Р·Р°РїСѓСЃРєР° |

`websockets` вЂ” РµРґРёРЅСЃС‚РІРµРЅРЅР°СЏ РІРЅРµС€РЅСЏСЏ Р·Р°РІРёСЃРёРјРѕСЃС‚СЊ Python: `core/mcp_tools.py` Рё
`core/server.py` РёСЃРїРѕР»СЊР·СѓСЋС‚ С‚РѕР»СЊРєРѕ СЃС‚Р°РЅРґР°СЂС‚РЅСѓСЋ Р±РёР±Р»РёРѕС‚РµРєСѓ. `uv` СЃРєР°С‡РёРІР°РµС‚СЃСЏ Р»РёС€СЊ РїРѕСЃР»Рµ
С‚РѕРіРѕ, РєР°Рє Р±РѕР»РµРµ РґРµС€С‘РІС‹Рµ РїСѓС‚Рё СѓР¶Рµ РЅРµ СЃСЂР°Р±РѕС‚Р°Р»Рё.

**РђСЂС…РёС‚РµРєС‚СѓСЂР° РѕРїСЂРµРґРµР»СЏРµС‚, РєР°РєР°СЏ СЃР±РѕСЂРєР° СЃРєР°С‡РёРІР°РµС‚СЃСЏ.** РЈСЃС‚Р°РЅРѕРІС‰РёРє СЃРїСЂР°С€РёРІР°РµС‚ СЃР°РјСѓ *РћРЎ*, Р°
РЅРµ РїСЂРѕС†РµСЃСЃ: 32-Р±РёС‚РЅС‹Р№ PowerShell РЅР° 64-Р±РёС‚РЅРѕР№ Windows СЃРѕРѕР±С‰Р°РµС‚ `x86` Рё РїСЂСЏС‡РµС‚ РЅР°СЃС‚РѕСЏС‰РµРµ
Р·РЅР°С‡РµРЅРёРµ РІ `PROCESSOR_ARCHITEW6432`, Р° СЌРјСѓР»РёСЂСѓРµРјС‹Р№ x64-РїСЂРѕС†РµСЃСЃ РЅР° ARM64 СЃРѕРѕР±С‰Р°РµС‚ `AMD64`:

| РњР°С€РёРЅР° | РЎР±РѕСЂРєР° Python | РђСЂС…РёРІ `uv` |
| :--- | :--- | :--- |
| Windows x64 | `python-3.12.5-amd64.exe` | `x86_64-pc-windows-msvc` |
| Windows ARM64 | `python-3.12.5-arm64.exe` | `aarch64-pc-windows-msvc` |
| Windows 32-Р±РёС‚ | `python-3.12.5.exe` | вЂ” (32-Р±РёС‚РЅС‹С… СЃР±РѕСЂРѕРє uv РґР»СЏ Windows РЅРµС‚) |
| Linux `x86_64` | СЃР±РѕСЂРєР° РїР°РєРµС‚РЅРѕРіРѕ РјРµРЅРµРґР¶РµСЂР° | `x86_64-unknown-linux-gnu` |
| Linux `aarch64` | СЃР±РѕСЂРєР° РїР°РєРµС‚РЅРѕРіРѕ РјРµРЅРµРґР¶РµСЂР° | `aarch64-unknown-linux-gnu` |
| Linux `armv7l` / `i686` | СЃР±РѕСЂРєР° РїР°РєРµС‚РЅРѕРіРѕ РјРµРЅРµРґР¶РµСЂР° | `armv7-unknown-linux-gnueabihf` / `i686-unknown-linux-gnu` |
| macOS `arm64` / `x86_64` | `brew` РёР»Рё `uv` | `aarch64-apple-darwin` / `x86_64-apple-darwin` |

Р’ Р°РІС‚РѕР·Р°РїСѓСЃРє РїСЂРѕРїРёСЃС‹РІР°РµС‚СЃСЏ С‚РѕС‚ РёРЅС‚РµСЂРїСЂРµС‚Р°С‚РѕСЂ, Сѓ РєРѕС‚РѕСЂРѕРіРѕ СЂРµР°Р»СЊРЅРѕ РµСЃС‚СЊ `websockets`, вЂ” СЌС‚Рѕ
РјРѕР¶РµС‚ Р±С‹С‚СЊ venv РёР»Рё CPython, РїРѕСЃС‚Р°РІР»РµРЅРЅС‹Р№ `uv`, Рё РѕР±Р° РЅР°РјРµСЂРµРЅРЅРѕ Р¶РёРІСѓС‚ РІРЅРµ `PATH`.
`install.ps1 -DryRun` Рё `install.sh --dry-run` РїРѕРєР°Р·С‹РІР°СЋС‚ Р°СЂС…РёС‚РµРєС‚СѓСЂСѓ, РЅРёС‡РµРіРѕ РЅРµ РјРµРЅСЏСЏ, Р°
РІРёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє РІС‹РІРѕРґРёС‚ РµС‘ РІ РїСЂРµРґРїРѕР»С‘С‚РЅРѕР№ РїСЂРѕРІРµСЂРєРµ.

### Р“РѕС‚РѕРІС‹Р№ .exe СѓСЃС‚Р°РЅРѕРІС‰РёРєР°

Р’ СЂРµР»РёР·Рµ РµСЃС‚СЊ РѕРґРёРЅ СЃРѕР±СЂР°РЅРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє вЂ” `AntigravityMesh-Setup-<РІРµСЂСЃРёСЏ>.exe`, РґР»СЏ РјР°С€РёРЅ,
РіРґРµ РЅРµ С…РѕС‡РµС‚СЃСЏ РЅРёС‡РµРіРѕ РєР»РѕРЅРёСЂРѕРІР°С‚СЊ:

```powershell
.\AntigravityMesh-Setup-0.4.1.exe            # РѕС‚РєСЂС‹С‚СЊ РІРёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє
.\AntigravityMesh-Setup-0.4.1.exe -Lang ru   # РЅР°С‡Р°С‚СЊ РЅР° СЂСѓСЃСЃРєРѕРј
.\AntigravityMesh-Setup-0.4.1.exe -SelfTest  # СЃР°РјРѕРїСЂРѕРІРµСЂРєР° Р±РµР· РѕРєРЅР°, РїРµС‡Р°С‚Р°РµС‚ JSON
.\AntigravityMesh-Setup-0.4.1.exe --version  # РїРѕРєР°Р·Р°С‚СЊ РІРµСЂСЃРёСЋ
```

Р’РЅСѓС‚СЂРё РЅРµРіРѕ Р»РµР¶Р°С‚ РјР°СЃС‚РµСЂ, `install.ps1`, `core/` Рё `install.sh`; РѕРЅ СЂР°СЃРїР°РєРѕРІС‹РІР°РµС‚ РёС… РІ
`%LOCALAPPDATA%\AntigravityMesh\setup\<РІРµСЂСЃРёСЏ>` (РјРѕР¶РЅРѕ РїРµСЂРµРѕРїСЂРµРґРµР»РёС‚СЊ РїРµСЂРµРјРµРЅРЅРѕР№
`MESH_SETUP_DIR`) Рё Р·Р°РїСѓСЃРєР°РµС‚ РјР°СЃС‚РµСЂР° РѕС‚С‚СѓРґР°. Р•РјСѓ РЅСѓР¶РЅС‹ С‚РѕР»СЊРєРѕ .NET Framework Рё
PowerShell, РєРѕС‚РѕСЂС‹Рµ РІ Windows СѓР¶Рµ РµСЃС‚СЊ, Р° СЃРѕР±РёСЂР°РµС‚СЃСЏ РѕРЅ РёР· СЌС‚РѕРіРѕ Р¶Рµ СЂРµРїРѕР·РёС‚РѕСЂРёСЏ СЃРєСЂРёРїС‚РѕРј
`.\build-installer-exe.ps1` вЂ” Р±РµР· SDK, Р±РµР· NuGet Рё Р±РµР· СЃРµС‚Рё.

Р¤Р°Р№Р» **РЅРµ РїРѕРґРїРёСЃР°РЅ**, РїРѕСЌС‚РѕРјСѓ SmartScreen РїСЂРё РїРµСЂРІРѕРј Р·Р°РїСѓСЃРєРµ РјРѕР¶РµС‚ РїРѕРїСЂРѕСЃРёС‚СЊ
РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ.

### Р’РёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє

![Р’РёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє РґР»СЏ Windows](docs/images/gui-welcome-ru.png)

- **Р‘С‹СЃС‚СЂР°СЏ РЅР°СЃС‚СЂРѕР№РєР°** (СЂРµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ) вЂ” РёРјСЏ СѓР·Р»Р° = РёРјСЏ РџРљ, РѕР±С‰РёР№ РґРѕРјРµРЅ С€Р»СЋР·Р°, Р°РІС‚РѕР·Р°РїСѓСЃРє Windows. Р­С‚Рѕ РІР°СЂРёР°РЅС‚ РґР»СЏ РґРѕРјР°С€РЅРµРіРѕ РџРљ РёР»Рё РЅРѕСѓС‚Р±СѓРєР°.
- **РљР°СЃС‚РѕРјРЅР°СЏ РЅР°СЃС‚СЂРѕР№РєР°** вЂ” СЃРІРѕС‘ РёРјСЏ СѓР·Р»Р°, СЃРІРѕР№ РѕР±С‰РёР№ РґРѕРјРµРЅ, РЅРµРѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ С‚РѕРєРµРЅ.
- **РЈРґР°Р»С‘РЅРЅРѕ РїРѕ SSH** вЂ” РєРѕРґ СѓР·Р»Р° РєРѕРїРёСЂСѓРµС‚СЃСЏ РЅР° Linux-С…РѕСЃС‚, С‚Р°Рј Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ `install.sh`. РќСѓР¶РµРЅ РєРѕРјРїРѕРЅРµРЅС‚ Windows *В«РљР»РёРµРЅС‚ OpenSSHВ»* Рё РІС…РѕРґ РїРѕ РєР»СЋС‡Сѓ; РІРІРѕРґ РїР°СЂРѕР»СЏ РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ.

РџРµСЂРІС‹Р№ СЌРєСЂР°РЅ Р·Р°РїСѓСЃРєР°РµС‚ `install.ps1 -DryRun` Рё РїРѕРєР°Р·С‹РІР°РµС‚, С‡С‚Рѕ РЅР°Р№РґРµРЅРѕ: Python, `websockets`, СЂР°Р·СЂРµС€С‘РЅРЅС‹Р№ РґРѕРјРµРЅ С€Р»СЋР·Р°, РєР°С‚Р°Р»РѕРі РєРѕРЅС„РёРіСѓСЂР°С†РёРё Рё РїСѓС‚СЊ Р°РІС‚РѕР·Р°РїСѓСЃРєР° вЂ” РЅРёС‡РµРіРѕ РЅРµ РјРµРЅСЏСЏ. РџРµСЂРµРґ Р·Р°РїСѓСЃРєРѕРј РѕРєРЅРѕ РїРѕРєР°Р·С‹РІР°РµС‚ С‚РѕС‡РЅСѓСЋ РєРѕРјР°РЅРґСѓ, РІРѕ РІСЂРµРјСЏ СѓСЃС‚Р°РЅРѕРІРєРё вЂ” РїРѕСЃС‚СЂРѕС‡РЅС‹Р№ РІС‹РІРѕРґ СѓСЃС‚Р°РЅРѕРІС‰РёРєР°, РІ РєРѕРЅС†Рµ вЂ” СЃСЃС‹Р»РєСѓ MCP СЃ РєРЅРѕРїРєРѕР№ РєРѕРїРёСЂРѕРІР°РЅРёСЏ, РёРЅСЃС‚СЂСѓРєС†РёСЋ РёР· С‚СЂС‘С… С€Р°РіРѕРІ Рё СЃРѕР·РґР°РЅРЅС‹Рµ РїСѓС‚Рё.

РџРѕРґСЂРѕР±РЅРѕСЃС‚Рё, СЃРЅРёРјРєРё СЌРєСЂР°РЅР° Рё РѕСЃРѕР±РµРЅРЅРѕСЃС‚Рё SSH: [docs/GUI_INSTALLER.md](docs/GUI_INSTALLER.md).

### Р§С‚Рѕ СЃРѕР·РґР°С‘С‚СЃСЏ РїСЂРё СѓСЃС‚Р°РЅРѕРІРєРµ РЅР° Windows

| РџСѓС‚СЊ | Р§С‚Рѕ СЌС‚Рѕ |
| :--- | :--- |
| `%USERPROFILE%\.config\antigravity-mesh\agent.env` | `MESH_GATEWAY`, `MESH_USER`, `MESH_TOKEN` |
| `%USERPROFILE%\.config\antigravity-mesh\domain.env` | `MESH_PUBLIC_URL` вЂ” СЂР°Р·СЂРµС€С‘РЅРЅС‹Р№ РѕР±С‰РёР№ РґРѕРјРµРЅ, С‡С‚РѕР±С‹ СЃСЃС‹Р»РєРё СѓР·Р»Р°, РµРіРѕ С‚СѓРЅРЅРµР»СЊ Рё С€Р»СЋР· РЅР°Р·С‹РІР°Р»Рё РѕРґРёРЅ Рё С‚РѕС‚ Р¶Рµ С…РѕСЃС‚ (РЅРµ РїРёС€РµС‚СЃСЏ, РµСЃР»Рё РЅР°СЃС‚СЂРѕРµРЅРѕ С‚РѕР»СЊРєРѕ РІСЃС‚СЂРѕРµРЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ) |
| `вЂ¦\Start Menu\Programs\Startup\antigravity-agent.vbs` | Р·Р°РїРёСЃСЊ Р°РІС‚РѕР·Р°РїСѓСЃРєР°, С‡С‚РѕР±С‹ СѓР·РµР» РїРѕРґРЅРёРјР°Р»СЃСЏ РїРѕСЃР»Рµ РїРµСЂРµР·Р°РіСЂСѓР·РєРё |
| `%USERPROFILE%\.config\antigravity-mesh\agent.log` | РІС‹РІРѕРґ Р°РіРµРЅС‚Р° вЂ” РЅР° СЃР»СѓС‡Р°Р№, РµСЃР»Рё Р°РІС‚РѕР·Р°РїСѓСЃРє РјРѕР»С‡Р° РЅРµ СЃСЂР°Р±РѕС‚Р°Р» |

### РЇР·С‹Рє

Р’РёР·СѓР°Р»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє СЃС‚Р°СЂС‚СѓРµС‚ РЅР° СЏР·С‹РєРµ РІР°С€РёС… СЏР·С‹РєРѕРІС‹С… РЅР°СЃС‚СЂРѕРµРє Windows вЂ” СЏР·С‹Рє РёРЅС‚РµСЂС„РµР№СЃР° Рё СЃРїРёСЃРѕРє РїСЂРµРґРїРѕС‡РёС‚Р°РµРјС‹С… СЏР·С‹РєРѕРІ С‡РёС‚Р°СЋС‚СЃСЏ РёР· СЂРµРµСЃС‚СЂР°, вЂ” Р° РµСЃР»Рё РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ, РЅР° Р°РЅРіР»РёР№СЃРєРѕРј. Р¤Р»Р°Рі `-Lang ru` / `-Lang en` РїРµСЂРµРѕРїСЂРµРґРµР»СЏРµС‚ Р°РІС‚РѕРѕРїСЂРµРґРµР»РµРЅРёРµ. РџРµСЂРµРєР»СЋС‡Р°С‚РµР»СЊ РІ С€Р°РїРєРµ РѕРєРЅР° РјРµРЅСЏРµС‚ РІСЃРµ РїРѕРґРїРёСЃРё СЃСЂР°Р·Сѓ.

### Р•СЃР»Рё С‡С‚Рѕ-С‚Рѕ РЅРµ СЂР°Р±РѕС‚Р°РµС‚

- **В«Р”РѕРјРµРЅ С€Р»СЋР·Р° РЅРµ Р·Р°РґР°РЅВ»** вЂ” СЌС‚Р° РєРѕРїРёСЏ РЅРµ Р±С‹Р»Р° РѕРїСѓР±Р»РёРєРѕРІР°РЅР° СЃ РґРѕРјРµРЅРѕРј. РЈРєР°Р¶РёС‚Рµ РµРіРѕ: СЂРµР¶РёРј *В«РљР°СЃС‚РѕРјРЅР°СЏ РЅР°СЃС‚СЂРѕР№РєР°В»* РІ РјР°СЃС‚РµСЂРµ, Р»РёР±Рѕ `.\install.ps1 -Gateway <РѕР±С‰РёР№-РґРѕРјРµРЅ>`, Р»РёР±Рѕ РїРµСЂРµРјРµРЅРЅР°СЏ `MESH_PUBLIC_URL`.
- **Р РµРіРёСЃС‚СЂР°С†РёСЏ РЅРµ РїСЂРѕС…РѕРґРёС‚** вЂ” СѓСЃС‚Р°РЅРѕРІС‰РёРє РѕСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ **РґРѕ** Р·Р°РїРёСЃРё С„Р°Р№Р»РѕРІ, РµСЃР»Рё С€Р»СЋР· РЅРµРґРѕСЃС‚СѓРїРµРЅ. РџСЂРѕРІРµСЂСЊС‚Рµ, С‡С‚Рѕ РґРѕРјРµРЅ СЂР°Р·СЂРµС€Р°РµС‚СЃСЏ, Рё РїРѕРІС‚РѕСЂРёС‚Рµ СЃ СЏРІРЅС‹Рј `-Gateway`.
- **`python` РѕС‚РєСЂС‹РІР°РµС‚ Microsoft Store** вЂ” СЌС‚Рѕ Р·Р°РіР»СѓС€РєР° App Execution Alias, Р° РЅРµ РёРЅС‚РµСЂРїСЂРµС‚Р°С‚РѕСЂ. РћР±Р° СѓСЃС‚Р°РЅРѕРІС‰РёРєР° РЅР°С…РѕРґСЏС‚ РЅР°СЃС‚РѕСЏС‰РёР№ `python.exe` РїРѕ РїРѕР»РЅРѕРјСѓ РїСѓС‚Рё; РІ Р°РІС‚РѕР·Р°РїСѓСЃРє Р·Р°РіР»СѓС€РєР° РЅРµ РїРѕРїР°РґР°РµС‚ РЅРёРєРѕРіРґР°.
- **РЈР·РµР» РЅРµ РїРѕРґРЅРёРјР°РµС‚СЃСЏ РїРѕСЃР»Рµ РїРµСЂРµР·Р°РіСЂСѓР·РєРё** вЂ” Р·Р°РїСѓСЃС‚РёС‚Рµ `.\ops\doctor.ps1`: РѕРЅ РїРѕРєР°Р¶РµС‚ РёРЅС‚РµСЂРїСЂРµС‚Р°С‚РѕСЂ РёР· Р°РІС‚РѕР·Р°РїСѓСЃРєР°, СЃРІРµР¶РµСЃС‚СЊ heartbeat, РїРѕСЃР»РµРґРЅРёРµ СЃС‚СЂРѕРєРё `agent.log` (РІРєР»СЋС‡Р°СЏ РѕР±РѕСЂРІР°РЅРЅСѓСЋ) Рё С‚Рѕ, С‡С‚Рѕ Рѕ СѓР·Р»Рµ РґСѓРјР°РµС‚ С€Р»СЋР·. `.\ops\windows\agent-watchdog.ps1` РІС‹РїРѕР»РЅСЏРµС‚ С‚Сѓ Р¶Рµ РїСЂРѕРІРµСЂРєСѓ РѕРґРёРЅ СЂР°Р· Рё РїРѕРґРЅРёРјР°РµС‚ Р°РіРµРЅС‚; Р·Р°РґР°С‡Р° `AntigravityMeshWatchdog` РїРѕРІС‚РѕСЂСЏРµС‚ РµС‘ РєР°Р¶РґС‹Рµ РїСЏС‚СЊ РјРёРЅСѓС‚, РїРѕСЌС‚РѕРјСѓ СѓРјРµСЂС€РёР№ Р°РіРµРЅС‚ РїРѕРґРЅРёРјР°РµС‚СЃСЏ СЃР°Рј.

### РђРІС‚РѕРЅРѕРјРЅС‹Р№ СЂРµР¶РёРј РЅР° Windows

Р’ РјР°СЃС‚РµСЂРµ РµРіРѕ РЅРµС‚, РёСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРѕРЅСЃРѕР»СЊРЅС‹Р№ СѓСЃС‚Р°РЅРѕРІС‰РёРє:

```powershell
.\install.ps1 -Mode standalone -Port 8096
```

РћРЅ РїРѕРґРЅРёРјР°РµС‚ Р»РѕРєР°Р»СЊРЅС‹Р№ FastMCP-СЃРµСЂРІРµСЂ РЅР° `http://localhost:8096/sse` Рё РєР»Р°РґС‘С‚ СЌС‚Сѓ СЃСЃС‹Р»РєСѓ РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°.

---

## рџ“± РЈСЃС‚Р°РЅРѕРІРєР° РЅР° Android (Termux)

РўРѕС‚ Р¶Рµ `install.sh` СЃС‚Р°РІРёС‚ СѓР·РµР» РЅР° СЃРјР°СЂС‚С„РѕРЅ РёР»Рё РїР»Р°РЅС€РµС‚ Р±РµР· root, Рё С‚РµР»РµС„РѕРЅ
РїРѕСЏРІР»СЏРµС‚СЃСЏ РІ Gemini Spark РєР°Рє РѕР±С‹С‡РЅР°СЏ РјР°С€РёРЅР°. РџРѕР»РЅР°СЏ РёРЅСЃС‚СЂСѓРєС†РёСЏ:
**[docs/TERMUX.md](docs/TERMUX.md)**.

```bash
# 1. Termux РёР· F-Droid (РЅРµ РёР· Google Play); РїРѕ Р¶РµР»Р°РЅРёСЋ Termux:Boot Рё Termux:API
pkg update -y
# 2. РѕР±С‹С‡РЅР°СЏ РѕРґРЅРѕСЃС‚СЂРѕС‡РЅР°СЏ СѓСЃС‚Р°РЅРѕРІРєР°
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

Р§С‚Рѕ РјРµРЅСЏРµС‚СЃСЏ РЅР° С‚РµР»РµС„РѕРЅРµ Рё РєР°Рє СЌС‚Рѕ СЂРµС€Р°РµС‚ СѓСЃС‚Р°РЅРѕРІС‰РёРє:

| | |
| :--- | :--- |
| **РџР°РєРµС‚С‹** | `pkg` РІРјРµСЃС‚Рѕ `apt`, `python`/`python-pip` РІРјРµСЃС‚Рѕ `python3-pip`/`python3-venv`: РЅР° С‚РµР»РµС„РѕРЅРµ РЅРµС‚ root Рё РЅРµС‚ `sudo`. |
| **РРјСЏ СѓР·Р»Р°** | РњРѕРґРµР»СЊ СѓСЃС‚СЂРѕР№СЃС‚РІР° (`Pixel 7 Pro` в†’ `pixel7pro`): Android РѕС‚РІРµС‡Р°РµС‚ `localhost` РІСЃРµРј РїСЂРёР»РѕР¶РµРЅРёСЏРј, Р° С€Р»СЋР· РґРµСЂР¶РёС‚ РѕРґРёРЅ С‚СѓРЅРЅРµР»СЊ РЅР° РёРјСЏ. |
| **Р¤Р°Р№Р» РґРѕРјРµРЅР°** | `~/.config/antigravity-mesh/domain.env` вЂ” РїРёСЃР°С‚СЊ РІ `/etc` РЅРµРєСѓРґР°. РЈСЃС‚Р°РЅРѕРІС‰РёРє Р·Р°РїРёСЃС‹РІР°РµС‚ С‚СѓРґР° СЂР°Р·СЂРµС€С‘РЅРЅС‹Р№ РѕР±С‰РёР№ РґРѕРјРµРЅ, С‡С‚РѕР±С‹ СЃСЃС‹Р»РєРё С‚РµР»РµС„РѕРЅР°, РµРіРѕ С‚СѓРЅРЅРµР»СЊ Рё С€Р»СЋР· РЅР°Р·С‹РІР°Р»Рё РѕРґРёРЅ Рё С‚РѕС‚ Р¶Рµ С…РѕСЃС‚ (РІСЃС‚СЂРѕРµРЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ РЅРµ Р·Р°РїРёСЃС‹РІР°РµС‚СЃСЏ). |
| **РђРІС‚РѕР·Р°РїСѓСЃРє** | РЎР»СѓР¶Р±Р° **runit** (`termux-services`, Р°РЅР°Р»РѕРі `Restart=always`) РїР»СЋСЃ СЃРєСЂРёРїС‚ **Termux:Boot**, РєРѕС‚РѕСЂС‹Р№ Р±РµСЂС‘С‚ wake lock РїРѕСЃР»Рµ РїРµСЂРµР·Р°РіСЂСѓР·РєРё. |
| **Р‘СѓС„РµСЂ РѕР±РјРµРЅР°** | `termux-clipboard-set` (Termux:API): СЃСЃС‹Р»РєР° MCP СЃСЂР°Р·Сѓ РІСЃС‚Р°РІР»СЏРµС‚СЃСЏ РІ РїСЂРёР»РѕР¶РµРЅРёРµ Gemini. |

> [!IMPORTANT]
> Р”РІР° РїРµСЂРµРєР»СЋС‡Р°С‚РµР»СЏ РЅР° СЃС‚РѕСЂРѕРЅРµ Android РЅСѓР¶РЅРѕ РІРєР»СЋС‡РёС‚СЊ РІР°Рј: СѓСЃС‚Р°РЅРѕРІРёС‚СЊ
> **Termux:Boot** Рё РѕС‚РєСЂС‹С‚СЊ РµРіРѕ РѕРґРёРЅ СЂР°Р·, Р° С‚Р°РєР¶Рµ РїРѕСЃС‚Р°РІРёС‚СЊ РѕРїС‚РёРјРёР·Р°С†РёСЋ Р±Р°С‚Р°СЂРµРё РІ
> В«Р‘РµР· РѕРіСЂР°РЅРёС‡РµРЅРёР№В» РґР»СЏ Termux. Р‘РµР· СЌС‚РѕРіРѕ Android РІС‹РіСЂСѓР¶Р°РµС‚ СѓР·РµР» РїСЂРё РІС‹РєР»СЋС‡РµРЅРЅРѕРј
> СЌРєСЂР°РЅРµ.

РўРµР»РµС„РѕРЅ С‚Р°РєР¶Рµ РґРѕР»Р¶РµРЅ **СЂРµР·РѕР»РІРёС‚СЊ РёРјСЏ С€Р»СЋР·Р°**. РЁР»СЋР· РІ РїСЂРёРІР°С‚РЅРѕР№ СЃРµС‚Рё (Tailscale,
VPN, Р·Р°РїРёСЃСЊ РІ DNS РЅР° РЅРѕСѓС‚Р±СѓРєРµ) РІРёРґРµРЅ С‚РѕР»СЊРєРѕ С‚Р°Рј, Р° РјРѕР±РёР»СЊРЅР°СЏ СЃРµС‚СЊ РїСЂРёРІР°С‚РЅС‹Рµ РёРјРµРЅР°
РЅРµ Р·РЅР°РµС‚. РџРѕРґРєР»СЋС‡РёС‚Рµ С‚РµР»РµС„РѕРЅ Рє СЌС‚РѕР№ СЃРµС‚Рё Р»РёР±Рѕ РґР°Р№С‚Рµ С€Р»СЋР·Сѓ РїСѓР±Р»РёС‡РЅРѕ СЂРµР·РѕР»РІРёРјРѕРµ РёРјСЏ:
СѓСЃС‚Р°РЅРѕРІС‰РёРє СЂР°СЃРїРѕР·РЅР°С‘С‚ РЅРµСЂРµР·РѕР»РІРёРјРѕРµ РёРјСЏ Рё СЃРѕРѕР±С‰Р°РµС‚ РѕР± СЌС‚РѕРј РґРѕ Р»СЋР±С‹С… Р·Р°РїРёСЃРµР№ вЂ” СЃРј.
[docs/TERMUX.md](docs/TERMUX.md#10-РїСЂРёРІР°С‚РЅС‹Р№-С€Р»СЋР·-tailscale--vpn).

```bash
sv status agy-agent                       # СЂР°Р±РѕС‚Р°РµС‚ Р»Рё
sv restart agy-agent                      # РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ
tail -f $PREFIX/var/log/sv/agy-agent/current
```

`--mode=standalone` С‚РѕР¶Рµ СЂР°Р±РѕС‚Р°РµС‚, РЅРѕ СЃР»СѓС€Р°РµС‚ С‚РѕР»СЊРєРѕ `127.0.0.1` вЂ” РґРѕСЃС‚СѓРїРµРЅ Р»РёС€СЊ
РІРЅСѓС‚СЂРё СЌС‚РѕРіРѕ С‚РµР»РµС„РѕРЅР°. Р§С‚РѕР±С‹ СѓРїСЂР°РІР»СЏС‚СЊ С‚РµР»РµС„РѕРЅРѕРј РёР· Gemini, РёСЃРїРѕР»СЊР·СѓР№С‚Рµ СЂРµР¶РёРј
С‚СѓРЅРЅРµР»СЏ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ).

---

## рџ”„ РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРёРµ РѕР±РЅРѕРІР»РµРЅРёСЏ

РЈР·РµР» СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РёР· СЂРµР»РёР·Р° РЅР° GitHub Рё РґР°Р»СЊС€Рµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚ СЃРµР±СЏ РІ Р°РєС‚СѓР°Р»СЊРЅРѕРј
СЃРѕСЃС‚РѕСЏРЅРёРё СЃР°Рј. РќРёС‡РµРіРѕ РЅРµ РЅСѓР¶РЅРѕ РїРµСЂРµСѓСЃС‚Р°РЅР°РІР»РёРІР°С‚СЊ СЂСѓРєР°РјРё, Рё РЅРёС‡РµРіРѕ
РЅРµРїСЂРѕРІРµСЂРµРЅРЅРѕРіРѕ РЅРµ СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ.

- **РЈР·РµР» РїСЂРѕРІРµСЂСЏРµС‚ СЃР°Рј.** Р Р°Р±РѕС‚Р°СЋС‰РёР№ Р°РіРµРЅС‚ СЂР°Р· РІ 6 С‡Р°СЃРѕРІ
  (`MESH_UPDATE_CHECK_INTERVAL`) СЃРїСЂР°С€РёРІР°РµС‚ GitHub Releases API, СЃСЂР°РІРЅРёРІР°РµС‚ С‚РµРі СЃ
  РІРµСЂСЃРёРµР№ РІ `core/version.py` Рё СЃС‚Р°РІРёС‚ РЅР°Р№РґРµРЅРЅРѕРµ. РќР° Windows С‚Рѕ Р¶Рµ СЃР°РјРѕРµ СЂР°Р· РІ
  СЃСѓС‚РєРё РґРµР»Р°РµС‚ Р·Р°РґР°С‡Р° `AntigravityMeshUpdater` вЂ” РґР°Р¶Рµ РµСЃР»Рё Р°РіРµРЅС‚ РЅРµ Р·Р°РїСѓС‰РµРЅ.
  `MESH_UPDATE_AUTO=0` РѕС‚РєР»СЋС‡Р°РµС‚ СѓСЃС‚Р°РЅРѕРІРєСѓ, РѕСЃС‚Р°РІР»СЏСЏ С‚РѕР»СЊРєРѕ СѓРІРµРґРѕРјР»РµРЅРёСЏ.
- **РЎРЅР°С‡Р°Р»Р° РїСЂРѕРІРµСЂРєР°, РїРѕС‚РѕРј СѓСЃС‚Р°РЅРѕРІРєР°.** Payload СЃРєР°С‡РёРІР°РµС‚СЃСЏ, РµРіРѕ SHA-256
  СЃСЂР°РІРЅРёРІР°РµС‚СЃСЏ СЃ РѕРїСѓР±Р»РёРєРѕРІР°РЅРЅРѕР№ РєРѕРЅС‚СЂРѕР»СЊРЅРѕР№ СЃСѓРјРјРѕР№, Рё С‚РѕР»СЊРєРѕ Р·Р°С‚РµРј Р·Р°РјРµРЅСЏСЋС‚СЃСЏ
  `core/`, `skills/` Рё `ops/`. РќРµС‚ СЃСѓРјРјС‹ вЂ” РЅРµС‚ РѕР±РЅРѕРІР»РµРЅРёСЏ.
- **РћС‚РєР°С‚ РІРѕР·РјРѕР¶РµРЅ РІСЃРµРіРґР°.** Р—Р°РјРµРЅС‘РЅРЅС‹Рµ РєР°С‚Р°Р»РѕРіРё СЃРѕС…СЂР°РЅСЏСЋС‚СЃСЏ РІ
  `%USERPROFILE%\.config\antigravity-mesh\backups\<РІРµСЂСЃРёСЏ>-<РІСЂРµРјСЏ>\`, Р°
  РѕР±РЅРѕРІР»РµРЅРёРµ, РїСЂРµСЂРІР°РЅРЅРѕРµ СѓР±РёР№СЃС‚РІРѕРј РїСЂРѕС†РµСЃСЃР° РёР»Рё РѕС‚РєР»СЋС‡РµРЅРёРµРј РїРёС‚Р°РЅРёСЏ,
  РѕС‚РєР°С‚С‹РІР°РµС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїСЂРё СЃР»РµРґСѓСЋС‰РµРј Р·Р°РїСѓСЃРєРµ.
- **РЈР·РµР» РІРѕР·РІСЂР°С‰Р°РµС‚СЃСЏ СѓР¶Рµ РЅРѕРІС‹Рј.** РђРіРµРЅС‚ РїРµСЂРµР·Р°РїСѓСЃРєР°РµС‚СЃСЏ С‚РµРј, С‡С‚Рѕ Р·Р° РЅРёРј
  РїСЂРёСЃРјР°С‚СЂРёРІР°РµС‚ (Р·Р°РґР°С‡Р°-СЃС‚РѕСЂРѕР¶ РЅР° Windows, `systemctl --user restart agy-agent.service`,
  `launchctl kickstart -k`), Р° РµСЃР»Рё РЅР°РґР·РѕСЂР° РЅРµС‚ вЂ” Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ РЅР°РїСЂСЏРјСѓСЋ.

Р’СЂСѓС‡РЅСѓСЋ, РІ Р»СЋР±РѕР№ РјРѕРјРµРЅС‚:

```powershell
gemini-computer-use update -Check      # РІС‹С€РµР» Р»Рё РЅРѕРІС‹Р№ СЂРµР»РёР·?
gemini-computer-use update             # СѓСЃС‚Р°РЅРѕРІРёС‚СЊ СЃРµР№С‡Р°СЃ Рё РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ
gemini-computer-use update -Check -Json
```

РР· Gemini: РїРѕРїСЂРѕСЃРёС‚Рµ СѓР·РµР» РІС‹РїРѕР»РЅРёС‚СЊ `mesh_update` (`status`, Р·Р°С‚РµРј `check`, Р·Р°С‚РµРј
`apply`).

Р РµР·СѓР»СЊС‚Р°С‚ РїРѕСЃР»РµРґРЅРµР№ РїСЂРѕРІРµСЂРєРё РїРѕРїР°РґР°РµС‚ РІ heartbeat, РїРѕСЌС‚РѕРјСѓ РµРіРѕ РїРѕРєР°Р·С‹РІР°РµС‚
`ops\doctor.ps1`, Р° РїРѕР»РЅР°СЏ РёСЃС‚РѕСЂРёСЏ вЂ” РІ `%USERPROFILE%\.config\antigravity-mesh\update.log`.
РќР°СЃС‚СЂРѕР№РєРё, РєРѕРґС‹ РІС‹С…РѕРґР°, РѕС‚РєР°С‚ Рё РґРёР°РіРЅРѕСЃС‚РёРєР°: [docs/UPDATES.md](docs/UPDATES.md).

---

## рџ”— РћРґРёРЅ РѕР±С‰РёР№ РґРѕРјРµРЅ (РєР°РЅРѕРЅРёС‡РµСЃРєРёР№ С„РѕСЂРјР°С‚ URL)

РЈР·РµР» **РЅРёРєРѕРіРґР°** РЅРµ Р°РґСЂРµСЃСѓРµС‚СЃСЏ СЃРѕР±СЃС‚РІРµРЅРЅС‹Рј РёРјРµРЅРµРј С…РѕСЃС‚Р°. РЁР»СЋР· РїСѓР±Р»РёРєСѓРµС‚ РѕРґРёРЅ РѕР±С‰РёР№ РґРѕРјРµРЅ, Р° РёРјСЏ СѓР·Р»Р° РїРµСЂРµРґР°С‘С‚СЃСЏ РїР°СЂР°РјРµС‚СЂРѕРј:

| РќР°Р·РЅР°С‡РµРЅРёРµ | РљР°РЅРѕРЅРёС‡РµСЃРєРёР№ URL |
| :--- | :--- |
| MCP С‡РµСЂРµР· SSE | `https://<РѕР±С‰РёР№-РґРѕРјРµРЅ>/sse?user=<РёРјСЏ-СѓР·Р»Р°>&token=<С‚РѕРєРµРЅ>` |
| MCP Streamable HTTP | `https://<РѕР±С‰РёР№-РґРѕРјРµРЅ>/mcp?user=<РёРјСЏ-СѓР·Р»Р°>&token=<С‚РѕРєРµРЅ>` |
| РћР±СЂР°С‚РЅС‹Р№ С‚СѓРЅРЅРµР»СЊ (Р°РіРµРЅС‚) | `wss://<РѕР±С‰РёР№-РґРѕРјРµРЅ>/ws/tunnel?user=<РёРјСЏ-СѓР·Р»Р°>&token=<С‚РѕРєРµРЅ>` |

РџРѕС‡РµРјСѓ С‚Р°Рє: РєР°Р¶РґРѕРјСѓ РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕРјСѓ РёРјРµРЅРё С…РѕСЃС‚Р° РЅСѓР¶РЅР° СЃРІРѕСЏ DNS-Р·Р°РїРёСЃСЊ **Рё** СЃРІРѕР№ SAN РІ TLS-СЃРµСЂС‚РёС„РёРєР°С‚Рµ. Р•СЃР»Рё РёРјРµРЅРё РЅРµС‚ РІ СЃРµСЂС‚РёС„РёРєР°С‚Рµ, TLS-СЂСѓРєРѕРїРѕР¶Р°С‚РёРµ РїР°РґР°РµС‚, Рё РєР»РёРµРЅС‚ Gemini РїРѕРєР°Р·С‹РІР°РµС‚ РЅРµРІРЅСЏС‚РЅСѓСЋ РѕС€РёР±РєСѓ В«РЅРµ СѓРґР°С‘С‚СЃСЏ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє С…РѕСЃС‚СѓВ». РЎ РѕРґРЅРёРј РѕР±С‰РёРј РґРѕРјРµРЅРѕРј СЃРµСЂС‚РёС„РёРєР°С‚ РїРѕРєСЂС‹РІР°РµС‚ РІСЃРµ СѓР·Р»С‹ СЃСЂР°Р·Сѓ, Р° РґРѕР±Р°РІР»РµРЅРёРµ СѓР·Р»Р° вЂ” СЌС‚Рѕ С‚РѕР»СЊРєРѕ РІС‹Р·РѕРІ СЂРµРіРёСЃС‚СЂР°С†РёРё.

- РћР±С‰РёР№ РґРѕРјРµРЅ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ `smart-server.online`; РїРµСЂРµРѕРїСЂРµРґРµР»СЏРµС‚СЃСЏ С‡РµСЂРµР· `./install.sh --domain=<РѕР±С‰РёР№-РґРѕРјРµРЅ>` (РЅР° СЃС‚РѕСЂРѕРЅРµ СѓР·Р»Р°) РёР»Рё `MESH_PUBLIC_URL` (РЅР° СЃС‚РѕСЂРѕРЅРµ С€Р»СЋР·Р°).
- РЎС‚Р°СЂС‹Рµ СЃСЃС‹Р»РєРё СЃ СЃСѓР±РґРѕРјРµРЅР°РјРё СѓСЃС‚СЂРѕР№СЃС‚РІР° РїСЂРѕРґРѕР»Р¶Р°СЋС‚ СЂР°Р±РѕС‚Р°С‚СЊ РґР»СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё Рё РїРёС€СѓС‚ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ РІ Р»РѕРі; `MESH_LEGACY_SUBDOMAIN=0` РЅР° С€Р»СЋР·Рµ РѕС‚РєР»СЋС‡Р°РµС‚ РёС….
- РЈСЃС‚Р°РЅРѕРІС‰РёРє С‚РµРїРµСЂСЊ Р·Р°РїРёСЃС‹РІР°РµС‚ СЂР°Р·СЂРµС€С‘РЅРЅС‹Р№ РґРѕРјРµРЅ РІ С„Р°Р№Р» РґРѕРјРµРЅР°, С‡С‚РѕР±С‹ РїСѓР±Р»РёС‡РЅС‹Рµ СЃСЃС‹Р»РєРё СѓР·Р»Р°, РµРіРѕ С‚СѓРЅРЅРµР»СЊ Рё С€Р»СЋР· РЅР°Р·С‹РІР°Р»Рё РѕРґРёРЅ Рё С‚РѕС‚ Р¶Рµ С…РѕСЃС‚: `MESH_PUBLIC_URL=https://<РѕР±С‰РёР№-РґРѕРјРµРЅ>` РІ `domain.env` вЂ” РІ РѕР±РµРёС… РІРµС‚РєР°С… СѓСЃС‚Р°РЅРѕРІРєРё (standalone Рё В«РѕР±Р»Р°С‡РЅС‹Р№ С€Р»СЋР·/С‚СѓРЅРЅРµР»СЊВ» СЃСЂР°Р·Сѓ РїРѕСЃР»Рµ `agent.env`). Р—РЅР°С‡РµРЅРёРµ СЃРЅР°С‡Р°Р»Р° РЅРѕСЂРјР°Р»РёР·СѓРµС‚СЃСЏ, РїР»РµР№СЃС…РѕР»РґРµСЂ `__MESH_DOMAIN__` Рё РїСѓСЃС‚РѕРµ Р·РЅР°С‡РµРЅРёРµ РЅРµ РїРёС€СѓС‚СЃСЏ РЅРёРєРѕРіРґР°, СѓР¶Рµ РЅР°Р·РІР°РЅРЅС‹Р№ С…РѕСЃС‚ РЅРµ РїРµСЂРµРїРёСЃС‹РІР°РµС‚СЃСЏ, Р° РЅРµСѓРґР°С‡РЅР°СЏ Р·Р°РїРёСЃСЊ РЅРµ С„Р°С‚Р°Р»СЊРЅР° вЂ” СѓСЃС‚Р°РЅРѕРІС‰РёРє РїРµС‡Р°С‚Р°РµС‚ С‚РѕС‡РЅСѓСЋ РєРѕРјР°РЅРґСѓ РґР»СЏ СЂСѓС‡РЅРѕРіРѕ Р·Р°РїСѓСЃРєР°. Р’СЃС‚СЂРѕРµРЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ РЅРµ Р·Р°РїРёСЃС‹РІР°РµС‚СЃСЏ РІРѕРѕР±С‰Рµ: СЌС‚Рѕ Р·Р°РїР°СЃРЅРѕР№ РІР°СЂРёР°РЅС‚, Р° РЅРµ РєРѕРЅС„РёРіСѓСЂР°С†РёСЏ, Рё Р·Р°РєСЂРµРїР»РµРЅРёРµ РµРіРѕ РІ С„Р°Р№Р»Рµ Р·Р°СЃС‚Р°РІРёР»Рѕ Р±С‹ `core/domain.py` РІРѕРІСЃРµ РїРµСЂРµСЃС‚Р°С‚СЊ СЃРјРѕС‚СЂРµС‚СЊ РЅР° СѓСЃС‚Р°СЂРµРІС€РёР№ `MESH_GATEWAY`, С‚Р°Рє С‡С‚Рѕ РїРѕР·РґРЅРµР№С€Р°СЏ РїСЂР°РІРєР° `agent.env` РјРѕР»С‡Р° РёРіРЅРѕСЂРёСЂРѕРІР°Р»Р°СЃСЊ Р±С‹ вЂ” РїРѕС‚РµСЂСЊ РЅРµС‚, РїРѕС‚РѕРјСѓ С‡С‚Рѕ Р±РµР· С„Р°Р№Р»Р° С‚Рѕ Р¶Рµ Р·РЅР°С‡РµРЅРёРµ Рё РµСЃС‚СЊ РїРѕСЃР»РµРґРЅРёР№ Р·Р°РїР°СЃРЅРѕР№ РІР°СЂРёР°РЅС‚ СЂРµР·РѕР»РІРµСЂР°. РЎСѓС…РѕР№ РїСЂРѕРіРѕРЅ СЃРѕРѕР±С‰Р°РµС‚ С„Р°Р№Р» Рё Р·РЅР°С‡РµРЅРёРµ, РєРѕС‚РѕСЂРѕРµ Р±С‹Р»Рѕ Р±С‹ Р·Р°РїРёСЃР°РЅРѕ, вЂ” РІРєР»СЋС‡Р°СЏ `not written (the built-in default is a fallback, not a configuration)`, вЂ” Рё РїРѕ-РїСЂРµР¶РЅРµРјСѓ РЅРёС‡РµРіРѕ РЅРµ РјРµРЅСЏРµС‚ РЅР° РґРёСЃРєРµ.
- Р—Р°С‡РµРј СЌС‚Рѕ РЅСѓР¶РЅРѕ: СЃСЃС‹Р»РєРё РЅР° С„Р°Р№Р»С‹ СЃС‚СЂРѕРёС‚ `core/domain.py` РїРѕ С†РµРїРѕС‡РєРµ `MESH_PUBLIC_URL` в†’ `AGY_PUBLIC_BASE_URL` в†’ С„Р°Р№Р» `domain.env` в†’ РІСЃС‚СЂРѕРµРЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ, Р° СѓСЃС‚Р°СЂРµРІС€РёР№ `MESH_GATEWAY` РёР· `agent.env` РѕРЅ РЅР°РјРµСЂРµРЅРЅРѕ РЅРµ С‡РёС‚Р°РµС‚ (РµРіРѕ С‡РёС‚Р°РµС‚ С‚РѕР»СЊРєРѕ `gateway_host()` вЂ” С…РѕСЃС‚ С‚СѓРЅРЅРµР»СЏ). РџРѕСЌС‚РѕРјСѓ СѓР·РµР», РїРѕСЃС‚Р°РІР»РµРЅРЅС‹Р№ РѕР±С‹С‡РЅС‹Рј СЃРїРѕСЃРѕР±РѕРј (`agent.env` СЃ `MESH_GATEWAY`/`MESH_USER`/`MESH_TOKEN` Рё Р±РµР· С„Р°Р№Р»Р° РґРѕРјРµРЅР°), Р·РІРѕРЅРёР» РЅР° РїСЂР°РІРёР»СЊРЅС‹Р№ С€Р»СЋР·, Р° СЃСЃС‹Р»РєРё РїСѓР±Р»РёРєРѕРІР°Р» РЅР° РІСЃС‚СЂРѕРµРЅРЅРѕРј РґРѕРјРµРЅРµ. РќР° СѓР¶Рµ СѓСЃС‚Р°РЅРѕРІР»РµРЅРЅРѕРј СѓР·Р»Рµ: РїРѕРІС‚РѕСЂРёС‚Рµ СѓСЃС‚Р°РЅРѕРІРєСѓ РёР»Рё РґРѕРїРёС€РёС‚Рµ РѕРґРЅСѓ СЃС‚СЂРѕРєСѓ РІСЂСѓС‡РЅСѓСЋ вЂ” `mkdir -p ~/.config/antigravity-mesh && echo 'MESH_PUBLIC_URL=https://<РґРѕРјРµРЅ>' > ~/.config/antigravity-mesh/domain.env`, РІ Linux С‚Рѕ Р¶Рµ С‡РµСЂРµР· `sudo tee /etc/antigravity-mesh/domain.env`. РЈР¶Рµ РІС‹РґР°РЅРЅС‹Рµ СЃСЃС‹Р»РєРё РїСЂРѕРґРѕР»Р¶Р°СЋС‚ СЂР°Р±РѕС‚Р°С‚СЊ, РЅРѕРІС‹Рµ Р±РµСЂСѓС‚ РЅР°СЃС‚СЂРѕРµРЅРЅС‹Р№ РґРѕРјРµРЅ.
- `MESH_DOMAIN_FILE` РїРµСЂРµРѕРїСЂРµРґРµР»СЏРµС‚ С‚РѕР»СЊРєРѕ *РїСѓС‚СЊ*, Рё Р·Р°РґР°РІР°С‚СЊ РµРіРѕ РЅСѓР¶РЅРѕ РІ РєРѕРЅС„РёРіСѓСЂР°С†РёРё СЃР°РјРѕРіРѕ СѓР·Р»Р°, Р° РЅРµ РІ Р·Р°РїСѓСЃРєРµ СѓСЃС‚Р°РЅРѕРІС‰РёРєР°: РґРµСЂР¶РёС‚Рµ РµРіРѕ РІ `agent.env` (РІСЃРµ РєР»СЋС‡Рё `MESH_*` РѕС‚С‚СѓРґР° СЌРєСЃРїРѕСЂС‚РёСЂСѓСЋС‚СЃСЏ СѓР·Р»Сѓ), РёРЅР°С‡Рµ РґРѕРјРµРЅ РѕРєР°Р¶РµС‚СЃСЏ РІ С„Р°Р№Р»Рµ, РєРѕС‚РѕСЂС‹Р№ СѓР·РµР» РЅРёРєРѕРіРґР° РЅРµ РїСЂРѕС‡РёС‚Р°РµС‚. РљР°С‚Р°Р»РѕРі С‚РѕРіРѕ С„Р°Р№Р»Р°, РєРѕС‚РѕСЂС‹Р№ СЂРµР°Р»СЊРЅРѕ РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ, СѓСЃС‚Р°РЅРѕРІС‰РёРє СЃРѕР·РґР°С‘С‚ СЃР°Рј. РџСЂРё `--ssh=<С…РѕСЃС‚>` СЂР°Р·СЂРµС€С‘РЅРЅС‹Р№ Р·РґРµСЃСЊ РґРѕРјРµРЅ РїРµСЂРµРґР°С‘С‚СЃСЏ СѓРґР°Р»С‘РЅРЅРѕРјСѓ `install.sh` РєР°Рє `--domain=`, РїРѕСЌС‚РѕРјСѓ С†РµР»СЊ Р·Р°РїРёСЃС‹РІР°РµС‚ РёРјРµРЅРЅРѕ СЌС‚РѕС‚ С…РѕСЃС‚, Р° РЅРµ СЂР°Р·СЂРµС€Р°РµС‚ РґРѕРјРµРЅ Р·Р°РЅРѕРІРѕ (РєРѕРїРёСЏ РёР· СЂРµРїРѕР·РёС‚РѕСЂРёСЏ СЃ РїР»РµР№СЃС…РѕР»РґРµСЂРѕРј `__MESH_DOMAIN__` РёРЅР°С‡Рµ СѓС€Р»Р° Р±С‹ РЅР° РІСЃС‚СЂРѕРµРЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ).

---

## рџ”’ Р‘РµР·РѕРїР°СЃРЅРѕСЃС‚СЊ

- **Р”РѕСЃС‚СѓРї С‚РѕР»СЊРєРѕ РїРѕ С‚РѕРєРµРЅСѓ** вЂ” РєР°Р¶РґС‹Р№ Р·Р°РїСЂРѕСЃ РґРѕР»Р¶РµРЅ СЃРѕРґРµСЂР¶Р°С‚СЊ СЃРµРєСЂРµС‚РЅС‹Р№ С‚РѕРєРµРЅ СѓР·Р»Р°.
- **РўРѕР»СЊРєРѕ РёСЃС…РѕРґСЏС‰РёР№ С‚СѓРЅРЅРµР»СЊ** вЂ” СѓР·Р»С‹ РЅРµ СЃР»СѓС€Р°СЋС‚ РїСѓР±Р»РёС‡РЅС‹Рµ РїРѕСЂС‚С‹; Р°РіРµРЅС‚ СЃР°Рј РїРѕРґРєР»СЋС‡Р°РµС‚СЃСЏ Рє С€Р»СЋР·Сѓ.
- **РЁР»СЋР· вЂ” С‚РѕР»СЊРєРѕ СЂРµР»РµР№** вЂ” РѕРЅ С‚РµСЂРјРёРЅРёСЂСѓРµС‚ TLS Рё РїРµСЂРµРґР°С‘С‚ РІС‹Р·РѕРІС‹; РєРѕРјР°РЅРґС‹ РІС‹РїРѕР»РЅСЏСЋС‚СЃСЏ С‚РѕР»СЊРєРѕ РЅР° РІР°С€РµРј СѓР·Р»Рµ.
- **Р‘РµР· root РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ** вЂ” Р°РіРµРЅС‚ СЂР°Р±РѕС‚Р°РµС‚ РІ РїСЂРѕСЃС‚СЂР°РЅСЃС‚РІРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.

> [!WARNING]
> Р›СЋР±РѕР№, Сѓ РєРѕРіРѕ РµСЃС‚СЊ РІР°С€Р° MCP-СЃСЃС‹Р»РєР°, РјРѕР¶РµС‚ РІС‹РїРѕР»РЅСЏС‚СЊ РєРѕРјР°РЅРґС‹ РЅР° СЌС‚РѕР№ РјР°С€РёРЅРµ. РҐСЂР°РЅРёС‚Рµ РµС‘ РєР°Рє РїР°СЂРѕР»СЊ: РЅРµ РїСѓР±Р»РёРєСѓР№С‚Рµ, Р° РїСЂРё СѓС‚РµС‡РєРµ РїРµСЂРµСѓСЃС‚Р°РЅРѕРІРёС‚Рµ СѓР·РµР», С‡С‚РѕР±С‹ СЃРјРµРЅРёС‚СЊ С‚РѕРєРµРЅ.

---

## рџљў РЎРІРѕР№ С€Р»СЋР· (self-hosting)

РЁР»СЋР· вЂ” РѕРґРёРЅ С…РѕСЃС‚ РЅР° РІСЃРµ СѓР·Р»С‹. РџСѓР±Р»РёРєСѓР№С‚Рµ РµРіРѕ С‚РѕР»СЊРєРѕ СЃРєСЂРёРїС‚РѕРј, С‡С‚РѕР±С‹ СЂР°Р±РѕС‚Р°СЋС‰РёР№ С€Р»СЋР·, РѕС‚РґР°РІР°РµРјС‹Рµ СѓСЃС‚Р°РЅРѕРІС‰РёРєРё Рё bootstrap-РєРѕРґ СѓР·Р»РѕРІ РЅРµ СЂР°СЃС…РѕРґРёР»РёСЃСЊ:

```bash
MESH_GATEWAY_SSH=root@<ip-С€Р»СЋР·Р°> ./deploy_gateway.sh            # Р·Р°РґРµРїР»РѕРёС‚СЊ
MESH_GATEWAY_SSH=root@<ip-С€Р»СЋР·Р°> ./deploy_gateway.sh --dry-run  # РїСЂРµРґРїСЂРѕСЃРјРѕС‚СЂ
```
РЎРєСЂРёРїС‚ РїСЂРѕРІРµСЂСЏРµС‚ РєР°Р¶РґСѓСЋ Р·Р°РіСЂСѓР·РєСѓ РјР°РЅРёС„РµСЃС‚РѕРј sha256 РЅР° С…РѕСЃС‚Рµ, РґРµР»Р°РµС‚ Р±СЌРєР°Рї СЃ РјРµС‚РєРѕР№ РІСЂРµРјРµРЅРё, СЃС‚Р°РІРёС‚ С„Р°Р№Р»С‹ СЃ РЅСѓР¶РЅС‹Рј РІР»Р°РґРµР»СЊС†РµРј, РїРµСЂРµР·Р°РїСѓСЃРєР°РµС‚ СЃР»СѓР¶Р±Сѓ Рё Р·Р°РІРµСЂС€Р°РµС‚ РїСѓР±Р»РёС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРѕР№ Р·РґРѕСЂРѕРІСЊСЏ. РџСЂРё РЅРµСЃРѕРІРїР°РґРµРЅРёРё С…СЌС€РµР№ РЅРёС‡РµРіРѕ РЅРµ СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ.

> [!TIP]
> **Р”РѕРјРµРЅ Р·Р°РґР°С‘С‚СЃСЏ РІ РѕРґРЅРѕРј РјРµСЃС‚Рµ.** `MESH_PUBLIC_URL` РІ `domain.env`
> (`/etc/antigravity-mesh/domain.env` РІ Linux, `%USERPROFILE%\.config\antigravity-mesh\domain.env`
> РІ Windows) вЂ” РµРґРёРЅСЃС‚РІРµРЅРЅР°СЏ РЅР°СЃС‚СЂРѕР№РєР°, РіРґРµ РѕРЅ РЅР°Р·РІР°РЅ: СѓР·РµР», С€Р»СЋР·, СѓСЃС‚Р°РЅРѕРІС‰РёРєРё, РїСѓР±Р»РёС‡РЅС‹Рµ СЃСЃС‹Р»РєРё
> РЅР° С„Р°Р№Р»С‹ Рё РєРѕРЅС„РёРіРё nginx Р±РµСЂСѓС‚ РµРіРѕ С‡РµСЂРµР· `core/domain.py`. РЎРјРµРЅР° РґРѕРјРµРЅР° вЂ” СЌС‚Рѕ РѕРґРЅРѕ Р·РЅР°С‡РµРЅРёРµ Рё
> `ops/nginx/render-domain.sh --apply`, РїРѕРґСЂРѕР±РЅРѕСЃС‚Рё РІ [docs/DOMAIN.md](docs/DOMAIN.md).

> [!IMPORTANT]
> **РљРѕРґРёСЂРѕРІРєР° `install.ps1` вЂ” РѕРґРёРЅ С„Р°Р№Р», РґРІР° РїСЂРµРґСЃС‚Р°РІР»РµРЅРёСЏ.** Р’ СЂРµРїРѕР·РёС‚РѕСЂРёРё С„Р°Р№Р» Р»РµР¶РёС‚ РІ UTF-8 *СЃ* РјРµС‚РєРѕР№ BOM: Windows PowerShell 5.1 С‡РёС‚Р°РµС‚ СЃРєСЂРёРїС‚ Р±РµР· РјРµС‚РєРё РІ ANSI-РєРѕРґРёСЂРѕРІРєРµ, СЂСѓСЃСЃРєРёР№ С‚РµРєСЃС‚ РїСЂРµРІСЂР°С‰Р°РµС‚СЃСЏ РІ В«СѓРјРЅС‹Рµ РєР°РІС‹С‡РєРёВ», Рё РїР°СЂСЃРµСЂ РѕС‚РІРµСЂРіР°РµС‚ С„Р°Р№Р» С†РµР»РёРєРѕРј вЂ” С‚Рѕ РµСЃС‚СЊ `.\install.ps1` РёР· РєР»РѕРЅР° РїСЂРѕСЃС‚Рѕ РЅРµ Р·Р°РїСѓСЃС‚РёС‚СЃСЏ. РљРѕРїРёСЋ, РєРѕС‚РѕСЂСѓСЋ РѕС‚РґР°С‘С‚ С€Р»СЋР·, `deploy_gateway.sh` Р·Р°РїРёСЃС‹РІР°РµС‚ *Р±РµР·* РјРµС‚РєРё: `irm вЂ¦ | iex` РїРѕР»СѓС‡Р°РµС‚ РјРµС‚РєСѓ РєР°Рє С‡Р°СЃС‚СЊ РїРµСЂРІРѕРіРѕ С‚РѕРєРµРЅР°, Рё `param(...)` РїРµСЂРµСЃС‚Р°С‘С‚ Р±С‹С‚СЊ РїРµСЂРІС‹Рј РѕРїРµСЂР°С‚РѕСЂРѕРј. Р”Р»СЏ СЌС‚РѕРіРѕ Р°РґСЂРµСЃР° nginx РѕР±СЉСЏРІР»СЏРµС‚ `charset utf-8`, РїРѕСЌС‚РѕРјСѓ РєРѕРїРёСЏ Р±РµР· РјРµС‚РєРё С‡РёС‚Р°РµС‚СЃСЏ РєРѕСЂСЂРµРєС‚РЅРѕ. РџРѕР¶Р°Р»СѓР№СЃС‚Р°, СЃРѕС…СЂР°РЅСЏР№С‚Рµ РѕР±Р° СЃРІРѕР№СЃС‚РІР° РїСЂРё РїСЂР°РІРєРµ С„Р°Р№Р»Р°.

**РџРѕР»РёС‚РёРєР° TLS-СЃРµСЂС‚РёС„РёРєР°С‚Р°: С‚РѕР»СЊРєРѕ РѕР±С‰РёР№ РґРѕРјРµРЅ, Р±РµР· SAN РЅР° СѓСЃС‚СЂРѕР№СЃС‚РІР°.** РРјРµРЅР° СѓР·Р»РѕРІ РІ СЃРµСЂС‚РёС„РёРєР°С‚ РЅРµ РґРѕР±Р°РІР»СЏСЋС‚СЃСЏ РЅРёРєРѕРіРґР° вЂ” СѓР·РµР» РІС‹Р±РёСЂР°РµС‚СЃСЏ `?user=`:

```bash
sudo certbot certificates | grep -A1 'Certificate Name: smart-server.online'
sudo certbot renew --dry-run --cert-name smart-server.online   # РїСЂРѕРІРµСЂРєР° РїСЂРѕРґР»РµРЅРёСЏ
```
РЎС‚Р°СЂС‹Рµ SAN'С‹ СѓСЃС‚СЂРѕР№СЃС‚РІ РјРѕРіСѓС‚ РѕСЃС‚Р°С‚СЊСЃСЏ СЃ РїСЂРµР¶РЅРµРіРѕ РєРѕРЅС‚СЂР°РєС‚Р°: РѕРЅРё Р±РµР·РІСЂРµРґРЅС‹ Рё РїРѕРґРґРµСЂР¶РёРІР°СЋС‚ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ СЃС‚Р°СЂС‹С… СЃСЃС‹Р»РѕРє. РЈР±СЂР°С‚СЊ РёС… РјРѕР¶РЅРѕ РїСЂРё СЃР»РµРґСѓСЋС‰РµРј РїСЂРѕРґР»РµРЅРёРё, РєРѕРіРґР° СЃСѓР±РґРѕРјРµРЅРЅС‹РјРё СЃСЃС‹Р»РєР°РјРё РЅРёРєС‚Рѕ РЅРµ РїРѕР»СЊР·СѓРµС‚СЃСЏ.

---

## рџ“„ Р›РёС†РµРЅР·РёСЏ

**MIT** вЂ” РїРѕРґСЂРѕР±РЅРѕСЃС‚Рё РІ С„Р°Р№Р»Рµ [LICENSE](LICENSE).
