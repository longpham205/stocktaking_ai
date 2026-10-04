@echo off
rem One click on Windows: runs run_e2e.sh in Git Bash (the Makefile needs a POSIX shell).
rem   run_e2e.bat          start, seed, smoke test, open the browser
rem   run_e2e.bat full     the same, after lint, type-check and the test suites
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

"%BASH%" --login "./run_e2e.sh" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo Done.) else (echo Stopped with an error ^(code %CODE%^). Read the lines above.)
pause
exit /b %CODE%
