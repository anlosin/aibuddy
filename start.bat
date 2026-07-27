@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ========================================
echo   AI Chat Assistant
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found!
    pause
    exit /b 1
)

echo Starting...
echo.

.venv\Scripts\python.exe main.py
set err=%errorlevel%

echo.
if %err% neq 0 (
    echo [ERROR] Exit code: %err%
)
pause
exit /b %err%
