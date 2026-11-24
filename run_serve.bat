@echo off
REM Starts the bot (daily scout, Telegram approvals, scheduled publishing).
REM   run_serve.bat             one channel in channels\
REM   run_serve.bat mychannel   several channels: name the one to run
cd /d "%~dp0"
set PYTHONUTF8=1
if "%~1"=="" (".venv\Scripts\python.exe" autopilot.py serve) else (".venv\Scripts\python.exe" autopilot.py serve --channel %~1)
