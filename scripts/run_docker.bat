@echo off
rem One click on Windows: the whole web POS in Docker (scripts/run_docker.sh).
rem   run_docker.bat        demo recognizer, open http://localhost:5173
rem   run_docker.bat gpu    real recognition on the NVIDIA GPU inside Docker (large image)
rem   run_docker.bat stop   stop the containers (the database is kept)
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

"%BASH%" --login "./scripts/run_docker.sh" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo Done.) else (echo Stopped with code %CODE%. Read the lines above.)
pause
exit /b %CODE%
