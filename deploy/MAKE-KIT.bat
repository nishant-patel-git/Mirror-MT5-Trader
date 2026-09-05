@echo off
REM  MT5-Trader - build the USB stick that installs every other PC.
REM
REM  Double-click it. It makes the MetaTrader 5 template and puts it,
REM  with the three files SETUP needs, into one folder ready to copy.
REM
REM  There are only ever TWO jobs in this rollout, and this is the first:
REM
REM    THIS PC   ->  run MAKE-KIT.BAT once, copy the ROLLOUT-KIT folder
REM                  onto a USB stick.
REM    EVERY PC  ->  plug the stick in, double-click SETUP.bat.
REM
REM  Before running this, prepare the terminal - three steps:
REM
REM    1. Install MetaTrader 5 with the BROKER'S installer into C:\MT5-A.
REM    2. Do NOT log in. Cancel the Open an Account dialog.
REM    3. Close the terminal.
REM
REM      MAKE-KIT.BAT                 uses C:\MT5-A
REM      MAKE-KIT.BAT D:\MT5-Source   uses that folder instead
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - build the rollout kit
cd /d "%~dp0"
color 0F

set "SOURCE=%~1"
if not defined SOURCE set "SOURCE=C:\MT5-A"
set "KIT=%~dp0ROLLOUT-KIT"

echo.
echo   =====================================================
echo     MT5-Trader - building the rollout kit
echo   =====================================================
echo.
echo   Terminal to copy: %SOURCE%
echo   Kit goes to:      %KIT%
echo.

REM --- The two things people get wrong, checked before any work --------
if not exist "%SOURCE%\terminal64.exe" (
  echo   [X] There is no terminal64.exe in %SOURCE%.
  echo.
  echo       Install MetaTrader 5 there with the broker's own setup.exe,
  echo       do NOT log in, and close it. Or name the folder you used:
  echo.
  echo           MAKE-KIT.BAT D:\wherever
  echo.
  pause
  exit /b 1
)
tasklist /fi "imagename eq terminal64.exe" /nh 2>nul | find /i "terminal64.exe" >nul
if not errorlevel 1 (
  echo   [X] MetaTrader 5 is running. Close it and run this again -
  echo       a copy taken while it is open is half-written files.
  echo.
  pause
  exit /b 1
)

REM --- The three files SETUP cannot run without -------------------------
for %%F in (SETUP.bat setup.ps1 rollout.json) do (
  if not exist "%~dp0%%F" (
    echo   [X] %%F is missing from this deploy folder.
    echo       The kit would not work without it. Re-download the repo.
    echo.
    pause
    exit /b 1
  )
)

REM --- 1. The terminal template ----------------------------------------
echo   Building the terminal template - this takes a minute...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-golden-terminal.ps1" -Source "%SOURCE%"
if errorlevel 1 (
  echo.
  echo   [X] The template was not built - the reason is above.
  echo.
  pause
  exit /b 1
)

REM --- 2. Gather the kit ------------------------------------------------
REM  A FOLDER, not four files to remember. The commonest rollout mistake
REM  is a stick with three of the four on it, and SETUP then refuses on
REM  a machine somebody has already walked to.
echo.
echo   Gathering the kit...
if not exist "%KIT%" mkdir "%KIT%"
copy /y "%~dp0SETUP.bat"       "%KIT%\" >nul
copy /y "%~dp0setup.ps1"       "%KIT%\" >nul
copy /y "%~dp0rollout.json"    "%KIT%\" >nul
copy /y "%~dp0MT5-golden.zip"  "%KIT%\" >nul

for %%F in (SETUP.bat setup.ps1 rollout.json MT5-golden.zip) do (
  if not exist "%KIT%\%%F" (
    echo   [X] %%F did not make it into the kit.
    echo.
    pause
    exit /b 1
  )
)

echo.
echo   =====================================================
echo     Done. The kit is here:
echo.
echo       %KIT%
echo.
echo     Copy that WHOLE FOLDER onto a USB stick.
echo.
echo     On each fresh PC: plug the stick in, open the folder,
echo     double-click SETUP.bat. That is the whole install.
echo   =====================================================
echo.
echo   Check rollout.json first if the repository or branch has
echo   changed - that file is the only place they are set.
echo.
pause
exit /b 0
