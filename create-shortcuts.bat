@echo off
rem Adds LocalFlow to the Start Menu (search "LocalFlow") and the Desktop.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m localflow --create-shortcuts
pause
