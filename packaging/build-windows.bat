@echo off
rem Double-click to build the Windows program (dist\ThrashDash-<version>-windows-x64.zip). See build.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
echo.
pause
