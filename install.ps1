# ==============================================================================
#  Antigravity Mesh - Windows PowerShell Universal Installer
# ==============================================================================
param(
    [switch]$Quick,
    [string]$Mode = "tunnel",
    [string]$User = "",
    [string]$Token = "",
    # Empty on purpose: the shared domain is resolved from the one source of truth
    # (see the PUBLIC DOMAIN block below), never from a literal in this file.
    [string]$Gateway = "",
    [int]$Port = 8096,
    [string]$Lang = "en",
    [switch]$DryRun
)

# Ensure UTF-8 output encoding in PowerShell console
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ErrorActionPreference = "Continue"
if (Test-Path variable:global:PSNativeCommandUseErrorActionPreference) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

$Hostname = $env:COMPUTERNAME.ToLower() -replace '[^a-z0-9_-]', ''
$IsLaptop = [bool](Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue)

if ($Lang -eq "ru") {
    $DetectedType = if ($IsLaptop) { "Windows Ноутбук (Laptop)" } else { "Windows Десктоп / Сервер" }
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "| [*] Обнаружено устройство:                                           |" -ForegroundColor Cyan
    Write-Host "|   * Имя хоста : $Hostname" -ForegroundColor Green
    Write-Host "|   * Тип       : $DetectedType" -ForegroundColor Yellow
    Write-Host "|   * ОС        : Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor White
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
} else {
    $DetectedType = if ($IsLaptop) { "Windows Laptop" } else { "Windows Desktop / Server" }
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "| [*] Detected Device:                                                 |" -ForegroundColor Cyan
    Write-Host "|   * Hostname : $Hostname" -ForegroundColor Green
    Write-Host "|   * Type     : $DetectedType" -ForegroundColor Yellow
    Write-Host "|   * OS       : Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor White
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
}

# The dry run is handled after the (read-only) interpreter detection below: a
# preflight that exits before checking anything cannot honestly report that the
# dependencies were verified.

# Python check & auto-install.
# Windows ships a "python.exe" App Execution Alias that opens the Microsoft Store
# instead of running Python, so "python --version" can look successful while doing
# nothing. Resolve a real interpreter once and pin its full path in $PyExe.
$hasPython = $false
$PyExe = $null
$candidates = @()
$cmdPython = Get-Command python -ErrorAction SilentlyContinue
if ($cmdPython) { $candidates += $cmdPython.Source }
$candidates += @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python311\python.exe"
)
$cmdPy = Get-Command py -ErrorAction SilentlyContinue
if ($cmdPy) { $candidates += $cmdPy.Source }

foreach ($cand in $candidates) {
    if (-not $cand -or -not (Test-Path $cand)) { continue }
    try {
        # Require a working sys import, not just a zero exit code.
        $probe = & $cand -c "import sys; print(sys.version_info[0])" 2>&1
        if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq "3") {
            if ((Split-Path $cand -Leaf) -eq "py.exe") { $PyExe = "$cand -3" } else { $PyExe = $cand }
            $pyVer = & $cand --version 2>&1
            if ($Lang -eq "ru") {
                Write-Host "[1/3] Python обнаружен: $pyVer" -ForegroundColor Green
            } else {
                Write-Host "[1/3] Python detected: $pyVer" -ForegroundColor Green
            }
            $hasPython = $true
            break
        }
    } catch { continue }
}

if (-not $hasPython) {
    # Any per-user 3.x the python.org installer left behind, newest first. The
    # check used to be hardcoded to 3.12/3.11 *and* forgot to record the path it
    # found, so a machine with only 3.13 was pushed into a fresh download, and
    # one without "python" on PATH then fell back to the bare name anyway.
    $localRoot = "$env:LOCALAPPDATA\Programs\Python"
    $foundPy = @()
    if (Test-Path $localRoot) {
        $foundPy = Get-ChildItem -Path $localRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' } |
            Where-Object { Test-Path $_ }
    }
    if ($foundPy.Count -gt 0) {
        $localPyDir = Split-Path -Parent $foundPy[0]
        $PyExe = $foundPy[0]
        $env:Path = "$localPyDir;$localPyDir\Scripts;$env:Path"
        $hasPython = $true
        if ($Lang -eq "ru") {
            Write-Host "[1/3] Python обнаружен: $PyExe" -ForegroundColor Green
        } else {
            Write-Host "[1/3] Python detected: $PyExe" -ForegroundColor Green
        }
    }
}

# ------------------------------------------------------------------------------
#  PUBLIC DOMAIN - resolved from the one source of truth
#
#  The domain is declared exactly once, in core/domain.py. This block asks that
#  same chain, in the same order, and it is the ONLY place in this script that
#  picks a hostname:
#
#    1. -Gateway on the command line;
#    2. $env:MESH_PUBLIC_URL;
#    3. $env:AGY_PUBLIC_BASE_URL (legacy alias, same meaning);
#    4. the domain file - $env:MESH_DOMAIN_FILE when set, otherwise
#       %USERPROFILE%\.config\antigravity-mesh\domain.env;
#    5. $PublishedDomain - the domain this copy was published with. The repository
#       carries the placeholder __MESH_DOMAIN__ there and deploy_gateway.sh
#       rewrites that line while publishing, so a served copy knows its domain;
#    6. core/domain.py's DEFAULT_PUBLIC_BASE_URL, read through the interpreter
#       found above (a repository checkout, or a node that bootstrapped already).
#
#  If none of them yields a real hostname the installer stops. A node registered
#  against a domain that does not resolve can never connect, and its autostart
#  entry would retry that dead endpoint forever.
# ------------------------------------------------------------------------------
$DomainPlaceholder = "__MESH_DOMAIN__"
# deploy_gateway.sh rewrites THIS line only (anchored on $PublishedDomain), so the
# placeholder above survives as the "this copy was not published" marker.
$PublishedDomain = "__MESH_DOMAIN__"
$ScriptRoot = $null
if ($MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Get-NormalisedHost([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    $text = $Value.Trim().Trim('"').Trim("'")
    $text = $text -replace '^[A-Za-z][A-Za-z0-9+.-]*://', ''
    $text = $text.Split('/')[0]
    return $text.Trim()
}

function Get-DomainFileValue([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return "" }
    $values = @{}
    try {
        # -Encoding UTF8 also absorbs the BOM that PowerShell itself writes.
        foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
            $index = $trimmed.IndexOf('=')
            $key = $trimmed.Substring(0, $index).Trim()
            $value = $trimmed.Substring($index + 1).Trim().Trim('"').Trim("'")
            if ($key) { $values[$key] = $value }
        }
    } catch { return "" }
    if ($values['MESH_PUBLIC_URL']) { return $values['MESH_PUBLIC_URL'] }
    if ($values['AGY_PUBLIC_BASE_URL']) { return $values['AGY_PUBLIC_BASE_URL'] }
    return ""
}

function Get-ModuleDefaultDomain {
    if (-not $ScriptRoot) { return "" }
    $module = Join-Path $ScriptRoot 'core\domain.py'
    if (-not (Test-Path -LiteralPath $module)) { return "" }
    $value = ""
    if ($PyExe) {
        $interpreter = ($PyExe -split ' ')[0]
        $probe = "import sys; sys.path.insert(0, r'$ScriptRoot'); from core import domain; print(domain.DEFAULT_PUBLIC_BASE_URL)"
        try { $value = & $interpreter -c $probe 2>$null } catch { $value = "" }
    }
    if (-not $value) {
        $match = Select-String -LiteralPath $module -Pattern '^DEFAULT_PUBLIC_BASE_URL\s*=\s*"([^"]+)"' |
            Select-Object -First 1
        if ($match) { $value = $match.Matches[0].Groups[1].Value }
    }
    return "$value".Trim()
}

$DomainFilePath = if ($env:MESH_DOMAIN_FILE) { $env:MESH_DOMAIN_FILE } else { "$env:USERPROFILE\.config\antigravity-mesh\domain.env" }
$ResolvedDomain = Get-NormalisedHost $Gateway
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost $env:MESH_PUBLIC_URL }
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost $env:AGY_PUBLIC_BASE_URL }
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost (Get-DomainFileValue $DomainFilePath) }
if (-not $ResolvedDomain -and $PublishedDomain -ne $DomainPlaceholder) {
    $ResolvedDomain = Get-NormalisedHost $PublishedDomain
}
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost (Get-ModuleDefaultDomain) }

$DomainMissing = [string]::IsNullOrWhiteSpace($ResolvedDomain) -or $ResolvedDomain -eq $DomainPlaceholder
if ($DomainMissing) {
    if ($Lang -eq "ru") {
        Write-Host ""
        Write-Host "[!] Домен шлюза не задан." -ForegroundColor Red
        # ${...} matters: "$DomainPlaceholder:" would be read as a drive-qualified
        # variable reference and the whole script fails to parse in PowerShell 5.1.
        Write-Host "    В этой копии установщика остался плейсхолдер ${DomainPlaceholder}: она не была" -ForegroundColor Red
        Write-Host "    опубликована скриптом deploy_gateway.sh, а домена нет ни в окружении, ни в" -ForegroundColor Red
        Write-Host "    файле $DomainFilePath." -ForegroundColor Red
        Write-Host "    Укажите домен одним из способов:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <общий-домен>" -ForegroundColor Yellow
        Write-Host "      `$env:MESH_PUBLIC_URL='https://<общий-домен>'; .\install.ps1" -ForegroundColor Yellow
        Write-Host "      Set-Content '$DomainFilePath' 'MESH_PUBLIC_URL=https://<общий-домен>'" -ForegroundColor Yellow
        Write-Host "    Установка остановлена: регистрация на несуществующем домене не выполняется," -ForegroundColor Yellow
        Write-Host "    файл конфигурации и автозапуск не создаются." -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "[!] No gateway domain configured." -ForegroundColor Red
        Write-Host "    This copy of the installer still carries the $DomainPlaceholder placeholder: it" -ForegroundColor Red
        Write-Host "    was not published by deploy_gateway.sh, and no domain was found in the" -ForegroundColor Red
        Write-Host "    environment or in $DomainFilePath." -ForegroundColor Red
        Write-Host "    Pass the domain in one of these ways:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <shared-domain>" -ForegroundColor Yellow
        Write-Host "      `$env:MESH_PUBLIC_URL='https://<shared-domain>'; .\install.ps1" -ForegroundColor Yellow
        Write-Host "      Set-Content '$DomainFilePath' 'MESH_PUBLIC_URL=https://<shared-domain>'" -ForegroundColor Yellow
        Write-Host "    Stopping: a node is never registered against a domain that does not exist," -ForegroundColor Yellow
        Write-Host "    no config file and no autostart are created." -ForegroundColor Yellow
    }
    Write-Host ""
    if (-not $DryRun) { exit 1 }
} else {
    $Gateway = $ResolvedDomain
}

# Preflight: report what was detected and change nothing. This runs after the
# detection steps above and before any install (winget, the Python installer,
# pip), so the reported state is real and the machine is untouched.
if ($DryRun) {
    $probeExe = if ($PyExe) { ($PyExe -split " ")[0] } else { $null }
    $wsState = "unknown"
    if ($probeExe) {
        & $probeExe -c "import websockets" 2>$null
        $wsState = if ($LASTEXITCODE -eq 0) { "installed" } else { "missing" }
    }
    $pyState = if ($hasPython) { $PyExe } else { "not found" }
    $domainState = if ($DomainMissing) { "NOT CONFIGURED" } else { $Gateway }
    if ($Lang -eq "ru") {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Домен       : $domainState"
        Write-Host "[DRY-RUN] Конфиг      : $env:USERPROFILE\.config\antigravity-mesh"
        Write-Host "[DRY-RUN] Автозапуск  : $([Environment]::GetFolderPath('Startup'))\antigravity-agent.vbs"
        Write-Host "[DRY-RUN] Действий не выполнено." -ForegroundColor Yellow
    } else {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Domain      : $domainState"
        Write-Host "[DRY-RUN] Config      : $env:USERPROFILE\.config\antigravity-mesh"
        Write-Host "[DRY-RUN] Autostart   : $([Environment]::GetFolderPath('Startup'))\antigravity-agent.vbs"
        Write-Host "[DRY-RUN] Nothing was changed." -ForegroundColor Yellow
    }
    if ($DomainMissing -or -not $hasPython) { exit 2 }
    exit 0
}

if (-not $hasPython) {
    if ($Lang -eq "ru") {
        Write-Host "[1/3] Python не найден. Автоматическая установка через winget..." -ForegroundColor Yellow
    } else {
        Write-Host "[1/3] Python not found. Installing automatically via winget..." -ForegroundColor Yellow
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
        $localPyDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
        if (Test-Path "$localPyDir\python.exe") {
            $env:Path = "$localPyDir;$localPyDir\Scripts;$env:Path"
        }
    } else {
        try {
            if ($Lang -eq "ru") { Write-Host "[1/3] Загрузка официального установщика Python 3.12..." -ForegroundColor Yellow } else { Write-Host "[1/3] Downloading official Python 3.12 installer..." -ForegroundColor Yellow }
            $pyInstaller = "$env:TEMP\python-3.12.5-amd64.exe"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.5/python-3.12.5-amd64.exe" -OutFile $pyInstaller -UseBasicParsing
            Start-Process -FilePath $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait
            $pyPaths = @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:ProgramFiles\Python312")
            foreach ($p in $pyPaths) {
                if (Test-Path "$p\python.exe") {
                    $env:Path = "$p;$p\Scripts;$env:Path"
                    $hasPython = $true
                    break
                }
            }
        } catch {
            Write-Error "Python 3 auto-installation failed: $_"
            exit 1
        }
    }
}

    # Install dependencies.
    # The tunnel agent needs "websockets"; core/mcp_tools.py and core/server.py
    # are standard library only, so this is the single external requirement.
    if ($Lang -eq "ru") {
        Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
    } else {
        Write-Host "[2/3] Checking dependencies (websockets)..." -ForegroundColor Yellow
    }

    # Use the interpreter resolved above; fall back to PATH only if that failed.
    $pyRun = if ($PyExe) { $PyExe } else { "python" }
    $pyExeOnly = ($pyRun -split " ")[0]

    # Upgrading pip first avoids the "old pip cannot build wheels" failure common
    # on a fresh Python installation.
    & $pyExeOnly -c "import websockets" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $pyExeOnly -m pip install --disable-pip-version-check --quiet --upgrade pip 2>&1 | Out-Null
        & $pyExeOnly -m pip install --disable-pip-version-check --quiet websockets 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & $pyExeOnly -m ensurepip --default-pip 2>&1 | Out-Null
            & $pyExeOnly -m pip install --disable-pip-version-check --quiet websockets 2>&1 | Out-Null
        }
    }

    & $pyExeOnly -c "import websockets" 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось установить websockets автоматически." -ForegroundColor Red
            Write-Host "    Выполните вручную: $pyExeOnly -m pip install websockets" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not install websockets automatically." -ForegroundColor Red
            Write-Host "    Run manually: $pyExeOnly -m pip install websockets" -ForegroundColor Yellow
        }
        exit 1
    }
    if ($Lang -eq "ru") {
        Write-Host "[OK] websockets установлен." -ForegroundColor Green
    } else {
        Write-Host "[OK] websockets is installed." -ForegroundColor Green
    }

$ConfigDir = "$env:USERPROFILE\.config\antigravity-mesh"
if (!(Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }

# Bootstrap project files if piped from irm/iex
$ScriptDir = if ($MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { $null }
if (-not $ScriptDir -or -not (Test-Path "$ScriptDir\core\agent.py")) {
    $BootstrapDir = "$env:USERPROFILE\.gemini-computer-use"
    $CoreDir = "$BootstrapDir\core"
    $SkillsDir = "$BootstrapDir\skills"
    if (!(Test-Path $CoreDir)) { New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null }
    if (!(Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }
    
    $files = @(
        "core/agent.py",
        "core/server.py",
        "core/mcp_tools.py",
        "core/web_share.py",
        "core/domain.py",
        "core/vitals.py",
        "core/__init__.py",
        "skills/orchestrator.md"
    )
    foreach ($f in $files) {
        $dest = "$BootstrapDir\$($f -replace '/', '\')"
        try {
            Invoke-WebRequest -Uri "https://$Gateway/$f" -OutFile $dest -UseBasicParsing -ErrorAction Stop
        } catch {
            try {
                Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LevRa7/Computer-use-for-Gemini-App-Web/main/$f" -OutFile $dest -UseBasicParsing
            } catch {}
        }
    }
    $ScriptDir = $BootstrapDir
}

function Print-McpBox($Title, $Url, $CopiedText, $BorderColor = "Green") {
    $MinLen = [Math]::Max($Title.Length, $Url.Length)
    if ($CopiedText) {
        $MinLen = [Math]::Max($MinLen, $CopiedText.Length)
    }
    $ContentWidth = [Math]::Max($MinLen + 4, 84)
    $Border = "  +" + ("-" * $ContentWidth) + "+"
    $Empty  = "  |" + (" " * $ContentWidth) + "|"

    Write-Host $Border -ForegroundColor $BorderColor
    
    $padTitle = $ContentWidth - 2 - $Title.Length
    Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
    Write-Host $Title -NoNewline -ForegroundColor White
    Write-Host ((" " * $padTitle) + " |") -ForegroundColor $BorderColor

    Write-Host $Empty -ForegroundColor $BorderColor

    $padUrl = $ContentWidth - 2 - $Url.Length
    Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
    Write-Host $Url -NoNewline -ForegroundColor Yellow
    Write-Host ((" " * $padUrl) + " |") -ForegroundColor $BorderColor

    Write-Host $Empty -ForegroundColor $BorderColor

    if ($CopiedText) {
        $padCopy = $ContentWidth - 2 - $CopiedText.Length
        Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
        Write-Host $CopiedText -NoNewline -ForegroundColor Green
        Write-Host ((" " * $padCopy) + " |") -ForegroundColor $BorderColor
    }

    Write-Host $Border -ForegroundColor $BorderColor
}

if ($Mode -eq "standalone") {
    if ($Lang -eq "ru") {
        Write-Host "=== Запуск в режиме Local Standalone на порту $Port ===" -ForegroundColor Blue
    } else {
        Write-Host "=== Starting in Local Standalone Mode on port $Port ===" -ForegroundColor Blue
    }
    Start-Process $pyExeOnly -ArgumentList "-m core.server --host 127.0.0.1 --port=$Port" -WorkingDirectory $ScriptDir -WindowStyle Hidden
    $LocalUrl = "http://localhost:$Port/sse"
    $Copied = $false
    try {
        Set-Clipboard -Value $LocalUrl -ErrorAction Stop
        $Copied = $true
    } catch {
        try { $LocalUrl | clip.exe 2>$null; $Copied = $true } catch {}
    }

    Clear-Host

if ($Lang -eq "ru") {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "Скопируйте ссылку выше" }
    Print-McpBox "ССЫЛКА ЛОКАЛЬНОГО MCP-СЕРВЕРА (СКОПИРУЙТЕ):" $LocalUrl $copyMsg
    Write-Host ""
    Write-Host " Куда вставлять ссылку в Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Откройте в браузере: https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host " 2. Нажмите 'Добавить приложение' (Add app / Настройки MCP)"
    Write-Host " 3. Вставьте скопированную ссылку в поле 'URL сервера' (Ctrl+V) и нажмите Подключить." -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "Copy the URL above" }
    Print-McpBox "LOCAL MCP SERVER URL (COPY THIS):" $LocalUrl $copyMsg
    Write-Host ""
    Write-Host " Where to paste this URL in Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Open in your browser: https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host " 2. Click 'Add app' (or navigate to MCP settings)"
    Write-Host " 3. Paste the URL into the 'Server URL' field (Ctrl+V) and click Connect." -ForegroundColor White
    Write-Host ""
}
exit 0
}

# Gateway Tunnel Mode
if ([string]::IsNullOrWhiteSpace($User)) {
    $User = $Hostname
}

if ($Lang -eq "ru") {
    Write-Host "[2/3] Регистрация узла '$User' на общем домене ($Gateway)..." -ForegroundColor Yellow
} else {
    Write-Host "[2/3] Registering node '$User' on the shared domain ($Gateway)..." -ForegroundColor Yellow
}
$detectedMac = ""
try {
    $nic = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled = 'TRUE'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nic -and $nic.MACAddress) {
        $detectedMac = $nic.MACAddress
    }
} catch {}
if ([string]::IsNullOrWhiteSpace($detectedMac)) {
    try {
        # Use the interpreter resolved above: a bare "python" may be the Microsoft
        # Store App Execution Alias, which is on PATH but runs nothing.
        $res = & $pyExeOnly -c "import uuid; print(':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1]))" 2>&1
        if ($LASTEXITCODE -eq 0 -and $res) {
            $detectedMac = $res.ToString().Trim()
        }
    } catch {}
}

$detectedOs = "Windows $([System.Environment]::OSVersion.Version.ToString())"

$regPayloadObj = @{
    username = $User
    auto_suffix = $true
    mac_address = $detectedMac
    os = $detectedOs
}
if (-not [string]::IsNullOrWhiteSpace($Token)) {
    $regPayloadObj.token = $Token
}
$regPayload = $regPayloadObj | ConvertTo-Json
# Registration is the one step that cannot be skipped or guessed: without a real
# node name and token the agent can never connect. This used to run under
# $ErrorActionPreference = "Continue", so a DNS/TLS/HTTP failure printed an error,
# the script carried on, wrote an EMPTY agent.env, installed a permanently broken
# Startup launcher, printed a link with empty user/token and claimed that link had
# been copied - leaving the operator with a node that retries a dead endpoint after
# every login. Fail closed instead, before anything on disk is touched.
try {
    $response = Invoke-RestMethod -Uri "https://$Gateway/api/register" -Method Post `
        -Body $regPayload -ContentType "application/json" -ErrorAction Stop
} catch {
    if ($Lang -eq "ru") {
        Write-Host "[!] Не удалось зарегистрировать узел на '$Gateway': $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    Проверьте, что домен шлюза разрешается в DNS и сервер доступен, затем повторите:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <ваш-домен>" -ForegroundColor Yellow
        Write-Host "    Ничего не установлено: файл конфигурации и автозапуск не создавались." -ForegroundColor Yellow
    } else {
        Write-Host "[!] Could not register this node on '$Gateway': $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    Check that the gateway domain resolves in DNS and the server is reachable, then re-run:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <your-domain>" -ForegroundColor Yellow
        Write-Host "    Nothing was installed: no config file and no autostart were created." -ForegroundColor Yellow
    }
    exit 1
}

$AssignedUser = $response.username
$AssignedToken = $response.token

if ([string]::IsNullOrWhiteSpace($AssignedUser) -or [string]::IsNullOrWhiteSpace($AssignedToken)) {
    if ($Lang -eq "ru") {
        Write-Host "[!] Шлюз '$Gateway' ответил без имени узла или токена - установка остановлена." -ForegroundColor Red
    } else {
        Write-Host "[!] Gateway '$Gateway' answered without a node name or token - stopping." -ForegroundColor Red
    }
    exit 1
}

# ------------------------------------------------------------------------------
# SHARED-DOMAIN CONTRACT: one public domain serves every node and the node is
# selected by ?user=<node-name>. Never build a per-device subdomain URL: each
# extra hostname needs its own DNS record and TLS SAN, and a missing SAN makes
# the Gemini client fail with an opaque "cannot connect to host" error.
# The gateway returns this canonical URL; the fallback keeps older gateways
# working without ever advertising a subdomain.
# ------------------------------------------------------------------------------
$PublicBaseUrl = "https://$Gateway"
$SseUrl = $response.sse_url
if ([string]::IsNullOrWhiteSpace($SseUrl)) {
    $SseUrl = "$PublicBaseUrl/sse?user=$AssignedUser&token=$AssignedToken"
}

# Quick Copy to Clipboard
$Copied = $false
try {
    Set-Clipboard -Value $SseUrl -ErrorAction Stop
    $Copied = $true
} catch {
    try {
        $SseUrl | clip.exe 2>$null
        $Copied = $true
    } catch {}
}

if ($Lang -eq "ru") {
    Write-Host "[3/3] Настройка автозапуска в Windows..." -ForegroundColor Green
} else {
    Write-Host "[3/3] Configuring Windows background autostart..." -ForegroundColor Green
}
$envFile = "$ConfigDir\agent.env"
@"
MESH_GATEWAY=$Gateway
MESH_USER=$AssignedUser
MESH_TOKEN=$AssignedToken
"@ | Out-File -FilePath $envFile -Encoding utf8

# Start agent in background
$env:MESH_GATEWAY = $Gateway
$env:MESH_USER = $AssignedUser
$env:MESH_TOKEN = $AssignedToken

    # Create Windows Startup launcher for persistent autostart.
    # The interpreter is pinned by full path: relying on PATH breaks when the
    # Microsoft Store "python" alias is present or PATH changes between sessions.
    $startupDir = [Environment]::GetFolderPath("Startup")
    $startupVbs = "$startupDir\antigravity-agent.vbs"
    $pyExeForVbs = if ($pyExeOnly) { $pyExeOnly } else { "python" }
    $agentLog = "$ConfigDir\agent.log"
    @"
    Set WshShell = CreateObject("WScript.Shell")
    WshShell.Environment("PROCESS")("MESH_GATEWAY") = "$Gateway"
    WshShell.Environment("PROCESS")("MESH_USER") = "$AssignedUser"
    WshShell.Environment("PROCESS")("MESH_TOKEN") = "$AssignedToken"
    WshShell.CurrentDirectory = "$ScriptDir"
    ' Log the output so a silent autostart failure can be diagnosed later.
    WshShell.Run "cmd /c """"$pyExeForVbs"" -m core.agent >> """"$agentLog"""" 2>&1""", 0, False
"@ | Out-File -FilePath $startupVbs -Encoding Unicode

# Launch now
Start-Process $pyExeOnly -ArgumentList "-m core.agent" -WorkingDirectory $ScriptDir -WindowStyle Hidden

Clear-Host

if ($Lang -eq "ru") {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "Скопируйте ссылку выше" }
    Print-McpBox "ССЫЛКА MCP-СЕРВЕРА ДЛЯ ПОДКЛЮЧЕНИЯ (СКОПИРУЙТЕ):" $SseUrl $copyMsg
    Write-Host ""
    Write-Host " Куда вставлять ссылку в Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Откройте в браузере: https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host " 2. Нажмите 'Добавить приложение' (Add app / Настройки MCP)"
    Write-Host " 3. Вставьте скопированную ссылку в поле 'URL сервера' (Ctrl+V) и нажмите Подключить." -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "Copy the URL above" }
    Print-McpBox "MCP SERVER URL (COPY THIS):" $SseUrl $copyMsg
    Write-Host ""
    Write-Host " Where to paste this URL in Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Open in your browser: https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host " 2. Click 'Add app' (or navigate to MCP settings)"
    Write-Host " 3. Paste the URL into the 'Server URL' field (Ctrl+V) and click Connect." -ForegroundColor White
    Write-Host ""
}
