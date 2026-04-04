#!/bin/bash
# MES Trading Intelligence System — launch script
set -e
cd "$(dirname "$0")"

# Kill any existing instance
pkill -9 -f "python3 -m mes_intel" 2>/dev/null || true
sleep 1

# Activate venv if not already active
if [[ "$VIRTUAL_ENV" == "" ]]; then
    source bin/activate
fi

# Health checks
echo "◈ MES INTEL — Pre-flight checks..."

# Check DB directory exists
if [ ! -d "var/mes_intel" ]; then
    mkdir -p var/mes_intel
    echo "  ✓ Created var/mes_intel/"
fi

# Check config exists
if [ ! -f "var/mes_intel/config.json" ]; then
    echo "  ⚠ No config.json — using defaults"
else
    echo "  ✓ Config found"
fi

# Check Python version
PY_VER=$(python3 --version 2>&1)
echo "  ✓ $PY_VER"

# Quick import test
if ! python3 -c "from mes_intel.ui.app import MainWindow" 2>/dev/null; then
    echo "  ✗ Import check FAILED — check dependencies"
    exit 1
fi
echo "  ✓ Import check passed"

# Check if market is open (Phoenix time, RTH 6:30-14:00)
HOUR=$(TZ="America/Phoenix" date +%H)
MIN=$(TZ="America/Phoenix" date +%M)
MINS=$((HOUR * 60 + MIN))
if [ $MINS -ge 390 ] && [ $MINS -lt 840 ]; then
    echo "  ✓ RTH session active ($(TZ='America/Phoenix' date +%H:%M) Phoenix)"
else
    echo "  ⚠ Outside RTH ($(TZ='America/Phoenix' date +%H:%M) Phoenix) — overnight session"
fi

echo "◈ Launching MES Trading Intelligence..."
echo ""

python3 -m mes_intel
