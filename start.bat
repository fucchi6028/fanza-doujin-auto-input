@echo off
cd /d "%~dp0"

echo ========================================
echo   FANZA Auto Input Tool (tkinter)
echo ========================================
echo.

where python > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

pip show selenium > nul 2>&1
if %errorlevel% neq 0 (
    echo Installing dependencies...
    pip install selenium requests openai
)

echo.
echo Starting application...
echo.

python main_tk.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start application
    pause
)
