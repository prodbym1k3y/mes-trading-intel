#!/bin/bash
# MES Trading Intelligence System — launch script
cd "$(dirname "$0")"

# Kill any existing instance
pkill -f "mes_intel" 2>/dev/null
sleep 1

# Activate venv if not already active
if [[ "$VIRTUAL_ENV" == "" ]]; then
    source bin/activate
fi

python3 -m mes_intel
