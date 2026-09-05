@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
if exist "C:\ProgramData\anaconda3\python.exe" set "PY=C:\ProgramData\anaconda3\python.exe"

rem --- approver settings (model / effort / interval ...). "start.bat /q" skips the questions.
if /i "%~1"=="/q" (
    "%PY%" "%~dp0termpilot.py" setup --quick
) else (
    "%PY%" "%~dp0termpilot.py" setup
)

rem --- firewall: allow port 8787 from the Tailscale range (only works when elevated; silently skipped otherwise)
netsh advfirewall firewall show rule name="termpilot 8787" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="termpilot 8787" dir=in action=allow protocol=TCP localport=8787 remoteip=100.64.0.0/10 >nul 2>&1
    if errorlevel 1 (echo [termpilot] firewall rule not added - run this .bat as Administrator once) else (echo [termpilot] firewall rule added)
)

rem --- (re)start the server in the background (auto-starts the approver if enabled)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
    echo [termpilot] start failed, see serve.err
    pause
    exit /b 1
)
echo.
echo [termpilot] CLI:  "%PY%" "%~dp0termpilot.py" ls ^| new NAME ^| prompts ^| approve ID ^| setup
echo [termpilot] stop: stop.bat      quick restart without questions: start.bat /q
endlocal
