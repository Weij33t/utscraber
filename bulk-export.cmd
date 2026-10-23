@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First install the project dependencies using the README instructions.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0bulk-export.py"
