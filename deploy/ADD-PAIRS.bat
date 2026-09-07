@echo off
REM  MT5-Trader - add this month's contracts.
REM
REM  Double-click it. The pairs listed in pairs.json beside this file
REM  are added to this machine, and the new ladders appear on the
REM  Exchanges page.
REM
REM  It only ADDS. A pair you already have is left exactly as it is -
REM  including any position you are holding on it - and nothing is ever
REM  deleted or switched off. So it is safe to run twice, and safe to
REM  run in the middle of the day.
REM
REM  To see what it WOULD do without changing anything:
REM      ADD-PAIRS.BAT --dry-run
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - add pairs
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - adding this month's contracts
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
  echo   [X] No Python was found on this machine, so this PC has not
  echo       been set up yet. Run deploy\SETUP.bat first.
  echo.
  pause
  exit /b 1
)

if not exist config.json (
  echo   [X] This machine has no config.json, so it has not been set up
  echo       yet. Run deploy\SETUP.bat first - there is nothing for a
  echo       pair to hang on until the two accounts exist.
  echo.
  pause
  exit /b 1
)

%PY% deploy\add_pairs.py --config config.json --pairs deploy\pairs.json %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] Nothing was changed - the reason is above.
) else (
  echo   Done.
)
echo.
pause
exit /b %RC%
