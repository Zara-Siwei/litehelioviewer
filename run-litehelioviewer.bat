@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-LiteHelioviewer.ps1"
if errorlevel 1 pause
