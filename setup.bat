@echo off
setlocal

REM ============================================================================
REM Stocktaking AI - Windows E2E Setup
REM Must be located in project root. Main setup logic runs scripts\setup.py.
REM ============================================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   Stocktaking AI - Windows E2E Setup
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo         Install Python 3.11 or 3.12 first.
    exit /b 1
)

python --version
if errorlevel 1 (
    echo [ERROR] Python could not be executed.
    exit /b 1
)

if not exist "scripts\setup.py" (
    echo [ERROR] scripts\setup.py was not found.
    echo         Current directory: %CD%
    exit /b 1
)

echo.
echo [setup] Running scripts\setup.py ...
echo.

python "scripts\setup.py"
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   SETUP FAILED
    echo ============================================================
    echo.
    exit /b 1
)

echo.
echo ============================================================
echo   SETUP COMPLETED SUCCESSFULLY
echo ============================================================
echo.

exit /b 0