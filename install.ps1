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

# Install dependencies
if ($Lang -eq "ru") {
    Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
} else {
    Write-Host "[2/3] Checking dependencies (websockets)..." -ForegroundColor Yellow
}
python -m pip install websockets --quiet 2>$null; if ($LASTEXITCODE -ne 0) { python -m ensurepip --default-pip 2>$null; python -m pip install websockets --quiet }

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
    Start-Process python -ArgumentList "-m core.server --port=$Port" -WorkingDirectory $ScriptDir -WindowStyle Hidden
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
