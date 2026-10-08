# ==============================================================================
#  Antigravity Mesh - Windows node doctor (read-only diagnostic)
#
#  Answers "why is this node offline?" without changing anything on the machine:
#  it only reads files, probes the pinned interpreter, asks the task scheduler and
#  calls the gateway's public /health endpoint.
#
#  It exists because of one real incident: after a Windows Update reboot the
#  autostart silently did nothing. The interpreter pinned in the Startup .vbs was
#  the Microsoft Store App Execution Alias <...>\WindowsApps\python.exe, whose only
#  trace was a truncated "Python " line appended to agent.log WITHOUT a trailing
#  newline - a torn line. The node stayed offline for ~29 hours. Every signal that
#  would have made that obvious in five seconds is reported below, and the torn
#  line is flagged explicitly (torn-line: true).
#
#  WHAT IT REPORTS
#    * the interpreter taken from the Startup .vbs, its --version, an
#      "import websockets" probe, and whether it is the Store alias;
#    * the .vbs path and its content, with the token masked as ****;
#    * the Scheduled Task "AntigravityMeshWatchdog" (state, action, last run,
#      last result) whenever the ScheduledTasks cmdlets are available;
#    * agent.env with the token masked;
#    * the heartbeat JSON and its age in seconds;
#    * the last -TailLines lines of agent.log, plus torn-line: true when the log
#      does not end with a newline;
#    * the GET https://<gateway>/health?user=<node> JSON;
#    * whether a VERIFIED agent process is running: a python.exe/pythonw.exe/py.exe
#      running "-m core.agent", or the cmd.exe launcher wrapper that also carries
#      the agent.log redirect. A process that merely mentions core.agent (a
#      PowerShell session, a grep) is never reported as the agent;
#    * the verdict lines interpreter_ok, autostart_ok, heartbeat_fresh,
#      node_online.
#
#  EXIT CODES
#    0  healthy: interpreter_ok, autostart_ok and node_online are all true
#    2  anything else (the verdict lines say which part failed)
#
#  This file is deliberately ASCII-only: Windows PowerShell 5.1 reads a BOM-less
#  script with the ANSI code page, so non-ASCII text here is a portability bug.
#
#  Usage:
#     .\doctor.ps1
#     .\doctor.ps1 -Json
#     .\doctor.ps1 -ConfigDir "$env:USERPROFILE\.config\antigravity-mesh" -TailLines 50
# ==============================================================================
[CmdletBinding()]
param(
    [string]$ConfigDir = '',
    [switch]$Json,
    [int]$TailLines = 20
)

$ErrorActionPreference = 'Continue'

$AsJson = [bool]$Json
$TaskName = 'AntigravityMeshWatchdog'
# The watchdog treats a heartbeat as fresh within its -GraceSeconds (60 s by
# default) and runs every 5 minutes. The doctor has no -GraceSeconds parameter, so
# it is deliberately more generous: a heartbeat younger than this proves the agent
# is alive right now, and the exact age is always reported next to it.
$HeartbeatFreshSeconds = 180
$ProbeTimeoutMs = 20000

function Get-TextFile {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        return [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
        return [System.Text.Encoding]::BigEndianUnicode.GetString($bytes, 2, $bytes.Length - 2)
    }
    $hasNul = $false
    $limit = [Math]::Min($bytes.Length, 512)
    for ($i = 0; $i -lt $limit; $i++) {
        if ($bytes[$i] -eq 0) { $hasNul = $true; break }
    }
    if ($hasNul) { return [System.Text.Encoding]::Unicode.GetString($bytes) }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

function Get-EnvMap {
    param([string]$Path)
    $values = @{}
    try { $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop) } catch { return $values }
    foreach ($line in $lines) {
        if ($null -eq $line) { continue }
        $text = ([string]$line).Trim()
        if ($text.Length -eq 0) { continue }
        if ($text.StartsWith('#')) { continue }
        $index = $text.IndexOf('=')
        if ($index -lt 1) { continue }
        $values[$text.Substring(0, $index).Trim()] = $text.Substring($index + 1).Trim().Trim('"').Trim("'")
    }
    return $values
}

# Nothing that a human can read leaves this script without passing through here:
# the literal secret (when long enough to be unambiguous) and both shipped shapes
# of the token line are replaced with ****.
function Hide-Secret {
    param([string]$Text, [string]$Secret)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $result = $Text
    if ($Secret -and $Secret.Length -ge 4) { $result = $result.Replace($Secret, '****') }
    $result = [regex]::Replace($result, '(?im)^(\s*MESH_TOKEN\s*=\s*).*$', '${1}****')
    $result = [regex]::Replace($result, '(?im)(MESH_TOKEN"\)\s*=\s*").*?(")', '${1}****${2}')
    return $result
}

function Get-LauncherInfo {
    param([string]$VbsPath)
    $info = @{ Present = $false; Text = ''; Interpreter = ''; PayloadDir = ''; Error = '' }
    if (-not (Test-Path -LiteralPath $VbsPath -PathType Leaf)) { $info.Error = 'not found'; return $info }
    $info.Present = $true
    try {
        $info.Text = Get-TextFile $VbsPath
    } catch {
        $info.Error = $_.Exception.Message
        return $info
    }
    # The pattern stops at the line boundary ([^\r\n]*, no $ anchor: a .vbs written
    # by Out-File has CRLF endings and .NET's multiline $ refuses to match before
    # the \r of a CRLF pair).
    $run = [regex]::Match($info.Text, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?cmd[ \t]+/c[ \t]+"+(?<exe>[^"]+?)"+[ \t]+(?:-u[ \t]+)?-m[ \t]+core\.agent[^\r\n]*')
    if (-not $run.Success) {
        $run = [regex]::Match($info.Text, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?(?<exe>[A-Za-z]:\\[^"]*?\.(?:exe|cmd|bat))')
    }
    if (-not $run.Success) {
        $run = [regex]::Match($info.Text, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?cmd[ \t]+/c[ \t]+"+(?<exe>[^"]+?)"+')
    }
    if ($run.Success) { $info.Interpreter = $run.Groups['exe'].Value.Trim() }
    $dir = [regex]::Match($info.Text, '(?im)^[^\r\n]*WshShell\.CurrentDirectory[ \t]*=[ \t]*"(?<dir>[^"]+)"')
    if ($dir.Success) { $info.PayloadDir = $dir.Groups['dir'].Value.Trim() }
    return $info
}

function ConvertTo-UtcDateTime {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [datetime]) {
        $stamp = [datetime]$Value
        if ($stamp.Kind -eq [System.DateTimeKind]::Utc) { return $stamp }
        if ($stamp.Kind -eq [System.DateTimeKind]::Local) { return $stamp.ToUniversalTime() }
        return [System.DateTime]::SpecifyKind($stamp, [System.DateTimeKind]::Utc)
    }
    $text = ([string]$Value).Trim().Trim('"')
    if ($text.Length -eq 0) { return $null }
    $epoch = New-Object System.DateTime -ArgumentList @(1970, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc)
    $number = 0.0
    $isNumber = $false
    if ($Value -is [double] -or $Value -is [single] -or $Value -is [decimal] -or $Value -is [int] -or $Value -is [long]) {
        $number = [double]$Value
        $isNumber = $true
    } elseif ([double]::TryParse($text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
        $isNumber = $true
    }
    # core/agent.py writes "ts" as a Unix timestamp in seconds, often fractional.
    # Numbers are read with the invariant culture on purpose: on a comma-decimal
    # locale "1767225600.25" would otherwise fail to parse and a live node would be
    # declared dead.
    if ($isNumber) {
        # Unix seconds (10 digits) or Unix milliseconds (13 digits).
        if ($number -gt 100000000000.0) { return $epoch.AddMilliseconds($number) }
        if ($number -gt 100000000.0) { return $epoch.AddSeconds($number) }
        return $null
    }
    $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal
    $parsed = [datetime]::MinValue
    if ([datetime]::TryParse($text, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) {
        return [System.DateTime]::SpecifyKind($parsed, [System.DateTimeKind]::Utc)
    }
    return $null
}

function Get-HeartbeatInfo {
    param([string]$Path, [int]$FreshSeconds)
    $info = @{ Present = $false; Parsed = $false; Json = ''; AgeSeconds = $null; Fresh = $false; Detail = 'missing' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $info }
    $info.Present = $true
    try {
        $raw = [System.IO.File]::ReadAllText($Path)
    } catch {
        $info.Detail = 'unreadable: ' + $_.Exception.Message
        return $info
    }
    $info.Json = $raw.Trim()
    try {
        $parsed = $raw | ConvertFrom-Json
    } catch {
        $info.Detail = 'not valid JSON'
        return $info
    }
    $info.Parsed = $true
    $stampRaw = $null
    foreach ($name in @('ts', 'timestamp', 'time', 'last_seen', 'checked_at')) {
        $prop = $parsed.PSObject.Properties[$name]
        if ($prop -and $null -ne $prop.Value -and ([string]$prop.Value).Trim().Length -gt 0) {
            $stampRaw = $prop.Value
            break
        }
    }
    if ($null -eq $stampRaw) { $info.Detail = 'JSON has no ts field'; return $info }
    $stamp = ConvertTo-UtcDateTime $stampRaw
    if ($null -eq $stamp) { $info.Detail = 'ts is not a timestamp: ' + ([string]$stampRaw); return $info }
    $age = ((Get-Date).ToUniversalTime() - $stamp.ToUniversalTime()).TotalSeconds
    $info.AgeSeconds = [Math]::Round($age, 1)
    if ($age -le $FreshSeconds) {
        $info.Fresh = $true
        $info.Detail = 'fresh (' + $info.AgeSeconds + 's old, fresh within ' + $FreshSeconds + 's)'
    } else {
        $info.Detail = 'stale (' + $info.AgeSeconds + 's old, fresh within ' + $FreshSeconds + 's)'
    }
    return $info
}

function Invoke-Captured {
    # Runs a program and returns its output, bounded by a timeout: the Store alias
    # can hang instead of failing, and a diagnostic must not hang with it.
    param([string]$FilePath, [string]$ArgumentLine, [string]$WorkingDirectory = '', [int]$TimeoutMs = 20000)
    $result = @{ Ran = $false; ExitCode = $null; TimedOut = $false; Output = ''; Error = '' }
    if ([string]::IsNullOrWhiteSpace($FilePath)) { $result.Error = 'no program to run'; return $result }
    if ([string]::IsNullOrWhiteSpace($WorkingDirectory) -or -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        $WorkingDirectory = ''
    }
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FilePath
        $startInfo.Arguments = $ArgumentLine
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        if ($WorkingDirectory) { $startInfo.WorkingDirectory = $WorkingDirectory }
        $process = [System.Diagnostics.Process]::Start($startInfo)
        $result.Ran = $true
        $exited = $process.WaitForExit($TimeoutMs)
        if (-not $exited) {
            $result.TimedOut = $true
            try { $process.Kill() } catch { }
            $null = $process.WaitForExit(3000)
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        if ($exited) { $result.ExitCode = [int]$process.ExitCode }
        $result.Output = (($stdout + ' ' + $stderr) -replace '\s+', ' ').Trim()
    } catch {
        $result.Error = $_.Exception.Message
    }
    return $result
}

function Get-ScheduledTaskState {
    param([string]$Name)
    $state = @{
        CmdletsAvailable = $false; Present = $false; Name = $Name; State = ''; Actions = @()
        LastRunTime = ''; LastResult = $null; LastResultMeaning = ''; NextRunTime = ''
        MissedRuns = $null; Detail = ''
    }
    if (-not (Get-Command -Name 'Get-ScheduledTask' -ErrorAction SilentlyContinue)) {
        $state.Detail = 'the ScheduledTasks cmdlets are not available on this system'
        return $state
    }
    $state.CmdletsAvailable = $true
    try {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    } catch {
        $state.Detail = 'not registered'
        return $state
    }
    $state.Present = $true
    $state.State = [string]$task.State
    $actions = @()
    foreach ($action in @($task.Actions)) {
        $text = ([string]$action.Execute).Trim()
        if ($action.Arguments) { $text = ($text + ' ' + [string]$action.Arguments).Trim() }
        if ($text.Length -gt 0) { $actions += $text }
    }
    $state.Actions = $actions
    try {
        $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop
        if ($info) {
            if ($info.LastRunTime) { $state.LastRunTime = ([datetime]$info.LastRunTime).ToString('o') }
            if ($null -ne $info.LastTaskResult) { $state.LastResult = [int64]$info.LastTaskResult }
            if ($info.NextRunTime) { $state.NextRunTime = ([datetime]$info.NextRunTime).ToString('o') }
            if ($null -ne $info.NumberOfMissedRuns) { $state.MissedRuns = [int64]$info.NumberOfMissedRuns }
        }
    } catch {
        $state.Detail = 'registered, but its run information is unreadable: ' + $_.Exception.Message
    }
    $meanings = @{
        0 = 'ok'
        2 = 'the watchdog refused the configuration (agent.env, the .vbs or the interpreter)'
        3 = 'the watchdog tried to recover the node and it was still offline'
        267009 = 'the task is currently running'
        267011 = 'the task has not run yet'
        2147942401 = 'access denied (the task may require elevation)'
    }
    if ($null -ne $state.LastResult) {
        if ($meanings.ContainsKey([int]$state.LastResult)) { $state.LastResultMeaning = $meanings[[int]$state.LastResult] }
        else { $state.LastResultMeaning = 'unrecognised result code' }
    }
    return $state
}

function Test-AgentProcess {
    # The command line ALONE is not evidence. A PowerShell session that merely
    # mentions "core.agent" (a grep, a script that prints the module name) matches
    # the bare substring, so a dead node would look alive and would never be
    # restarted. The process must be a Python interpreter running the module, or
    # the cmd.exe wrapper the logon launcher uses - which also carries the
    # agent.log append redirect. PowerShell, wscript and everything else never
    # qualifies, whatever its command line says.
    param([string]$Name, [string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $processName = ''
    if ($Name) { $processName = ([string]$Name).Trim().ToLowerInvariant() }
    foreach ($shell in @('powershell', 'pwsh', 'wscript', 'cscript')) {
        if ($processName.StartsWith($shell)) { return $false }
    }
    # "-m core.agent" has to be a real argument, so it must open the command line
    # or follow a space or a quote.
    if ($CommandLine -notmatch '(?i)(^|[\s"])-m[\s"]+core\.agent\b') { return $false }
    if ($processName -eq 'py.exe') { return $true }
    if ($processName.StartsWith('python')) { return $true }
    if ($processName -eq 'cmd.exe' -or $processName -eq 'cmd') {
        # Launcher wrapper: cmd /c ""<python>" -u -m core.agent >> "<agent.log>" 2>&1"
        return [bool]($CommandLine -match '(?i)agent\.log')
    }
    return $false
}

function Get-AgentProcesses {
    # Read-only, and deliberately the SAME rule as the watchdog's last liveness
    # check, so a reader can tell "the agent is dead" from "the gateway cannot see
    # it" without a different answer from the two scripts.
    $found = @()
    $procs = @()
    try {
        $procs = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    } catch {
        try { $procs = @(Get-WmiObject -Class Win32_Process -ErrorAction Stop) } catch { $procs = @() }
    }
    foreach ($proc in $procs) {
        $commandLine = ''
        $prop = $proc.PSObject.Properties['CommandLine']
        if ($prop -and $prop.Value) { $commandLine = [string]$prop.Value }
        if (-not (Test-AgentProcess -Name ([string]$proc.Name) -CommandLine $commandLine)) { continue }
        $found += @{ Pid = [int]$proc.ProcessId; Name = [string]$proc.Name; CommandLine = $commandLine }
    }
    return $found
}

function Get-LastByte {
    param([string]$Path)
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            if ($stream.Length -eq 0) { return $null }
            $null = $stream.Seek(-1, [System.IO.SeekOrigin]::End)
            return $stream.ReadByte()
        } finally { $stream.Dispose() }
    } catch {
        return $null
    }
}

function Get-LogInfo {
    param([string]$Path, [int]$Lines)
    $info = @{ Present = $false; Bytes = 0; LastWriteTime = ''; Tail = @(); TornLine = $false; LastByte = $null; Error = '' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $info }
    $info.Present = $true
    try {
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        $info.Bytes = [int64]$item.Length
        $info.LastWriteTime = ([datetime]$item.LastWriteTimeUtc).ToString('o')
    } catch {
        $info.Error = $_.Exception.Message
    }
    if ($Lines -gt 0) {
        try {
            $info.Tail = @(Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 -ErrorAction Stop)
        } catch {
            $info.Error = $_.Exception.Message
        }
    }
    # A log whose last byte is not LF ends in a torn line - the truncated
    # "Python " fragment was the only trace the incident left behind.
    $info.LastByte = Get-LastByte $Path
    if ($null -ne $info.LastByte -and $info.LastByte -ne 0x0A) { $info.TornLine = $true }
    return $info
}

function Get-GatewayInfo {
    param([string]$Gateway, [string]$NodeUser, [int]$TimeoutSec = 15)
    $info = @{ Url = ''; Checked = $false; Online = $false; Json = ''; Detail = 'not checked' }
    if ([string]::IsNullOrWhiteSpace($Gateway) -or [string]::IsNullOrWhiteSpace($NodeUser)) {
        $info.Detail = 'gateway or node name is missing from agent.env'
        return $info
    }
    $hostName = ($Gateway.Trim() -replace '^[A-Za-z][A-Za-z0-9+.-]*://', '').Trim().TrimEnd('/')
    if ($hostName.Length -eq 0) {
        $info.Detail = 'the gateway value is empty after normalisation'
        return $info
    }
    $info.Url = 'https://' + $hostName + '/health?user=' + [uri]::EscapeDataString($NodeUser)
    $info.Checked = $true
    try {
        $response = Invoke-RestMethod -Uri $info.Url -Method Get -TimeoutSec $TimeoutSec -ErrorAction Stop
    } catch {
        $info.Detail = 'unreachable: ' + $_.Exception.Message
        return $info
    }
    try { $info.Json = ($response | ConvertTo-Json -Compress -Depth 5) } catch { $info.Json = '' }
    $prop = $response.PSObject.Properties['node_online']
    $online = $false
    if ($prop) {
        if ($prop.Value -is [bool]) { $online = [bool]$prop.Value } else { $online = (([string]$prop.Value).Trim().ToLower() -eq 'true') }
    }
    $info.Online = $online
    if ($online) { $info.Detail = 'node_online=true' } else { $info.Detail = 'node_online=false' }
    return $info
}

# ==============================================================================
#  Collect (read-only)
# ==============================================================================
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

if ([string]::IsNullOrWhiteSpace($ConfigDir)) {
    $profileDir = $env:USERPROFILE
    if ([string]::IsNullOrWhiteSpace($profileDir)) { $profileDir = [Environment]::GetFolderPath('UserProfile') }
    if ([string]::IsNullOrWhiteSpace($profileDir)) { $profileDir = (Get-Location).Path }
    $ConfigDir = Join-Path $profileDir '.config\antigravity-mesh'
}
$envFile = Join-Path $ConfigDir 'agent.env'
$settings = Get-EnvMap $envFile
$gateway = ''
$nodeUser = ''
$token = ''
if ($settings.ContainsKey('MESH_GATEWAY')) { $gateway = [string]$settings['MESH_GATEWAY'] }
if ($settings.ContainsKey('MESH_USER')) { $nodeUser = [string]$settings['MESH_USER'] }
if ($settings.ContainsKey('MESH_TOKEN')) { $token = [string]$settings['MESH_TOKEN'] }

$missingKeys = @()
foreach ($key in @('MESH_GATEWAY', 'MESH_USER', 'MESH_TOKEN')) {
    if (-not $settings.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($settings[$key])) { $missingKeys += $key }
}

$envPresent = Test-Path -LiteralPath $envFile -PathType Leaf
$envRaw = ''
if ($envPresent) {
    try { $envRaw = Get-TextFile $envFile } catch { $envRaw = 'unreadable: ' + $_.Exception.Message }
}
$envMasked = Hide-Secret -Text $envRaw -Secret $token

# --- launcher -----------------------------------------------------------------
$startupDir = [Environment]::GetFolderPath('Startup')
$vbsPath = ''
if ([string]::IsNullOrWhiteSpace($startupDir)) { $vbsPath = 'antigravity-agent.vbs (the Startup folder could not be resolved)' }
else { $vbsPath = Join-Path $startupDir 'antigravity-agent.vbs' }
$launcher = Get-LauncherInfo $vbsPath
$interpreter = $launcher.Interpreter
$storeAlias = [bool]($interpreter -and ($interpreter -match '(?i)\\WindowsApps\\'))
$interpreterExists = [bool]($interpreter -and (Test-Path -LiteralPath $interpreter -PathType Leaf))

$payloadDir = $launcher.PayloadDir
$payloadProblem = ''
if ([string]::IsNullOrWhiteSpace($payloadDir)) {
    $payloadProblem = 'WshShell.CurrentDirectory is missing from the launcher'
} elseif (-not (Test-Path -LiteralPath $payloadDir -PathType Container)) {
    $payloadProblem = 'WshShell.CurrentDirectory is not a directory: ' + $payloadDir
}
$payloadOk = [bool]($payloadProblem -eq '')
$coreModule = ''
if ($payloadOk) {
    $coreModule = Join-Path $payloadDir 'core\agent.py'
}
$coreModuleOk = [bool]($coreModule -and (Test-Path -LiteralPath $coreModule -PathType Leaf))

# --- interpreter probes -------------------------------------------------------
$versionProbe = Invoke-Captured -FilePath $interpreter -ArgumentLine '--version' -WorkingDirectory $payloadDir -TimeoutMs $ProbeTimeoutMs
$websocketsProbe = Invoke-Captured -FilePath $interpreter -ArgumentLine '-c "import websockets"' -WorkingDirectory $payloadDir -TimeoutMs $ProbeTimeoutMs
$versionOk = [bool]($interpreterExists -and $versionProbe.Ran -and (-not $versionProbe.TimedOut) -and ($versionProbe.ExitCode -eq 0) -and ($versionProbe.Output -match '(?i)^Python\s+\d'))
$websocketsOk = [bool]($interpreterExists -and $websocketsProbe.Ran -and (-not $websocketsProbe.TimedOut) -and ($websocketsProbe.ExitCode -eq 0))

$interpreterOk = [bool]($interpreter -and (-not $storeAlias) -and $interpreterExists -and $versionOk)
$autostartOk = [bool]($launcher.Present -and $interpreter -and (-not $storeAlias) -and $interpreterExists -and $payloadOk)

# --- heartbeat / log / task / gateway ----------------------------------------
$heartbeatPath = Join-Path $ConfigDir 'agent.heartbeat'
$heartbeat = Get-HeartbeatInfo -Path $heartbeatPath -FreshSeconds $HeartbeatFreshSeconds
$agentLogPath = Join-Path $ConfigDir 'agent.log'
$logLines = [Math]::Max(0, $TailLines)
$log = Get-LogInfo -Path $agentLogPath -Lines $logLines
$task = Get-ScheduledTaskState -Name $TaskName
$processes = @(Get-AgentProcesses)
$health = Get-GatewayInfo -Gateway $gateway -NodeUser $nodeUser

$healthy = [bool]($interpreterOk -and $autostartOk -and $health.Online)

$problems = @()
if (-not $interpreterOk) {
    if (-not $interpreter) { $problems += 'no interpreter could be read from the launcher' }
    elseif ($storeAlias) { $problems += 'the pinned interpreter is the Microsoft Store alias (it never runs Python)' }
    elseif (-not $interpreterExists) { $problems += 'the pinned interpreter does not exist' }
    elseif (-not $versionOk) { $problems += ('the pinned interpreter does not answer --version' + $(if ($versionProbe.TimedOut) { ' (it timed out)' } else { '' })) }
}
if (-not $autostartOk) {
    if (-not $launcher.Present) { $problems += 'the Startup launcher is missing: ' + $vbsPath }
    if (-not $payloadOk) { $problems += $payloadProblem }
}
if ($autostartOk -and -not $coreModuleOk) { $problems += 'core\agent.py is not in the payload directory: ' + $coreModule }
if ($log.TornLine) { $problems += 'agent.log ends in a torn line (a truncated write, exactly like the post-reboot "Python " failure)' }
if (-not $health.Online) { $problems += 'the gateway does not report this node as online: ' + $health.Detail }
if ($processes.Count -eq 0) { $problems += 'no agent process found: nothing is running python -m core.agent (the process list may also be unreadable from this session)' }

# ==============================================================================
#  Report
# ==============================================================================
$report = [ordered]@{
    generated_at = ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') + 'Z')
    config_dir = $ConfigDir
    launcher = [ordered]@{
        path = $vbsPath
        present = $launcher.Present
        read_error = $launcher.Error
        interpreter = $interpreter
        payload_dir = $payloadDir
        payload_ok = $payloadOk
        payload_problem = $payloadProblem
        core_module = $coreModule
        core_module_present = $coreModuleOk
        content = (Hide-Secret -Text $launcher.Text -Secret $token)
    }
    interpreter = [ordered]@{
        path = $interpreter
        exists = $interpreterExists
        store_alias = $storeAlias
        version = $versionProbe.Output
        version_exit_code = $versionProbe.ExitCode
        version_timed_out = $versionProbe.TimedOut
        version_ok = $versionOk
        websockets_ok = $websocketsOk
        websockets_output = $websocketsProbe.Output
        probe_error = ($versionProbe.Error + ' ' + $websocketsProbe.Error).Trim()
        probe_working_directory = $payloadDir
    }
    scheduled_task = [ordered]@{
        name = $task.Name
        cmdlets_available = $task.CmdletsAvailable
        present = $task.Present
        state = $task.State
        actions = $task.Actions
        last_run_time = $task.LastRunTime
        last_result = $task.LastResult
        last_result_meaning = $task.LastResultMeaning
        next_run_time = $task.NextRunTime
        missed_runs = $task.MissedRuns
        detail = $task.Detail
    }
    agent_env = [ordered]@{
        path = $envFile
        present = $envPresent
        gateway = $gateway
        user = $nodeUser
        token = '****'
        missing_keys = $missingKeys
        content = $envMasked
    }
    heartbeat = [ordered]@{
        path = $heartbeatPath
        present = $heartbeat.Present
        parsed = $heartbeat.Parsed
        json = $heartbeat.Json
        age_seconds = $heartbeat.AgeSeconds
        fresh = $heartbeat.Fresh
        fresh_within_seconds = $HeartbeatFreshSeconds
        detail = $heartbeat.Detail
    }
    agent_log = [ordered]@{
        path = $agentLogPath
        present = $log.Present
        bytes = $log.Bytes
        last_write_utc = $log.LastWriteTime
        tail_lines = $logLines
        lines = $log.Tail
        torn_line = $log.TornLine
        last_byte = $log.LastByte
        read_error = $log.Error
    }
    agent_processes = @($processes | ForEach-Object { [ordered]@{ pid = $_.Pid; name = $_.Name; command_line = $_.CommandLine } })
    gateway = [ordered]@{
        url = $health.Url
        checked = $health.Checked
        node_online = $health.Online
        json = $health.Json
        detail = $health.Detail
    }
    problems = $problems
    verdict = [ordered]@{
        interpreter_ok = $interpreterOk
        autostart_ok = $autostartOk
        heartbeat_fresh = $heartbeat.Fresh
        node_online = $health.Online
    }
}

if ($AsJson) {
    # Exactly one JSON object, nothing else: the exit code carries the verdict, and
    # ConvertTo-Json escapes any non-ASCII log text, so this stays 7-bit clean.
    $report | ConvertTo-Json -Depth 8
} else {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

    $out = New-Object System.Collections.ArrayList
    function Add-Out {
        param([string]$Text)
        [void]$out.Add($Text)
    }
    function Add-Field {
        param([string]$Name, $Value)
        # Booleans print as lowercase true/false so the verdict lines can be read by
        # eye and by grep, exactly as documented.
        if ($Value -is [bool]) {
            $text = 'false'
            if ($Value) { $text = 'true' }
        } else {
            $text = ''
            if ($null -ne $Value) { $text = [string]$Value }
        }
        [void]$out.Add(('{0,-22} : {1}' -f $Name, $text))
    }
    function Add-Block {
        param([string]$Name, [string[]]$Lines)
        Add-Out ($Name + ':')
        if (-not $Lines -or $Lines.Count -eq 0) { Add-Out '    (empty)'; return }
        foreach ($line in $Lines) { Add-Out ('    ' + $line) }
    }

    Add-Out 'Antigravity Mesh doctor (read-only)'
    Add-Out '================================='
    Add-Field 'generated_at' $report.generated_at
    Add-Field 'config_dir' $ConfigDir
    Add-Out ''
    Add-Out '[launcher - Windows Startup .vbs]'
    Add-Field 'vbs_path' $vbsPath
    Add-Field 'vbs_present' $launcher.Present
    if ($launcher.Error) { Add-Field -Name 'vbs_read_error' -Value $launcher.Error }
    Add-Field 'vbs_interpreter' $interpreter
    Add-Field 'payload_dir' $payloadDir
    Add-Field 'payload_ok' $payloadOk
    if ($payloadProblem) { Add-Field -Name 'payload_problem' -Value $payloadProblem }
    Add-Field 'core_module_present' $coreModuleOk
    if ($report.launcher.content) { Add-Block 'vbs_content (token masked)' @($report.launcher.content -split "`r?`n") }
    Add-Out ''
    Add-Out '[interpreter]'
    Add-Field 'interpreter_exists' $interpreterExists
    Add-Field 'store_alias' $storeAlias
    Add-Field -Name 'interpreter_version' -Value $versionProbe.Output
    Add-Field 'version_exit_code' $versionProbe.ExitCode
    Add-Field 'version_timed_out' $versionProbe.TimedOut
    Add-Field 'websockets_ok' $websocketsOk
    Add-Field -Name 'websockets_probe' -Value $websocketsProbe.Output
    Add-Field 'probe_cwd' $payloadDir
    Add-Out ''
    Add-Out '[scheduled task]'
    Add-Field 'task_name' $TaskName
    Add-Field 'task_cmdlets_available' $task.CmdletsAvailable
    Add-Field 'task_present' $task.Present
    Add-Field 'task_state' $task.State
    Add-Field 'task_last_run_time' $task.LastRunTime
    Add-Field 'task_last_result' $task.LastResult
    Add-Field 'task_last_result_meaning' $task.LastResultMeaning
    Add-Field 'task_next_run_time' $task.NextRunTime
    Add-Field 'task_missed_runs' $task.MissedRuns
    if ($task.Detail) { Add-Field -Name 'task_detail' -Value $task.Detail }
    Add-Block 'task_actions' @($task.Actions)
    Add-Out ''
    Add-Out '[agent.env]'
    Add-Field 'agent_env_path' $envFile
    Add-Field 'agent_env_present' $envPresent
    Add-Field 'MESH_GATEWAY' $gateway
    Add-Field 'MESH_USER' $nodeUser
    Add-Field 'MESH_TOKEN' '****'
    if ($missingKeys.Count -gt 0) { Add-Field 'missing_keys' ($missingKeys -join ', ') }
    if ($envMasked) { Add-Block 'agent_env_content (token masked)' @($envMasked -split "`r?`n") }
    Add-Out ''
    Add-Out '[heartbeat]'
    Add-Field 'heartbeat_path' $heartbeatPath
    Add-Field 'heartbeat_present' $heartbeat.Present
    Add-Field 'heartbeat_parsed' $heartbeat.Parsed
    Add-Field 'heartbeat_age_seconds' $heartbeat.AgeSeconds
    Add-Field 'heartbeat_fresh' $heartbeat.Fresh
    Add-Field 'heartbeat_detail' $heartbeat.Detail
    if ($heartbeat.Json) { Add-Block 'heartbeat_json' @($heartbeat.Json -split "`r?`n") }
    Add-Out ''
    Add-Out '[agent.log]'
    Add-Field 'agent_log_path' $agentLogPath
    Add-Field 'agent_log_present' $log.Present
    Add-Field 'agent_log_bytes' $log.Bytes
    Add-Field 'agent_log_last_write_utc' $log.LastWriteTime
    Add-Field 'torn-line' $log.TornLine
    if ($log.Error) { Add-Field -Name 'agent_log_read_error' -Value $log.Error }
    Add-Block ('agent_log_tail (last ' + $logLines + ' lines)') @($log.Tail)
    Add-Out ''
    Add-Out '[agent process]'
    Add-Field 'core_agent_processes' $processes.Count
    foreach ($proc in $processes) { Add-Field -Name ('pid ' + $proc.Pid) -Value $proc.CommandLine }
    Add-Out ''
    Add-Out '[gateway health]'
    Add-Field 'health_url' $health.Url
    Add-Field 'health_checked' $health.Checked
    Add-Field -Name 'health_detail' -Value $health.Detail
    if ($health.Json) { Add-Block 'health_json' @($health.Json -split "`r?`n") }
    Add-Out ''
    Add-Out '[problems]'
    if ($problems.Count -eq 0) { Add-Out '    (none)' } else { foreach ($problem in $problems) { Add-Out ('    - ' + $problem) } }
    Add-Out ''
    Add-Out '[verdict]'
    Add-Field 'interpreter_ok' $interpreterOk
    Add-Field 'autostart_ok' $autostartOk
    Add-Field 'heartbeat_fresh' $heartbeat.Fresh
    Add-Field 'node_online' $health.Online

    foreach ($line in $out) { Write-Host $line }
}

if ($healthy) { exit 0 }
exit 2
