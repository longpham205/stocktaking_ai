@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------------------------------------
rem  Khoi dong Web POS.   Cach dung:   launch.bat [demo^|real] [tham so them cho backend]
rem    demo : configs\config.demo.yaml + data_demo   (du lieu tong hop, backend mock)
rem    real : configs\config.yaml      + data        (mac dinh)
rem  Vi du:  launch.bat demo --fake        launch.bat real --port 8080
rem  (Thong bao trong file nay viet khong dau de cmd khong hien thi loi chu.)
rem ---------------------------------------------------------------------------------------------

set "CFG=configs\config.yaml"
set "DATA=data"
set "MODE=real"
if /I "%~1"=="demo" goto :mode_demo
if /I "%~1"=="real" goto :mode_real
goto :args

:mode_demo
set "MODE=demo"
set "CFG=configs\config.demo.yaml"
set "DATA=data_demo"
shift
goto :args

:mode_real
shift

:args
set "EXTRA="
:argloop
if "%~1"=="" goto :activate
set "EXTRA=%EXTRA% %1"
shift
goto :argloop

:activate
if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo [CANH BAO] Khong thay thu muc venv hoac .venv, dung Python hien tai.
)

echo.
echo ==== Kiem tra moi truong: che do %MODE% ====
python scripts\check_env.py --config "%CFG%" --data-dir "%DATA%"%EXTRA%
if errorlevel 1 (
  echo.
  echo Moi truong chua san sang. Hay sua cac dong [FAIL] o tren roi chay lai.
  pause
  exit /b 1
)

echo.
echo ==== Khoi dong Web POS ====
python -m backend --config "%CFG%" --data-dir "%DATA%"%EXTRA%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Server dung voi ma loi %RC%.
  pause
)
exit /b %RC%
