#!/bin/bash
# 🧪 THE LAB - Trade Journal Launcher
cd "$(dirname "$0")/.."
source bin/activate
echo ""
echo "  🧪 THE LAB is cooking..."
echo "  ➜ http://localhost:5050"
echo "  Press Ctrl+C to shut it down"
echo ""
open http://localhost:5050
python trade_journal/app.py
