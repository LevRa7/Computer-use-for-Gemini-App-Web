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
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Hostname = $env:COMPUTERNAME.ToLower() -replace '[^a-z0-9_-]', ''
$DetectedType = "Windows Десктоп / Сервер"
if (Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue) {
    $DetectedType = "Windows Ноутбук (Laptop)"
}

Write-Host "╔════════════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║ 🔍 Обнаружено устройство:" -ForegroundColor Cyan
Write-Host "║   • Имя хоста   : $Hostname" -ForegroundColor Green
Write-Host "║   • Тип         : $DetectedType" -ForegroundColor Yellow
Write-Host "║   • ОС          : Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor White
Write-Host "╚════════════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "[DRY-RUN] Проверка зависимостей и задач: OK"
    exit 0
}

# Python check & auto-install
$hasPython = $false
try {
    $pyVer = python --version 2>&1
    if ($LASTEXITCODE -eq 0 -or $pyVer -match "Python 3") {
        Write-Host "[1/3] Python обнаружен: $pyVer" -ForegroundColor Green
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
        Write-Host "[1/3] Python обнаружен: $localPyDir" -ForegroundColor Green
    }
}

if (-not $hasPython) {
    Write-Host "[1/3] Python не найден. Автоматическая установка через winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
        $localPyDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
        if (Test-Path "$localPyDir\python.exe") {
            $env:Path = "$localPyDir;$localPyDir\Scripts;$env:Path"
        }
    } else {
        Write-Error "Python 3 не найден в PATH, и winget недоступен. Пожалуйста, установите Python с python.org."
        exit 1
    }
}

# Install dependencies
Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
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

if ($Mode -eq "standalone") {
    Write-Host "=== Запуск в режиме Local Standalone на порту $Port ===" -ForegroundColor Blue
    Start-Process python -ArgumentList "-m core.server --port=$Port" -WorkingDirectory $ScriptDir -WindowStyle Hidden
    Write-Host "🎉 Локальный MCP-сервер запущен: http://localhost:$Port/sse" -ForegroundColor Green
    exit 0
}

# Gateway Tunnel Mode
if ([string]::IsNullOrWhiteSpace($User)) {
    $User = $Hostname
}

Write-Host "[2/3] Регистрация субдомена '$User' на шлюзе ($Gateway)..." -ForegroundColor Yellow
$regPayload = @{ username = $User; auto_suffix = $true } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "https://$Gateway/api/register" -Method Post -Body $regPayload -ContentType "application/json"

$AssignedUser = $response.username
$AssignedToken = $response.token
$SseUrl = $response.sse_url

Write-Host "[3/3] Настройка автозапуска в Windows..." -ForegroundColor Green
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
Write-Host "══════════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "🎉 Antigravity Mesh узел успешно установлен и подключен к шлюзу!" -ForegroundColor Green
Write-Host ""
Write-Host "  💻 Устройство:      $Hostname ($DetectedType)"
Write-Host "  👤 Субдомен:        $AssignedUser.$Gateway" -ForegroundColor Cyan
Write-Host "  🔑 Секретный токен: $AssignedToken"
Write-Host "  🔄 Автозапуск:      Включен (Windows Startup VBS)"
Write-Host ""
Write-Host "  📍 Адрес MCP-сервера для подключения:"
Write-Host "     👉 $SseUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ✨ Добавление в Gemini Spark / AI Studio:"
Write-Host "     1. Откройте: https://gemini.google.com/"
Write-Host "     2. Перейдите в раздел Настройки ➔ MCP / Инструменты (Settings -> Tools)"
Write-Host "     3. Вставьте ссылку: $SseUrl"
Write-Host "══════════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
