#!/bin/bash
# Launch MES Trading Intelligence System
cd "$(dirname "$0")"
source bin/activate 2>/dev/null || true
python -m mes_intel
