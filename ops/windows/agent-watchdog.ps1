# ==============================================================================
#  Antigravity Mesh - Windows agent watchdog (single shot supervisor)
#
#  Meant to be run every 5 minutes by the Scheduled Task "AntigravityMeshWatchdog".
#  It answers one question: is this node's agent alive, and if it is not, start it
#  exactly the way the logon launcher would?
#
#  WHY THIS EXISTS
#  After a Windows Update reboot the logon launcher silently did nothing. The
#  interpreter pinned in the Startup .vbs was the Microsoft Store App Execution
#  Alias <...>\WindowsApps\python.exe: a file that exists and can be launched, but
#  runs no Python at all. The only trace was a truncated "Python " line appended to
#  agent.log without a trailing newline, and the node stayed offline for ~29 hours
#  while the Gemini client showed a generic frontend error. This watchdog refuses
#  that interpreter outright, and recovers the node from a pinned, verified one.
#
#  WHAT IT DOES, IN ORDER
#    1. reads <ConfigDir>\agent.env (MESH_GATEWAY / MESH_USER / MESH_TOKEN);
#    2. reads the Startup launcher and takes the interpreter from its
#       WshShell.Run line and the payload directory from WshShell.CurrentDirectory;
#    3. refuses (exit 2) when the interpreter is missing, is not a file, or lives
#       under \WindowsApps\;
#    4. probes liveness: (a) a fresh <ConfigDir>\agent.heartbeat whose ts is newer
#       than -GraceSeconds (default 60), else (b) the gateway's
#       /health?user=<node> reporting node_online=true, else (c) a verified agent
#       process - a python.exe/pythonw.exe/py.exe running "-m core.agent", or the
#       cmd.exe launcher wrapper that also carries the agent.log redirect. A
#       process that merely MENTIONS core.agent (a PowerShell session, a grep)
#       never counts, or a dead node would look alive forever;
#    5. when the node is dead, appends exactly one diagnostic line to agent.log,
#       starts the agent hidden with the same command line / environment / working
#       directory contract as the .vbs, waits up to 20 s for liveness and logs the
#       outcome.
#
#  EXIT CODES
#    0  the node is online (already was, or was recovered by this run), or another
#       watchdog instance is currently handling it
#    2  configuration / launcher problem: agent.env, the Startup .vbs or the pinned
#       interpreter is missing, unusable, or is the Microsoft Store alias
#    3  the node is still offline after the recovery attempt
#
#  PARAMETERS
#    -ConfigDir    where agent.env / agent.log / agent.heartbeat live.
#                  Default: %USERPROFILE%\.config\antigravity-mesh
#    -LogFile      this script's journal. Default: <ConfigDir>\watchdog.log
#    -GraceSeconds heartbeat freshness threshold, in seconds, default 60: a
#                  heartbeat whose ts is older than this is not fresh. The agent
#                  beats every 15 s, so the default tolerates three missed beats.
#    -Quiet        write to -LogFile only, print nothing to the console.
#
#  IDEMPOTENT BY DESIGN
#    * A watchdog that sees a live node (or a verified agent process) never starts
#      a second one.
#    * A named mutex serialises overlapping runs, so a slow run cannot double-start
#      the agent and the script never depends on the task scheduler being up.
#    * The token is never printed and never written to any log; it is masked as
#      "****" wherever a human would see it.
#
#  This file is deliberately ASCII-only: Windows PowerShell 5.1 reads a BOM-less
#  script with the ANSI code page, so non-ASCII text here is a portability bug.
#
#  Usage:
#     .\agent-watchdog.ps1
#     .\agent-watchdog.ps1 -ConfigDir "$env:USERPROFILE\.config\antigravity-mesh"
#     .\agent-watchdog.ps1 -GraceSeconds 120
#     .\agent-watchdog.ps1 -Quiet -LogFile C:\logs\watchdog.log
# ==============================================================================
[CmdletBinding()]
param(
    [string]$ConfigDir = '',
    [string]$LogFile = '',
    [int]$GraceSeconds = 60,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'

# ------------------------------------------------------------------ logging ---
# Two logs are in play and they must never be confused:
#   * agent.log    - the running agent's stdout/stderr; the watchdog appends ONE
#                    diagnostic line to it so the reason for a restart is visible
#                    next to the failure that caused it;
#   * watchdog.log - this script's own journal (-LogFile), append-only.
$script:QuietMode = [bool]$Quiet
$script:LogTarget = ''
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList @($false)

function Write-LogLine {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($script:LogTarget)) { return }
    try {
        $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
        [System.IO.File]::AppendAllText($script:LogTarget, "[$stamp] $Text" + [Environment]::NewLine, $script:Utf8NoBom)
    } catch { }
}

function Write-Note {
    param([string]$Text)
    Write-LogLine $Text
    if (-not $script:QuietMode) { Write-Host $Text }
}

function Stop-WithConfigError {
    param([string]$Message)
    Write-Note ("CONFIG ERROR: " + $Message)
    exit 2
}

function Add-AgentLogLine {
    param([string]$Line)
    $path = Join-Path $ConfigDir 'agent.log'
    $single = ($Line -replace '[\r\n]+', ' ').Trim()
    try {
        [System.IO.File]::AppendAllText($path, $single + [Environment]::NewLine, $script:Utf8NoBom)
        return $true
    } catch {
        Write-Note ("could not append to " + $path + ": " + $_.Exception.Message)
        return $false
    }
}

# ------------------------------------------------------------------- secrets --
# Anything a human can read goes through here. The literal secret is replaced when
# it is long enough to be unambiguous, and the two shipped shapes of the token
# (agent.env KEY=value and the .vbs Environment(...) call) are always masked, so a
# short token cannot leak through pattern matching alone.
function Hide-Secret {
    param([string]$Text, [string]$Secret)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $result = $Text
    if ($Secret -and $Secret.Length -ge 4) { $result = $result.Replace($Secret, '****') }
    $result = [regex]::Replace($result, '(?im)^(\s*MESH_TOKEN\s*=\s*).*$', '${1}****')
    $result = [regex]::Replace($result, '(?im)(MESH_TOKEN"\)\s*=\s*").*?(")', '${1}****${2}')
    return $result
}

# ---------------------------------------------------------------- file input --
function Read-TextFile {
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
    # No BOM: UTF-16 without a BOM is full of zero bytes, UTF-8/ANSI is not. The
    # launcher is written as UTF-16 ("Unicode") by install.ps1, but a hand-edited
    # file may be ANSI or UTF-8, and both must be readable.
    $hasNul = $false
    $limit = [Math]::Min($bytes.Length, 512)
    for ($i = 0; $i -lt $limit; $i++) {
        if ($bytes[$i] -eq 0) { $hasNul = $true; break }
    }
    if ($hasNul) { return [System.Text.Encoding]::Unicode.GetString($bytes) }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

function Read-EnvFile {
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
        $key = $text.Substring(0, $index).Trim()
        $value = $text.Substring($index + 1).Trim().Trim('"').Trim("'")
        $values[$key] = $value
    }
    return $values
}

# ------------------------------------------------------------ launcher (.vbs) --
function Get-LauncherInfo {
    param([string]$VbsPath)
    $info = @{ Present = $false; Text = ''; Interpreter = ''; PayloadDir = ''; Error = '' }
    if (-not (Test-Path -LiteralPath $VbsPath -PathType Leaf)) { $info.Error = 'not found'; return $info }
    $info.Present = $true
    try {
        $info.Text = Read-TextFile $VbsPath
    } catch {
        $info.Error = $_.Exception.Message
        return $info
    }
    # The Run line, as written by install.ps1:
    #   WshShell.Run "cmd /c """"<python.exe>"" -u -m core.agent >> """"<agent.log>"""" 2>&1""", 0, False
    # The number of doubled quotes is a function of which layer wrote the file, so
    # the pattern tolerates one or more quote characters on either side of the path.
    # The pattern stops at the line boundary ([^\r\n]*, no $ anchor: a .vbs written
    # by Out-File has CRLF endings and .NET's multiline $ refuses to match before
    # the \r of a CRLF pair).
    $run = [regex]::Match($info.Text, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?cmd[ \t]+/c[ \t]+"+(?<exe>[^"]+?)"+[ \t]+(?:-u[ \t]+)?-m[ \t]+core\.agent[^\r\n]*')
    if (-not $run.Success) {
        # A launcher that no longer spells out "-m core.agent" is still worth
        # reading: take the first absolute executable on the Run line.
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

# ------------------------------------------------------------- liveness (a/b/c) --
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

function Get-HeartbeatState {
    param([string]$Path, [int]$Grace)
    $state = @{ Present = $false; Parsed = $false; AgeSeconds = $null; Fresh = $false; Detail = 'missing' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $state }
    $state.Present = $true
    try {
        $raw = [System.IO.File]::ReadAllText($Path)
    } catch {
        $state.Detail = 'unreadable: ' + $_.Exception.Message
        return $state
    }
    try {
        $json = $raw | ConvertFrom-Json
    } catch {
        $state.Detail = 'not valid JSON'
        return $state
    }
    $state.Parsed = $true
    $stampRaw = $null
    foreach ($name in @('ts', 'timestamp', 'time', 'last_seen', 'checked_at')) {
        $prop = $json.PSObject.Properties[$name]
        if ($prop -and $null -ne $prop.Value -and ([string]$prop.Value).Trim().Length -gt 0) {
            $stampRaw = $prop.Value
            break
        }
    }
    if ($null -eq $stampRaw) { $state.Detail = 'JSON has no ts field'; return $state }
    $stamp = ConvertTo-UtcDateTime $stampRaw
    if ($null -eq $stamp) { $state.Detail = 'ts is not a timestamp: ' + ([string]$stampRaw); return $state }
    $age = ((Get-Date).ToUniversalTime() - $stamp.ToUniversalTime()).TotalSeconds
    $state.AgeSeconds = [Math]::Round($age, 1)
    if ($age -le $Grace) {
        $state.Fresh = $true
        $state.Detail = 'fresh (' + $state.AgeSeconds + 's old)'
    } else {
        $state.Detail = 'stale (' + $state.AgeSeconds + 's old, grace ' + $Grace + 's)'
    }
    return $state
}

function Get-GatewayHealth {
    param([string]$Gateway, [string]$NodeUser, [int]$TimeoutSec = 10)
    $state = @{ Url = ''; Ok = $false; Text = ''; Detail = 'not checked' }
    if ([string]::IsNullOrWhiteSpace($Gateway) -or [string]::IsNullOrWhiteSpace($NodeUser)) {
        $state.Detail = 'gateway or node name missing'
        return $state
    }
    $hostName = ($Gateway.Trim() -replace '^[A-Za-z][A-Za-z0-9+.-]*://', '').Trim().TrimEnd('/')
    if ($hostName.Length -eq 0) {
        $state.Detail = 'gateway is empty after normalisation'
        return $state
    }
    $state.Url = 'https://' + $hostName + '/health?user=' + [uri]::EscapeDataString($NodeUser)
    try {
        $response = Invoke-RestMethod -Uri $state.Url -Method Get -TimeoutSec $TimeoutSec -ErrorAction Stop
    } catch {
        $state.Detail = 'unreachable: ' + $_.Exception.Message
        return $state
    }
    try { $state.Text = ($response | ConvertTo-Json -Compress -Depth 5) } catch { $state.Text = '' }
    $prop = $response.PSObject.Properties['node_online']
    $online = $false
    if ($prop) {
        if ($prop.Value -is [bool]) { $online = [bool]$prop.Value } else { $online = (([string]$prop.Value).Trim().ToLower() -eq 'true') }
    }
    $state.Ok = $online
    if ($online) { $state.Detail = 'node_online=true' } else { $state.Detail = 'node_online=false' }
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
    # A verified agent process is the third and last liveness signal: it is the
    # only one that still works when the gateway is unreachable and the heartbeat
    # was never written.
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
        if ($proc.ProcessId -eq $PID) { continue }
        if (-not (Test-AgentProcess -Name ([string]$proc.Name) -CommandLine $commandLine)) { continue }
        $found += @{ Pid = [int]$proc.ProcessId; Name = [string]$proc.Name; CommandLine = $commandLine }
    }
    return $found
}

function Get-LivenessState {
    param([string]$HeartbeatPath, [string]$Gateway, [string]$NodeUser, [int]$Grace, [int]$TimeoutSec)
    $state = @{ Alive = $false; Via = ''; Heartbeat = $null; Gateway = $null; Processes = @(); Reason = '' }
    # (a) heartbeat, (b) gateway health, (c) a verified agent process - in order.
    $state.Heartbeat = Get-HeartbeatState -Path $HeartbeatPath -Grace $Grace
    if ($state.Heartbeat.Fresh) { $state.Alive = $true; $state.Via = 'fresh heartbeat'; return $state }
    $state.Gateway = Get-GatewayHealth -Gateway $Gateway -NodeUser $NodeUser -TimeoutSec $TimeoutSec
    if ($state.Gateway.Ok) { $state.Alive = $true; $state.Via = 'gateway health'; return $state }
    $state.Processes = @(Get-AgentProcesses)
    if ($state.Processes.Count -gt 0) {
        $state.Alive = $true
        $state.Via = 'running process (pid ' + $state.Processes[0].Pid + ')'
        return $state
    }
    $state.Reason = 'no fresh heartbeat (' + $state.Heartbeat.Detail + '), gateway ' + $state.Gateway.Detail + ', no verified agent process (a python.exe running -m core.agent, or the cmd.exe launcher wrapper)'
    return $state
}

function Resolve-FullPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    try { return [System.IO.Path]::GetFullPath($Path) } catch { return $Path }
}

# ==============================================================================
#  Main
# ==============================================================================

# --- 1. paths ----------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($ConfigDir)) {
    $profileDir = $env:USERPROFILE
    if ([string]::IsNullOrWhiteSpace($profileDir)) { $profileDir = [Environment]::GetFolderPath('UserProfile') }
    if ([string]::IsNullOrWhiteSpace($profileDir)) {
        if (-not $Quiet) { Write-Host 'CONFIG ERROR: cannot resolve the user profile directory; pass -ConfigDir explicitly.' }
        exit 2
    }
    $ConfigDir = Join-Path $profileDir '.config\antigravity-mesh'
}
$ConfigDir = Resolve-FullPath $ConfigDir
if ([string]::IsNullOrWhiteSpace($LogFile)) { $LogFile = Join-Path $ConfigDir 'watchdog.log' }
$LogFile = Resolve-FullPath $LogFile
try {
    $logDir = Split-Path -Parent $LogFile
    if ($logDir -and -not (Test-Path -LiteralPath $logDir -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $logDir -Force -ErrorAction Stop
    }
    $script:LogTarget = $LogFile
} catch {
    $script:LogTarget = ''
}

# --- 2. single instance ------------------------------------------------------
# Overlapping runs (a slow gateway probe plus the next 5-minute tick) must not
# start two agents. The OS releases the mutex when this process exits, so a
# crashed run can never wedge the watchdog.
$mutexName = 'AntigravityMeshWatchdog-' + ($env:USERNAME)
$mutex = $null
$acquired = $true
foreach ($candidate in @(('Global\' + $mutexName), $mutexName)) {
    try {
        $mutex = New-Object System.Threading.Mutex -ArgumentList @($false, $candidate)
        $acquired = [bool]$mutex.WaitOne(0)
        break
    } catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
        break
    } catch {
        $mutex = $null
        $acquired = $true
    }
}
if (-not $acquired) {
    Write-LogLine 'another watchdog instance is already running; nothing to do'
    exit 0
}

if ($GraceSeconds -lt 0) { $GraceSeconds = 0 }
$heartbeatPath = Join-Path $ConfigDir 'agent.heartbeat'

# --- 3. configuration --------------------------------------------------------
$envFile = Join-Path $ConfigDir 'agent.env'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Stop-WithConfigError ('no agent.env at ' + $envFile + ' - this node was never installed; run install.ps1')
}
$settings = Read-EnvFile $envFile
$missing = @()
foreach ($key in @('MESH_GATEWAY', 'MESH_USER', 'MESH_TOKEN')) {
    if (-not $settings.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($settings[$key])) { $missing += $key }
}
if ($missing.Count -gt 0) {
    Stop-WithConfigError ('agent.env is missing or has an empty ' + ($missing -join ', ') + ' (' + $envFile + ') - refusing to start an agent from an incomplete configuration')
}
$gateway = $settings['MESH_GATEWAY'].Trim()
$nodeUser = $settings['MESH_USER'].Trim()
$token = $settings['MESH_TOKEN'].Trim()

Write-Note ('watchdog start: config=' + $ConfigDir + ' gateway=' + $gateway + ' user=' + $nodeUser + ' token=****')

# --- 4. the logon launcher ---------------------------------------------------
$startupDir = [Environment]::GetFolderPath('Startup')
if ([string]::IsNullOrWhiteSpace($startupDir)) {
    Stop-WithConfigError 'cannot resolve the Startup folder ([Environment]::GetFolderPath("Startup") returned nothing)'
}
$vbsPath = Join-Path $startupDir 'antigravity-agent.vbs'
if (-not (Test-Path -LiteralPath $vbsPath -PathType Leaf)) {
    Stop-WithConfigError ('the autostart launcher is missing: ' + $vbsPath + ' - this node would not start after a reboot; re-run install.ps1')
}
$launcher = Get-LauncherInfo $vbsPath
if (-not $launcher.Present) {
    Stop-WithConfigError ('cannot read the autostart launcher ' + $vbsPath + ': ' + $launcher.Error)
}
$interpreter = $launcher.Interpreter
if ([string]::IsNullOrWhiteSpace($interpreter)) {
    Stop-WithConfigError ('no interpreter found in the WshShell.Run line of ' + $vbsPath + ' - the launcher is unusable; re-run install.ps1')
}
if ($interpreter -match '(?i)\\WindowsApps\\') {
    Stop-WithConfigError ('the pinned interpreter is the Microsoft Store App Execution Alias: ' + $interpreter + ' - that alias never runs Python and was the silent post-reboot failure; re-run install.ps1 so a real python.exe is pinned')
}
if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
    Stop-WithConfigError ('the pinned interpreter does not exist: ' + $interpreter + ' - re-run install.ps1, or fix the WshShell.Run line in ' + $vbsPath)
}

# The payload directory decides where "import core.agent" resolves from. Prefer
# exactly what the .vbs sets; fall back to the payload this watchdog was shipped
# inside, then to the config directory, and always say which one was used.
$payloadReason = 'WshShell.CurrentDirectory'
if ([string]::IsNullOrWhiteSpace($launcher.PayloadDir)) {
    $payloadProblem = 'missing'
} elseif (-not (Test-Path -LiteralPath $launcher.PayloadDir -PathType Container)) {
    $payloadProblem = 'not a directory (' + $launcher.PayloadDir + ')'
} else {
    $payloadProblem = ''
}
if ($payloadProblem) {
    $fallbackDir = ''
    if ($PSScriptRoot) { $fallbackDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
    if ($fallbackDir -and (Test-Path -LiteralPath $fallbackDir -PathType Container)) {
        $payloadDir = $fallbackDir
        $payloadReason = 'the watchdog location (WshShell.CurrentDirectory is ' + $payloadProblem + ')'
    } else {
        $payloadDir = $ConfigDir
        $payloadReason = 'the config directory (WshShell.CurrentDirectory is ' + $payloadProblem + ' and no payload was found next to the watchdog)'
    }
} else {
    $payloadDir = $launcher.PayloadDir
}
Write-Note ('launcher=' + $vbsPath + ' interpreter=' + $interpreter + ' payload=' + $payloadDir + ' (' + $payloadReason + ')')

# --- 5. is the node alive? ---------------------------------------------------
$state = Get-LivenessState -HeartbeatPath $heartbeatPath -Gateway $gateway -NodeUser $nodeUser -Grace $GraceSeconds -TimeoutSec 10
if ($state.Alive) {
    Write-Note ('node is online via ' + $state.Via + '; nothing to do')
    exit 0
}
Write-Note ('node is offline: ' + $state.Reason)

# --- 6. recover --------------------------------------------------------------
# Exactly one line goes into agent.log: it says why the node was considered dead
# and which interpreter is being started. Everything written to a log passes
# through Hide-Secret, so the token cannot leak into agent.log even by accident.
$stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
$diagnostic = '[' + $stamp + '] watchdog: node is offline (' + $state.Reason + '); starting the agent with interpreter ' + $interpreter + ' from ' + $payloadDir
$null = Add-AgentLogLine (Hide-Secret -Text $diagnostic -Secret $token)

# Same contract as the logon launcher: cmd /c ""<interpreter>" -u -m core.agent
# >> "<agent.log>" 2>&1 - cmd strips the outer quote pair, leaving one quoted
# interpreter, an unbuffered core.agent run and an append redirection.
$env:MESH_GATEWAY = $gateway
$env:MESH_USER = $nodeUser
$env:MESH_TOKEN = $token
$agentLog = Join-Path $ConfigDir 'agent.log'
$argumentLine = '/c ""' + $interpreter + '" -u -m core.agent >> "' + $agentLog + '" 2>&1"'
$launched = $false
try {
    $agentProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList $argumentLine -WorkingDirectory $payloadDir -WindowStyle Hidden -PassThru -ErrorAction Stop
    $launched = $true
    Write-Note ('started the agent: pid=' + $agentProcess.Id + ' interpreter=' + $interpreter + ' cwd=' + $payloadDir)
} catch {
    Write-Note ('could not start the agent: ' + $_.Exception.Message)
}

# --- 7. wait for liveness ----------------------------------------------------
$finalState = $null
if ($launched) {
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $finalState = Get-LivenessState -HeartbeatPath $heartbeatPath -Gateway $gateway -NodeUser $nodeUser -Grace $GraceSeconds -TimeoutSec 5
        if ($finalState.Alive) { break }
    }
} else {
    Write-Note 'not waiting for liveness: the launch failed'
}

if ($finalState -and $finalState.Alive) {
    Write-Note ('node is online again via ' + $finalState.Via + '; recovery succeeded')
    exit 0
}
if ($finalState) {
    Write-Note ('node is STILL offline after the recovery attempt: ' + $finalState.Reason)
} else {
    Write-Note 'node is STILL offline: no agent process was started'
}
exit 3
