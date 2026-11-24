@echo off
REM Windows: double-click this file. It runs setup.ps1 (Python, packages, Chromium, FFmpeg, voice model),
REM then renders a demo video and opens it. Safe to run again: finished steps are skipped.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup stopped. Read the red [XX] line above, or paste it into Claude Code and ask for help.
) else (
  echo.
  echo Rendering a demo video, this takes about a minute...
  set PYTHONUTF8=1
  ".venv\Scripts\python.exe" autopilot.py demo && start "" "channels\_template\data\videos\demo.mp4"
  if errorlevel 1 echo If FFmpeg was just installed, close this window and double-click setup.bat again.
)
echo.
pause
