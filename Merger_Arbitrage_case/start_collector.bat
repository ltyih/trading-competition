@echo off
title Merger Arbitrage Data Collector - *REMOVED* 2026
color 0A

echo ============================================================
echo   MERGER ARBITRAGE DATA COLLECTOR - *REMOVED* 2026
echo   24/7 Market Data Collection System
echo ============================================================
echo.
echo   5 Deals: TGX/PHR, BYL/CLD, GGD/PNR, FSR/ATB, SPK/EEC
echo   News-driven probability tracking
echo.
echo ============================================================
echo.

cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.8+ and add it to PATH
    pause
    exit /b 1
)

REM Install dependencies if needed
echo Checking dependencies...
pip install requests psutil pyautogui pygetwindow >nul 2>&1

echo.
echo Starting collector... Press Ctrl+C to stop.
echo Logs are saved to the logs/ folder.
echo.

:LOOP
python main.py
echo.
echo Collector stopped. Restarting in 10 seconds...
echo Press Ctrl+C to exit completely.
timeout /t 10 /nobreak
goto LOOP
