# ==============================================================================
#  Antigravity Mesh - Windows node updater (single shot)
#
#  Meant to be run by the Scheduled Task "AntigravityMeshUpdater" once a day, and
#  by hand through "gemini-computer-use update". It answers one question: is a
#  newer release published, and - unless -Check was given - install it.
#
#  WHY A POWERSHELL WRAPPER
#  The updater itself is Python (core/updater.py), because it has to work on
#  Linux and macOS too. This wrapper exists for the two things only Windows
#  knows: which interpreter the node is actually pinned to (the Startup .vbs),
#  and where the payload lives (WshShell.CurrentDirectory). Starting the updater
#  with the WRONG interpreter - the Microsoft Store alias - was the failure that
#  kept a node offline for a day, so the interpreter is taken from the same
#  launcher the agent itself is started from and refused if it is an alias.
#
#  WHAT IT DOES, IN ORDER
#    1. resolves the install directory (-InstallDir, the Startup launcher, this
#       script's own location, the config directory - in that order) and refuses
#       a directory that holds no core\agent.py;
#    2. resolves the interpreter (-Python, the Startup launcher's Run line, the
#       heartbeat, MESH_PYTHON, then a real python.exe on PATH) and refuses the
#       Microsoft Store alias, which never runs Python;
#    3. runs "python -m core.updater --check" (with -Check) or "--apply",
#       capturing stdout and stderr separately so the JSON answer stays parseable
#       on Windows PowerShell 5.1;
#    4. prints (or logs, with -Quiet) the local version, the newest published
#       version and what was done.
#
#  EXIT CODES
#    0  the node is on the newest release (or was updated without a restart)
#    2  an update is available and was not installed (-Check), or the node is
#       misconfigured (no payload, no usable interpreter)
#    3  the update failed: network, checksum mismatch, unusable payload
#    4  the update was installed and the node is restarting
#
#  PARAMETERS
#    -Check        report only; never download or replace anything
#    -Force        ignore the check interval and the failed-attempt backoff
#    -NoRestart    install the payload but do not restart the agent
#    -Offline      with -Check: report the cached answer, touch no network
#    -Json         print the updater's JSON answer verbatim (for scripts)
#    -InstallDir   payload directory override
#    -Python       interpreter override
#    -ConfigDir    where agent.env / agent.heartbeat live.
#                  Default: %USERPROFILE%\.config\antigravity-mesh
#    -LogFile      this script's journal. Default: <ConfigDir>\update-task.log
#    -Quiet        write to -LogFile only, print nothing to the console
#
#  This file is deliberately ASCII-only: Windows PowerShell 5.1 reads a BOM-less
#  script with the ANSI code page, so non-ASCII text here is a portability bug.
#
#  Usage:
#     .\update.ps1
#     .\update.ps1 -Check
#     .\update.ps1 -Quiet -LogFile C:\logs\update.log
# ==============================================================================
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Force,
    [switch]$NoRestart,
    [switch]$Offline,
    [switch]$Json,
    [string]$InstallDir = '',
    [string]$Python = '',
    [string]$ConfigDir = '',
    [string]$LogFile = '',
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$WindowsAppsMarker = '\WindowsApps\'

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
    Write-Note ('CONFIG ERROR: ' + $Message)
    exit 2
}

function Resolve-FullPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    try { return [System.IO.Path]::GetFullPath($Path) } catch { return $Path }
}

function Test-File {
    # Test-Path refuses an empty -LiteralPath with a parameter binding error, and
    # that error aborts the whole statement it sits in - including the body of the
    # "if (Test-Path $x)" it guards. Every path check in this script goes through
    # here, so an unresolved value is simply "not a file".
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath $Path -PathType Leaf)
}

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
    # The launcher is written as UTF-16 ("Unicode") by install.ps1, but a
    # hand-edited file may be ANSI or UTF-8, so a NUL byte decides.
    $hasNul = $false
    $limit = [Math]::Min($bytes.Length, 512)
    for ($i = 0; $i -lt $limit; $i++) {
        if ($bytes[$i] -eq 0) { $hasNul = $true; break }
    }
    if ($hasNul) { return [System.Text.Encoding]::Unicode.GetString($bytes) }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

# ------------------------------------------------------------------ paths ----
if ([string]::IsNullOrWhiteSpace($ConfigDir)) {
    $profileDir = $env:USERPROFILE
    if ([string]::IsNullOrWhiteSpace($profileDir)) { $profileDir = [Environment]::GetFolderPath('UserProfile') }
    if ([string]::IsNullOrWhiteSpace($profileDir)) {
        Stop-WithConfigError 'cannot resolve the user profile directory; pass -ConfigDir explicitly'
    }
    $ConfigDir = Join-Path $profileDir '.config\antigravity-mesh'
}
$ConfigDir = Resolve-FullPath $ConfigDir
if ([string]::IsNullOrWhiteSpace($LogFile)) { $LogFile = Join-Path $ConfigDir 'update-task.log' }
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

# ------------------------------------------------------------- the launcher --
# install.ps1 writes this file; it names both the interpreter to run and the
# directory the agent runs from, which is exactly what the updater needs.
$startupDir = [Environment]::GetFolderPath('Startup')
$vbsPath = ''
$launcherText = ''
if (-not [string]::IsNullOrWhiteSpace($startupDir)) {
    $vbsPath = Join-Path $startupDir 'antigravity-agent.vbs'
    if (Test-Path -LiteralPath $vbsPath -PathType Leaf) {
        try { $launcherText = Read-TextFile $vbsPath } catch { $launcherText = '' }
    }
}

function Get-LauncherInterpreter {
    if ([string]::IsNullOrWhiteSpace($launcherText)) { return '' }
    $run = [regex]::Match($launcherText, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?cmd[ \t]+/c[ \t]+"+(?<exe>[^"]+?)"+[ \t]+(?:-u[ \t]+)?-m[ \t]+core\.agent[^\r\n]*')
    if (-not $run.Success) {
        $run = [regex]::Match($launcherText, '(?im)^[^\r\n]*WshShell\.Run[^\r\n]*?(?<exe>[A-Za-z]:\\[^"]*?\.(?:exe|cmd|bat))')
    }
    if ($run.Success) { return $run.Groups['exe'].Value.Trim() }
    return ''
}

function Get-LauncherPayloadDir {
    if ([string]::IsNullOrWhiteSpace($launcherText)) { return '' }
    $dir = [regex]::Match($launcherText, '(?im)^[^\r\n]*WshShell\.CurrentDirectory[ \t]*=[ \t]*"(?<dir>[^"]+)"')
    if ($dir.Success) { return $dir.Groups['dir'].Value.Trim() }
    return ''
}

function Test-NodePayload {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'core\agent.py') -PathType Leaf)
}

# --- install directory --------------------------------------------------------
$PayloadReason = '-InstallDir'
if (-not (Test-NodePayload $InstallDir)) {
    $candidate = Get-LauncherPayloadDir
    if (Test-NodePayload $candidate) {
        $InstallDir = $candidate
        $PayloadReason = 'the Startup launcher (WshShell.CurrentDirectory)'
    } else {
        $candidate = ''
        if ($PSScriptRoot) { $candidate = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
        if (Test-NodePayload $candidate) {
            $InstallDir = $candidate
            $PayloadReason = 'the location of this script'
        } elseif (Test-NodePayload $ConfigDir) {
            $InstallDir = $ConfigDir
            $PayloadReason = 'the config directory'
        } else {
            Stop-WithConfigError ('no node payload found: -InstallDir, the Startup launcher and the script location all lack core\agent.py')
        }
    }
}
$InstallDir = Resolve-FullPath $InstallDir
if (-not (Test-NodePayload $InstallDir)) {
    Stop-WithConfigError ('the install directory holds no core\agent.py: ' + $InstallDir)
}

# --- interpreter --------------------------------------------------------------
function Get-HeartbeatInterpreter {
    $heartbeat = Join-Path $ConfigDir 'agent.heartbeat'
    if (-not (Test-Path -LiteralPath $heartbeat -PathType Leaf)) { return '' }
    try {
        $json = [System.IO.File]::ReadAllText($heartbeat) | ConvertFrom-Json
    } catch { return '' }
    $prop = $json.PSObject.Properties['interpreter']
    if ($prop -and $prop.Value) { return ([string]$prop.Value).Trim() }
    return ''
}

function Get-PythonOnPath {
    # The first "python" on PATH is usually the Microsoft Store alias, so every
    # candidate is checked and the alias is skipped rather than accepted.
    foreach ($cmd in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($cmd -and $cmd.Source -and $cmd.Source -notlike "*$WindowsAppsMarker*" -and (Test-Path -LiteralPath $cmd.Source -PathType Leaf)) {
            return $cmd.Source
        }
    }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return ''
}

$InterpreterReason = '-Python'
if (-not (Test-File $Python)) {
    $Python = ''
    $fromLauncher = Get-LauncherInterpreter
    if ($fromLauncher) {
        $Python = $fromLauncher
        $InterpreterReason = 'the Startup launcher (WshShell.Run)'
    }
    if (-not (Test-File $Python)) {
        $Python = Get-HeartbeatInterpreter
        $InterpreterReason = 'the heartbeat'
    }
    if (-not (Test-File $Python)) {
        $Python = $env:MESH_PYTHON
        $InterpreterReason = 'MESH_PYTHON'
    }
    if (-not (Test-File $Python)) {
        $Python = Get-PythonOnPath
        $InterpreterReason = 'PATH'
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    Stop-WithConfigError 'no python.exe found: pass -Python, or re-run install.ps1 so the node pins a real interpreter'
}
if ($Python -like "*$WindowsAppsMarker*") {
    Stop-WithConfigError ('the resolved interpreter is the Microsoft Store alias: ' + $Python + ' - that alias never runs Python; re-run install.ps1')
}
if (-not (Test-File $Python)) {
    Stop-WithConfigError ('the resolved interpreter does not exist: ' + $Python)
}

Write-Note ('updater start: install=' + $InstallDir + ' (' + $PayloadReason + ') interpreter=' + $Python + ' (' + $InterpreterReason + ') mode=' + $(if ($Check) { 'check' } else { 'apply' }))

# --------------------------------------------------------------- run Python --
# stdout and stderr are redirected to files by cmd.exe, not captured by
# Start-Process: on Windows PowerShell 5.1 a process started that way reports an
# empty ExitCode once it has exited, and mixing stderr into stdout would break
# the JSON answer the updater prints.
$outFile = Join-Path $env:TEMP ('mesh-update-out-' + [Guid]::NewGuid().ToString('N') + '.json')
$errFile = Join-Path $env:TEMP ('mesh-update-err-' + [Guid]::NewGuid().ToString('N') + '.log')
if ($Check) { $mode = '--check' } else { $mode = '--apply' }
$arguments = @('-m', 'core.updater', $mode, '--json', '--dir', $InstallDir)
if ($Force) { $arguments += '--force' }
if ($Offline -and $Check) { $arguments += '--offline' }
if ($NoRestart -and -not $Check) { $arguments += '--no-restart' }

$quoted = @($arguments | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
$commandLine = '/c ""' + $Python + '" ' + $quoted + ' > "' + $outFile + '" 2> "' + $errFile + '""'
$exitCode = 3
try {
    $null = & cmd.exe $commandLine
    $exitCode = $LASTEXITCODE
} catch {
    Write-Note ('could not start the interpreter: ' + $_.Exception.Message)
    exit 3
}

$answer = ''
if (Test-Path -LiteralPath $outFile) {
    try { $answer = ([System.IO.File]::ReadAllText($outFile)).Trim() } catch { $answer = '' }
}
$errors = ''
if (Test-Path -LiteralPath $errFile) {
    try { $errors = ([System.IO.File]::ReadAllText($errFile)).Trim() } catch { $errors = '' }
}
Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue

$parsed = $null
if ($answer) {
    try { $parsed = $answer | ConvertFrom-Json } catch { $parsed = $null }
}

# The updater's own log lines (a traceback, a watchdog note) belong in the
# journal: they are the only trace when --apply fails before printing JSON.
if ($errors) {
    foreach ($line in @($errors -split "`r?`n")) {
        if ($line.Trim()) { Write-LogLine ('python: ' + $line.Trim()) }
    }
}

if ($parsed) {
    $current = [string]$parsed.current
    $latest = [string]$parsed.latest
    $reason = [string]$parsed.reason
    if ($Json) {
        # The updater's own answer, verbatim: scripts read this, so it is not
        # decorated with the human summary below.
        Write-Output $answer
    } else {
        if ($Check) {
            if ($exitCode -eq 2) {
                Write-Note ('update available: ' + $current + ' -> ' + $latest + ' (' + $reason + ')')
            } elseif ($exitCode -eq 0) {
                Write-Note ('up to date: ' + $current + ' (' + $reason + ')')
            } else {
                Write-Note ('check failed: ' + $reason)
            }
        } else {
            if ($exitCode -eq 0 -or $exitCode -eq 4) {
                Write-Note ('updated: ' + $reason + ' (exit ' + $exitCode + ')')
            } else {
                Write-Note ('update failed: ' + $reason)
            }
        }
        if (-not $script:QuietMode) {
            if ($latest) { Write-Host ('latest  : ' + $latest) }
            if ($reason) { Write-Host ('detail  : ' + $reason) }
        }
    }
} elseif (-not $script:QuietMode -and -not $Json) {
    Write-Host ('the updater produced no JSON answer (exit ' + $exitCode + ')')
    if ($errors) { Write-Host $errors }
}

if ($Check -and $exitCode -eq 0 -and $parsed -and $parsed.update_available) { exit 2 }
if ($exitCode -eq 2 -and -not $Check) { exit 2 }
exit $exitCode
