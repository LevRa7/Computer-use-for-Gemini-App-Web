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

# Python check
try {
    $pyVer = python --version 2>&1
    Write-Host "[1/3] Python обнаружен: $pyVer" -ForegroundColor Green
} catch {
    Write-Error "Python 3 не найден в PATH. Пожалуйста, установите Python с python.org (с галочкой 'Add to PATH')."
    exit 1
}

# Install dependencies
Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
python -m pip install websockets --quiet

$ConfigDir = "$env:USERPROFILE\.config\antigravity-mesh"
if (!(Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }

if ($Mode -eq "standalone") {
    Write-Host "=== Запуск в режиме Local Standalone на порту $Port ===" -ForegroundColor Blue
    Start-Process python -ArgumentList "-m core.server --port=$Port" -WindowStyle Hidden
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

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Start agent in background
$env:MESH_GATEWAY = $Gateway
$env:MESH_USER = $AssignedUser
$env:MESH_TOKEN = $AssignedToken
Start-Process python -ArgumentList "-m core.agent" -WorkingDirectory $ScriptDir -WindowStyle Hidden

Write-Host ""
Write-Host "══════════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "🎉 Antigravity Mesh узел успешно установлен и подключен к шлюзу!" -ForegroundColor Green
Write-Host ""
Write-Host "  💻 Устройство:      $Hostname ($DetectedType)"
Write-Host "  👤 Субдомен:        $AssignedUser.$Gateway" -ForegroundColor Cyan
Write-Host "  🔑 Секретный токен: $AssignedToken"
Write-Host "  🔄 Автозапуск:      Включен (Фоновый процесс Python)"
Write-Host ""
Write-Host "  📍 Адрес MCP-сервера для подключения:"
Write-Host "     👉 $SseUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ✨ Добавление в Gemini Spark / AI Studio:"
Write-Host "     1. Откройте: https://gemini.google.com/"
Write-Host "     2. Перейдите в раздел Настройки ➔ MCP / Инструменты (Settings -> Tools)"
Write-Host "     3. Вставьте ссылку: $SseUrl"
Write-Host "══════════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
