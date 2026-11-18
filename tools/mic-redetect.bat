@echo off
REM Double-click: re-detect a USB microphone that failed to enumerate (asks for admin once).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mic-redetect.ps1" %*
