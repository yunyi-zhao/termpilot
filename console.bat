@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
if exist "C:\ProgramData\anaconda3\python.exe" set "PY=C:\ProgramData\anaconda3\python.exe"
title termpilot console

rem start the server if nothing is listening on 8787
netstat -ano | findstr /R /C:":8787 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [termpilot] server not running, starting it...
    call "%~dp0start.bat"
)

"%PY%" "%~dp0termpilot.py" console
endlocal
