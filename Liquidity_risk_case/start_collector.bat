@echo off
echo ========================================
echo RIT Data Collector - Liquidity Risk Case
echo ========================================
echo.

cd /d "%~dp0"

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo.
echo Starting data collector...
echo (Press Ctrl+C to stop)
echo.

python main.py %*

pause
