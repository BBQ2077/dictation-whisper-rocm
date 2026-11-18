@echo off
REM Debug launcher: keeps a console window open to show errors.
REM Python lookup order: config\local.bat (set DICTATION_PYTHON=...) -> .venv\Scripts\python.exe -> python on PATH
setlocal
set "PYC="
if exist "%~dp0config\local.bat" call "%~dp0config\local.bat"
if defined DICTATION_PYTHON set "PYC=%DICTATION_PYTHON%"
if not defined PYC if exist "%~dp0.venv\Scripts\python.exe" set "PYC=%~dp0.venv\Scripts\python.exe"
if not defined PYC set "PYC=python"

"%PYC%" "%~dp0ui.py"
pause
