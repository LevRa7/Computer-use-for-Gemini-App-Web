# Windows agent watchdog and doctor

| File | Role |
| :--- | :--- |
| `agent-watchdog.ps1` | single-shot supervisor: check the node, restart the agent if it is dead |
| `../doctor.ps1` | read-only diagnostic: report every fact that explains an offline node |
| `../update.ps1` | single-shot updater: install a newer release and let the node restart onto it |
| `run-hidden.vbs` | the windowless launcher both scheduled tasks go through: no console is ever drawn |

All three PowerShell scripts are ASCII-only, target Windows PowerShell 5.1 and use
nothing but in-box cmdlets; `run-hidden.vbs` is ASCII-only too and needs only
`wscript.exe`. None of them needs the task scheduler to be running in order to work.

## Why `run-hidden.vbs` exists

A scheduled task has no console of its own. When the action is `powershell.exe`,
Windows therefore allocates a **new** console for it and only then applies
`-WindowStyle Hidden`: the window is created, drawn and hidden again, and the user
sees a black window appear and disappear. The watchdog fires every five minutes, so
that flash is the most noticeable thing this project does on a desktop.

`wscript.exe` is a GUI host and never allocates a console, and
`WshShell.Run command, 0, False` hands the child `SW_HIDE` in its `STARTUPINFO` —
the window is hidden from the first moment, so nothing is ever painted. That is the
same mechanism as the logon launcher (`antigravity-agent.vbs`), which is why a node
starts without a window either. Output goes to
`<ConfigDir>\<script name>.out.log`, because a window that never appears also never
shows an error.

```powershell
# exactly how the two tasks call it; the script path is relative to the payload
wscript.exe "$Payload\ops\windows\run-hidden.vbs" "ops\windows\agent-watchdog.ps1" -Quiet
wscript.exe "$Payload\ops\windows\run-hidden.vbs" "ops\update.ps1" -Quiet
```

## Why they exist

After a Windows Update reboot the logon launcher did nothing, and the node stayed
offline for about 29 hours. The interpreter pinned in the Startup `.vbs` was the
Microsoft Store App Execution Alias `<...>\WindowsApps\python.exe` - a file that
exists and starts, but runs no Python at all. The only trace was a truncated
`Python ` fragment appended to `agent.log` with no trailing newline.

The watchdog therefore **refuses any interpreter whose path contains
`\WindowsApps\`**, and the doctor surfaces the same fact (plus the torn log line)
within seconds.

## `agent-watchdog.ps1` - single-shot supervisor

Run every 5 minutes by the Scheduled Task **`AntigravityMeshWatchdog`**.

| Parameter | Default | Meaning |
| :--- | :--- | :--- |
| `-ConfigDir` | `%USERPROFILE%\.config\antigravity-mesh` | where `agent.env`, `agent.log`, `agent.heartbeat` live |
| `-LogFile` | `<ConfigDir>\watchdog.log` | the watchdog's own journal |
| `-GraceSeconds` | `60` | a heartbeat younger than this counts as alive |
| `-Quiet` | off | log only, no console output |

Liveness is checked in this order, and the first positive answer wins:

1. `<ConfigDir>\agent.heartbeat` is valid JSON and its `ts` is newer than
   `-GraceSeconds`;
2. `GET https://<gateway>/health?user=<node>` reports `node_online: true`;
3. a verified agent process is running: a `python.exe`/`pythonw.exe`/`py.exe` with
   `-m core.agent` as a real argument, or the `cmd.exe` launcher wrapper that also
   carries the `agent.log` redirect. A process that merely mentions `core.agent`
   (a PowerShell session, a grep) never counts - otherwise a dead node would look
   alive and never be restarted.

When the node is dead it appends **exactly one** diagnostic line to `agent.log`
(reason + interpreter being started), starts the agent hidden with the same
command line, environment and working directory as the `.vbs`
(`cmd /c ""<python.exe>" -u -m core.agent >> "<agent.log>" 2>&1`), waits up to 20 s
for liveness and journals the outcome.

| Exit code | Meaning |
| :--- | :--- |
| `0` | the node is online (already, or recovered by this run); also when another watchdog instance is handling it |
| `2` | configuration or launcher problem: `agent.env`, the Startup `.vbs` or the pinned interpreter is missing, unusable, or is the Store alias |
| `3` | the node is still offline after the recovery attempt |

It is idempotent: a live node (or a live `core.agent` process) is never started
twice, and a named mutex serialises overlapping runs. The token is never printed
and never logged - it is masked as `****`.

## `../doctor.ps1` - read-only diagnostic

| Parameter | Default | Meaning |
| :--- | :--- | :--- |
| `-ConfigDir` | `%USERPROFILE%\.config\antigravity-mesh` | same directory as above |
| `-Json` | off | print exactly one JSON object and nothing else |
| `-TailLines` | `20` | how many `agent.log` lines to show |

It reports the interpreter from the `.vbs` (with `--version` and an
`import websockets` probe, and whether it is the Store alias), the `.vbs` path and
content with the token masked, the state of `AntigravityMeshWatchdog`, `agent.env`
with the token masked, the heartbeat JSON and its age, the last `-TailLines` lines
of `agent.log` with `torn-line: true` when the file does not end in a newline, the
gateway `/health` JSON, and whether a verified `core.agent` process is running
(the same rule the watchdog applies: a Python interpreter running `-m core.agent`,
never a process that merely mentions it).

It finishes with the verdict lines `interpreter_ok`, `autostart_ok`,
`heartbeat_fresh`, `node_online`. Exit code `0` means all of `interpreter_ok`,
`autostart_ok` and `node_online` are true; anything else is `2`. Nothing is
written to disk.

## Running them by hand

```powershell
# from the payload / repository root
.\ops\doctor.ps1
.\ops\doctor.ps1 -Json
.\ops\doctor.ps1 -TailLines 50
.\ops\windows\agent-watchdog.ps1
.\ops\windows\agent-watchdog.ps1 -Quiet

# through the CLI (same scripts, found next to bin/cli.js)
npx gemini-computer-use doctor
npx gemini-computer-use doctor --json
npx gemini-computer-use restart

# the scheduled task
Get-ScheduledTask -TaskName AntigravityMeshWatchdog
Get-ScheduledTaskInfo -TaskName AntigravityMeshWatchdog
Start-ScheduledTask -TaskName AntigravityMeshWatchdog
```

## What the installer must register

Task name: **`AntigravityMeshWatchdog`**.

* **Action**: `wscript.exe`
* **Arguments**: `"<payload>\ops\windows\run-hidden.vbs" "ops\windows\agent-watchdog.ps1" -Quiet`
* **Working directory**: the payload directory (the directory that contains
  `core\`, next to `install.ps1`). The launcher resolves the script against its own
  location, so the registered path survives a payload that moves.
* **Triggers**: once at logon of the installing user, plus a repeating trigger
  (`-Once -RepetitionInterval 5 minutes`, indefinite duration). The logon trigger
  mirrors the `.vbs`; the repeating trigger is what actually recovers the node, and
  `-StartWhenAvailable` makes up a run missed during a reboot.
* **Settings**: `-MultipleInstances IgnoreNew`, `-StartWhenAvailable`,
  `-ExecutionTimeLimit 10 minutes`.

```powershell
$PayloadDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher   = Join-Path $PayloadDir 'ops\windows\run-hidden.vbs'
$action     = New-ScheduledTaskAction -Execute 'wscript.exe' `
                -Argument ('"' + $Launcher + '" "ops\windows\agent-watchdog.ps1" -Quiet') `
                -WorkingDirectory $PayloadDir
$atLogon    = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat     = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
                -RepetitionInterval (New-TimeSpan -Minutes 5) `
                -RepetitionDuration ([TimeSpan]::MaxValue)
$settings   = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -StartWhenAvailable `
                -MultipleInstances IgnoreNew `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'AntigravityMeshWatchdog' -Action $action `
  -Trigger @($atLogon, $repeat) -Settings $settings -Force `
  -Description 'Antigravity Mesh: keep the node agent running (every 5 minutes).'
```

The `ops\` directory must travel with the payload: it has to be listed in
`package.json` (`files`), in `build-installer-exe.ps1` (`$PAYLOAD`) and in
whatever `deploy_gateway.sh` stages for the node bootstrap.

## `../update.ps1` - single-shot self-update

Run once a day by the Scheduled Task **`AntigravityMeshUpdater`**, and by hand as
`gemini-computer-use update [-Check]`.

| Parameter | Default | Meaning |
| :--- | :--- | :--- |
| `-Check` | off | report only; never download or replace anything |
| `-Force` | off | ignore the check interval and the failed-attempt backoff |
| `-NoRestart` | off | install the payload but do not restart the agent |
| `-Offline` | off | with `-Check`: report the cached answer, touch no network |
| `-Json` | off | print the updater's JSON answer verbatim |
| `-InstallDir` | from the Startup `.vbs` | payload directory |
| `-Python` | from the Startup `.vbs` | interpreter to run the updater with |
| `-ConfigDir` | `%USERPROFILE%\.config\antigravity-mesh` | where `agent.env` and `agent.heartbeat` live |
| `-LogFile` | `<ConfigDir>\update-task.log` | this script's own journal |
| `-Quiet` | off | log only, no console output |

It resolves the install directory and the interpreter the same way the watchdog
does - from the Startup launcher `install.ps1` wrote - and **refuses the Microsoft
Store alias** for the same reason: that alias never runs Python. The work itself is
done by `python -m core.updater` (`--check` or `--apply`), which downloads the
release payload, verifies its published SHA-256, swaps it in with a rollback backup
and starts a helper that brings the agent back. See
[docs/UPDATES.md](../../docs/UPDATES.md).

| Exit code | Meaning |
| :--- | :--- |
| `0` | the node is on the newest release, or was updated without a restart |
| `2` | an update exists and was not installed (`-Check`), or the node is misconfigured |
| `3` | the update failed: network, checksum mismatch, unusable payload |
| `4` | the update was installed and the node is restarting |

Register it as **`AntigravityMeshUpdater`**: action `wscript.exe` (through
`run-hidden.vbs`, so the daily run never shows a window), arguments
`"<payload>\ops\windows\run-hidden.vbs" "ops\update.ps1" -Quiet`, working directory
the payload directory, trigger daily (the installer uses 03:30), settings
`-MultipleInstances IgnoreNew` and `-StartWhenAvailable` (so a machine that was off
at 03:30 updates at the next opportunity).

---

# Сторож и доктор для Windows

| Файл | Назначение |
| :--- | :--- |
| `agent-watchdog.ps1` | однократная проверка: если узел мёртв - поднять агента |
| `../doctor.ps1` | диагностика только на чтение: показать всё, что объясняет простой |

Оба скрипта - ASCII-only, рассчитаны на Windows PowerShell 5.1 и используют
только встроенные командлеты. Планировщик заданий для их работы не нужен.

**Зачем это нужно.** После перезагрузки (Windows Update) автозапуск молча ничего
не сделал, и узел оставался offline около 29 часов. В `.vbs` был прописан
интерпретатор-алиас Microsoft Store `<...>\WindowsApps\python.exe`: файл
существует и запускается, но Python не выполняет. Единственным следствием была
обрезанная строка `Python ` в `agent.log` без завершающего перевода строки.

Поэтому сторож **отказывается работать с интерпретатором, чей путь содержит
`\WindowsApps\`**, а доктор за секунды показывает и это, и оборванную строку
журнала.

## `agent-watchdog.ps1`

Запускается каждые 5 минут заданием планировщика **`AntigravityMeshWatchdog`**.

| Параметр | Значение по умолчанию | Смысл |
| :--- | :--- | :--- |
| `-ConfigDir` | `%USERPROFILE%\.config\antigravity-mesh` | где лежат `agent.env`, `agent.log`, `agent.heartbeat` |
| `-LogFile` | `<ConfigDir>\watchdog.log` | собственный журнал сторожа |
| `-GraceSeconds` | `60` | heartbeat моложе этого возраста считается живым |
| `-Quiet` | выключен | только журнал, без вывода в консоль |

Признаки жизни проверяются по порядку, побеждает первый положительный:
сердцебиение `agent.heartbeat` (свежее), затем `GET https://<gateway>/health?user=<узел>`
с `node_online: true`, затем подтверждённый процесс агента: `python.exe`/`pythonw.exe`/`py.exe`
с настоящим аргументом `-m core.agent` либо обёртка `cmd.exe`, в которой есть
перенаправление вывода в `agent.log`. Процесс, который лишь упоминает `core.agent`
(сессия PowerShell, grep), агентом не считается - иначе мёртвый узел выглядел бы
живым и никогда не перезапускался.

Если узел мёртв, сторож дописывает в `agent.log` **ровно одну** строку с причиной
и путём интерпретатора, скрытно запускает агента ровно так же, как это делает
`.vbs`, ждёт до 20 секунд и записывает результат в `-LogFile`.

Коды возврата: `0` - узел онлайн (был или восстановлен); `2` - проблема
конфигурации или автозапуска (`agent.env`, `.vbs` или интерпретатор отсутствуют,
непригодны либо это алиас Store); `3` - узел так и остался offline. Токен никогда
не печатается и не пишется в журнал: он маскируется как `****`.

## `../doctor.ps1`

| Параметр | Значение по умолчанию | Смысл |
| :--- | :--- | :--- |
| `-ConfigDir` | `%USERPROFILE%\.config\antigravity-mesh` | тот же каталог |
| `-Json` | выключен | вывести ровно один JSON-объект и ничего больше |
| `-TailLines` | `20` | сколько последних строк `agent.log` показать |

Доктор показывает интерпретатор из `.vbs` (плюс `--version` и проверку
`import websockets`, плюс признак алиаса Store), путь и содержимое `.vbs` с
замаскированным токеном, состояние задания `AntigravityMeshWatchdog`, `agent.env`
с замаскированным токеном, JSON сердцебиения и его возраст, последние строки
`agent.log` с флагом `torn-line: true` (файл не заканчивается переводом строки),
JSON ответа `/health` и наличие подтверждённого процесса агента.
Завершается строки-вердикты
`interpreter_ok`, `autostart_ok`, `heartbeat_fresh`, `node_online`. Код возврата
`0` - всё в порядке, иначе `2`. На диск ничего не записывается.

## Ручной запуск

```powershell
.\ops\doctor.ps1
.\ops\doctor.ps1 -Json
.\ops\windows\agent-watchdog.ps1
.\ops\windows\agent-watchdog.ps1 -Quiet
npx gemini-computer-use doctor
npx gemini-computer-use restart
Get-ScheduledTaskInfo -TaskName AntigravityMeshWatchdog
Start-ScheduledTask -TaskName AntigravityMeshWatchdog
```

Установщик регистрирует задание **`AntigravityMeshWatchdog`**: действие
`powershell.exe` с аргументами
`-NoProfile -ExecutionPolicy Bypass -File "<payload>\ops\windows\agent-watchdog.ps1" -Quiet`,
рабочий каталог - каталог payload (там, где лежит `core\`), триггеры - при входе
пользователя и повтор каждые 5 минут без ограничения длительности, настройки -
`-MultipleInstances IgnoreNew`, `-StartWhenAvailable`,
`-ExecutionTimeLimit 10 минут`. Готовый вызов `Register-ScheduledTask` приведён в
английской части выше. Каталог `ops\` должен попадать в payload: его нужно указать
в `package.json` (`files`), в `build-installer-exe.ps1` (`$PAYLOAD`) и в том, что
`deploy_gateway.sh` выкладывает для узла.
