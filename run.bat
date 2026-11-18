@echo off
REM Dictation - double-click to start (no console window).
REM Python lookup order: config\local.bat (set PY=...) -> .venv\Scripts\pythonw.exe -> pythonw on PATH
setlocal
set "PY="
if exist "%~dp0config\local.bat" call "%~dp0config\local.bat"
if not defined PY if exist "%~dp0.venv\Scripts\pythonw.exe" set "PY=%~dp0.venv\Scripts\pythonw.exe"
if not defined PY set "PY=pythonw"

start "" "%PY%" "%~dp0ui.py"
exit /b 0
