@echo off
rem One click on Windows: the web POS with the REAL recognition pipeline on the GPU.
rem Runs run_real.sh in Git Bash. Keep this window open; Ctrl+C (or closing it) stops the API and the web.
setlocal
rem keep the current directory when bash starts as a login shell
set "CHERE_INVOKING=1"
cd /d "%~dp0.."

set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH (
  echo Git Bash not found. Install Git for Windows: https://git-scm.com/download/win
  pause
  exit /b 1
)

"%BASH%" --login "./scripts/run_real.sh" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo Stopped.) else (echo Stopped with code %CODE%. Read the lines above.)
pause
exit /b %CODE%
