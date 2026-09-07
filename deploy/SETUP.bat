@echo off
REM  MT5-Trader - run this ONCE on a new office PC.
REM
REM  It installs Git and Python, fetches the code, unpacks two separate
REM  MetaTrader 5 folders, runs the safety tests, asks you six questions
REM  and puts START TRADING on the Desktop. About ten minutes, most of
REM  it unattended.
REM
REM  This file is only a shim. It does two things batch is good at -
REM  ask Windows for Administrator, and hand over to PowerShell - and
REM  nothing else. The work is in setup.ps1 beside it, where a failure
REM  can stop the script instead of scrolling past.
REM
REM  Copy this file, setup.ps1 and MT5-golden.zip into one folder (a USB
REM  stick or a network share) and double-click this.

setlocal
cd /d "%~dp0"

REM --- Administrator ----------------------------------------------------
REM  Needed to install Git and Python for all users. Asking here means
REM  the trader never has to know to right-click.
REM
REM  The arguments are carried across. Elevating without them is how a
REM  -Token typed on the command line disappears and the clone then
REM  fails on a private repository for no visible reason.
net session >nul 2>&1
if errorlevel 1 (
  echo   Asking for Administrator...
  REM  Two branches, because -ArgumentList '' is an error rather than
  REM  an empty list - and the no-arguments case is the normal one.
  if "%*"=="" (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  ) else (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
  )
  exit /b 0
)

if not exist "%~dp0setup.ps1" (
  echo   [X] setup.ps1 is not in this folder.
  echo.
  echo       Copy SETUP.bat, setup.ps1 and MT5-golden.zip together -
  echo       this file cannot do anything on its own.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] Setup did not finish. The reason is above.
) else (
  echo   Setup finished. Double-click START TRADING on the Desktop.
)
echo.
pause
exit /b %RC%
