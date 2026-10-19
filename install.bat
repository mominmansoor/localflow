@echo off
rem Double-click this to install LocalFlow. Add "startup" to also launch at login:  install.bat startup
cd /d "%~dp0"
set EXTRA=
if /i "%1"=="startup" set EXTRA=-Startup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %EXTRA%
if errorlevel 1 pause
