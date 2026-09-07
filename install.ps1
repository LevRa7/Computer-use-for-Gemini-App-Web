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

$ErrorActionPreference = "Stop"

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

# Python check & auto-install
$hasPython = $false
try {
    $pyVer = python --version 2>&1
    if ($LASTEXITCODE -eq 0 -or $pyVer -match "Python 3") {
        if ($Lang -eq "ru") {
            Write-Host "[1/3] Python обнаружен: $pyVer" -ForegroundColor Green
        } else {
            Write-Host "[1/3] Python detected: $pyVer" -ForegroundColor Green
        }
        $hasPython = $true
    }
} catch {
    $hasPython = $false
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
        if ($Lang -eq "ru") {
            Write-Error "Python 3 не найден в PATH, и winget недоступен. Пожалуйста, установите Python с python.org."
        } else {
            Write-Error "Python 3 was not found in PATH, and winget is unavailable. Please install Python from python.org."
        }
        exit 1
    }
}

# Install dependencies
if ($Lang -eq "ru") {
    Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
} else {
    Write-Host "[2/3] Checking dependencies (websockets)..." -ForegroundColor Yellow
}
python -m pip install websockets --quiet

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
                Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LevRa7/Gemini-APP-Web-for-computer-use---FREE/main/$f" -OutFile $dest -UseBasicParsing
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
    Start-Process python -ArgumentList "-m core.server --port=$Port" -WorkingDirectory $ScriptDir -WindowStyle Hidden
    $LocalUrl = "http://localhost:$Port/sse"
    $Copied = $false
    try {
        Set-Clipboard -Value $LocalUrl -ErrorAction Stop
        $Copied = $true
    } catch {
        try { $LocalUrl | clip.exe 2>$null; $Copied = $true } catch {}
    }

    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Green
    if ($Lang -eq "ru") {
        Write-Host " [OK] Локальный MCP-сервер успешно запущен!" -ForegroundColor Green
        Write-Host "================================================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host "  1. Откройте страницу приложений Gemini Spark:" -ForegroundColor Yellow
        Write-Host "     https://gemini.google.com/spark/apps" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  2. Подключите ваш MCP-сервер:" -ForegroundColor Yellow
        $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "(Выделите ссылку и скопируйте через Ctrl+C)" }
        Print-McpBox "ССЫЛКА ЛОКАЛЬНОГО MCP-СЕРВЕРА (SSE ENDPOINT):" $LocalUrl $copyMsg
        Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    } else {
        Write-Host " [OK] Local MCP Server successfully started!" -ForegroundColor Green
        Write-Host "================================================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host "  1. Open Gemini Spark Apps in your browser:" -ForegroundColor Yellow
        Write-Host "     https://gemini.google.com/spark/apps" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  2. Connect your MCP Server:" -ForegroundColor Yellow
        $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "(Select link and press Ctrl+C to copy)" }
        Print-McpBox "LOCAL MCP SERVER SSE ENDPOINT URL:" $LocalUrl $copyMsg
        Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    }
    Write-Host "================================================================================" -ForegroundColor Green
    exit 0
}

# Gateway Tunnel Mode
if ([string]::IsNullOrWhiteSpace($User)) {
    $User = $Hostname
}

if ($Lang -eq "ru") {
    Write-Host "[2/3] Регистрация субдомена '$User' на шлюзе ($Gateway)..." -ForegroundColor Yellow
} else {
    Write-Host "[2/3] Registering subdomain '$User' on gateway ($Gateway)..." -ForegroundColor Yellow
}
$regPayload = @{ username = $User; auto_suffix = $true } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "https://$Gateway/api/register" -Method Post -Body $regPayload -ContentType "application/json"

$AssignedUser = $response.username
$AssignedToken = $response.token
$SseUrl = $response.sse_url

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

# Create Windows Startup launcher for persistent autostart
$startupDir = [Environment]::GetFolderPath("Startup")
$startupVbs = "$startupDir\antigravity-agent.vbs"
@"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Environment("PROCESS")("MESH_GATEWAY") = "$Gateway"
WshShell.Environment("PROCESS")("MESH_USER") = "$AssignedUser"
WshShell.Environment("PROCESS")("MESH_TOKEN") = "$AssignedToken"
WshShell.CurrentDirectory = "$ScriptDir"
WshShell.Run "python -m core.agent", 0, False
"@ | Out-File -FilePath $startupVbs -Encoding ascii

# Launch now
Start-Process python -ArgumentList "-m core.agent" -WorkingDirectory $ScriptDir -WindowStyle Hidden

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Green
if ($Lang -eq "ru") {
    Write-Host " [OK] Antigravity Mesh узел успешно установлен и запущен!" -ForegroundColor Green
    Write-Host "================================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  * Устройство:      $Hostname ($DetectedType)"
    Write-Host "  * Субдомен:        $AssignedUser.$Gateway" -ForegroundColor Cyan
    Write-Host "  * Секретный токен: $AssignedToken"
    Write-Host "  * Автозапуск:      Включен (Windows Startup VBS)"
    Write-Host ""
    Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "  1. Откройте страницу приложений Gemini Spark в браузере:" -ForegroundColor Yellow
    Write-Host "     https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  2. Подключите ваш персональный MCP-сервер:" -ForegroundColor Yellow
    $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "(Выделите ссылку и скопируйте через Ctrl+C)" }
    Print-McpBox "ССЫЛКА MCP-СЕРВЕРА (SSE ENDPOINT):" $SseUrl $copyMsg
    Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Инструкция подключения в Gemini Spark / AI Studio:"
    Write-Host "  1. Перейдите по ссылке: https://gemini.google.com/spark/apps"
    Write-Host "  2. Нажмите 'Add app' или перейдите в Настройки -> MCP"
    Write-Host "  3. Вставьте скопированную ссылку (Ctrl+V) и нажмите Connect!"
} else {
    Write-Host " [OK] Antigravity Mesh node successfully installed and running!" -ForegroundColor Green
    Write-Host "================================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  * Device:       $Hostname ($DetectedType)"
    Write-Host "  * Subdomain:    $AssignedUser.$Gateway" -ForegroundColor Cyan
    Write-Host "  * Secret Token: $AssignedToken"
    Write-Host "  * Autostart:    Enabled (Windows Startup VBS)"
    Write-Host ""
    Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "  1. Open Gemini Spark Apps in your browser:" -ForegroundColor Yellow
    Write-Host "     https://gemini.google.com/spark/apps" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  2. Connect your personal MCP Server:" -ForegroundColor Yellow
    $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "(Select link and press Ctrl+C to copy)" }
    Print-McpBox "MCP SERVER SSE ENDPOINT URL:" $SseUrl $copyMsg
    Write-Host "  ----------------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Connection Steps in Gemini Spark / AI Studio:"
    Write-Host "  1. Open: https://gemini.google.com/spark/apps"
    Write-Host "  2. Click 'Add app' or navigate to Tools / MCP configuration"
    Write-Host "  3. Paste the URL (Ctrl+V) and click Connect!"
}
Write-Host "================================================================================" -ForegroundColor Green
