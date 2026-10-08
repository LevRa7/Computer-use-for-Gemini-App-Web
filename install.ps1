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
# Windows ships a "python.exe" App Execution Alias under
# %LOCALAPPDATA%\Microsoft\WindowsApps that runs Python only while the Microsoft
# Store package stays healthy: it can be on PATH, print nothing usable and exit
# non-zero, and it stops working silently after a Store repair or an update. A
# node whose autostart pins that alias never comes back after a reboot - the only
# trace is a truncated "Python " line in agent.log - so an alias is never
# accepted here. Every candidate must be a real interpreter outside WindowsApps
# that prints 3 for sys.version_info[0] and whose own sys.executable does not
# resolve back into WindowsApps.
$WindowsAppsMarker = "\WindowsApps\"

function Test-RealPython {
    param([string]$Path)
    if (-not $Path) { return $false }
    if ($Path -like "*$WindowsAppsMarker*") { return $false }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        # Two answers in one probe: the major version, and the interpreter the
        # alias would actually run (an alias reports a WindowsApps path there).
        $probe = @(& $Path -c "import sys; print(sys.version_info[0]); print(sys.executable)" 2>&1)
        if ($LASTEXITCODE -ne 0) { return $false }
        $rows = @($probe | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
        if ($rows.Count -lt 1 -or "$($rows[0])" -ne "3") { return $false }
        if ($rows.Count -ge 2 -and "$($rows[1])" -like "*$WindowsAppsMarker*") { return $false }
        return $true
    } catch { return $false }
}

function Test-PythonWebsockets {
    param([string]$Path)
    try {
        & $Path -c "import websockets" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-PythonCandidates {
    $list = @()
    # Every "python" on PATH, not just the first: the first hit is usually the
    # Store alias while a real install sits further down the same PATH.
    foreach ($cmd in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($cmd -and $cmd.Source) { $list += $cmd.Source }
    }
    $list += @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    # Any per-user 3.x the python.org installer left behind, newest first. This
    # used to be a second, separate fallback that forgot to record the path it
    # found, so a machine with only 3.13 was pushed into a fresh download.
    $localRoot = "$env:LOCALAPPDATA\Programs\Python"
    if (Test-Path -LiteralPath $localRoot) {
        $list += @(Get-ChildItem -Path $localRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' })
    }
    # The "py" launcher is not an interpreter: ask it which real python.exe it
    # runs, so the autostart entry always names a python.exe, never "py -3".
    $cmdPy = Get-Command py -ErrorAction SilentlyContinue
    if ($cmdPy -and $cmdPy.Source) {
        try {
            $real = @(& $cmdPy.Source -3 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $real.Count -ge 1 -and "$($real[0])".Trim()) {
                $list += "$($real[0])".Trim()
            }
        } catch {}
    }
    return @($list | Where-Object { $_ } | Select-Object -Unique)
}

function Select-RealPython {
    # An interpreter that already has websockets wins: the dependency step then
    # cannot silently install into a different, more fragile Python.
    $fallback = $null
    foreach ($cand in (Get-PythonCandidates)) {
        if (-not (Test-RealPython $cand)) { continue }
        if (Test-PythonWebsockets $cand) { return $cand }
        if (-not $fallback) { $fallback = $cand }
    }
    return $fallback
}

function Add-PythonToPath {
    param([string]$Path)
    if (-not $Path) { return }
    $dir = Split-Path -Parent $Path
    if ($dir -and ($env:Path -notlike "*$dir*")) {
        $env:Path = "$dir;$dir\Scripts;$env:Path"
    }
}

$hasPython = $false
$PyExe = $null
$PyExe = Select-RealPython
if ($PyExe) {
    Add-PythonToPath $PyExe
    $hasPython = $true
    $pyVer = & $PyExe --version 2>&1
    if ($Lang -eq "ru") {
        Write-Host "[1/3] Python обнаружен: $pyVer ($PyExe)" -ForegroundColor Green
    } else {
        Write-Host "[1/3] Python detected: $pyVer ($PyExe)" -ForegroundColor Green
    }
    if (-not (Test-PythonWebsockets $PyExe)) {
        if ($Lang -eq "ru") {
            Write-Host "[1/3] В выбранном Python нет websockets - он будет установлен на шаге 2." -ForegroundColor DarkYellow
        } else {
            Write-Host "[1/3] The selected Python has no websockets yet - it will be installed in step 2." -ForegroundColor DarkYellow
        }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[1/3] Рабочий Python 3 не найден (ярлык Microsoft Store за интерпретатор не считается)." -ForegroundColor Yellow
} else {
    Write-Host "[1/3] No working Python 3 found (the Microsoft Store alias does not count)." -ForegroundColor Yellow
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
    # The live node state, so a preflight can say "your node is offline right now"
    # before anything on disk is touched. The name may still gain a suffix at
    # registration (auto_suffix), so this stays best effort.
    $nodeState = "not checked"
    if (-not $DomainMissing) {
        $probeNode = if ($User) { $User } else { $Hostname }
        try {
            $probeHealth = Invoke-RestMethod -Uri "https://$Gateway/health?user=$probeNode" -TimeoutSec 5 -ErrorAction Stop
            $nodeState = if ($probeHealth.node_online) { "online ($probeNode)" } else { "offline ($probeNode)" }
        } catch {
            $nodeState = "gateway unreachable"
        }
    }
    if ($Lang -eq "ru") {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Домен       : $domainState"
        Write-Host "[DRY-RUN] Конфиг      : $env:USERPROFILE\.config\antigravity-mesh"
        Write-Host "[DRY-RUN] Автозапуск  : $([Environment]::GetFolderPath('Startup'))\antigravity-agent.vbs"
        Write-Host "[DRY-RUN] Узел        : $nodeState"
        Write-Host "[DRY-RUN] Действий не выполнено." -ForegroundColor Yellow
    } else {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Domain      : $domainState"
        Write-Host "[DRY-RUN] Config      : $env:USERPROFILE\.config\antigravity-mesh"
        Write-Host "[DRY-RUN] Autostart   : $([Environment]::GetFolderPath('Startup'))\antigravity-agent.vbs"
        Write-Host "[DRY-RUN] Node        : $nodeState"
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
    # Re-resolve with exactly the same rules: a fresh interpreter only counts if it
    # is a real python.exe outside WindowsApps and it actually runs.
    if (-not $hasPython) {
        $PyExe = Select-RealPython
        if ($PyExe) {
            Add-PythonToPath $PyExe
            $hasPython = $true
            if ($Lang -eq "ru") {
                Write-Host "[1/3] Python обнаружен: $PyExe" -ForegroundColor Green
            } else {
                Write-Host "[1/3] Python detected: $PyExe" -ForegroundColor Green
            }
        }
    }
}

# Fail closed. Without a real interpreter the autostart entry points at nothing and
# the node stays offline until a human reads the log - the exact state the
# Microsoft Store alias produced, where the only trace was one torn log line.
if (-not $hasPython -or -not $PyExe) {
    if ($Lang -eq "ru") {
        Write-Host "[!] Рабочий Python 3 не найден." -ForegroundColor Red
        Write-Host "    Установите Python с python.org (НЕ из Microsoft Store) и повторите установку." -ForegroundColor Yellow
        Write-Host "    Остановка: без интерпретатора автозапуск узла работать не будет." -ForegroundColor Yellow
    } else {
        Write-Host "[!] No working Python 3 found." -ForegroundColor Red
        Write-Host "    Install Python from python.org (NOT from the Microsoft Store) and re-run." -ForegroundColor Yellow
        Write-Host "    Stopping: an autostart entry without an interpreter can never work." -ForegroundColor Yellow
    }
    exit 1
}
if ("$PyExe" -like "*$WindowsAppsMarker*") {
    if ($Lang -eq "ru") {
        Write-Host "[!] Отказ: выбранный интерпретатор - ярлык Microsoft Store ($PyExe)." -ForegroundColor Red
    } else {
        Write-Host "[!] Refusing to use the Microsoft Store alias as the interpreter ($PyExe)." -ForegroundColor Red
    }
    exit 1
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
    # The watchdog, the doctor and the updater are fetched too: the scheduled
    # tasks registered below run the first and the last, and a node whose agent
    # dies - or whose code goes stale - must heal and update itself without
    # anyone re-running the installer by hand.
    $OpsDir = "$BootstrapDir\ops\windows"
    if (!(Test-Path $CoreDir)) { New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null }
    if (!(Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }
    if (!(Test-Path $OpsDir)) { New-Item -ItemType Directory -Path $OpsDir -Force | Out-Null }
    
    $files = @(
        "core/agent.py",
        "core/server.py",
        "core/mcp_tools.py",
        "core/web_share.py",
        "core/domain.py",
        "core/vitals.py",
        "core/updater.py",
        "core/version.py",
        "core/__init__.py",
        "skills/orchestrator.md",
        "ops/windows/agent-watchdog.ps1",
        "ops/windows/run-hidden.vbs",
        "ops/update.ps1",
        "ops/doctor.ps1"
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
    # The interpreter is pinned by full path because PATH may hold the Microsoft
    # Store alias, which stops working after a Store repair or an update. "-u"
    # keeps stdout unbuffered, so a killed process cannot leave a torn log line as
    # the only evidence of what happened.
    if (-not $pyExeOnly -or "$pyExeOnly" -like "*$WindowsAppsMarker*") {
        Write-Host "[!] Refusing to write an autostart entry for '$pyExeOnly'." -ForegroundColor Red
        exit 1
    }
    $startupDir = [Environment]::GetFolderPath("Startup")
    $startupVbs = "$startupDir\antigravity-agent.vbs"
    $pyExeForVbs = $pyExeOnly
    $agentLog = "$ConfigDir\agent.log"
    @"
    Set WshShell = CreateObject("WScript.Shell")
    WshShell.Environment("PROCESS")("MESH_GATEWAY") = "$Gateway"
    WshShell.Environment("PROCESS")("MESH_USER") = "$AssignedUser"
    WshShell.Environment("PROCESS")("MESH_TOKEN") = "$AssignedToken"
    WshShell.CurrentDirectory = "$ScriptDir"
    ' Log the output so a silent autostart failure can be diagnosed later.
    WshShell.Run "cmd /c """"$pyExeForVbs"" -u -m core.agent >> """"$agentLog"""" 2>&1""", 0, False
"@ | Out-File -FilePath $startupVbs -Encoding Unicode

# A Startup entry runs once per logon: it cannot recover a node whose agent died
# (crash, Windows Update reboot, broken dependency). A Scheduled Task repeats the
# watchdog every five minutes, so a dead agent comes back on its own.
#
# Both tasks are started through ops\windows\run-hidden.vbs rather than
# powershell.exe directly. A task has no console of its own, so Windows allocates
# one for powershell.exe and only then honours "-WindowStyle Hidden": the window is
# created, drawn and hidden again, and the user sees a black window flash every
# five minutes. wscript.exe is a GUI host - it never allocates a console - and
# WshShell.Run with window style 0 passes SW_HIDE to the child, so nothing is ever
# drawn. The task argument names the node script relative to the payload, which
# run-hidden.vbs resolves from its own location, so the action keeps working if the
# payload moves.
$watchdog = Join-Path $ScriptDir 'ops\windows\agent-watchdog.ps1'
$runHidden = Join-Path $ScriptDir 'ops\windows\run-hidden.vbs'
if (Test-Path -LiteralPath $watchdog) {
    # Two spellings of the same action, because the two registration paths parse
    # quotes differently: schtasks.exe needs the quotes around the script path
    # backslash-escaped (otherwise a profile path with a space is split into two
    # arguments and the task is rejected outright), while the ScheduledTasks
    # cmdlets take the argument literally.
    if (Test-Path -LiteralPath $runHidden) {
        $watchdogHost = 'wscript.exe'
        $watchdogArguments = '"' + $runHidden + '" "ops\windows\agent-watchdog.ps1" -Quiet'
        $watchdogTaskCommand = 'wscript.exe \"' + $runHidden + '\" \"ops\windows\agent-watchdog.ps1\" -Quiet'
    } else {
        # A payload without the launcher still self-heals; it just flashes. Saying
        # so is better than leaving the operator with a window they cannot explain.
        if ($Lang -eq "ru") {
            Write-Host "[!] $runHidden не найден: задача-сторож будет запускаться напрямую и мелькать окном." -ForegroundColor Yellow
        } else {
            Write-Host "[!] $runHidden is missing: the watchdog task will run PowerShell directly and flash a window." -ForegroundColor Yellow
        }
        $watchdogHost = 'powershell.exe'
        $watchdogArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchdog + '" -Quiet'
        $watchdogTaskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $watchdog + '\" -Quiet'
    }
    $watchdogRegistered = $false
    $watchdogError = ''
    # schtasks.exe is tried first because it registers a task for the CURRENT user
    # without elevation, while Register-ScheduledTask needs write access to the task
    # store (denied for a normal user on a hardened or domain-joined machine - that
    # was measured on the machine this watchdog was built for). The minute schedule
    # repeats indefinitely, so logon is already covered by the Startup entry above
    # and needs no second trigger here.
    $watchdogError = (& schtasks.exe /Create /TN 'AntigravityMeshWatchdog' /TR $watchdogTaskCommand `
        /SC MINUTE /MO 5 /F 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { $watchdogRegistered = $true }
    if (-not $watchdogRegistered) {
        try {
            $watchdogAction = New-ScheduledTaskAction -Execute $watchdogHost -Argument $watchdogArguments -WorkingDirectory $ScriptDir
            # No -RepetitionDuration on purpose: PowerShell serialises
            # [TimeSpan]::MaxValue as P99999999DT23H59M59S, which the Task Scheduler
            # schema rejects outright; an omitted duration means "repeat
            # indefinitely", which is exactly what this needs.
            $watchdogTriggers = @(
                (New-ScheduledTaskTrigger -AtLogOn),
                (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
            )
            $watchdogSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
            Register-ScheduledTask -TaskName 'AntigravityMeshWatchdog' -Action $watchdogAction `
                -Trigger $watchdogTriggers -Settings $watchdogSettings -Force `
                -Description 'Restarts the Antigravity Mesh node agent when it dies.' | Out-Null
            $watchdogRegistered = $true
            $watchdogError = ''
        } catch {
            $watchdogRegistered = $false
            $watchdogError = $_.Exception.Message
        }
    }
    if ($watchdogRegistered) {
        if ($Lang -eq "ru") {
            Write-Host "[OK] Задача-сторож 'AntigravityMeshWatchdog' зарегистрирована (каждые 5 минут)." -ForegroundColor Green
        } else {
            Write-Host "[OK] Watchdog task 'AntigravityMeshWatchdog' registered (every 5 minutes)." -ForegroundColor Green
        }
    } else {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось зарегистрировать задачу-сторож. Автозапуск при входе в систему всё равно создан." -ForegroundColor Yellow
            Write-Host "    Запустить проверку вручную: $watchdog" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not register the watchdog task. The Startup entry still starts the agent at logon." -ForegroundColor Yellow
            Write-Host "    Run the check by hand: $watchdog" -ForegroundColor Yellow
        }
        if ($watchdogError) { Write-Host "    $watchdogError" -ForegroundColor DarkYellow }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] $watchdog не найден: самовосстановления у узла не будет." -ForegroundColor Yellow
} else {
    Write-Host "[!] $watchdog not found: the node has no self-healing task." -ForegroundColor Yellow
}

# The watchdog above recovers a node that is DOWN; this task moves a node that is
# merely OUT OF DATE. A machine nobody logs into would otherwise keep running
# whatever was installed the day it was set up, so the updater checks the newest
# GitHub release once a day, installs it with a rollback backup and lets its
# helper bring the agent back on the new code. The agent also checks for itself
# (MESH_UPDATE_* in core/updater.py); this task is what covers a node whose agent
# is not running at 03:30.
$updaterScript = Join-Path $ScriptDir 'ops\update.ps1'
if (Test-Path -LiteralPath $updaterScript) {
    # Same windowless host as the watchdog: a daily flash is still a flash.
    if (Test-Path -LiteralPath $runHidden) {
        $updaterHost = 'wscript.exe'
        $updaterArguments = '"' + $runHidden + '" "ops\update.ps1" -Quiet'
        $updaterTaskCommand = 'wscript.exe \"' + $runHidden + '\" \"ops\update.ps1\" -Quiet'
    } else {
        $updaterHost = 'powershell.exe'
        $updaterArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $updaterScript + '" -Quiet'
        $updaterTaskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $updaterScript + '\" -Quiet'
    }
    $updaterRegistered = $false
    $updaterError = ''
    # schtasks.exe first, for the same reason as the watchdog: it needs no write
    # access to the task store, which a normal user does not have on a hardened
    # or domain-joined machine.
    $updaterError = (& schtasks.exe /Create /TN 'AntigravityMeshUpdater' /TR $updaterTaskCommand `
        /SC DAILY /MO 1 /ST 03:30 /F 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { $updaterRegistered = $true }
    if (-not $updaterRegistered) {
        try {
            $updaterAction = New-ScheduledTaskAction -Execute $updaterHost -Argument $updaterArguments -WorkingDirectory $ScriptDir
            $updaterTriggers = @((New-ScheduledTaskTrigger -Daily -At '03:30'))
            $updaterSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
            Register-ScheduledTask -TaskName 'AntigravityMeshUpdater' -Action $updaterAction `
                -Trigger $updaterTriggers -Settings $updaterSettings -Force `
                -Description 'Updates the Antigravity Mesh node from the newest GitHub release.' | Out-Null
            $updaterRegistered = $true
            $updaterError = ''
        } catch {
            $updaterRegistered = $false
            $updaterError = $_.Exception.Message
        }
    }
    if ($updaterRegistered) {
        if ($Lang -eq "ru") {
            Write-Host "[OK] Задача обновления 'AntigravityMeshUpdater' зарегистрирована (ежедневно в 03:30)." -ForegroundColor Green
            Write-Host "    Проверить сейчас: powershell -File `"$updaterScript`" -Check" -ForegroundColor DarkGray
        } else {
            Write-Host "[OK] Update task 'AntigravityMeshUpdater' registered (daily at 03:30)." -ForegroundColor Green
            Write-Host "    Check right now: powershell -File `"$updaterScript`" -Check" -ForegroundColor DarkGray
        }
    } else {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось зарегистрировать задачу обновления. Узел всё равно проверяет версию сам, пока агент запущен." -ForegroundColor Yellow
            Write-Host "    Запустить проверку вручную: powershell -File `"$updaterScript`" -Check" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not register the update task. A running agent still checks for releases itself." -ForegroundColor Yellow
            Write-Host "    Check by hand: powershell -File `"$updaterScript`" -Check" -ForegroundColor Yellow
        }
        if ($updaterError) { Write-Host "    $updaterError" -ForegroundColor DarkYellow }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] $updaterScript не найден: автоматического обновления у узла не будет." -ForegroundColor Yellow
} else {
    Write-Host "[!] $updaterScript not found: the node has no automatic update task." -ForegroundColor Yellow
}

# Launch now
Start-Process $pyExeOnly -ArgumentList "-u -m core.agent" -WorkingDirectory $ScriptDir -WindowStyle Hidden

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

# ------------------------------------------------------------------------------
# Post-install check. An install that looks successful while the agent never
# connects is what leaves a client with an opaque frontend error, so the node's
# state is verified here and reported with the interpreter and the log to read.
#
# Deliberately no non-zero exit code: install-gui.ps1 only extracts the MCP URL
# from a successful run, and that link matters most exactly when the node is not
# up yet. The watchdog task retries every five minutes in the meantime.
# ------------------------------------------------------------------------------
$NodeOnline = $false
for ($attempt = 1; $attempt -le 15; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "$PublicBaseUrl/health?user=$AssignedUser" -TimeoutSec 10 -ErrorAction Stop
        if ($health.node_online) { $NodeOnline = $true; break }
    } catch {}
}
if ($NodeOnline) {
    if ($Lang -eq "ru") {
        Write-Host "[OK] Узел '$AssignedUser' на связи со шлюзом." -ForegroundColor Green
    } else {
        Write-Host "[OK] Node '$AssignedUser' is online on the gateway." -ForegroundColor Green
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] Узел '$AssignedUser' не вышел на связь за 30 с." -ForegroundColor Red
    Write-Host "    Интерпретатор автозапуска: $pyExeOnly" -ForegroundColor Yellow
    Write-Host "    Смотрите хвост $agentLog и запустите .\ops\doctor.ps1" -ForegroundColor Yellow
    Write-Host "    Задача 'AntigravityMeshWatchdog' повторит попытку через 5 минут." -ForegroundColor Yellow
} else {
    Write-Host "[!] Node '$AssignedUser' did not come online within 30 s." -ForegroundColor Red
    Write-Host "    Autostart interpreter: $pyExeOnly" -ForegroundColor Yellow
    Write-Host "    Read the tail of $agentLog and run .\ops\doctor.ps1" -ForegroundColor Yellow
    Write-Host "    The 'AntigravityMeshWatchdog' task retries every 5 minutes." -ForegroundColor Yellow
}
