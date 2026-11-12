@echo off
rem Double-click to stop this repository's release runner; Start-Release-Runner.cmd resumes it.
set "ps=powershell"
where pwsh >nul 2>nul && set "ps=pwsh"
%ps% -NoProfile -ExecutionPolicy Bypass -File "%~dp0runner\start-runner.ps1" -Stop
set status=%errorlevel%
pause
exit /b %status%
