@echo off
rem One click on Windows: install everything this project needs on a fresh clone (scripts/setup.sh).
rem Safe to run again. Needs Git for Windows and Docker Desktop; uv and Node.js are installed if missing.
rem   setup.bat         NVIDIA GPU -> real recognition; no GPU -> demo recognizer
rem   setup.bat --cpu   light install even with a GPU
setlocal
rem keep the current directory when bash starts as a login shell
set "CHERE_INVOKING=1"
cd /d "%~dp0"

set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH (
  echo Git Bash not found. Install Git for Windows: https://git-scm.com/download/win
  pause
  exit /b 1
)

"%BASH%" --login "./scripts/setup.sh" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo Setup finished.) else (echo Setup stopped with code %CODE%. Read the lines above, fix it, run setup.bat again.)
pause
exit /b %CODE%
