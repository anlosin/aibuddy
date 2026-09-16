@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ========================================
echo   qwen  -  build Windows exe (onedir)
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please create it first:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/3] Checking PyInstaller ...
.venv\Scripts\python.exe -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed
    pause
    exit /b 1
)

echo [2/3] Building (may take a few minutes) ...
.venv\Scripts\python.exe -m PyInstaller qwen.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] build failed
    pause
    exit /b 1
)

echo [3/3] Self-test of the packaged exe ...
dist\qwen\qwen.exe --selftest
set selftest_rc=%errorlevel%
echo.
echo ---- selftest.log ----
type "dist\qwen\data\logs\selftest.log"
echo ----------------------
echo.

if %selftest_rc% neq 0 (
    echo [WARN] selftest exit code = %selftest_rc%
) else (
    echo [OK] selftest passed
)

echo.
echo Output : dist\qwen\qwen.exe
echo Ship   : zip the whole dist\qwen folder (data\ and plugins\ are created on first run)
echo.
pause
exit /b %selftest_rc%
