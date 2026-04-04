@echo off
REM MES Trading Intelligence System — Windows launcher
cd /d "%~dp0"

echo.
echo  MES INTEL — Pre-flight checks...
echo.

REM Kill any existing instance
taskkill /f /fi "WINDOWTITLE eq *mes_intel*" 2>nul
taskkill /f /fi "COMMANDLINE eq *python*mes_intel*" 2>nul

REM Check for venv
if not exist "Scripts\activate.bat" (
    echo  [X] No venv found. Run: python -m venv .
    exit /b 1
)
call Scripts\activate.bat

REM Check DB directory
if not exist "var\mes_intel" (
    mkdir var\mes_intel
    echo  [+] Created var\mes_intel\
)

REM Check config
if exist "var\mes_intel\config.json" (
    echo  [OK] Config found
) else (
    echo  [!] No config.json — using defaults
)

REM Python version
python --version

REM Quick import test
python -c "from mes_intel.ui.app import MainWindow" 2>nul
if errorlevel 1 (
    echo  [X] Import check FAILED — check dependencies
    exit /b 1
)
echo  [OK] Import check passed

echo.
echo  Starting MES Trading Intelligence...
echo.
python -m mes_intel
