@echo off
echo ========================================
echo RIT Data Collector - Volatility Case
echo ========================================
echo.

cd /d "%~dp0"

REM Try to activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo No virtual environment found, using system Python...
)

echo.
echo Starting data collector...
echo (Press Ctrl+C to stop)
echo.
echo Auto-login credentials from config.py:
echo   Username: UBCT-2
echo   Server: flserver.*REMOVED*.utoronto.ca
echo   Port: 16520
echo.

python main.py %*

pause
