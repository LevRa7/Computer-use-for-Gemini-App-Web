@echo off
rem ============================================================================
rem  Antigravity Mesh - launcher for the visual installer (install-gui.ps1)
rem
rem  Double-click this file, or run it from a terminal. It only picks the
rem  interpreter and keeps the window open when something goes wrong; all the
rem  real work is in install-gui.ps1.
rem
rem  Windows PowerShell 5.1 is used deliberately: it is present on every
rem  supported Windows build, and it is the interpreter install.ps1 and the
rem  repository tests target.
rem ============================================================================
setlocal

set "SCRIPT=%~dp0install-gui.ps1"
if not exist "%SCRIPT%" (
    echo.
    echo install-gui.ps1 was not found next to this launcher:
    echo   %SCRIPT%
    echo.
    pause
    exit /b 1
)

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"

rem -Sta is required: the wizard puts the MCP URL on the clipboard, and the
rem clipboard needs a single-threaded apartment.
"%PS%" -NoProfile -ExecutionPolicy Bypass -Sta -File "%SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The visual installer exited with code %EXITCODE%.
    echo.
    pause
)

endlocal & exit /b %EXITCODE%
