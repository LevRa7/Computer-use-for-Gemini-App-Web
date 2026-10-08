# ==============================================================================
#  Antigravity Mesh - Windows Visual Installer (WinForms wizard)
# ==============================================================================
#
#  WHY THIS FILE IS PURE ASCII
#
#  Windows PowerShell 5.1 decodes a script that has no UTF-8 BOM using the ANSI
#  code page. Russian text then turns into characters the parser reads as string
#  delimiters and the whole file fails to parse - exactly why install.ps1 carries
#  a BOM and why docs/WINDOWS_TESTING.md tells you not to normalise it. Keeping
#  this source ASCII-only makes the wizard parse correctly no matter how the file
#  was saved or transferred, and it moves every user-visible string into
#  install-gui.strings.json, which is read explicitly as UTF-8. That file is also
#  what makes the language switch a data change instead of a code change.
#
#  WHY IT CALLS install.ps1 INSTEAD OF REIMPLEMENTING IT
#
#  The wizard collects the same options as the console installer and then runs
#  install.ps1 with them. Node registration, the fail-closed check on an empty
#  token, the autostart entry and the shared-domain URL contract therefore stay in
#  one place, and the visual and console installers cannot drift apart.
#
#  SSH mode is the one exception: install.ps1 has no remote branch, so the wizard
#  drives ssh.exe/scp.exe the way install.sh --ssh does and runs install.sh on the
#  remote host, which is where the real work happens.
#
#  Usage:
#      powershell -NoProfile -ExecutionPolicy Bypass -Sta -File install-gui.ps1
#      install-gui.cmd                      (double-click launcher)
#      install-gui.ps1 -Lang ru             (start in Russian)
#      install-gui.ps1 -SelfTest            (headless self-check, no window)
# ==============================================================================

[CmdletBinding()]
param(
    # Start in this language instead of following the OS UI language.
    [ValidateSet('en', 'ru')]
    [string]$Lang = '',

    # Build every page in every language, exercise the process pipeline and the
    # URL parser, then exit. Used by tests/test_installer_gui.py.
    [switch]$SelfTest,

    # Install without opening a window: the same steps the wizard runs, their
    # output on stdout, and the resulting MCP URL as MESH_URL=<url>. This is what
    # makes the compiled installer verifiable end to end.
    #
    # Every name here is prefixed on purpose. At script scope "$script:Mode" and a
    # parameter "$Mode" are the SAME variable, so the initialiser for the wizard's
    # state would overwrite whatever the caller passed - the bug that once made
    # -Lang do nothing at all.
    [switch]$RunInstall,
    [ValidateSet('quick', 'custom', 'ssh')]
    [string]$InstallMode = 'quick',
    [string]$InstallUser = '',
    [string]$InstallGateway = '',
    [string]$InstallToken = '',
    [string]$InstallSshTarget = '',
    [int]$InstallSshPort = 22
)

$ErrorActionPreference = 'Continue'

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ------------------------------------------------------------------------------
#  Paths and constants
# ------------------------------------------------------------------------------

$ScriptPath = $null
if ($MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $ScriptPath = $MyInvocation.MyCommand.Path
}
$ScriptDir = $null
if ($ScriptPath) { $ScriptDir = Split-Path -Parent $ScriptPath }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
if (-not $ScriptPath) { $ScriptPath = Join-Path $ScriptDir 'install-gui.ps1' }

$StringsFile      = Join-Path $ScriptDir 'install-gui.strings.json'
$ConsoleInstaller = Join-Path $ScriptDir 'install.ps1'
$ShellInstaller   = Join-Path $ScriptDir 'install.sh'
$CoreDir          = Join-Path $ScriptDir 'core'
$SkillsDir        = Join-Path $ScriptDir 'skills'

$ConfigDir     = Join-Path $env:USERPROFILE '.config\antigravity-mesh'
$AgentEnvFile  = Join-Path $ConfigDir 'agent.env'
$AgentLogFile  = Join-Path $ConfigDir 'agent.log'
$StartupDir    = [Environment]::GetFolderPath('Startup')
$AutostartFile = Join-Path $StartupDir 'antigravity-agent.vbs'

$GeminiAppsUrl = 'https://gemini.google.com/spark/apps'

# The canonical shared-domain URL: one domain for every node, the node selected
# by ?user=. This pattern only reads back the URL install.ps1 printed; it never
# invents one. The gateway's own sse_url has exactly this shape (see
# tests/test_gateway_url_contract.py), which is what makes the agent.env
# fallback below faithful rather than a guess.
$UrlPattern = 'https?://[^\s|"'']+/sse[^\s|"'']*'

# ------------------------------------------------------------------------------
#  Tolerant readers
#
#  One session sees three encodings on Windows: PowerShell and cmd write the
#  console OEM code page, most programs write ANSI, and python/git/curl write
#  UTF-8. Decoding with the wrong one produces mojibake, so try UTF-8 strictly
#  first and fall back rather than trusting the locale. Same rule as
#  core/mcp_tools.py:_decode_output.
# ------------------------------------------------------------------------------

function Read-TextFileTolerant {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return '' }
    try { $bytes = [System.IO.File]::ReadAllBytes($Path) } catch { return '' }
    if ($bytes.Length -eq 0) { return '' }

    $text = $null
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $strict.GetString($bytes)
    } catch {
        $codePage = 0
        try { $codePage = [Console]::OutputEncoding.CodePage } catch { }
        if ($codePage -le 0) { $codePage = 866 }
        try { $text = [System.Text.Encoding]::GetEncoding($codePage).GetString($bytes) }
        catch { $text = [System.Text.Encoding]::Default.GetString($bytes) }
    }
    return ($text -replace "`r`n", "`n")
}

function Read-Utf8Json {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { throw 'file is empty' }
    $offset = 0
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $offset = 3
    }
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $strict.GetString($bytes, $offset, $bytes.Length - $offset)
    } catch {
        # Not valid UTF-8: an editor saved it as ANSI. Russian still has a chance
        # of being right under the system default code page.
        $text = [System.Text.Encoding]::Default.GetString($bytes, $offset, $bytes.Length - $offset)
    }
    return ($text | ConvertFrom-Json)
}


# ------------------------------------------------------------------------------
#  Localisation
# ------------------------------------------------------------------------------

$script:Strings = $null
$script:UiLang = 'en'

function Initialize-Strings {
    if (-not (Test-Path -LiteralPath $StringsFile)) {
        throw ('__STRINGS_MISSING__' + $StringsFile)
    }
    try {
        $script:Strings = Read-Utf8Json -Path $StringsFile
    } catch {
        throw ('__STRINGS_INVALID__' + $_.Exception.Message)
    }
    foreach ($code in @('en', 'ru')) {
        if (-not ($script:Strings.PSObject.Properties.Name -contains $code)) {
            throw ('__LANG_MISSING__' + $code)
        }
    }
}

function Get-UiText {
    param([string]$Key)
    $table = $script:Strings.($script:UiLang)
    if ($null -ne $table -and ($table.PSObject.Properties.Name -contains $Key)) {
        return [string]$table.$Key
    }
    $fallback = $script:Strings.en
    if ($null -ne $fallback -and ($fallback.PSObject.Properties.Name -contains $Key)) {
        return [string]$fallback.$Key
    }
    return $Key
}

# get text and apply {0}-style formatting in one step
function Format-UiText {
    param([string]$Key, [object[]]$Values)
    $template = Get-UiText -Key $Key
    if ($null -eq $Values -or $Values.Count -eq 0) { return $template }
    try { return ($template -f $Values) } catch { return $template }
}

# ------------------------------------------------------------------------------
#  Which language to start in
#
#  CultureInfo.CurrentUICulture is NOT a reliable answer on Windows. On the
#  machine this was developed on, Windows is installed and used in Russian
#  (PreferredUILanguages = ru-RU, InstalledUICulture = ru-RU, CurrentCulture =
#  ru-RU, Get-WinSystemLocale = ru-RU) while the process reports
#  CurrentUICulture = en-US, so a check against it alone started the wizard in
#  English. The user's own language list is the signal that matches what they
#  actually see, so it is read first and the process culture is only a fallback.
# ------------------------------------------------------------------------------

# One registry value holding a ';'-separated language list, split into entries.
function Get-RegistryLanguageList {
    param([string]$Path, [string]$Name)
    $values = New-Object System.Collections.ArrayList
    try {
        $raw = (Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop).$Name
    } catch {
        return $values.ToArray()
    }
    foreach ($entry in @($raw)) {
        foreach ($part in ("$entry" -split ';')) {
            $trimmed = $part.Trim()
            if ($trimmed) { [void]$values.Add($trimmed) }
        }
    }
    return $values.ToArray()
}

# Every language signal, most authoritative first.
function Get-PreferredLanguageCodes {
    $codes = New-Object System.Collections.ArrayList

    # 1. The Windows display language.
    foreach ($value in @(Get-RegistryLanguageList 'HKCU:\Control Panel\Desktop' 'PreferredUILanguages')) {
        [void]$codes.Add([string]$value)
    }
    # 2. The ordered list of preferred languages from Settings.
    foreach ($value in @(Get-RegistryLanguageList 'HKCU:\Control Panel\International\User Profile' 'Languages')) {
        [void]$codes.Add([string]$value)
    }
    # 3. The International module, only when the registry said nothing.
    if ($codes.Count -eq 0 -and (Get-Command 'Get-WinUserLanguageList' -ErrorAction SilentlyContinue)) {
        try {
            foreach ($item in (Get-WinUserLanguageList -ErrorAction Stop)) {
                if ($item.LanguageTag) { [void]$codes.Add([string]$item.LanguageTag) }
            }
        } catch { }
    }
    # 4. The process UI language, the OS install language, then the format.
    foreach ($culture in @(
            [System.Globalization.CultureInfo]::CurrentUICulture,
            [System.Globalization.CultureInfo]::InstalledUICulture,
            [System.Globalization.CultureInfo]::CurrentCulture)) {
        if ($culture -and $culture.Name) { [void]$codes.Add([string]$culture.Name) }
    }
    return $codes.ToArray()
}

# First code that this wizard can actually speak, else English.
function Select-Language {
    param([string[]]$Codes, [string[]]$Supported = @('en', 'ru'))
    foreach ($code in @($Codes)) {
        $short = ("$code" -split '-')[0].Trim().ToLower()
        if ($short -and ($Supported -contains $short)) { return $short }
    }
    return 'en'
}

function Get-DefaultLanguage {
    return (Select-Language -Codes @(Get-PreferredLanguageCodes))
}

# ------------------------------------------------------------------------------
#  Small helpers
# ------------------------------------------------------------------------------

function Quote-Arg {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-DeviceInfo {
    $rawHost = "$env:COMPUTERNAME"
    $hostName = ($rawHost.ToLower() -replace '[^a-z0-9_-]', '')
    $isLaptop = $false
    try {
        $isLaptop = [bool](Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue)
    } catch { }
    return @{
        Hostname = $hostName
        Raw      = $rawHost
        IsLaptop = $isLaptop
        Os       = 'Windows ' + [System.Environment]::OSVersion.Version.ToString()
    }
}

function Set-UrlClipboard {
    param([string]$Text)
    try { Set-Clipboard -Value $Text -ErrorAction Stop; return $true } catch { }
    try { [System.Windows.Forms.Clipboard]::SetText($Text); return $true } catch { }
    try { $Text | clip.exe 2>$null; return $true } catch { }
    return $false
}

function Start-Url {
    param([string]$Url)
    try { Start-Process $Url; return $true } catch { }
    try { [System.Diagnostics.Process]::Start($Url) | Out-Null; return $true } catch { }
    return $false
}

function Open-Folder {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try { Start-Process explorer.exe -ArgumentList (Quote-Arg $Path); return $true } catch { }
    return $false
}

# A domain value is "configured" when it is non-empty, carries no whitespace and
# is not the deployment placeholder. Both install.ps1's English "NOT CONFIGURED"
# and its Russian equivalent contain a space, so this test does not depend on the
# language the dry run was printed in.
function Test-DomainConfigured {
    param([string]$Value)
    $text = "$Value".Trim()
    if (-not $text) { return $false }
    if ($text -match '\s') { return $false }
    if ($text -eq '__MESH_DOMAIN__') { return $false }
    return $true
}

# ------------------------------------------------------------------------------
#  Process pipeline
#
#  A queue of external commands run one after another, with stdout/stderr
#  redirected straight to files and a WinForms timer polling them. The child
#  writes to files itself, so there is nothing to deadlock on and no second
#  runspace to marshal across - both of which a BeginOutputReadLine handler would
#  have needed under PowerShell 5.1.
# ------------------------------------------------------------------------------

$script:Headless = $false
$script:Timer = $null
$script:ScratchDir = $null

$script:Pipe = @{
    Steps       = @()
    Index       = -1
    Proc        = $null
    OutFile     = ''
    ErrFile     = ''
    Output      = ''
    Failed      = $false
    FailExit    = 0
    Running     = $false
    OnFinish    = $null
    LogBox      = $null
    StatusLabel = $null
}

function New-PipelineStep {
    param(
        [string]$File,
        [string]$Arguments,
        [string]$Label,
        [switch]$IgnoreExit
    )
    return @{
        File       = $File
        Arguments  = $Arguments
        Label      = $Label
        IgnoreExit = [bool]$IgnoreExit
    }
}

# Where the pipeline keeps the child's stdout/stderr and the self-test keeps its
# probes. %TEMP% is the obvious choice but not always a writable one: on a locked
# down machine, and inside a sandboxed folder, the write is refused and the child
# then produces nothing at all - which used to be reported as "no gateway domain
# configured". Each candidate is probed with a real write before it is used.
function Get-ScratchDirectory {
    if ($script:ScratchDir) { return $script:ScratchDir }

    $candidates = New-Object System.Collections.ArrayList
    if ($env:TEMP) { [void]$candidates.Add($env:TEMP) }
    [void]$candidates.Add((Join-Path $ScriptDir 'runtime'))
    if ($env:LOCALAPPDATA) {
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'AntigravityMesh\runtime'))
    }

    foreach ($candidate in $candidates) {
        try {
            if (-not (Test-Path -LiteralPath $candidate)) {
                [void](New-Item -ItemType Directory -Path $candidate -Force -ErrorAction Stop)
            }
            $probe = Join-Path $candidate ('write-probe-' + [Guid]::NewGuid().ToString('N') + '.tmp')
            Set-Content -LiteralPath $probe -Value '' -Encoding ASCII -ErrorAction Stop
            Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
            $script:ScratchDir = $candidate
            return $candidate
        } catch {
            continue
        }
    }

    # Nothing worked; return the first choice so the failure names a real path.
    $script:ScratchDir = $candidates[0]
    return $script:ScratchDir
}

function Reset-Pipeline {
    param(
        [object[]]$Steps,
        [scriptblock]$OnFinish,
        $LogBox = $null,
        $StatusLabel = $null
    )
    $script:Pipe.Steps = @($Steps)
    $script:Pipe.Index = -1
    $script:Pipe.Output = ''
    $script:Pipe.Failed = $false
    $script:Pipe.FailExit = 0
    $script:Pipe.Running = $true
    $script:Pipe.OnFinish = $OnFinish
    $script:Pipe.LogBox = $LogBox
    $script:Pipe.StatusLabel = $StatusLabel
    $script:Pipe.Proc = $null

    $stamp = [Guid]::NewGuid().ToString('N')
    $scratch = Get-ScratchDirectory
    $script:Pipe.OutFile = Join-Path $scratch ('mesh-gui-out-' + $stamp + '.log')
    $script:Pipe.ErrFile = Join-Path $scratch ('mesh-gui-err-' + $stamp + '.log')
    try {
        Set-Content -LiteralPath $script:Pipe.OutFile -Value '' -Encoding ASCII
        Set-Content -LiteralPath $script:Pipe.ErrFile -Value '' -Encoding ASCII
    } catch { }

    Start-NextPipelineStep
}

function Start-NextPipelineStep {
    $script:Pipe.Index++
    if ($script:Pipe.Index -ge $script:Pipe.Steps.Count) {
        Complete-Pipeline
        return
    }
    $step = $script:Pipe.Steps[$script:Pipe.Index]
    if ($script:Pipe.StatusLabel) {
        try { $script:Pipe.StatusLabel.Text = $step.Label } catch { }
    }
    $script:Pipe.Output += ('--- ' + $step.Label + "`n")

    # cmd.exe applies the redirection, so the child writes straight to the files.
    # Start-Process -RedirectStandardOutput captures output just as well, but the
    # Process object it hands back has already lost the handle: its ExitCode comes
    # back empty (measured on Windows PowerShell 5.1), and an installer that
    # cannot tell success from failure is worse than useless. Creating the Process
    # through ProcessStartInfo keeps ExitCode readable, and CreateNoWindow keeps
    # the console from flashing.
    $inner = (Quote-Arg $step.File) + ' ' + $step.Arguments
    $cmdLine = '/c "' + $inner + ' > "' + $script:Pipe.OutFile + '" 2> "' + $script:Pipe.ErrFile + '""'

    $proc = $null
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $env:ComSpec
        $startInfo.Arguments = $cmdLine
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($startInfo)
    } catch {
        $script:Pipe.Failed = $true
        $script:Pipe.FailExit = -1
        $script:Pipe.Output += ('[launch failed] ' + $_.Exception.Message + "`n")
        Complete-Pipeline
        return
    }
    $script:Pipe.Proc = $proc
    if ($script:Timer -and -not $script:Headless) { $script:Timer.Start() }
}

function Set-LogBoxText {
    param($Box, [string]$Text)
    if (-not $Box) { return }
    try {
        $Box.Text = $Text
        $Box.SelectionStart = $Box.TextLength
        $Box.ScrollToCaret()
    } catch { }
}

function Update-Pipeline {
    if (-not $script:Pipe.Running) { return }

    $out = Read-TextFileTolerant -Path $script:Pipe.OutFile
    $err = Read-TextFileTolerant -Path $script:Pipe.ErrFile
    $live = $out
    if ($err) { $live = $out + $err }

    if ($live -ne $script:Pipe.Output) {
        $script:Pipe.Output = $live
        Set-LogBoxText -Box $script:Pipe.LogBox -Text $live
    }

    $proc = $script:Pipe.Proc
    if ($null -eq $proc) { return }
    $exited = $false
    try { $exited = $proc.HasExited } catch { $exited = $true }
    if (-not $exited) { return }

    # The process has exited but its handles may not have flushed yet: read once,
    # give the files a moment, then keep whichever read is longer.
    $first = Read-TextFileTolerant -Path $script:Pipe.OutFile
    $firstErr = Read-TextFileTolerant -Path $script:Pipe.ErrFile
    Start-Sleep -Milliseconds 120
    $second = Read-TextFileTolerant -Path $script:Pipe.OutFile
    $secondErr = Read-TextFileTolerant -Path $script:Pipe.ErrFile
    if ($second.Length -lt $first.Length) { $second = $first }
    if ($secondErr.Length -lt $firstErr.Length) { $secondErr = $firstErr }

    $final = $second
    if ($secondErr) { $final = $second + $secondErr }
    $script:Pipe.Output = $final
    Set-LogBoxText -Box $script:Pipe.LogBox -Text $final

    $code = 0
    try { $code = $proc.ExitCode } catch { $code = -1 }
    try { $proc.Dispose() } catch { }
    $script:Pipe.Proc = $null

    $step = $script:Pipe.Steps[$script:Pipe.Index]
    if ($code -ne 0 -and -not $step.IgnoreExit) {
        $script:Pipe.Failed = $true
        $script:Pipe.FailExit = $code
        Complete-Pipeline
        return
    }
    Start-NextPipelineStep
}

function Complete-Pipeline {
    $script:Pipe.Running = $false
    if ($script:Timer -and -not $script:Headless) { try { $script:Timer.Stop() } catch { } }
    if ($script:Pipe.Proc) {
        try { $script:Pipe.Proc.Dispose() } catch { }
        $script:Pipe.Proc = $null
    }
    foreach ($path in @($script:Pipe.OutFile, $script:Pipe.ErrFile)) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            try { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
    $onFinish = $script:Pipe.OnFinish
    if ($onFinish) {
        & $onFinish $script:Pipe.Failed $script:Pipe.FailExit $script:Pipe.Output
    }
}

function Stop-Pipeline {
    $proc = $script:Pipe.Proc
    if ($proc) {
        try {
            if (-not $proc.HasExited) {
                # /T matters: install.ps1 spawns python children, and killing only
                # the shell would leave the agent behind.
                & taskkill.exe /T /F /PID $proc.Id 2>$null | Out-Null
            }
        } catch { }
    }
    $script:Pipe.Running = $false
    if ($script:Timer -and -not $script:Headless) { try { $script:Timer.Stop() } catch { } }
}

# ------------------------------------------------------------------------------
#  Reading install.ps1's output back
# ------------------------------------------------------------------------------

# install.ps1 -DryRun prints six "[DRY-RUN] <label> : <value>" lines in a fixed
# order (Python, websockets, domain, config, autostart, node). The labels are
# localised, so this reads them positionally instead of matching on the label.
function Get-PreflightFacts {
    param([string]$Text)
    $facts = @{ Python = ''; Websockets = ''; Domain = ''; Config = ''; Autostart = ''; Node = '' }
    $values = New-Object System.Collections.ArrayList
    foreach ($line in ($Text -split "`n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed.StartsWith('[DRY-RUN]')) { continue }
        $match = [regex]::Match($trimmed, '^\[DRY-RUN\]\s+[^:]+:\s*(.*)$')
        if ($match.Success) { [void]$values.Add($match.Groups[1].Value.Trim()) }
    }
    if ($values.Count -ge 1) { $facts.Python = $values[0] }
    if ($values.Count -ge 2) { $facts.Websockets = $values[1] }
    if ($values.Count -ge 3) { $facts.Domain = $values[2] }
    if ($values.Count -ge 4) { $facts.Config = $values[3] }
    if ($values.Count -ge 5) { $facts.Autostart = $values[4] }
    # An older install.ps1 copy prints five lines: Node stays empty and the report
    # simply omits that row.
    if ($values.Count -ge 6) { $facts.Node = $values[5] }
    return $facts
}

# The URL install.ps1 printed, or - when that line is unreadable - the canonical
# URL rebuilt from agent.env.
function Get-ResultUrl {
    param(
        [string]$Text,
        [string]$Mode,
        [string]$AgentEnvPath = ''
    )
    if (-not $AgentEnvPath) { $AgentEnvPath = $AgentEnvFile }

    $found = [regex]::Matches($Text, $UrlPattern)
    if ($found.Count -gt 0) {
        return $found[$found.Count - 1].Value
    }

    if ($Mode -eq 'ssh') { return '' }

    $values = @{}
    $raw = Read-TextFileTolerant -Path $AgentEnvPath
    foreach ($line in ($raw -split "`n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $index = $trimmed.IndexOf('=')
        $values[$trimmed.Substring(0, $index).Trim()] = $trimmed.Substring($index + 1).Trim()
    }
    $gateway = "$($values['MESH_GATEWAY'])".Trim()
    $user = "$($values['MESH_USER'])".Trim()
    $token = "$($values['MESH_TOKEN'])".Trim()
    if ($gateway -and $user -and $token) {
        return ('https://' + $gateway + '/sse?user=' + $user + '&token=' + $token)
    }
    return ''
}

# ------------------------------------------------------------------------------
#  Command construction
# ------------------------------------------------------------------------------

function Get-PowerShellExe {
    $candidate = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return 'powershell.exe'
}

function New-ConsoleInstallerSteps {
    param([string]$Mode, [string]$Node, [string]$Domain, [string]$Token)

    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Arg $ConsoleInstaller),
        '-Lang', $script:UiLang)
    if ($Mode -eq 'custom') {
        $argList += @('-Mode', 'tunnel')
        if ($Node) { $argList += @('-User', (Quote-Arg $Node)) }
        if ($Domain) { $argList += @('-Gateway', (Quote-Arg $Domain)) }
        if ($Token) { $argList += @('-Token', (Quote-Arg $Token)) }
    }
    return @(New-PipelineStep -File (Get-PowerShellExe) -Arguments ($argList -join ' ') `
        -Label (Get-UiText -Key 'prog_running'))
}

function New-SshSteps {
    param([string]$Target, [int]$Port, [string]$Domain)

    $steps = New-Object System.Collections.ArrayList
    $sshOptions = @('-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes')
    $scpOptions = @('-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes')
    $remoteRoot = '~/antigravity-mesh'

    # 1. create the destination tree. ssh joins the trailing arguments with
    #    spaces and hands the result to the remote shell, so ~ expands remotely.
    # Quoted for cmd.exe as well as for ssh: unquoted, the local command
    # interpreter would treat the remote command's metacharacters as its own.
    $mkdirCmd = 'mkdir -p ' + $remoteRoot + '/core ' + $remoteRoot + '/skills'
    $mkdirArgs = $sshOptions + @('-p', "$Port", $Target, (Quote-Arg $mkdirCmd))
    [void]$steps.Add((New-PipelineStep -File 'ssh.exe' -Arguments ($mkdirArgs -join ' ') `
        -Label 'ssh: prepare remote directories'))

    # 2. core/ - top-level .py files only. install.sh ships core/* verbatim, but a
    #    __pycache__ built for another interpreter is useless on the far side.
    if (Test-Path -LiteralPath $CoreDir) {
        $coreFiles = @(Get-ChildItem -LiteralPath $CoreDir -File -Filter '*.py' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
        if ($coreFiles.Count -gt 0) {
            $scpCore = $scpOptions + @('-P', "$Port") +
                @($coreFiles | ForEach-Object { Quote-Arg $_ }) +
                @("${Target}:$remoteRoot/core/")
            [void]$steps.Add((New-PipelineStep -File 'scp.exe' -Arguments ($scpCore -join ' ') -Label 'scp: core/*'))
        }
    }

    # 3. install.sh - the remote side needs it to do the actual installation
    $scpShell = $scpOptions + @('-P', "$Port", (Quote-Arg $ShellInstaller), "${Target}:$remoteRoot/")
    [void]$steps.Add((New-PipelineStep -File 'scp.exe' -Arguments ($scpShell -join ' ') -Label 'scp: install.sh'))

    # 4. skills/ - optional, exactly as install.sh treats it
    if (Test-Path -LiteralPath $SkillsDir) {
        $skillFiles = @(Get-ChildItem -LiteralPath $SkillsDir -File -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
        if ($skillFiles.Count -gt 0) {
            $scpSkills = $scpOptions + @('-P', "$Port") +
                @($skillFiles | ForEach-Object { Quote-Arg $_ }) +
                @("${Target}:$remoteRoot/skills/")
            [void]$steps.Add((New-PipelineStep -File 'scp.exe' -Arguments ($scpSkills -join ' ') -Label 'scp: skills/*'))
        }
    }

    # 5. run the installer on the remote host. install.sh resolves the shared
    #    domain itself (core/domain.py travels with it); --domain only overrides.
    $remoteCmd = 'cd ' + $remoteRoot + ' && bash install.sh --quick --lang=' + $script:UiLang
    if ($Domain) { $remoteCmd += ' --domain=' + $Domain }
    $runArgs = $sshOptions + @('-p', "$Port", $Target, (Quote-Arg $remoteCmd))
    [void]$steps.Add((New-PipelineStep -File 'ssh.exe' -Arguments ($runArgs -join ' ') -Label 'ssh: run install.sh'))

    return $steps.ToArray()
}

function Get-CommandPreview {
    param([string]$Mode, [string]$Node, [string]$Domain, [string]$Token, [string]$Target, [int]$Port)

    if ($Mode -eq 'quick' -or $Mode -eq 'custom') {
        # @() matters: PowerShell unrolls a function's return value, so a single
        # step would arrive as a bare hashtable and $steps[0] would then be read
        # as a key lookup that finds nothing.
        $steps = @(New-ConsoleInstallerSteps -Mode $Mode -Node $Node -Domain $Domain -Token $Token)
        return ($steps[0].File + ' ' + $steps[0].Arguments)
    }
    $lines = New-Object System.Collections.ArrayList
    foreach ($step in (New-SshSteps -Target $Target -Port $Port -Domain $Domain)) {
        [void]$lines.Add(($step.File + ' ' + $step.Arguments))
    }
    return ($lines -join "`r`n")
}

# ------------------------------------------------------------------------------
#  UI state
# ------------------------------------------------------------------------------

$script:Form = $null
$script:Content = $null
$script:HeaderTitle = $null
$script:LangBox = $null
$script:StepLabel = $null
$script:BtnBack = $null
$script:BtnPrimary = $null
$script:BtnClose = $null
$script:Device = @{ Hostname = ''; Raw = ''; IsLaptop = $false; Os = '' }

$script:Page = 0
$script:Mode = 'quick'
$script:Node = ''
$script:Domain = ''
$script:Token = ''
$script:SshTarget = ''
$script:SshPort = 22

$script:Preflight = @{ Running = $false; Done = $false; Text = ''; Facts = $null; Problems = $false; Parsed = $false }
$script:Install = @{ Running = $false; Failed = $false; Exit = 0; Output = ''; Url = ''; Notice = ''; LinkNotice = '' }
$script:OptionError = ''

function New-Label {
    param([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H, [switch]$Bold, [switch]$Dim)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($W, $H)
    if ($Bold) { $label.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold) }
    if ($Dim) { $label.ForeColor = [System.Drawing.Color]::FromArgb(90, 90, 90) }
    return $label
}

function New-GroupBox {
    param([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H)
    $group = New-Object System.Windows.Forms.GroupBox
    $group.Text = $Text
    $group.Location = New-Object System.Drawing.Point($X, $Y)
    $group.Size = New-Object System.Drawing.Size($W, $H)
    return $group
}

function New-ReadOnlyBox {
    param([int]$X, [int]$Y, [int]$W, [int]$H, [switch]$MultiLine, [switch]$Monospace)
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point($X, $Y)
    $box.Size = New-Object System.Drawing.Size($W, $H)
    $box.ReadOnly = $true
    $box.BackColor = [System.Drawing.Color]::White
    if ($MultiLine) {
        $box.Multiline = $true
        $box.ScrollBars = [System.Windows.Forms.ScrollBars]::Both
        $box.WordWrap = $false
    }
    if ($Monospace) {
        $box.Font = New-Object System.Drawing.Font('Consolas', 9)
    }
    return $box
}

function New-InputBox {
    param([int]$X, [int]$Y, [int]$W, [string]$Text, [scriptblock]$OnChange)
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point($X, $Y)
    $box.Size = New-Object System.Drawing.Size($W, 23)
    $box.Text = $Text
    if ($OnChange) { $box.Add_TextChanged($OnChange) }
    return $box
}

function Set-Language {
    param([string]$Code)

    if ($Code -eq $script:UiLang) { return }
    $script:UiLang = $Code
    if ($script:Form) { $script:Form.Text = Get-UiText -Key 'app_title' }
    if ($script:HeaderTitle) { $script:HeaderTitle.Text = Get-UiText -Key 'app_title' }
    Show-Page -Index $script:Page
}

# ---- page 0: welcome ---------------------------------------------------------

function Build-WelcomePage {
    $script:Content.Controls.Add((New-Label -Text (Get-UiText -Key 'welcome_intro') -X 16 -Y 10 -W 760 -H 34))

    $device = New-GroupBox -Text (Get-UiText -Key 'device_group') -X 16 -Y 50 -W 760 -H 100
    $typeKey = 'device_desktop'
    if ($script:Device.IsLaptop) { $typeKey = 'device_laptop' }
    $rows = @(
        @((Get-UiText -Key 'device_hostname'), $script:Device.Raw),
        @((Get-UiText -Key 'device_type'), (Get-UiText -Key $typeKey)),
        @((Get-UiText -Key 'device_os'), $script:Device.Os)
    )
    $y = 24
    foreach ($row in $rows) {
        $device.Controls.Add((New-Label -Text ($row[0] + ':') -X 14 -Y $y -W 120 -H 20))
        $device.Controls.Add((New-Label -Text $row[1] -X 140 -Y $y -W 600 -H 20))
        $y += 22
    }
    $script:Content.Controls.Add($device)

    $preflight = New-GroupBox -Text (Get-UiText -Key 'preflight_group') -X 16 -Y 160 -W 760 -H 300
    $box = New-ReadOnlyBox -X 14 -Y 22 -W 732 -H 266 -MultiLine -Monospace
    if ($script:Preflight.Done) {
        $box.Text = $script:Preflight.Text
    } else {
        $box.Text = Get-UiText -Key 'preflight_running'
    }
    $preflight.Controls.Add($box)
    $script:Content.Controls.Add($preflight)

    # Gated on Parsed: when the preflight could not run, the report above already
    # says so, and an extra "no domain configured" would be a wrong diagnosis.
    if ($script:Preflight.Done -and $script:Preflight.Parsed) {
        if (-not (Test-DomainConfigured -Value $script:Preflight.Facts.Domain)) {
            $hint = New-Label -Text (Get-UiText -Key 'preflight_no_domain') -X 20 -Y 466 -W 752 -H 34
            $hint.ForeColor = [System.Drawing.Color]::FromArgb(176, 96, 0)
            $script:Content.Controls.Add($hint)
        }
    }
}

# ---- page 1: mode ------------------------------------------------------------

function Build-ModePage {
    $script:Content.Controls.Add((New-Label -Text (Get-UiText -Key 'mode_prompt') -X 16 -Y 12 -W 760 -H 24))

    $group = New-GroupBox -Text (Get-UiText -Key 'page_mode_title') -X 16 -Y 44 -W 760 -H 220
    $modes = @(
        @{ Id = 'quick';  Title = 'mode_quick_title';  Desc = 'mode_quick_desc' },
        @{ Id = 'custom'; Title = 'mode_custom_title'; Desc = 'mode_custom_desc' },
        @{ Id = 'ssh';    Title = 'mode_ssh_title';    Desc = 'mode_ssh_desc' }
    )
    $y = 26
    foreach ($mode in $modes) {
        $radio = New-Object System.Windows.Forms.RadioButton
        $radio.Text = Get-UiText -Key $mode.Title
        $radio.Location = New-Object System.Drawing.Point(16, $y)
        $radio.Size = New-Object System.Drawing.Size(720, 22)
        $radio.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
        $radio.Tag = $mode.Id
        if ($script:Mode -eq $mode.Id) { $radio.Checked = $true }
        # Attached after Checked is set, so building the page cannot look like a
        # user click and overwrite the selected mode.
        $radio.Add_CheckedChanged({
            if ($this.Checked) {
                $script:Mode = [string]$this.Tag
                $script:OptionError = ''
            }
        })
        $group.Controls.Add($radio)
        $group.Controls.Add((New-Label -Text (Get-UiText -Key $mode.Desc) -X 36 -Y ($y + 22) -W 700 -H 20 -Dim))
        $y += 62
    }
    $script:Content.Controls.Add($group)
}

# ---- page 2: options ---------------------------------------------------------

function Build-OptionsPage {
    if ($script:Mode -eq 'quick') {
        $group = New-GroupBox -Text (Get-UiText -Key 'opt_quick_group') -X 16 -Y 12 -W 760 -H 120
        $domain = "$($script:Domain)".Trim()
        if (-not $domain) { $domain = '- (resolved by install.ps1)' }
        $summary = New-ReadOnlyBox -X 14 -Y 24 -W 732 -H 84 -MultiLine -Monospace
        $summary.Text = (Format-UiText -Key 'opt_quick_summary' -Values @($script:Node, $domain))
        $group.Controls.Add($summary)
        $script:Content.Controls.Add($group)
    }

    if ($script:Mode -eq 'custom') {
        $group = New-GroupBox -Text (Get-UiText -Key 'page_options_title') -X 16 -Y 12 -W 760 -H 230

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_node_label') + ':') -X 16 -Y 32 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 29 -W 520 -Text $script:Node -OnChange {
            $script:Node = $this.Text; $script:OptionError = ''
        }))
        $group.Controls.Add((New-Label -Text (Get-UiText -Key 'opt_node_hint') -X 220 -Y 54 -W 520 -H 18 -Dim))

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_domain_label') + ':') -X 16 -Y 90 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 87 -W 520 -Text $script:Domain -OnChange {
            $script:Domain = $this.Text; $script:OptionError = ''
        }))
        $group.Controls.Add((New-Label -Text (Get-UiText -Key 'opt_domain_hint') -X 220 -Y 112 -W 520 -H 18 -Dim))

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_token_label') + ':') -X 16 -Y 148 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 145 -W 520 -Text $script:Token -OnChange {
            $script:Token = $this.Text; $script:OptionError = ''
        }))
        $group.Controls.Add((New-Label -Text (Get-UiText -Key 'opt_token_hint') -X 220 -Y 170 -W 520 -H 18 -Dim))

        $script:Content.Controls.Add($group)
    }

    if ($script:Mode -eq 'ssh') {
        $group = New-GroupBox -Text (Get-UiText -Key 'page_options_title') -X 16 -Y 12 -W 760 -H 210

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_ssh_target_label') + ':') -X 16 -Y 32 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 29 -W 520 -Text $script:SshTarget -OnChange {
            $script:SshTarget = $this.Text; $script:OptionError = ''
        }))
        $group.Controls.Add((New-Label -Text (Get-UiText -Key 'opt_ssh_target_hint') -X 220 -Y 54 -W 520 -H 18 -Dim))

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_ssh_port_label') + ':') -X 16 -Y 90 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 87 -W 120 -Text "$($script:SshPort)" -OnChange {
            $parsed = 0
            if ([int]::TryParse($this.Text.Trim(), [ref]$parsed)) { $script:SshPort = $parsed }
            $script:OptionError = ''
        }))

        $group.Controls.Add((New-Label -Text ((Get-UiText -Key 'opt_ssh_domain_label') + ':') -X 16 -Y 126 -W 200 -H 20))
        $group.Controls.Add((New-InputBox -X 220 -Y 123 -W 520 -Text $script:Domain -OnChange {
            $script:Domain = $this.Text; $script:OptionError = ''
        }))

        $group.Controls.Add((New-Label -Text (Format-UiText -Key 'opt_ssh_note' -Values @($script:UiLang)) `
            -X 16 -Y 162 -W 730 -H 20 -Dim))
        $script:Content.Controls.Add($group)
    }

    $preview = New-GroupBox -Text (Get-UiText -Key 'opt_summary_group') -X 16 -Y 252 -W 760 -H 210
    $box = New-ReadOnlyBox -X 14 -Y 22 -W 732 -H 176 -MultiLine -Monospace
    $box.Text = Get-CommandPreview -Mode $script:Mode -Node $script:Node -Domain $script:Domain `
        -Token $script:Token -Target $script:SshTarget -Port $script:SshPort
    $preview.Controls.Add($box)
    $script:Content.Controls.Add($preview)

    if ($script:OptionError) {
        $errorLabel = New-Label -Text $script:OptionError -X 20 -Y 470 -W 750 -H 22
        $errorLabel.ForeColor = [System.Drawing.Color]::FromArgb(176, 0, 0)
        $script:Content.Controls.Add($errorLabel)
    }
}

# ---- page 3: progress --------------------------------------------------------

function Build-ProgressPage {
    $status = New-Label -Text (Get-UiText -Key 'prog_running') -X 16 -Y 12 -W 760 -H 22
    $status.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $script:Content.Controls.Add($status)

    $bar = New-Object System.Windows.Forms.ProgressBar
    $bar.Location = New-Object System.Drawing.Point(16, 38)
    $bar.Size = New-Object System.Drawing.Size(760, 16)
    $bar.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
    $bar.MarqueeAnimationSpeed = 30
    $script:Content.Controls.Add($bar)

    $script:Content.Controls.Add((New-Label -Text (Get-UiText -Key 'prog_log') -X 16 -Y 64 -W 760 -H 20))
    $log = New-ReadOnlyBox -X 16 -Y 86 -W 760 -H 372 -MultiLine -Monospace
    $log.Text = $script:Install.Output
    $script:Content.Controls.Add($log)

    $script:Pipe.LogBox = $log
    $script:Pipe.StatusLabel = $status
}

# ---- page 4: result ----------------------------------------------------------

function Build-ResultPage {
    $failed = $script:Install.Failed
    $titleKey = 'res_success_title'
    if ($failed) { $titleKey = 'res_fail_title' }
    $title = New-Label -Text (Get-UiText -Key $titleKey) -X 16 -Y 10 -W 760 -H 26 -Bold
    if ($failed) {
        $title.ForeColor = [System.Drawing.Color]::FromArgb(176, 0, 0)
    } else {
        $title.ForeColor = [System.Drawing.Color]::FromArgb(0, 120, 60)
    }
    $script:Content.Controls.Add($title)

    $url = $script:Install.Url

    $urlGroup = New-GroupBox -Text (Get-UiText -Key 'res_url_group') -X 16 -Y 42 -W 760 -H 96
    $urlBox = New-Object System.Windows.Forms.TextBox
    $urlBox.Location = New-Object System.Drawing.Point(14, 26)
    $urlBox.Size = New-Object System.Drawing.Size(600, 23)
    $urlBox.ReadOnly = $true
    $urlBox.BackColor = [System.Drawing.Color]::White
    $urlBox.Font = New-Object System.Drawing.Font('Consolas', 9)
    if ($url) { $urlBox.Text = $url } else { $urlBox.Text = Get-UiText -Key 'res_url_missing' }
    $urlGroup.Controls.Add($urlBox)

    $copy = New-Object System.Windows.Forms.Button
    $copy.Text = Get-UiText -Key 'btn_copy'
    # Sizes itself: the Russian label is longer than the English one, and a fixed
    # width clipped it.
    $copy.AutoSize = $true
    $copy.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
    $copy.MinimumSize = New-Object System.Drawing.Size(122, 26)
    $copy.Enabled = [bool]$url
    $copy.Add_Click({
        if (-not $script:Install.Url) { return }
        if (Set-UrlClipboard -Text $script:Install.Url) {
            $script:Install.Notice = Get-UiText -Key 'res_copied'
        } else {
            $script:Install.Notice = Get-UiText -Key 'res_copy_failed'
        }
        Show-Page -Index 4
    })
    $urlGroup.Controls.Add($copy)
    $copy.Location = New-Object System.Drawing.Point((746 - $copy.Width), 24)

    $notice = "$($script:Install.Notice)"
    $noticeLabel = New-Label -Text $notice -X 16 -Y 56 -W 730 -H 20
    if ($notice) { $noticeLabel.ForeColor = [System.Drawing.Color]::FromArgb(0, 120, 60) }
    $urlGroup.Controls.Add($noticeLabel)

    if ($script:Mode -eq 'ssh' -and -not $failed) {
        $urlGroup.Controls.Add((New-Label -Text (Get-UiText -Key 'res_remote_note') -X 16 -Y 74 -W 730 -H 18 -Dim))
    }
    $script:Content.Controls.Add($urlGroup)

    $stepsGroup = New-GroupBox -Text (Get-UiText -Key 'res_steps_title') -X 16 -Y 146 -W 760 -H 126
    $stepsGroup.Controls.Add((New-Label -Text (Get-UiText -Key 'res_step1_prefix') -X 16 -Y 24 -W 250 -H 20))

    # A real link, not a label: clicking it opens the page, and the button beside
    # it puts the address on the clipboard. A plain label could do neither.
    $link = New-Object System.Windows.Forms.LinkLabel
    $link.Text = $GeminiAppsUrl
    $link.Location = New-Object System.Drawing.Point(268, 24)
    $link.Size = New-Object System.Drawing.Size(310, 20)
    $link.Add_LinkClicked({ [void](Start-Url -Url $GeminiAppsUrl) })
    $stepsGroup.Controls.Add($link)

    $copyLink = New-Object System.Windows.Forms.Button
    $copyLink.Text = Get-UiText -Key 'btn_copy_link'
    $copyLink.AutoSize = $true
    $copyLink.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
    $copyLink.MinimumSize = New-Object System.Drawing.Size(112, 26)
    $copyLink.Add_Click({
        if (Set-UrlClipboard -Text $GeminiAppsUrl) {
            $script:Install.LinkNotice = Get-UiText -Key 'res_copied'
        } else {
            $script:Install.LinkNotice = Get-UiText -Key 'res_copy_failed'
        }
        Show-Page -Index 4
    })
    $stepsGroup.Controls.Add($copyLink)
    $copyLink.Location = New-Object System.Drawing.Point((746 - $copyLink.Width), 21)

    $linkNotice = "$($script:Install.LinkNotice)"
    $linkNoticeLabel = New-Label -Text $linkNotice -X 16 -Y 94 -W 720 -H 20
    if ($linkNotice) { $linkNoticeLabel.ForeColor = [System.Drawing.Color]::FromArgb(0, 120, 60) }
    $stepsGroup.Controls.Add($linkNoticeLabel)

    $stepsGroup.Controls.Add((New-Label -Text (Get-UiText -Key 'res_step2') -X 16 -Y 46 -W 730 -H 20))
    $stepsGroup.Controls.Add((New-Label -Text (Get-UiText -Key 'res_step3') -X 16 -Y 68 -W 730 -H 20))
    $script:Content.Controls.Add($stepsGroup)

    if ($failed) {
        $hint = New-Label -Text (Get-UiText -Key 'res_fail_hint') -X 20 -Y 278 -W 752 -H 40
        $hint.ForeColor = [System.Drawing.Color]::FromArgb(176, 96, 0)
        $script:Content.Controls.Add($hint)
    } else {
        $configGroup = New-GroupBox -Text (Get-UiText -Key 'res_config_group') -X 16 -Y 278 -W 760 -H 110
        $rows = @()
        if ($script:Mode -eq 'ssh') {
            $rows = @(@((Get-UiText -Key 'res_config_file'), '~/antigravity-mesh (remote)'))
        } else {
            $rows = @(
                @((Get-UiText -Key 'res_config_file'), $AgentEnvFile),
                @((Get-UiText -Key 'res_autostart_file'), $AutostartFile),
                @((Get-UiText -Key 'res_agent_log'), $AgentLogFile)
            )
        }
        $y = 24
        foreach ($row in $rows) {
            $configGroup.Controls.Add((New-Label -Text ($row[0] + ':') -X 14 -Y $y -W 130 -H 20))
            $configGroup.Controls.Add((New-Label -Text $row[1] -X 150 -Y $y -W 460 -H 20 -Dim))
            $y += 24
        }
        $openConfig = New-Object System.Windows.Forms.Button
        $openConfig.Text = Get-UiText -Key 'btn_open_config'
        $openConfig.AutoSize = $true
        $openConfig.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
        $openConfig.MinimumSize = New-Object System.Drawing.Size(128, 26)
        $openConfig.Add_Click({ [void](Open-Folder -Path $ConfigDir) })
        $configGroup.Controls.Add($openConfig)
        $openConfig.Location = New-Object System.Drawing.Point((746 - $openConfig.Width), 74)
        $script:Content.Controls.Add($configGroup)
    }

    $logGroup = New-GroupBox -Text (Get-UiText -Key 'res_log_group') -X 16 -Y 396 -W 760 -H 84
    $log = New-ReadOnlyBox -X 14 -Y 20 -W 732 -H 56 -MultiLine -Monospace
    $log.Text = $script:Install.Output
    $logGroup.Controls.Add($log)
    $script:Content.Controls.Add($logGroup)
}

# ---- navigation --------------------------------------------------------------

# Buttons grow with their label, so the footer is positioned from the right edge
# instead of at fixed coordinates: "Open Gemini Apps" is far wider than "Next >",
# and the Russian labels are wider still.
function Format-FooterButtons {
    $right = $script:Form.ClientSize.Width - 16
    foreach ($button in @($script:BtnClose, $script:BtnPrimary, $script:BtnBack)) {
        $right = $right - $button.Width
        $button.Location = New-Object System.Drawing.Point($right, 14)
        $right = $right - 8
    }
}

function Show-Page {
    param([int]$Index)

    $script:Page = $Index
    $script:Content.Controls.Clear()
    $script:Pipe.LogBox = $null
    $script:Pipe.StatusLabel = $null

    $script:BtnBack.Visible = $false
    # Re-applied on every page: the label was set once in Initialize-Form, so a
    # runtime language switch left this button in the previous language.
    $script:BtnBack.Text = Get-UiText -Key 'btn_back'
    $script:BtnPrimary.Visible = $true
    $script:BtnPrimary.Enabled = $true
    $script:BtnClose.Text = Get-UiText -Key 'btn_close'

    switch ($Index) {
        0 {
            $script:StepLabel.Text = Format-UiText -Key 'step_indicator' -Values @(1, 3)
            Build-WelcomePage
            $script:BtnPrimary.Text = Get-UiText -Key 'btn_next'
        }
        1 {
            $script:StepLabel.Text = Format-UiText -Key 'step_indicator' -Values @(2, 3)
            $script:BtnBack.Visible = $true
            Build-ModePage
            $script:BtnPrimary.Text = Get-UiText -Key 'btn_next'
        }
        2 {
            $script:StepLabel.Text = Format-UiText -Key 'step_indicator' -Values @(3, 3)
            $script:BtnBack.Visible = $true
            Build-OptionsPage
            $script:BtnPrimary.Text = Get-UiText -Key 'btn_install'
        }
        3 {
            $script:StepLabel.Text = Get-UiText -Key 'page_progress_title'
            Build-ProgressPage
            $script:BtnPrimary.Visible = $false
        }
        4 {
            $script:StepLabel.Text = Get-UiText -Key 'page_result_title'
            Build-ResultPage
            $script:BtnPrimary.Text = Get-UiText -Key 'btn_open_gemini'
            $script:BtnClose.Text = Get-UiText -Key 'btn_finish'
        }
    }

    Format-FooterButtons
}

# ---- install -----------------------------------------------------------------

function Test-InstallOptions {
    if ($script:Mode -eq 'quick' -or $script:Mode -eq 'custom') {
        if (-not (Test-Path -LiteralPath $ConsoleInstaller)) {
            $script:OptionError = Format-UiText -Key 'opt_err_installer_missing' -Values @($ConsoleInstaller)
            return $false
        }
    }
    if ($script:Mode -eq 'custom') {
        $node = "$($script:Node)".Trim()
        if (-not $node) {
            $script:OptionError = Get-UiText -Key 'opt_err_node_required'
            return $false
        }
        if ($node -notmatch '^[a-z0-9_-]+$') {
            $script:OptionError = Get-UiText -Key 'opt_err_node_chars'
            return $false
        }
        if (-not "$($script:Domain)".Trim()) {
            $script:OptionError = Get-UiText -Key 'opt_err_domain_required'
            return $false
        }
    }
    if ($script:Mode -eq 'ssh') {
        $target = "$($script:SshTarget)".Trim()
        if (-not $target -or $target -notmatch '^[^@\s]+@[^@\s]+$') {
            $script:OptionError = Get-UiText -Key 'opt_err_ssh_required'
            return $false
        }
        if ($script:SshPort -lt 1 -or $script:SshPort -gt 65535) {
            $script:OptionError = Get-UiText -Key 'opt_err_ssh_port'
            return $false
        }
        if (-not (Test-Path -LiteralPath $ShellInstaller)) {
            $script:OptionError = Format-UiText -Key 'opt_err_install_sh_missing' -Values @($ShellInstaller)
            return $false
        }
        if (-not (Test-Path -LiteralPath $CoreDir)) {
            $script:OptionError = Format-UiText -Key 'opt_err_core_missing' -Values @($CoreDir)
            return $false
        }
        if (-not (Get-Command 'ssh.exe' -ErrorAction SilentlyContinue) -or
            -not (Get-Command 'scp.exe' -ErrorAction SilentlyContinue)) {
            $script:OptionError = Get-UiText -Key 'opt_err_ssh_missing'
            return $false
        }
    }
    $script:OptionError = ''
    return $true
}

function Start-Installation {
    if (-not (Test-InstallOptions)) {
        Show-Page -Index 2
        return
    }

    $script:Install = @{ Running = $true; Failed = $false; Exit = 0; Output = ''; Url = ''; Notice = ''; LinkNotice = '' }
    Show-Page -Index 3

    if ($script:Mode -eq 'ssh') {
        $steps = New-SshSteps -Target "$($script:SshTarget)".Trim() -Port $script:SshPort `
            -Domain "$($script:Domain)".Trim()
    } else {
        $steps = New-ConsoleInstallerSteps -Mode $script:Mode -Node "$($script:Node)".Trim() `
            -Domain "$($script:Domain)".Trim() -Token "$($script:Token)".Trim()
    }

    Reset-Pipeline -Steps $steps -OnFinish {
        param($failed, $exitCode, $output)
        $script:Install.Running = $false
        $script:Install.Failed = [bool]$failed
        $script:Install.Exit = $exitCode
        $script:Install.Output = $output
        if (-not $failed) {
            $script:Install.Url = Get-ResultUrl -Text $output -Mode $script:Mode
        }
        Show-Page -Index 4
    } -LogBox $script:Pipe.LogBox -StatusLabel $script:Pipe.StatusLabel
}

function Start-Preflight {
    if ($script:Preflight.Running -or $script:Preflight.Done) { return }
    if (-not (Test-Path -LiteralPath $ConsoleInstaller)) {
        $script:Preflight.Done = $true
        $script:Preflight.Text = Format-UiText -Key 'opt_err_installer_missing' -Values @($ConsoleInstaller)
        return
    }
    $script:Preflight.Running = $true
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Arg $ConsoleInstaller),
        '-DryRun', '-Lang', $script:UiLang) -join ' '
    $steps = @(New-PipelineStep -File (Get-PowerShellExe) -Arguments $argList `
        -Label (Get-UiText -Key 'preflight_group'))
    Reset-Pipeline -Steps $steps -OnFinish {
        param($failed, $exitCode, $output)
        $script:Preflight.Running = $false
        $script:Preflight.Done = $true
        $facts = Get-PreflightFacts -Text $output
        $script:Preflight.Facts = $facts
        # Whether the check actually produced anything. A run that could not even
        # write its output files parses to nothing, and that is not the same thing
        # as "no domain configured".
        $script:Preflight.Parsed = [bool]($facts.Python -or $facts.Domain)
        # exit 2 is install.ps1's "preflight found problems" code, not a failure
        # of the check itself, so the report is still worth showing.
        $script:Preflight.Problems = ($exitCode -eq 2)
        if (-not $facts.Python -and -not $facts.Domain) {
            $script:Preflight.Text = (Format-UiText -Key 'preflight_failed' -Values @("exit $exitCode")) +
                "`r`n`r`n" + $output
        } else {
            $lines = New-Object System.Collections.ArrayList
            [void]$lines.Add(((Get-UiText -Key 'preflight_python') + ' : ' + $facts.Python))
            [void]$lines.Add(((Get-UiText -Key 'preflight_websockets') + ' : ' + $facts.Websockets))
            [void]$lines.Add(((Get-UiText -Key 'preflight_domain') + ' : ' + $facts.Domain))
            [void]$lines.Add(((Get-UiText -Key 'preflight_config') + ' : ' + $facts.Config))
            [void]$lines.Add(((Get-UiText -Key 'preflight_autostart') + ' : ' + $facts.Autostart))
            if ($facts.Node) {
                [void]$lines.Add(((Get-UiText -Key 'preflight_node') + ' : ' + $facts.Node))
            }
            if ($script:Preflight.Problems) {
                [void]$lines.Add('')
                [void]$lines.Add((Get-UiText -Key 'preflight_problems'))
            }
            $script:Preflight.Text = ($lines -join "`r`n")
        }
        if ($script:Page -eq 0) { Show-Page -Index 0 }
    }
    if ($script:Page -eq 0) { Show-Page -Index 0 }
}

# ------------------------------------------------------------------------------
#  Form construction
# ------------------------------------------------------------------------------

function Initialize-Form {
    $script:Form = New-Object System.Windows.Forms.Form
    # The layout below is absolute pixels. With the default AutoScaleMode of Font,
    # WinForms resized every control from the form's default font to Segoe UI 9,
    # which pushed the right-hand column and the language box past the window
    # edge. Measured on Windows 11, PowerShell 5.1.
    $script:Form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None
    $script:Form.Text = Get-UiText -Key 'app_title'

    $script:Form.ClientSize = New-Object System.Drawing.Size(804, 620)
    $script:Form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $script:Form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::Sizable
    $script:Form.MaximizeBox = $true
    $script:Form.MinimumSize = New-Object System.Drawing.Size(720, 560)
    $script:Form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = [System.Windows.Forms.DockStyle]::Top
    $header.Height = 56
    $header.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $script:Form.Controls.Add($header)

    $script:HeaderTitle = New-Label -Text (Get-UiText -Key 'app_title') -X 16 -Y 14 -W 520 -H 28 -Bold
    $script:HeaderTitle.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
    $header.Controls.Add($script:HeaderTitle)

    $languageLabel = New-Label -Text ((Get-UiText -Key 'lang_label') + ':') -X 596 -Y 20 -W 74 -H 20
    $languageLabel.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    $header.Controls.Add($languageLabel)

    $script:LangBox = New-Object System.Windows.Forms.ComboBox
    $script:LangBox.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
    $script:LangBox.Location = New-Object System.Drawing.Point(672, 17)
    $script:LangBox.Size = New-Object System.Drawing.Size(118, 23)
    $script:LangBox.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    [void]$script:LangBox.Items.Add('English')
    [void]$script:LangBox.Items.Add('Russian')
    # SelectedIndex is set before the handler is attached, so restoring the
    # startup language does not fire a rebuild.
    if ($script:UiLang -eq 'ru') { $script:LangBox.SelectedIndex = 1 } else { $script:LangBox.SelectedIndex = 0 }

    $script:LangBox.Add_SelectedIndexChanged({
        $code = 'en'
        if ($this.SelectedIndex -eq 1) { $code = 'ru' }
        Set-Language -Code $code
    })
    $header.Controls.Add($script:LangBox)

    $footer = New-Object System.Windows.Forms.Panel
    $footer.Dock = [System.Windows.Forms.DockStyle]::Bottom
    $footer.Height = 56
    $footer.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $script:Form.Controls.Add($footer)

    $script:StepLabel = New-Label -Text '' -X 16 -Y 20 -W 360 -H 20 -Dim
    $footer.Controls.Add($script:StepLabel)

    $script:BtnClose = New-Object System.Windows.Forms.Button
    $script:BtnClose.Text = Get-UiText -Key 'btn_close'
    $script:BtnClose.Location = New-Object System.Drawing.Point(672, 14)
    $script:BtnClose.AutoSize = $true
    $script:BtnClose.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
    $script:BtnClose.MinimumSize = New-Object System.Drawing.Size(118, 28)
    $script:BtnClose.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    $script:BtnClose.Add_Click({ $script:Form.Close() })
    $footer.Controls.Add($script:BtnClose)

    $script:BtnPrimary = New-Object System.Windows.Forms.Button
    $script:BtnPrimary.Location = New-Object System.Drawing.Point(548, 14)
    $script:BtnPrimary.AutoSize = $true
    $script:BtnPrimary.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
    $script:BtnPrimary.MinimumSize = New-Object System.Drawing.Size(118, 28)
    $script:BtnPrimary.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    # One dispatcher for every page: attaching a handler per page would stack
    # them, and the second click would run the previous page's action as well.
    $script:BtnPrimary.Add_Click({
        switch ($script:Page) {
            0 { Show-Page -Index 1 }
            1 { $script:OptionError = ''; Show-Page -Index 2 }
            2 { Start-Installation }
            4 { [void](Start-Url -Url $GeminiAppsUrl) }
        }
    })
    $footer.Controls.Add($script:BtnPrimary)

    # Enter activates the primary button, so the wizard can be driven from the
    # keyboard alone. On the progress page the button is hidden and the
    # dispatcher has no action for that page, so Enter is harmless there.
    $script:Form.AcceptButton = $script:BtnPrimary

    $script:BtnBack = New-Object System.Windows.Forms.Button
    $script:BtnBack.Text = Get-UiText -Key 'btn_back'
    $script:BtnBack.Location = New-Object System.Drawing.Point(424, 14)
    $script:BtnBack.AutoSize = $true
    $script:BtnBack.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
    $script:BtnBack.MinimumSize = New-Object System.Drawing.Size(118, 28)
    $script:BtnBack.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    $script:BtnBack.Add_Click({
        if ($script:Page -gt 0 -and $script:Page -lt 3) {
            $script:OptionError = ''
            Show-Page -Index ($script:Page - 1)
        }
    })
    $footer.Controls.Add($script:BtnBack)

    # Positioned explicitly rather than docked Fill: docking order depends on
    # z-order, and this form is fixed-size anyway.
    $script:Content = New-Object System.Windows.Forms.Panel
    $script:Content.Location = New-Object System.Drawing.Point(0, 56)
    $script:Content.Size = New-Object System.Drawing.Size(804, 508)
    $script:Content.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor `
        [System.Windows.Forms.AnchorStyles]::Bottom -bor `
        [System.Windows.Forms.AnchorStyles]::Left -bor `
        [System.Windows.Forms.AnchorStyles]::Right
    $script:Content.AutoScroll = $true
    $script:Content.BackColor = [System.Drawing.Color]::White
    $script:Form.Controls.Add($script:Content)

    $script:Timer = New-Object System.Windows.Forms.Timer
    $script:Timer.Interval = 250
    $script:Timer.Add_Tick({ Update-Pipeline })

    $script:Form.Add_FormClosing({
        param($sender, $eventArgs)
        if ($script:Pipe.Running) {
            $answer = [System.Windows.Forms.MessageBox]::Show(
                (Get-UiText -Key 'msg_cancel_confirm'),
                (Get-UiText -Key 'msg_cancel_title'),
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Question)
            if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) {
                $eventArgs.Cancel = $true
                return
            }
            Stop-Pipeline
        }
    })
}

# ------------------------------------------------------------------------------
#  Self-test
# ------------------------------------------------------------------------------

$script:SelfTestResults = New-Object System.Collections.ArrayList

function Add-SelfTestResult {
    param([string]$Name, [bool]$Pass, [string]$Detail = '')
    [void]$script:SelfTestResults.Add([pscustomobject]@{ name = $Name; pass = $Pass; detail = $Detail })
}

function Invoke-SelfTest {
    $script:Headless = $true
    $script:SelfTestResults = New-Object System.Collections.ArrayList
    # The language the entry point actually settled on, before this self-test
    # starts switching languages for its own checks.
    $entryLang = $script:UiLang

    # 1. the source must stay pure ASCII, or PowerShell 5.1 will not parse it
    #    when the file is read with the ANSI code page
    $nonAscii = -1
    try {
        $bytes = [System.IO.File]::ReadAllBytes($ScriptPath)
        $nonAscii = 0
        foreach ($b in $bytes) { if ($b -gt 127) { $nonAscii++ } }
    } catch { $nonAscii = -1 }
    Add-SelfTestResult 'source_is_ascii' ($nonAscii -eq 0) "non_ascii_bytes=$nonAscii"

    # 2. both languages carry exactly the same keys
    $enKeys = @($script:Strings.en.PSObject.Properties.Name | Sort-Object)
    $ruKeys = @($script:Strings.ru.PSObject.Properties.Name | Sort-Object)
    $missing = @($enKeys | Where-Object { $ruKeys -notcontains $_ })
    $extra = @($ruKeys | Where-Object { $enKeys -notcontains $_ })
    Add-SelfTestResult 'language_keys_match' (($missing.Count -eq 0) -and ($extra.Count -eq 0)) `
        ("missing_in_ru=[" + ($missing -join ',') + "] extra_in_ru=[" + ($extra -join ',') + "]")

    # 3. no key resolves to itself, which is what Get-UiText returns on a typo
    $unresolved = @()
    foreach ($key in $enKeys) {
        if ((Get-UiText -Key $key) -eq $key) { $unresolved += $key }
    }
    Add-SelfTestResult 'keys_resolve' ($unresolved.Count -eq 0) ("unresolved=[" + ($unresolved -join ',') + "]")

    # 4. the tolerant reader decodes UTF-8 Russian rather than mangling it
    $probe = Join-Path (Get-ScratchDirectory) ('mesh-gui-selftest-' + [Guid]::NewGuid().ToString('N') + '.txt')
    $cyrillic = [string][char]0x041F + [char]0x0440 + [char]0x0438 + [char]0x0432 + [char]0x0435 + [char]0x0442
    try {
        [System.IO.File]::WriteAllText($probe, $cyrillic, (New-Object System.Text.UTF8Encoding($false)))
        $readBack = Read-TextFileTolerant -Path $probe
        Add-SelfTestResult 'tolerant_reader_utf8' ($readBack -eq $cyrillic) ("read='" + $readBack + "'")
    } catch {
        Add-SelfTestResult 'tolerant_reader_utf8' $false $_.Exception.Message
    } finally {
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
    }

    # 5. the URL parser finds the URL in output shaped like install.ps1's box
    $boxOutput = '  +----------------------------------------------------------+' + "`n" +
        '  | MCP SERVER URL (COPY THIS):                              |' + "`n" +
        '  |                                                          |' + "`n" +
        '  | https://mesh.example.test/sse?user=node-one&token=abc123 |' + "`n" +
        '  |                                                          |' + "`n" +
        '  | [OK] URL COPIED TO CLIPBOARD!                            |' + "`n" +
        '  +----------------------------------------------------------+' + "`n"
    $parsed = Get-ResultUrl -Text $boxOutput -Mode 'custom'
    Add-SelfTestResult 'url_parser_box' `
        ($parsed -eq 'https://mesh.example.test/sse?user=node-one&token=abc123') ("parsed='" + $parsed + "'")

    $parsedStandalone = Get-ResultUrl -Text 'http://localhost:8096/sse' -Mode 'custom'
    Add-SelfTestResult 'url_parser_standalone' ($parsedStandalone -eq 'http://localhost:8096/sse') `
        ("parsed='" + $parsedStandalone + "'")

    # 6. the agent.env fallback rebuilds the canonical URL
    $envProbe = Join-Path (Get-ScratchDirectory) ('mesh-gui-selftest-env-' + [Guid]::NewGuid().ToString('N') + '.env')
    try {
        $envText = "MESH_GATEWAY=mesh.example.test`r`nMESH_USER=node-two`r`nMESH_TOKEN=tok456`r`n"
        [System.IO.File]::WriteAllText($envProbe, $envText, (New-Object System.Text.UTF8Encoding($false)))
        $fromEnv = Get-ResultUrl -Text 'no url in this text' -Mode 'custom' -AgentEnvPath $envProbe
        Add-SelfTestResult 'url_from_agent_env' `
            ($fromEnv -eq 'https://mesh.example.test/sse?user=node-two&token=tok456') ("parsed='" + $fromEnv + "'")
    } catch {
        Add-SelfTestResult 'url_from_agent_env' $false $_.Exception.Message
    } finally {
        if (Test-Path -LiteralPath $envProbe) { Remove-Item -LiteralPath $envProbe -Force -ErrorAction SilentlyContinue }
    }

    # 7. command previews build for every mode (this also exercises New-SshSteps
    #    without opening a single connection)
    $previewErrors = @()
    $savedMode = $script:Mode
    foreach ($mode in @('quick', 'custom', 'ssh')) {
        $script:Mode = $mode
        try {
            $preview = Get-CommandPreview -Mode $mode -Node 'node-x' -Domain 'mesh.example.test' `
                -Token 'tok' -Target 'user@host' -Port 22
            # -notmatch on an array filters rather than compares, and the filtered
            # result is truthy; collapse to one string first.
            $previewText = ($preview | Out-String)
            if (-not $previewText.Trim()) { $previewErrors += "${mode}: empty preview" }
            if ($mode -eq 'ssh' -and $previewText -notmatch 'install\.sh --quick --lang=') {
                $previewErrors += "${mode}: remote install command missing"
            }
        } catch {
            $previewErrors += "${mode}: $($_.Exception.Message)"
        }
    }
    $script:Mode = $savedMode
    Add-SelfTestResult 'command_previews' ($previewErrors.Count -eq 0) ($previewErrors -join ' | ')

    # 8. every page builds in every language and in every mode
    $script:Device = Get-DeviceInfo
    Initialize-Form
    $buildErrors = @()
    $savedLang = $script:UiLang
    foreach ($code in @('en', 'ru')) {
        $script:UiLang = $code
        foreach ($mode in @('quick', 'custom', 'ssh')) {
            $script:Mode = $mode
            foreach ($index in @(0, 1, 2, 3, 4)) {
                try {
                    Show-Page -Index $index
                } catch {
                    $buildErrors += "$code/${mode}/page${index}: $($_.Exception.Message)"
                }
            }
        }
    }
    $script:UiLang = $savedLang
    $script:Mode = $savedMode
    Add-SelfTestResult 'pages_build' ($buildErrors.Count -eq 0) ($buildErrors -join ' | ')

    # 9. the process pipeline really captures output and reports the exit code
    $probeStep = New-PipelineStep -File (Get-PowerShellExe) `
        -Arguments '-NoProfile -Command "Write-Host ''https://pipe.example.test/sse?user=probe&token=tk''"' `
        -Label 'selftest-success'
    $script:PipeResult = $null
    Reset-Pipeline -Steps @($probeStep) -OnFinish {
        param($failed, $exitCode, $output)
        $script:PipeResult = @{ Failed = $failed; Exit = $exitCode; Output = $output }
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ($script:Pipe.Running -and (Get-Date) -lt $deadline) {
        Update-Pipeline
        Start-Sleep -Milliseconds 120
    }
    $pipeOk = $false
    $pipeDetail = 'pipeline did not finish'
    if ($script:PipeResult) {
        $captured = Get-ResultUrl -Text $script:PipeResult.Output -Mode 'custom'
        $pipeOk = (-not $script:PipeResult.Failed) -and ($captured -eq 'https://pipe.example.test/sse?user=probe&token=tk')
        $pipeDetail = "failed=$($script:PipeResult.Failed) exit=$($script:PipeResult.Exit) url='$captured'"
    }
    Add-SelfTestResult 'pipeline_captures_output' $pipeOk $pipeDetail

    # 10. a non-zero exit is reported as a failure
    $failStep = New-PipelineStep -File (Get-PowerShellExe) -Arguments '-NoProfile -Command "exit 7"' -Label 'selftest-failure'
    $script:PipeResult = $null
    Reset-Pipeline -Steps @($failStep) -OnFinish {
        param($failed, $exitCode, $output)
        $script:PipeResult = @{ Failed = $failed; Exit = $exitCode; Output = $output }
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ($script:Pipe.Running -and (Get-Date) -lt $deadline) {
        Update-Pipeline
        Start-Sleep -Milliseconds 120
    }
    $failOk = $false
    $failDetail = 'pipeline did not finish'
    if ($script:PipeResult) {
        $failOk = $script:PipeResult.Failed -and ($script:PipeResult.Exit -eq 7)
        $failDetail = "failed=$($script:PipeResult.Failed) exit=$($script:PipeResult.Exit)"
    }
    Add-SelfTestResult 'pipeline_reports_failure' $failOk $failDetail

    # 11. the files the wizard depends on are where it expects them
    Add-SelfTestResult 'console_installer_present' (Test-Path -LiteralPath $ConsoleInstaller) $ConsoleInstaller
    Add-SelfTestResult 'shell_installer_present' (Test-Path -LiteralPath $ShellInstaller) $ShellInstaller

    # 12. the dry-run parser reads install.ps1's six lines positionally, and an
    #     older five-line copy still parses (the node row is simply empty)
    $dryRunText = @(
        '[DRY-RUN] Python      : C:\Python312\python.exe',
        '[DRY-RUN] websockets  : installed',
        '[DRY-RUN] Domain      : mesh.example.test',
        '[DRY-RUN] Config      : C:\Users\probe\.config\antigravity-mesh',
        '[DRY-RUN] Autostart   : C:\Users\probe\Startup\antigravity-agent.vbs',
        '[DRY-RUN] Node        : offline (probe)'
    ) -join "`n"
    $facts = Get-PreflightFacts -Text $dryRunText
    $factsOk = ($facts.Python -eq 'C:\Python312\python.exe') -and
        ($facts.Websockets -eq 'installed') -and
        ($facts.Domain -eq 'mesh.example.test') -and
        ($facts.Config -eq 'C:\Users\probe\.config\antigravity-mesh') -and
        ($facts.Autostart -eq 'C:\Users\probe\Startup\antigravity-agent.vbs') -and
        ($facts.Node -eq 'offline (probe)')
    $legacyText = (($dryRunText -split "`n")[0..4] -join "`n")
    $factsOk = $factsOk -and ((Get-PreflightFacts -Text $legacyText).Node -eq '')
    Add-SelfTestResult 'preflight_parser' $factsOk ("domain='" + $facts.Domain + "' python='" + $facts.Python + "'")

    # 13. "not configured" is detected without depending on the language
    $domainOk = (-not (Test-DomainConfigured -Value 'NOT CONFIGURED')) -and
        (-not (Test-DomainConfigured -Value '')) -and
        (-not (Test-DomainConfigured -Value '__MESH_DOMAIN__')) -and
        (Test-DomainConfigured -Value 'mesh.example.test')
    Add-SelfTestResult 'domain_configured_detection' $domainOk ''

    # 14. the window chrome follows a runtime language switch, not only the page
    $chromeErrors = @()
    $savedChromeLang = $script:UiLang
    foreach ($code in @('en', 'ru')) {
        $script:UiLang = $code
        Show-Page -Index 2
        if ($script:BtnBack.Text -ne (Get-UiText -Key 'btn_back')) {
            $chromeErrors += "${code}: back='" + $script:BtnBack.Text + "'"
        }
        if ($script:BtnClose.Text -ne (Get-UiText -Key 'btn_close')) {
            $chromeErrors += "${code}: close='" + $script:BtnClose.Text + "'"
        }
        if ($script:BtnPrimary.Text -ne (Get-UiText -Key 'btn_install')) {
            $chromeErrors += "${code}: install='" + $script:BtnPrimary.Text + "'"
        }
        if ($script:StepLabel.Text -notmatch [regex]::Escape((Get-UiText -Key 'page_options_title')) -and
            $script:StepLabel.Text -notmatch '\d') {
            $chromeErrors += "${code}: step='" + $script:StepLabel.Text + "'"
        }
    }
    $script:UiLang = $savedChromeLang
    Add-SelfTestResult 'chrome_localised' ($chromeErrors.Count -eq 0) ($chromeErrors -join ' | ')

    # 15. the result page must offer the Gemini address as a real link and a copy
    #     button. It used to be a plain label, so it could be neither clicked nor
    #     copied.
    $script:Install = @{
        Running = $false; Failed = $false; Exit = 0; Output = ''
        Url = 'https://mesh.example.test/sse?user=probe&token=tok'; Notice = ''; LinkNotice = ''
    }
    $script:Mode = 'quick'
    Show-Page -Index 4
    $linkFound = $false
    $copyButtonFound = $false
    $expectedCopy = Get-UiText -Key 'btn_copy_link'
    $pending = New-Object System.Collections.Stack
    foreach ($control in $script:Content.Controls) { $pending.Push($control) }
    while ($pending.Count -gt 0) {
        $control = $pending.Pop()
        if ($control -is [System.Windows.Forms.LinkLabel] -and $control.Text -eq $GeminiAppsUrl) {
            $linkFound = $true
        }
        if ($control -is [System.Windows.Forms.Button] -and $control.Text -eq $expectedCopy) {
            $copyButtonFound = $true
        }
        foreach ($child in $control.Controls) { $pending.Push($child) }
    }
    Add-SelfTestResult 'result_page_link' ($linkFound -and $copyButtonFound) `
        ("link=$linkFound copyButton=$copyButtonFound")

    # 16. no control may be wider than the container it sits in, in any language:
    #     a fixed-width button clipped the Russian labels.
    $overflow = @()
    $savedOverflowLang = $script:UiLang
    foreach ($code in @('en', 'ru')) {
        $script:UiLang = $code
        foreach ($index in @(0, 1, 2, 4)) {
            Show-Page -Index $index
            $walk = New-Object System.Collections.Stack
            foreach ($control in $script:Content.Controls) { $walk.Push($control) }
            while ($walk.Count -gt 0) {
                $control = $walk.Pop()
                foreach ($child in $control.Controls) {
                    if ($child.Right -gt $control.ClientSize.Width) {
                        $overflow += ("{0}/page{1}: {2} right={3} > {4}" -f `
                            $code, $index, $child.GetType().Name, $child.Right, $control.ClientSize.Width)
                    }
                    $walk.Push($child)
                }
            }
        }
    }
    $script:UiLang = $savedOverflowLang
    Add-SelfTestResult 'no_control_overflow' ($overflow.Count -eq 0) (($overflow | Select-Object -First 5) -join ' | ')

    # 17. the start language follows the user's language list. Checking
    #     CurrentUICulture alone started this wizard in English on a Russian
    #     Windows, because the process reported en-US while the display language
    #     was ru-RU.
    $detectionErrors = @()
    if ((Select-Language -Codes @('ru-RU', 'en-US')) -ne 'ru') { $detectionErrors += 'ru-first' }
    if ((Select-Language -Codes @('en-US', 'ru-RU')) -ne 'en') { $detectionErrors += 'en-first' }
    if ((Select-Language -Codes @('ru')) -ne 'ru') { $detectionErrors += 'bare-ru' }
    if ((Select-Language -Codes @('de-DE')) -ne 'en') { $detectionErrors += 'unsupported' }
    if ((Select-Language -Codes @()) -ne 'en') { $detectionErrors += 'empty' }
    if ((Select-Language -Codes @('', 'ru-RU')) -ne 'ru') { $detectionErrors += 'blank-first' }
    $signals = @(Get-PreferredLanguageCodes)
    if ($signals.Count -eq 0) { $detectionErrors += 'no-signals' }
    Add-SelfTestResult 'language_detection' ($detectionErrors.Count -eq 0) `
        ("detected=" + (Get-DefaultLanguage) + " effective=" + $entryLang +
         " signals=[" + ($signals -join ',') + "] " + ($detectionErrors -join '|'))

    # 18. the pipeline must not depend on %TEMP% being writable
    $scratch = Get-ScratchDirectory
    $scratchOk = $false
    $scratchDetail = 'dir=' + $scratch
    try {
        $scratchProbe = Join-Path $scratch ('scratch-probe-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        Set-Content -LiteralPath $scratchProbe -Value 'x' -Encoding ASCII -ErrorAction Stop
        Remove-Item -LiteralPath $scratchProbe -Force -ErrorAction Stop
        $scratchOk = $true
    } catch {
        $scratchDetail += ' error=' + $_.Exception.Message
    }
    Add-SelfTestResult 'scratch_directory' $scratchOk $scratchDetail

    $failedCount = @($script:SelfTestResults | Where-Object { -not $_.pass }).Count
    return [pscustomobject]@{
        ok      = ($failedCount -eq 0)
        passed  = @($script:SelfTestResults | Where-Object { $_.pass }).Count
        failed  = $failedCount
        results = $script:SelfTestResults
    }
}

# ------------------------------------------------------------------------------
#  Main
# ------------------------------------------------------------------------------

try {
    Initialize-Strings
} catch {
    $message = $_.Exception.Message
    $text = $message
    if ($message.StartsWith('__STRINGS_MISSING__')) {
        $text = 'Language file not found: ' + $message.Substring(19)
    } elseif ($message.StartsWith('__STRINGS_INVALID__')) {
        $text = 'Language file is not valid JSON: ' + $message.Substring(19)
    } elseif ($message.StartsWith('__LANG_MISSING__')) {
        $text = 'Language is missing from the language file: ' + $message.Substring(16)
    }
    [Console]::Error.WriteLine($text)
    if (-not $SelfTest) {
        [void][System.Windows.Forms.MessageBox]::Show($text, 'Antigravity Mesh')
    }
    exit 1
}

if ($Lang) {
    # An explicit -Lang always wins over detection.
    $script:UiLang = $Lang
} else {
    $script:UiLang = Get-DefaultLanguage
}


if ($SelfTest) {
    $report = Invoke-SelfTest
    $report | ConvertTo-Json -Depth 6
    if ($report.ok) { exit 0 }
    exit 1
}

if ($RunInstall) {
    $script:Mode = $InstallMode
    $script:Node = $InstallUser
    $script:Domain = $InstallGateway
    $script:Token = $InstallToken
    $script:SshTarget = $InstallSshTarget
    $script:SshPort = $InstallSshPort
    $script:Headless = $true

    if (-not (Test-InstallOptions)) {
        Write-Output ('INSTALL_REFUSED ' + $script:OptionError)
        exit 2
    }

    if ($script:Mode -eq 'ssh') {
        $steps = New-SshSteps -Target "$($script:SshTarget)".Trim() -Port $script:SshPort `
            -Domain "$($script:Domain)".Trim()
    } else {
        $steps = New-ConsoleInstallerSteps -Mode $script:Mode -Node "$($script:Node)".Trim() `
            -Domain "$($script:Domain)".Trim() -Token "$($script:Token)".Trim()
    }

    $script:PipeResult = $null
    Reset-Pipeline -Steps $steps -OnFinish {
        param($failed, $exitCode, $output)
        $script:PipeResult = @{ Failed = $failed; Exit = $exitCode; Output = $output }
    }

    $deadline = (Get-Date).AddMinutes(15)
    while ($script:Pipe.Running -and (Get-Date) -lt $deadline) {
        Update-Pipeline
        Start-Sleep -Milliseconds 250
    }

    $failed = $true
    $code = 1
    $output = ''
    if ($script:PipeResult) {
        $failed = [bool]$script:PipeResult.Failed
        $code = $script:PipeResult.Exit
        $output = [string]$script:PipeResult.Output
    }
    Write-Output $output
    if ($failed) {
        Write-Output ('INSTALL_FAILED exit=' + $code)
        exit 1
    }
    $url = Get-ResultUrl -Text $output -Mode $script:Mode
    Write-Output ('MESH_URL=' + $url)
    exit 0
}

$script:Device = Get-DeviceInfo
Initialize-Form
Show-Page -Index 0
Start-Preflight
[void]$script:Form.ShowDialog()
