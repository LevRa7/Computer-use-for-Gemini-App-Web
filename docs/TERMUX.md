# 📱 Android / Termux installation

[English](#english) | [🇷🇺 Русская версия](#русская-версия)

---

## English

**A phone is a full node.** The same `install.sh` that installs the node on a PC
runs inside [Termux](https://termux.dev) on an unrooted Android phone or tablet,
and the phone then appears in Gemini Spark like any other machine: shell, files,
search, background jobs and file sharing all work.

Root is **not** required. Termux runs as an ordinary Android app, so the node has
exactly the reach of that app: no `/etc`, no system services, and no access to
`/sdcard` until you ask for it (`termux-setup-storage`).

```text
 📱 Phone (Gemini app)                    📱 Phone (Termux = the node)
        │  MCP over TLS                          ▲  outbound WebSocket tunnel
        ▼                                        │  (no open ports, no root)
  ☁️ Gateway ──────────────────────────────────┘
```

---

### 1. Prepare Termux

Install Termux **from F-Droid or GitHub**, not from Google Play (the Play build is
unmaintained). Two add-on apps are strongly recommended:

| App | Why |
| :--- | :--- |
| **Termux:Boot** | Android gives apps no boot hook; Termux:Boot runs `~/.termux/boot/*` after a reboot. Without it the node does not come back on its own. |
| **Termux:API** | Provides `termux-wake-lock` (keeps Android from freezing the node) and `termux-clipboard-set` (puts the MCP link on the clipboard). |

> [!IMPORTANT]
> Do not mix Termux and its add-ons between F-Droid and Google Play: the two stores
> sign their packages with different keys, and the add-ons then cannot talk to
> Termux.

Then, in Termux:

```bash
pkg update -y
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

The installer detects Termux (it does not mistake it for a small Linux) and adapts
every step that differs on Android:

| Step | What happens on Android |
| :--- | :--- |
| Packages | `pkg install python python-pip curl ca-certificates` — the Debian names (`python3-pip`, `python3-venv`) do not exist in Termux, and there is no `sudo` to escalate with. |
| Node name | The **device model** (`Pixel 7 Pro` → `pixel7pro`). Android answers `localhost` to `gethostname(2)` for *every* device, and the gateway keeps one tunnel per name — two phones would evict each other. |
| Domain file | `~/.config/antigravity-mesh/domain.env` instead of `/etc/antigravity-mesh/domain.env`: a phone has no `/etc` and no root. |
| Autostart | A **runit** service (`termux-services`) plus a **Termux:Boot** script — there is no systemd and no launchd. |
| Clipboard | `termux-clipboard-set`, so the MCP link can be pasted straight into the Gemini app. |

Optional overrides:

```bash
# choose the node name yourself (letters, digits, - and _ only)
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash -s -- --user=my-phone

# self-hosted gateway
MESH_PUBLIC_URL=https://mesh.example.com bash install.sh --quick
```

---

### 2. Make the node survive Android

Three Android behaviours kill background processes. All three have to be handled,
and only the first two are outside the installer's reach:

1. **Termux:Boot** — install the app and **open it once**. Android then allows it to
   run at boot, and it executes `~/.termux/boot/agy-agent.sh`.
2. **Battery optimisation** — set Termux (and Termux:Boot) to *Unrestricted*:
   `Android Settings → Apps → Termux → Battery → Unrestricted`. On many OEM builds
   there is also an "autostart" or "protected apps" list that has to include Termux.
3. **Wake lock** — the boot script calls `termux-wake-lock` (needs Termux:API). While
   the lock is held the CPU is not frozen when the screen turns off.

Verify:

```bash
termux-wake-lock          # only needed manually if the boot script never ran
sv status agy-agent       # runit: "run: agy-agent: (pid 12345) 12s"
```

---

### 3. Connect it to Gemini

The installer prints the personal link and copies it to the clipboard:

```text
https://<shared-domain>/sse?user=<node-name>&token=<secret>
```

Paste it at **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)**
→ **Add App** (or the Gemini app → **Settings → Tools / Extensions (MCP)**).

To copy it again later:

```bash
termux-clipboard-set 'https://<shared-domain>/sse?user=<node-name>&token=<secret>'
# or long-press anywhere in Termux and choose Copy
```

---

### 4. Managing the node

With `termux-services` (installed by the installer when `pkg` allows it):

```bash
sv status agy-agent                     # is it running?
sv restart agy-agent                    # restart now
sv down agy-agent                       # stop until the next boot or `sv up`
sv up agy-agent                         # start again
tail -f $PREFIX/var/log/sv/agy-agent/current
```

Without `termux-services` (a phone that refused the package — old mirror, no
network): the node still runs and still autostarts at boot, but nothing restarts it
after a crash.

```bash
~/.config/antigravity-mesh/agy-agent.sh          # run in the foreground
tail -f ~/.config/antigravity-mesh/agy-agent.log # the detached run's log
pkill -f core.agent                              # stop it
pkg install termux-services                      # then re-run the installer
```

**Automatic updates** work as they do on a PC: the agent checks for a newer release
(every 6 hours by default) and installs it, and runit starts the new code within a
few seconds. `MESH_UPDATE_AUTO=0` switches that to "report only" — see
[UPDATES.md](UPDATES.md).

### Files the installer creates

| Path | Purpose |
| :--- | :--- |
| `~/.config/antigravity-mesh/agent.env` | Gateway, node name and token (`chmod 600`). |
| `~/.config/antigravity-mesh/domain.env` | The shared domain, written by the installer as `MESH_PUBLIC_URL=https://<domain>` so the phone's public share links, its tunnel and the gateway name the same host. The built-in default is never written (`--dry-run` says `not written (the built-in default is a fallback, not a configuration)`), and `MESH_DOMAIN_FILE` is a path override that belongs in the node's own configuration, not in the installer run (its parent directory is created). |
| `~/.config/antigravity-mesh/agy-agent.sh` | Launcher (absolute paths only: runit and Termux:Boot start it with a minimal environment). |
| `~/.termux/boot/agy-agent.sh` | Termux:Boot entry: wake lock + supervisor. |
| `$PREFIX/var/service/agy-agent/run` | runit service (`Restart=always` equivalent). |
| `$PREFIX/var/log/sv/agy-agent/current` | runit log. |
| `~/.config/antigravity-mesh/agy-agent.log` | Log of the unsupervised fallback. |
| `~/.gemini-computer-use/` | Node code, when installed from `curl` rather than a clone. |

---

### 5. Standalone mode on a phone

```bash
bash install.sh --mode=standalone --port=8096
```

This serves MCP on `127.0.0.1` only, which on Android means *this phone only*: no
other device, and not the Gemini app on a different phone, can reach it. It is
useful for a local MCP client running inside Termux. To drive the phone from
Gemini, use the default tunnel mode.

The service is called `agy-standalone` in every command above.

---

### 6. Storage, permissions and what the node can do

* The node runs as the Termux app. It can read and write everything inside
  `$HOME` and `$PREFIX` — nothing else.
* `/sdcard` (photos, downloads, documents) is **not** visible until you run
  `termux-setup-storage`, which creates `~/storage/shared` symlinks. After that the
  node's file tools can reach it.
* Android grants no access to other apps' data, to `/system`, or to the network
  stack of other apps.
* `system_info()` reports the Android release, the device model and the shell in
  use (`bash` from `$PREFIX/bin`, since `bash_exec` runs through it).

---

### 7. Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `curl: (6) Could not resolve host` while installing | The gateway name is not visible to this phone — see [Private gateway](#private-gateway-tailscale--vpn). The installer says so and stops before writing anything. |
| Gemini says the node is offline | `sv status agy-agent`; `tail -50 $PREFIX/var/log/sv/agy-agent/current`. Re-run the installer if `agent.env` is missing. |
| The node dies with the screen off | Battery optimisation is still on, or the wake lock is missing: install Termux:API, open it once, re-run the installer, then `termux-wake-lock`. |
| It did not come back after a reboot | Termux:Boot is not installed, or never opened; check that `~/.termux/boot/agy-agent.sh` exists and is executable (`chmod 700`). |
| `pkg install` fails with 404s | The package index is stale: `pkg update -y`, then re-run the installer. |
| `websockets` could not be installed | `pkg install -y python-pip python-ensurepip-wheels`, then re-run. |
| Node name collides with another node | The gateway suffixes it automatically (`pixel7pro-1234`); for a fixed name use `--user=`. |
| Out of space | Termux data lives in the app's private storage: `pkg clean`, remove old jobs under `~/.cache/antigravity-mesh/`, or uninstall unused packages. |
| Architecture | `aarch64` (most phones) and `armv7l` are both supported. `uv` is deliberately not used on Android: its builds link against glibc, which Android's bionic libc is not. |
| Share links name the built-in default instead of your domain | The phone was installed before the installer wrote the domain file: re-run the installer, or write the one line by hand: `mkdir -p ~/.config/antigravity-mesh && echo 'MESH_PUBLIC_URL=https://<domain>' > ~/.config/antigravity-mesh/domain.env`. Existing share links keep working; new ones use the configured domain. If the built-in default *is* your configured domain, no file is written — that is by design, not a failure. |

---

### 8. Uninstall

```bash
sv down agy-agent && sv-disable agy-agent
rm -rf $PREFIX/var/service/agy-agent
rm -f  ~/.termux/boot/agy-agent.sh
rm -rf ~/.config/antigravity-mesh ~/.gemini-computer-use
```

Removing the node does not revoke its token on the gateway: the name and token
stay registered until they are replaced. Re-running the installer with the same
name reuses the stored token, and with `--user=<new-name>` registers a new node.

---

### Private gateway (Tailscale / VPN)

A node downloads its code from the gateway, registers there and then dials the same
name for its tunnel, so **the phone has to be able to resolve the gateway name**. A
gateway that lives on a private network resolves on the operator's laptop (a
Tailscale MagicDNS name, a VPN, a DNS override in the router) and nowhere else —
and a phone on mobile data resolves nothing private:

```text
$ curl -fsSL https://<private-gateway>/install.sh | bash
curl: (6) Could not resolve host: <private-gateway>
```

The installer now detects exactly this (`curl` exit code 6) and stops with that
explanation before writing anything, instead of failing later as "no authentication
token". Two ways forward:

**1. Put the phone on the same network (recommended for a private gateway).**
Install the Tailscale app (or another VPN client) on the phone, sign in to the same
tailnet, and confirm the name resolves before installing:

```bash
pkg install -y curl
curl -fsS https://<private-gateway>/health    # must print the gateway's JSON
# the installer itself must also come from somewhere reachable — a clone is simplest:
pkg install -y git
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick --domain=<private-gateway>
```

`--domain=` (or `MESH_PUBLIC_URL=https://<private-gateway>`) is what tells both the
installer and the node which gateway to use; it is stored in `agent.env` and — as the
shared domain — in the phone's domain file, `~/.config/antigravity-mesh/domain.env`.

**2. Give the gateway a publicly resolvable name** (an `A` record at the registrar
for a real domain). Then the one-line install works from any phone on any network,
which is what the top of this document assumes. Verify from an unrelated network:

```bash
curl -fsS https://<public-domain>/health
```

A name that only works because of a local `hosts` file or a router entry is not
enough: the phone does not use either.

---

### 9. The device branch: camera, microphone, SMS

With **Termux:API** installed, the node can also report and act on the phone it runs
on. Four tools cover it, and none of them needs root:

| Tool | What it does on a phone | Asks first |
| :--- | :--- | :--- |
| `device_info(section, sensor, fresh, quick)` | One-call report of the phone: battery and charging, network interfaces with Wi-Fi and cellular signal, language, time and timezone, CPU, RAM, storage, cameras, microphones, sensors and what this Termux:API install can actually reach. `section` takes one block (`battery`, `network`, `cameras`, ...) or `summary` for a short list of lines, `sensor` takes one live sample from a named sensor, `fresh` bypasses the cache and `quick` skips the slow sections. | no |
| `device_control(action, ...)` | Twenty reversible actions: `torch`, `vibrate`, `volume`, `volume_get`, `brightness`, `tts_speak`, `toast`, `notify`, `notify_list`, `notify_remove`, `clipboard_get`, `clipboard_set`, `media`, `media_scan`, `wakelock`, `download`, `open`, `share`, `dialog`, `wallpaper`. | no |
| `device_capture(action, ...)` | Eleven actions: `camera_list`, `camera_photo`, `mic_record_start`, `mic_record_stop`, `mic_record_status`, `location`, `fingerprint`, `usb_list`, `usb_access`, `infrared_frequencies`, `infrared_transmit`. | **yes** |
| `device_messages(action, ...)` | Five actions: `sms_list`, `sms_send`, `call_log`, `contacts`, `call`. Off until the operator sets `MESH_DEVICE_PIM=1`. | **yes** |

Telemetry comes from `termux-api`, `/proc`, `/sys` and `getprop`: Android gives an
app no `dumpsys` and the node has no root, so a value Android does not expose
through the API is simply absent instead of guessed. `device_info` and
`system_info` both carry these blocks; `mesh_status` reports `device_class`,
`scenario` and `battery_percent`, so a model knows it is talking to a phone on a
battery, and `system_vitals` carries the battery block and the thermal sensors.

`device_capture` and `device_messages` are advertised with `destructiveHint` on
purpose, although they delete nothing: that hint is the only one clients such as
Gemini Spark reliably turn into a confirmation prompt, and a camera or an SMS list
must not fire silently. The same annotation is why a torch or a vibrate stays
prompt-free.

#### Android permissions

The Termux:API **app** holds the Android permissions; Termux itself holds almost
none. Grant them in `Android Settings → Apps → Termux:API → Permissions`:

| Permission | Needed by |
| :--- | :--- |
| **Camera** | `camera_list`, `camera_photo` |
| **Microphone** | `mic_record_start`, `mic_record_stop`, `mic_record_status` |
| **Location**, with location services switched on | `location`; on Android 12+ `termux-telephony-cellinfo` can also come back empty without the *fine* location permission and location services on |
| **SMS** | `sms_list`, `sms_send` |
| **Call log**, **Contacts** | `call_log`, `contacts` |
| **Phone** | `call` |
| **Notifications**, **Storage** | notifications and the clipboard; storage for the shared-storage symlinks |

The node never opens the camera or the microphone just to find out whether the
permission is granted — a permission state is only *remembered* from a call that
really happened. When Android refuses a call, the answer carries `denied: true` and
the exact fix ("grant the camera permission to Termux:API in Android Settings >
Apps > Termux:API > Permissions"), and the collected fixes also appear in the
`capabilities` block of `device_info`.

#### Switches

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `MESH_DEVICE` | `auto` | `auto` keeps the branch answering everywhere (a phone-only action explains itself elsewhere), `0` switches it off, `1` forces it on. |
| `MESH_DEVICE_ACTIONS` | empty | Comma-separated allowlist of action names for the three action tools; empty means every action the build implements. |
| `MESH_DEVICE_CAPTURE_DIR` | shared storage after `termux-setup-storage`, otherwise `~/.cache/antigravity-mesh/captures` | Where photos and recordings are written. |
| `MESH_DEVICE_PIM` | `0` | `1` enables SMS, the call log, contacts and placing a call. |
| `MESH_DEVICE_QUICK` | `0` | `1` keeps `system_info` instant by skipping network, cameras, microphones and sensors. |
| `MESH_READ_ONLY` | `0` | `1` refuses every `device_control` and `device_capture` action and blocks `sms_send` and `call`. |

#### Where captures are written

`camera_photo` and `mic_record_start` write into the node's capture directory, and a
`path` argument overrides the file. That directory is `MESH_DEVICE_CAPTURE_DIR` when
the operator set it; on a phone it is `~/storage/dcim/antigravity-mesh` when DCIM is
reachable, else `~/storage/shared/AntigravityMesh`; and otherwise
`~/.cache/antigravity-mesh/captures`, which is what a phone that never ran
`termux-setup-storage` gets. `~/storage/*` exists only after `termux-setup-storage`
has been run and the storage permission granted — before that the node deliberately
stays in its own cache rather than writing to a path it cannot reach. A capture is
never uploaded by itself: `share_file` publishes it only when it is asked to.

#### The wake lock, and why `run_job` takes it

Android freezes a background app once the screen goes off, so a job that was running
happily stops making progress with the phone in a pocket. `run_job` now takes
Android's wake lock automatically for the lifetime of every job and releases it when
the job finishes or `job_kill` stops it. The `run_job` result and the `job_list`
entry report `wake_lock` (the current state: `true` while the job runs, `false` once
the hold is released), and a phone that refused the lock gets a `wake_lock_note`
naming the fix. `device_control(action="wakelock")` takes, releases or reports it by
hand.

Each job holds the lock exactly once and releases it exactly once — even though
`job_kill` and the reaper thread both finish the same job — so stopping one job can
never drop the protection of another one that is still running. A phone whose
Termux:API app is missing or frozen hangs on every call instead of failing: the
`capabilities` block then reports `app_reachable: false` with an `app_fix`, and a
report in which several sections timed out carries one `hint` (or `device_hint` inside
`system_info`) naming the single cause, rather than five identical timeouts.

#### When the phone refuses

| Symptom | Fix |
| :--- | :--- |
| A `termux-*` command is reported as `missing` | Install the Termux:API app from the **same store** as Termux and run `pkg install termux-api` — the package supplies the commands, the app answers them, and either half missing looks the same. |
| A camera, microphone, location or SMS call answers `denied` | Grant that permission to Termux:API: `Android Settings → Apps → Termux:API → Permissions`. The answer names the permission and the fix. |
| `cameras`, `microphones` or `sensors` come back empty | Termux:API is missing or has no permission; the `capabilities` block of `device_info` lists what this install can reach. |
| Cellular signal is empty and the answer carries `cellinfo_reason` | On Android 12+ `termux-telephony-cellinfo` needs the *fine* location permission for Termux:API and location services switched on. |
| A call hangs and comes back as a `timeout` | Android froze the Termux:API app (battery optimisation). Open the app once and set battery optimisation to *Unrestricted* for Termux and Termux:API. |
| A recording stops as soon as you leave the app | On Android 11+ `termux-microphone-record` runs inside the Termux:API app: keep it in the foreground while recording, or pass `seconds` so the recording ends by itself. |
| `usb_access` returns a descriptor but no data | Deliberate: the node asks Android for permission and hands back the file descriptor; reading and writing the device is left to a script the operator runs. |
| The node is gone and Termux is **not running at all** (`ps -A` shows no `com.termux`) | Android can kill the whole Termux app — memory pressure, a system update, a force-stop while changing permissions. Neither the runit service nor Termux:Boot covers that: they cover a reboot. **Open Termux once**: its login shell starts the supervisor again (`sv status agy-agent`), and the node reconnects by itself. On a phone with no `termux-services` nothing supervises the node, so it stays down until the launcher in `~/.config/antigravity-mesh/` is run again — worth installing `pkg install termux-services` on such a phone. |

---

## Русская версия

**Телефон — это полноценный узел.** Тот же `install.sh`, который ставит узел на
ПК, работает в [Termux](https://termux.dev) на Android-смартфоне или планшете без
root. После установки телефон появляется в Gemini Spark как обычная машина:
команды оболочки, файлы, поиск, фоновые задачи и публикация файлов.

Root **не нужен**: Termux — обычное приложение Android, и узел получает ровно те
права, что и оно. Нет `/etc`, нет системных служб, и `/sdcard` не виден, пока вы
не выполните `termux-setup-storage`.

### 1. Подготовка

Ставьте Termux **из F-Droid или GitHub**, а не из Google Play (сборка в Play
заброшена). Настоятельно рекомендуются два приложения-дополнения:

| Приложение | Зачем |
| :--- | :--- |
| **Termux:Boot** | Android не даёт приложениям точку входа при загрузке; Termux:Boot выполняет `~/.termux/boot/*` после перезагрузки. Без него узел сам не поднимется. |
| **Termux:API** | Даёт `termux-wake-lock` (не даёт Android «заморозить» узел) и `termux-clipboard-set` (кладёт MCP-ссылку в буфер обмена). |

> [!IMPORTANT]
> Не смешивайте Termux и дополнения из F-Droid и Google Play: магазины подписывают
> пакеты разными ключами, и дополнения не смогут работать с Termux.

Установка:

```bash
pkg update -y
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash
```

Что установщик делает иначе на Android:

| Шаг | Что происходит |
| :--- | :--- |
| Пакеты | `pkg install python python-pip curl ca-certificates`: в Termux нет имён `python3-pip`/`python3-venv`, и нет `sudo`. |
| Имя узла | Берётся **модель устройства** (`Pixel 7 Pro` → `pixel7pro`): Android отвечает `localhost` на `gethostname(2)` у всех устройств, а шлюз держит один туннель на имя — два телефона выбивали бы друг друга. |
| Файл домена | `~/.config/antigravity-mesh/domain.env` вместо `/etc/antigravity-mesh/domain.env`. |
| Автозапуск | Служба **runit** (`termux-services`) плюс скрипт **Termux:Boot** — systemd и launchd на телефоне нет. |
| Буфер обмена | `termux-clipboard-set`, чтобы сразу вставить ссылку в приложение Gemini. |

Своё имя узла или свой шлюз:

```bash
curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash -s -- --user=my-phone
MESH_PUBLIC_URL=https://mesh.example.com bash install.sh --quick
```

### 2. Чтобы узел выжил на Android

1. Установите **Termux:Boot** и **запустите его один раз** — иначе Android не
   разрешит ему выполняться при загрузке.
2. Отключите оптимизацию батареи для Termux и Termux:Boot:
   `Настройки Android → Приложения → Termux → Батарея → Без ограничений`. На многих
   прошивках есть ещё список «автозапуск»/«защищённые приложения» — добавьте туда
   Termux.
3. Скрипт автозапуска сам вызывает `termux-wake-lock` (нужно приложение
   Termux:API): пока удерживается блокировка, процесс не замерзает при выключенном
   экране.

Проверка:

```bash
sv status agy-agent
termux-wake-lock
```

### 3. Подключение к Gemini

Установщик печатает личную ссылку и копирует её в буфер:

```text
https://<общий-домен>/sse?user=<имя-узла>&token=<секрет>
```

Вставьте её на **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)**
→ **Добавить приложение** (или в приложении Gemini: **Настройки → Инструменты /
Расширения (MCP)**).

Скопировать ссылку позже:

```bash
termux-clipboard-set 'https://<общий-домен>/sse?user=<имя-узла>&token=<секрет>'
```

### 4. Управление узлом

```bash
sv status agy-agent                       # работает ли
sv restart agy-agent                      # перезапустить
sv down agy-agent                         # остановить (до перезагрузки или sv up)
tail -f $PREFIX/var/log/sv/agy-agent/current
```

Если `termux-services` не установился (старое зеркало, нет сети), узел всё равно
работает и поднимается при загрузке, но не перезапускается после сбоя:

```bash
~/.config/antigravity-mesh/agy-agent.sh            # запуск на переднем плане
tail -f ~/.config/antigravity-mesh/agy-agent.log   # лог фонового запуска
pkg install termux-services                        # затем повторите установку
```

**Автообновления** работают так же, как на ПК: агент раз в 6 часов проверяет новый
релиз и устанавливает его, а runit поднимает новый код через пару секунд.
`MESH_UPDATE_AUTO=0` оставляет только уведомления — см. [UPDATES.md](UPDATES.md).

### 5. Автономный режим на телефоне

```bash
bash install.sh --mode=standalone --port=8096
```

Сервер слушает только `127.0.0.1`, то есть доступен лишь внутри этого телефона —
для локального MCP-клиента в Termux. Чтобы управлять телефоном из Gemini,
используйте режим туннеля (по умолчанию). Имя службы — `agy-standalone`.

### 6. Файлы, которые создаёт установщик

| Путь | Назначение |
| :--- | :--- |
| `~/.config/antigravity-mesh/agent.env` | Шлюз, имя узла, токен (`chmod 600`). |
| `~/.config/antigravity-mesh/domain.env` | Общий домен: установщик записывает его как `MESH_PUBLIC_URL=https://<домен>`, чтобы публичные ссылки телефона, его туннель и шлюз называли один и тот же хост. Встроенное значение по умолчанию не пишется (`--dry-run` говорит `not written (the built-in default is a fallback, not a configuration)`), а `MESH_DOMAIN_FILE` — переопределение пути из конфигурации самого узла, а не из запуска установщика (каталог файла создаётся). |
| `~/.config/antigravity-mesh/agy-agent.sh` | Лаунчер (только абсолютные пути). |
| `~/.termux/boot/agy-agent.sh` | Запись Termux:Boot: блокировка + супервизор. |
| `$PREFIX/var/service/agy-agent/run` | Служба runit (аналог `Restart=always`). |
| `$PREFIX/var/log/sv/agy-agent/current` | Лог runit. |
| `~/.config/antigravity-mesh/agy-agent.log` | Лог запуска без супервизора. |
| `~/.gemini-computer-use/` | Код узла при установке через `curl`. |

### 7. Что узел может на телефоне

* Всё внутри `$HOME` и `$PREFIX` — читает и пишет.
* `/sdcard` (фото, загрузки, документы) — только после `termux-setup-storage`
  (появятся ссылки в `~/storage/shared`).
* Данные других приложений, `/system` и чужие сетевые стеки — недоступны.
* `system_info()` покажет версию Android, модель устройства и используемую
  оболочку (`bash` из `$PREFIX/bin` — через неё работают `bash_exec` и `run_job`).

### 8. Если что-то не работает

| Симптом | Что делать |
| :--- | :--- |
| `curl: (6) Could not resolve host` при установке | Имя шлюза не видно с телефона — см. раздел 10 «Приватный шлюз». Установщик теперь пишет это прямо и останавливается до любых записей. |
| Gemini пишет, что узел offline | `sv status agy-agent`; `tail -50 $PREFIX/var/log/sv/agy-agent/current`; если нет `agent.env` — повторите установку. |
| Узел пропадает при выключенном экране | Осталась оптимизация батареи или нет wake lock: установите Termux:API, откройте его, повторите установку и выполните `termux-wake-lock`. |
| Не поднялся после перезагрузки | Нет Termux:Boot или его не открывали ни разу; проверьте `~/.termux/boot/agy-agent.sh` и `chmod 700` на нём. |
| Узел пропал, и Termux **не запущен вообще** (`ps -A` не показывает `com.termux`) | Android умеет выгрузить приложение Termux целиком — нехватка памяти, обновление системы, принудительная остановка при смене разрешений. Ни служба runit, ни Termux:Boot этого не покрывают: они рассчитаны на перезагрузку. **Откройте Termux один раз** — его login-shell снова поднимет супервизор (`sv status agy-agent`), и узел переподключится сам. На телефоне без `termux-services` узел никто не сторожит, поэтому он останется лежать, пока вы вручную не запустите лаунчер в `~/.config/antigravity-mesh/`; на таком телефоне стоит выполнить `pkg install termux-services`. |
| `pkg install` отвечает 404 | Устаревший индекс: `pkg update -y`, затем повторите установку. |
| Не ставится `websockets` | `pkg install -y python-pip python-ensurepip-wheels`, затем повторите. |
| Имя узла занято | Шлюз добавит суффикс (`pixel7pro-1234`); для фиксированного имени — `--user=`. |
| Мало места | Данные Termux лежат в приватном хранилище приложения: `pkg clean`, очистите `~/.cache/antigravity-mesh/`. |
| Архитектура | Поддерживаются `aarch64` (большинство телефонов) и `armv7l`. `uv` на Android намеренно не используется: его сборки линкуются с glibc, а на Android — bionic. |
| Ссылки на файлы ведут на встроенный домен вместо вашего | Телефон поставлен до того, как установщик начал писать файл домена: повторите установку или допишите одну строку вручную: `mkdir -p ~/.config/antigravity-mesh && echo 'MESH_PUBLIC_URL=https://<домен>' > ~/.config/antigravity-mesh/domain.env`. Уже выданные ссылки продолжат работать, новые возьмут настроенный домен. Если встроенное значение и есть ваш домен, файл не появится — так и задумано, это не сбой. |

### 9. Удаление

```bash
sv down agy-agent && sv-disable agy-agent
rm -rf $PREFIX/var/service/agy-agent
rm -f  ~/.termux/boot/agy-agent.sh
rm -rf ~/.config/antigravity-mesh ~/.gemini-computer-use
```

Удаление узла не отзывает его токен на шлюзе: имя и токен остаются
зарегистрированными, пока их не заменят. Повторная установка с тем же именем
использует сохранённый токен, с `--user=<новое-имя>` — регистрирует новый узел.

---

### 10. Приватный шлюз (Tailscale / VPN)

Узел скачивает код со шлюза, регистрируется на нём и туда же поднимает туннель,
поэтому **телефон обязан уметь резолвить имя шлюза**. Шлюз в приватной сети
резолвится на ноутбуке владельца (имя Tailscale MagicDNS, VPN, запись в локальном
DNS) и больше нигде — а телефон в мобильной сети приватные имена не знает:

```text
$ curl -fsSL https://<приватный-шлюз>/install.sh | bash
curl: (6) Could not resolve host: <приватный-шлюз>
```

Установщик теперь распознаёт именно этот случай (код возврата curl 6) и
останавливается с объяснением **до** любых записей, а не падает позже с
«не удалось получить токен». Два пути:

**1. Подключить телефон к той же сети (рекомендуется для приватного шлюза).**
Поставьте на телефон Tailscale (или другой VPN-клиент), войдите в тот же tailnet и
убедитесь, что имя резолвится, ещё до установки:

```bash
pkg install -y curl
curl -fsS https://<приватный-шлюз>/health    # должен вернуть JSON шлюза
# сам установщик тоже нужно откуда-то взять — проще всего клоном:
pkg install -y git
git clone https://github.com/LevRa7/Computer-use-for-Gemini-App-Web.git
cd Computer-use-for-Gemini-App-Web
./install.sh --quick --domain=<приватный-шлюз>
```

Именно `--domain=` (или `MESH_PUBLIC_URL=https://<приватный-шлюз>`) сообщает
установщику и узлу, какой шлюз использовать; значение сохраняется в `agent.env` и —
как общий домен — в файле домена телефона, `~/.config/antigravity-mesh/domain.env`.

**2. Дать шлюзу публично резолвимое имя** (A-запись у регистратора для настоящего
домена). Тогда однострочная установка работает с любого телефона в любой сети —
именно это и предполагает инструкция в начале документа. Проверять нужно из
другой сети:

```bash
curl -fsS https://<публичный-домен>/health
```

Имени, которое работает только из-за записи в `hosts` или в роутере, недостаточно:
телефон не использует ни то, ни другое.

---

### 11. Ветка устройства: камера, микрофон, SMS

С установленным **Termux:API** узел умеет ещё и сообщать о телефоне, на котором
работает, и управлять им. Это четыре инструмента, и root им не нужен:

| Инструмент | Что делает на телефоне | Спрашивает |
| :--- | :--- | :--- |
| `device_info(section, sensor, fresh, quick)` | Отчёт о телефоне одним вызовом: батарея и зарядка, сетевые интерфейсы с уровнем Wi-Fi и сотового сигнала, язык, время и часовой пояс, CPU, ОЗУ, накопитель, камеры, микрофоны, датчики и то, до чего реально дотягивается эта установка Termux:API. `section` выбирает один блок (`battery`, `network`, `cameras`, ...) или `summary` для короткого списка строк, `sensor` берёт один живой замер с датчика по имени, `fresh` обходит кэш, `quick` пропускает медленные разделы. | нет |
| `device_control(action, ...)` | Двадцать обратимых действий: `torch`, `vibrate`, `volume`, `volume_get`, `brightness`, `tts_speak`, `toast`, `notify`, `notify_list`, `notify_remove`, `clipboard_get`, `clipboard_set`, `media`, `media_scan`, `wakelock`, `download`, `open`, `share`, `dialog`, `wallpaper`. | нет |
| `device_capture(action, ...)` | Одиннадцать действий: `camera_list`, `camera_photo`, `mic_record_start`, `mic_record_stop`, `mic_record_status`, `location`, `fingerprint`, `usb_list`, `usb_access`, `infrared_frequencies`, `infrared_transmit`. | **да** |
| `device_messages(action, ...)` | Пять действий: `sms_list`, `sms_send`, `call_log`, `contacts`, `call`. Выключен, пока оператор не задаст `MESH_DEVICE_PIM=1`. | **да** |

Телеметрия берётся из `termux-api`, `/proc`, `/sys` и `getprop`: `dumpsys`
приложению недоступен, root у узла нет, поэтому значение, которое Android не
отдаёт через API, просто отсутствует, а не выдумывается. Эти блоки несут и
`device_info`, и `system_info`; `mesh_status` сообщает `device_class`, `scenario` и
`battery_percent`, чтобы модель знала, что разговаривает с телефоном на батарее, а
`system_vitals` отдаёт блок батареи и термические датчики.

`device_capture` и `device_messages` объявлены с `destructiveHint` намеренно, хотя
ничего не удаляют: эта подсказка — единственная, которую клиенты вроде Gemini Spark
надёжно превращают в запрос подтверждения, а камера или список SMS не должны
срабатывать молча. По той же причине фонарик и вибрация подтверждения не требуют.

#### Разрешения Android

Разрешения Android держит **приложение** Termux:API, а сам Termux — почти никаких.
Выдаются в `Настройки Android → Приложения → Termux:API → Разрешения`:

| Разрешение | Кому нужно |
| :--- | :--- |
| **Камера** | `camera_list`, `camera_photo` |
| **Микрофон** | `mic_record_start`, `mic_record_stop`, `mic_record_status` |
| **Местоположение**, вместе с включённой геолокацией | `location`; на Android 12+ `termux-telephony-cellinfo` тоже может вернуть пусто без *точного* разрешения на местоположение и включённой геолокации |
| **SMS** | `sms_list`, `sms_send` |
| **Журнал вызовов**, **Контакты** | `call_log`, `contacts` |
| **Телефон** | `call` |
| **Уведомления**, **Хранилище** | уведомления и буфер обмена; хранилище — для ссылок в общее хранилище |

Узел никогда не открывает камеру или микрофон только чтобы узнать, выдано ли
разрешение: состояние разрешения лишь *запоминается* по реально выполненному вызову.
Когда Android отказывает, ответ несёт `denied: true` и точную подсказку («grant the
camera permission to Termux:API in Android Settings > Apps > Termux:API >
Permissions»), а собранные подсказки видны в блоке `capabilities` у `device_info`.

#### Переключатели

| Переменная | По умолчанию | Что делает |
| :--- | :--- | :--- |
| `MESH_DEVICE` | `auto` | `auto` — ветка отвечает везде (действие для телефона объясняется на другой платформе), `0` — выключает её, `1` — включает принудительно. |
| `MESH_DEVICE_ACTIONS` | пусто | Список разрешённых имён действий через запятую для трёх action-инструментов; пусто — все действия сборки. |
| `MESH_DEVICE_CAPTURE_DIR` | общее хранилище после `termux-setup-storage`, иначе `~/.cache/antigravity-mesh/captures` | Куда пишутся фото и записи. |
| `MESH_DEVICE_PIM` | `0` | `1` включает SMS, журнал вызовов, контакты и звонок. |
| `MESH_DEVICE_QUICK` | `0` | `1` оставляет `system_info` мгновенным, пропуская сеть, камеры, микрофоны и датчики. |
| `MESH_READ_ONLY` | `0` | `1` отказывает в каждом действии `device_control` и `device_capture` и блокирует `sms_send` и `call`. |

#### Куда пишутся снимки и записи

`camera_photo` и `mic_record_start` пишут в каталог съёмки узла, а аргумент `path`
переопределяет файл. Этот каталог — `MESH_DEVICE_CAPTURE_DIR`, если оператор его
задал; на телефоне — `~/storage/dcim/antigravity-mesh`, когда доступен DCIM, иначе
`~/storage/shared/AntigravityMesh`; иначе `~/.cache/antigravity-mesh/captures` —
именно это получит телефон, на котором ни разу не выполняли `termux-setup-storage`.
Каталог `~/storage/*` появляется только после `termux-setup-storage` и выдачи
разрешения на хранилище — до этого узел намеренно остаётся в своём кэше, а не пишет
по недоступному пути. Снимок никогда не отправляется сам: `share_file` публикует его
только по отдельной просьбе.

#### Wake lock и зачем его теперь берёт `run_job`

Android замораживает фоновое приложение при выключенном экране, поэтому задача,
которая бодро работала, перестаёт двигаться с телефоном в кармане. `run_job` теперь
сам берёт wake lock на всё время каждой задачи и отпускает его при завершении или
остановке через `job_kill`. Результат `run_job` и запись в `job_list` сообщают
`wake_lock` (текущее состояние: `true`, пока задача идёт, и `false` после
освобождения), а телефон, отказавший в блокировке, получает `wake_lock_note` с
подсказкой. Взять, отпустить или посмотреть блокировку вручную можно через
`device_control(action="wakelock")`.

Каждая задача берёт блокировку ровно один раз и ровно один раз её отпускает — хотя
`job_kill` и поток-жнец финализируют одну и ту же задачу, — поэтому остановка одной
задачи не может снять защиту с другой, всё ещё работающей. Телефон, у которого
приложение Termux:API не установлено или заморожено, виснет на каждом вызове вместо
ошибки: блок `capabilities` сообщает `app_reachable: false` и `app_fix`, а отчёт, где
несколько разделов истекли по таймауту, несёт одну подсказку `hint` (или `device_hint`
внутри `system_info`) с общей причиной, а не пять одинаковых «timed out».

#### Когда телефон отказывает

| Симптом | Что делать |
| :--- | :--- |
| Команда `termux-*` помечена `missing` | Поставьте приложение Termux:API из **того же магазина**, что и Termux, и выполните `pkg install termux-api`: команды даёт пакет, отвечает на них приложение, и без любой из половин выглядит одинаково. |
| Вызов камеры, микрофона, геолокации или SMS отвечает `denied` | Выдайте это разрешение Termux:API: `Настройки Android → Приложения → Termux:API → Разрешения`. В ответе названы и разрешение, и подсказка. |
| `cameras`, `microphones` или `sensors` пусты | Нет Termux:API или нет разрешения; блок `capabilities` у `device_info` показывает, до чего дотягивается эта установка. |
| Сотовый сигнал пуст, в ответе есть `cellinfo_reason` | На Android 12+ `termux-telephony-cellinfo` требует *точного* разрешения на местоположение для Termux:API и включённой геолокации. |
| Вызов зависает и возвращается как `timeout` | Android заморозил приложение Termux:API (оптимизация батареи). Откройте приложение один раз и поставьте оптимизацию батареи в «Без ограничений» для Termux и Termux:API. |
| Запись обрывается, стоит выйти из приложения | На Android 11+ `termux-microphone-record` работает внутри приложения Termux:API: держите его на переднем плане во время записи или передайте `seconds`, чтобы запись закончилась сама. |
| `usb_access` отдаёт дескриптор, но без данных | Так задумано: узел запрашивает у Android разрешение и возвращает файловый дескриптор, а чтение и запись устройства остаются скрипту, который оператор запускает сам. |
