@echo off
rem Runs with a console so you can see timings and errors.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m localflow --debug
pause
