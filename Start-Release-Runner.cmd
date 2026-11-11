@echo off
rem Double-click to start this repository's release runner; safe to rerun.
set "ps=powershell"
where pwsh >nul 2>nul && set "ps=pwsh"
%ps% -NoProfile -ExecutionPolicy Bypass -File "%~dp0runner\start-runner.ps1" %*
set status=%errorlevel%
pause
exit /b %status%
