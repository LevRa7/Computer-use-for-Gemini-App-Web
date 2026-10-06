# ==============================================================================
#  Antigravity Mesh - run the pytest suite on Windows
#
#  A plain "python -m pytest" is not enough on Windows:
#
#    1. pytest, starlette, websockets, google-antigravity and the MCP SDK are
#       not part of a fresh interpreter install.
#    2. The optional MCP SDK imports pywintypes, and pywin32 exposes it only
#       through a .pth file. "pip install --target" does not process .pth files,
#       so win32, win32\lib and pywin32_system32 have to be on PYTHONPATH or
#       tests/test_mcp_typed_integration.py fails with ModuleNotFoundError.
#    3. Console output must be UTF-8, otherwise Russian assertions print as
#       mojibake in the failure report.
#    4. Wheels are interpreter-specific: a deps directory built for 3.12 cannot
#       be imported by 3.13 ("No module named 'pydantic_core._pydantic_core'").
#       The default deps directory is therefore tagged with the interpreter's
#       version, and an interpreter change gets its own directory instead of
#       silently reusing an incompatible one.
#
#  This file is deliberately ASCII-only. Windows PowerShell 5.1 reads a BOM-less
#  script with the ANSI code page, while a UTF-8 BOM breaks "irm ... | iex"
#  because Invoke-WebRequest hands the mark to iex as part of the first token.
#  install.ps1 has to print Russian and therefore keeps a BOM; this helper can
#  avoid non-ASCII entirely and sidestep the whole issue.
#
#  Usage:
#     .\run_tests_windows.ps1                    # install missing deps, then test
#     .\run_tests_windows.ps1 -CheckOnly         # report the plan, change nothing
#     .\run_tests_windows.ps1 -SkipInstall
#     .\run_tests_windows.ps1 -Python C:\Python312\python.exe
#     .\run_tests_windows.ps1 -- -k windows_port -v
# ==============================================================================
param(
    [string]$DepsDir = "",
    [string]$Python = "",
    [switch]$SkipInstall,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Continue'

$REQUIRED = @('pytest', 'pytest_asyncio', 'starlette', 'websockets', 'google.antigravity', 'mcp', 'pywintypes')
$PACKAGES = @('pytest', 'pytest-asyncio', 'starlette', 'websockets', 'google-antigravity', 'mcp', 'pywin32')

# --- 1. find a real interpreter --------------------------------------------
# The "python.exe" in WindowsApps is an App Execution Alias: it can be on PATH
# and still run nothing at all, so every candidate is probed for a working sys.
function Resolve-Python {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $localRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $localRoot) {
        $candidates += Get-ChildItem $localRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' }
    }
    foreach ($cand in $candidates) {
        if (-not $cand -or -not (Test-Path $cand)) { continue }
        $probe = & $cand -c "import sys; print(sys.version_info[0])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq '3') { return $cand }
    }
    return $null
}

if (-not $Python) { $Python = Resolve-Python }
if (-not $Python) {
    Write-Host "No working Python 3 interpreter found. Pass -Python <path>." -ForegroundColor Red
    exit 2
}

$abi = (& $Python -c "import sys; print('py%d.%d' % sys.version_info[:2])" 2>$null)
if (-not $abi) { $abi = 'py3' }
if (-not $DepsDir) { $DepsDir = Join-Path $PSScriptRoot ".win-test-deps\$abi" }

Write-Host "Python   : $Python" -ForegroundColor Cyan
Write-Host "Version  : $(& $Python --version 2>&1)" -ForegroundColor Cyan
Write-Host "ABI tag  : $abi" -ForegroundColor Cyan
Write-Host "Deps dir : $DepsDir" -ForegroundColor Cyan

# --- 2. probe the dependencies --------------------------------------------
$env:PYTHONIOENCODING = 'utf-8'
# pywin32's .pth is not processed by "pip install --target", so its subdirs are
# added explicitly; without them the MCP SDK cannot import on Windows.
$depsOnPath = "$DepsDir;$DepsDir\win32;$DepsDir\win32\lib;$DepsDir\pywin32_system32"
$env:PYTHONPATH = $depsOnPath

$imports = ($REQUIRED | ForEach-Object { "import $_" }) -join '; '
& $Python -c $imports 2>$null
$depsOk = ($LASTEXITCODE -eq 0)

if (-not $depsOk) {
    $existing = @()
    if (Test-Path $DepsDir) {
        $existing = Get-ChildItem $DepsDir -Filter '*.dist-info' -Directory -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty Name
    }
    if ($existing.Count -gt 0) {
        Write-Host "The deps dir exists but does not satisfy this interpreter" -ForegroundColor Yellow
        Write-Host "  (wheels are ABI-specific; this looks like a $abi directory built by another version)." -ForegroundColor Yellow
        Write-Host "  It will be re-installed with --upgrade." -ForegroundColor Yellow
    }
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "CHECK-ONLY: dependencies satisfied = $depsOk"
    Write-Host "CHECK-ONLY: would run: $Python -m pytest -q -p no:cacheprovider in $PSScriptRoot"
    if (-not $depsOk -and -not $SkipInstall) {
        Write-Host "CHECK-ONLY: would install $($PACKAGES -join ', ') into $DepsDir"
    }
    exit 0
}

if (-not $depsOk -and -not $SkipInstall) {
    Write-Host "Installing test dependencies (this happens once per interpreter)..." -ForegroundColor Yellow
    & $Python -m pip install --disable-pip-version-check --no-input --quiet --upgrade --target $DepsDir @PACKAGES
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependency installation failed." -ForegroundColor Red
        exit 2
    }
    $env:PYTHONPATH = $depsOnPath
    & $Python -c $imports 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependencies still do not import. Delete $DepsDir and retry." -ForegroundColor Red
        exit 2
    }
}

# --- 3. run the suite ------------------------------------------------------
$forwarded = @()
if ($args.Count -gt 0) { $forwarded = $args }

Push-Location $PSScriptRoot
try {
    & $Python -m pytest -q -p no:cacheprovider @forwarded
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -eq 0) {
    Write-Host "All tests passed on Windows." -ForegroundColor Green
} else {
    Write-Host "pytest exit code: $code" -ForegroundColor Red
}
exit $code
