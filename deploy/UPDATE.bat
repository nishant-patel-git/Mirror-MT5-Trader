@echo off
REM  MT5-Trader - put a new version on this PC.
REM
REM  Run BY WHOEVER MAINTAINS IT, deliberately, on the machine being
REM  updated. START TRADING does not do this: a desk's version changes
REM  when somebody decides it changes, not because a commit landed
REM  overnight.
REM
REM  The order matters. The new code is fetched, the tests are run ON
REM  IT, and only if they pass does the update stand. A version that
REM  fails its own tests is put back before anyone can trade on it -
REM  which is the whole reason this is a script and not two git
REM  commands typed from memory.
REM
REM    deploy\UPDATE.BAT              go to the latest on this branch
REM    deploy\UPDATE.BAT --rollback   go back to the version before it
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - update
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - updating this PC
echo   =====================================================
echo.

REM --- The engine must not be running -----------------------------------
REM  Swapping files under a live engine is how a half-old, half-new
REM  process ends up holding positions.
REM
REM  Written with a goto rather than a parenthesised block on purpose:
REM  %GOON% inside the block it is SET in expands to what it held when
REM  the block was parsed - nothing - so the answer would be ignored and
REM  the update would always abort. Delayed expansion would also fix it;
REM  a label is harder to get wrong six months from now.
tasklist /fi "imagename eq python.exe" /nh 2>nul | find /i "python.exe" >nul
if errorlevel 1 goto :engine_is_not_running
echo   [!] Python is running on this machine.
echo.
echo       If that is MT5-Trader, close its window first. Updating
echo       underneath a running engine leaves it half on the old code
echo       and half on the new.
echo.
set "GOON="
set /p "GOON=      Type YES to carry on anyway: "
if /i not "%GOON%"=="YES" (
  echo   Nothing was changed.
  exit /b 1
)
:engine_is_not_running

REM --- Python and Git ---------------------------------------------------
set "PY="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3.11"
if not defined PY (
  python -c "import sys; assert sys.version_info[0] == 3" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [X] No Python was found on this machine.
  pause
  exit /b 1
)
where git >nul 2>&1
if errorlevel 1 (
  echo   [X] Git is not installed, so this machine cannot fetch an
  echo       update. Re-run deploy\SETUP.bat.
  pause
  exit /b 1
)

REM --- Where we are now, so we can come back ---------------------------
REM  Recorded BEFORE anything moves. A rollback that has to work out
REM  where it was going is a rollback that cannot run when it is needed.
set "WAS="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "WAS=%%h"
if not defined WAS (
  echo   [X] This folder is not a git clone, so there is nothing to
  echo       update. Re-run deploy\SETUP.bat.
  pause
  exit /b 1
)
echo   This PC is on %WAS%

if /i "%~1"=="--rollback" goto :rollback

REM --- Fetch ------------------------------------------------------------
echo   Fetching...
git fetch --quiet
if errorlevel 1 (
  echo   [X] Could not reach GitHub. Check this machine's internet, and
  echo       that its access token has not expired.
  pause
  exit /b 1
)

git merge --ff-only @{u}
if errorlevel 1 (
  echo.
  echo   [X] This machine cannot fast-forward. Either it has local
  echo       changes, or its branch has moved sideways. Nothing has
  echo       been changed. Whoever maintains this needs to look at it.
  echo.
  pause
  exit /b 1
)

set "NOW="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "NOW=%%h"
if "%NOW%"=="%WAS%" (
  echo   Already on the latest version - nothing to do.
  echo.
  pause
  exit /b 0
)
echo   Now on %NOW%

REM --- Dependencies the new version may need ---------------------------
echo   Updating dependencies...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo   [!] The dependencies could not be updated. The tests below will
  echo       say whether that matters.
)

REM --- The tests decide whether this update stands ----------------------
echo   Running the safety tests on the new version...
%PY% -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   [X] THE NEW VERSION FAILS ITS OWN TESTS.
  echo.
  echo       Putting this machine back on %WAS%.
  git reset --hard --quiet %WAS%
  %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
  echo.
  echo       This PC is back where it was and is safe to trade on.
  echo       Send the lines above to whoever maintains it - do NOT try
  echo       the update again until they say so.
  echo.
  pause
  exit /b 1
)

echo.
echo   Updated, and the safety tests pass.
echo   Nothing else to do - START TRADING as usual.
echo.
echo   If this version misbehaves, deploy\UPDATE.BAT --rollback puts
echo   this PC back on %WAS%.
echo.
pause
exit /b 0

:rollback
REM  Back one commit. Deliberately ONE: a trader on the phone can say
REM  "put it back", and a script that walks further than that is a
REM  script nobody can predict the result of.
echo   Rolling back one version...
git reset --hard --quiet HEAD~1
if errorlevel 1 (
  echo   [X] Could not roll back. Whoever maintains this needs to look.
  pause
  exit /b 1
)
set "NOW="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "NOW=%%h"
echo   This PC is now on %NOW%
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
%PY% -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   [X] The version this rolled back TO also fails its tests. Do
  echo       not trade on this machine. Call whoever maintains it.
  echo.
  pause
  exit /b 1
)
echo.
echo   Rolled back, and the safety tests pass.
echo.
pause
exit /b 0
