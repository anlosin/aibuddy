@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ========================================
echo   qwen  -  build Windows exe (onedir)
echo ========================================
echo.

set "EXE=dist\qwen\qwen.exe"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please create it first:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/4] Checking PyInstaller ...
.venv\Scripts\python.exe -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed
    pause
    exit /b 1
)

echo [2/4] Building (may take a few minutes) ...
.venv\Scripts\python.exe -m PyInstaller qwen.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] build failed
    pause
    exit /b 1
)

echo [3/4] Removing the non-runnable intermediate exe ...
rem ---------------------------------------------------------------------
rem PyInstaller also drops build\qwen\qwen.exe into its work directory.
rem That file is a bootloader half-product: there is no _internal\ beside
rem it, so double-clicking it ALWAYS fails with
rem   "Failed to load Python DLL ...\build\qwen\_internal\python313.dll"
rem Deleting it here so the project never contains a second, broken
rem qwen.exe that is easy to click by mistake.
rem ---------------------------------------------------------------------
if exist "build\qwen\qwen.exe" del /q "build\qwen\qwen.exe"

echo [4/4] Self-test of the packaged exe ...
"%EXE%" --selftest
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
echo ================================================================
echo   RUN THIS :  %CD%\%EXE%
echo   Ship     :  zip the whole dist\qwen folder
echo                (data\ and plugins\ are created on first run)
echo   NEVER RUN:  build\qwen\qwen.exe  ^<- intermediate, cannot work
echo ================================================================
echo.
echo Opening the folder that holds the runnable exe ...
start "" "%CD%\dist\qwen"
pause
exit /b %selftest_rc%
