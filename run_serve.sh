#!/usr/bin/env bash
# Starts the bot (daily scout, Telegram approvals, scheduled publishing).
#   ./run_serve.sh              one channel in channels/
#   ./run_serve.sh mychannel    several channels: name the one to run
cd "$(dirname "$0")"
export PYTHONUTF8=1
exec .venv/bin/python autopilot.py serve ${1:+--channel "$1"}
