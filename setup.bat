@echo off
REM One-time setup: .venv + packages + whisper.cpp engine + model. Extra args go to setup.ps1 (e.g. -Engine gfx110X).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
