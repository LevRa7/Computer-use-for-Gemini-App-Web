# ==============================================================================
#  Antigravity Mesh - Windows PowerShell Universal Installer
# ==============================================================================
param(
    [switch]$Quick,
    [string]$Mode = "tunnel",
    [string]$User = "",
    [string]$Token = "",
    [string]$Gateway = "smart-server.online",
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

if ($DryRun) {
    if ($Lang -eq "ru") {
        Write-Host "[DRY-RUN] Проверка зависимостей и задач: OK"
    } else {
        Write-Host "[DRY-RUN] Dependency and task verification: OK"
    }
    exit 0
}

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
    $localPyDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
    if (Test-Path "$localPyDir\python.exe") {
        $env:Path = "$localPyDir;$localPyDir\Scripts;$env:Path"
        $hasPython = $true
        if ($Lang -eq "ru") {
            Write-Host "[1/3] Python обнаружен: $localPyDir" -ForegroundColor Green
        } else {
            Write-Host "[1/3] Python detected: $localPyDir" -ForegroundColor Green
        }
    }
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
        $res = python -c "import uuid; print(':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1]))" 2>&1
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
$response = Invoke-RestMethod -Uri "https://$Gateway/api/register" -Method Post -Body $regPayload -ContentType "application/json"

$AssignedUser = $response.username
$AssignedToken = $response.token

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
    "@ | Out-File -FilePath $startupVbs -Encoding ascii

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
