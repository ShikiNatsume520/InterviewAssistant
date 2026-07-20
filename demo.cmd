@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_demo.ps1" %*
set "exit_code=%ERRORLEVEL%"
endlocal & exit /b %exit_code%
