@echo off
REM  MT5-Trader - check this machine can actually trade.
REM
REM  Double-click it. Nothing is changed, nothing is started, no order
REM  is ever placed: it logs each configured account in, reports what
REM  the broker said, and stops.
REM
REM  Run it when a desk says "it will not connect", and at the end of an
REM  install. It is the difference between a report you can act on and
REM  a trader saying the screen looks wrong.
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - verify
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - checking this machine
echo   =====================================================
echo.

set "PY="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3.11"
if not defined PY (
  python -c "import sys; assert sys.version_info[0] == 3" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [X] No Python on this machine - it has not been set up.
  echo       Run deploy\SETUP.bat.
  echo.
  pause
  exit /b 1
)

if not exist config.json (
  echo   [X] No config.json - this machine has not been set up.
  echo       Run deploy\SETUP.bat.
  echo.
  pause
  exit /b 1
)

REM --- 1. Is the config itself sane? ------------------------------------
REM  Cheap, offline, and catches the faults that make a connection
REM  pointless: one login, one port or one terminal folder on both rows.
set "TERMINALS=0"
for /f %%c in ('tasklist /fi "imagename eq terminal64.exe" /nh 2^>nul ^| find /c /i "terminal64.exe"') do set "TERMINALS=%%c"
if exist deploy\preflight.py (
  %PY% deploy\preflight.py --config config.json --terminals-running %TERMINALS%
  echo.
)

REM --- 2. Can it actually log in? ---------------------------------------
%PY% deploy\check_config.py --config config.json
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] This machine is NOT ready to trade - see above.
) else (
  echo   This machine can reach every account it is configured for.
  echo.
  echo   If a ladder still reads unknown, check Algo Trading is green
  echo   in BOTH terminals - it is a per-installation setting and a
  echo   fresh terminal has it off.
)
echo.
pause
exit /b %RC%
