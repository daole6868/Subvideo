@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Chua cai dat. Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m subvideo
