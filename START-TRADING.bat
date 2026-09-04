@echo off
REM  MT5-Trader - double-click this to start trading.
REM
REM  It does the whole start: finds a Python, picks up the latest code,
REM  brings the dependencies up to date, checks the machine can actually
REM  connect, runs the safety tests, starts the engine and opens the
REM  ladders in your browser.
REM
REM  Two rules it will not bend:
REM
REM    * It never starts the engine on a failing test suite. That rule
REM      is what keeps a bad build away from a live account.
REM
REM    * It never leaves the trader with a black window. Anything that
REM      is merely the OFFICE INTERNET being down - the update, the
REM      dependency check - warns and carries on with the copy already
REM      on the machine. Only a fault that would make trading WRONG
REM      stops the start.
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader
cd /d "%~dp0"
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - starting up
echo   =====================================================
echo.

REM --- 1. Python -------------------------------------------------------
REM  Three ways a working Python turns up on these boxes, and all three
REM  are normal: the py launcher from a python.org install, a plain
REM  "python" on PATH (a conda or venv prompt has this and NO launcher),
REM  or nothing at all. Assuming the launcher is what tells a machine
REM  that already has Python that it has none.
set "PY="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3.11"
if not defined PY (
  python -c "import sys; assert sys.version_info[0] == 3" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [X] No Python was found on this machine.
  echo.
  echo       Install Python 3.11, 64-bit, from python.org and tick
  echo       "Add python.exe to PATH" during the install. If you use
  echo       conda, open the prompt that has your environment active
  echo       and run this file from there.
  echo.
  pause
  exit /b 1
)
echo   Using Python: %PY%

REM --- 2. The latest code ----------------------------------------------
REM  The daily update, and it is deliberately allowed to fail. A trader
REM  with no internet must still get the ladder they had yesterday, so
REM  every failure here is a warning and the start continues.
REM
REM  --ff-only on purpose: if this machine somehow has local commits, a
REM  merge would be started that nobody is here to finish. Better to say
REM  so and run the code that is already here.
set "UPDATED=no"
where git >nul 2>&1
if errorlevel 1 (
  echo   [i] Git is not installed - skipping the update.
) else (
  git rev-parse --is-inside-work-tree >nul 2>&1
  if errorlevel 1 (
    echo   [i] This folder is not a git clone - skipping the update.
  ) else (
    echo   Checking for an update...
    git pull --ff-only >nul 2>&1
    if errorlevel 1 (
      echo   [!] Could not update - carrying on with the copy already
      echo       on this machine. If this keeps happening, tell whoever
      echo       maintains it; you are not running the latest code.
    ) else (
      set "UPDATED=yes"
      echo   Up to date.
    )
  )
)

REM --- 3. Configuration -------------------------------------------------
if not exist config.json (
  echo   [i] First run: creating config.json from the example.
  copy /y config.example.json config.json >nul
)
if not exist .env (
  copy /y .env.example .env >nul
)

REM --- 4. Dependencies --------------------------------------------------
REM  Also allowed to fail. What matters is not whether pip could reach
REM  the internet, it is whether the imports the engine needs are HERE.
echo   Checking dependencies...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt >nul 2>&1
if errorlevel 1 (
  %PY% -c "import flask, dotenv, MetaTrader5" >nul 2>&1
  if errorlevel 1 (
    echo   [X] The dependencies are not installed and could not be
    echo       fetched. Check this machine's internet connection and
    echo       run this again.
    pause
    exit /b 1
  )
  echo   [!] Could not check for newer dependencies - the ones already
  echo       installed are complete, so carrying on.
)

REM --- 5. Can this machine actually connect? ----------------------------
REM  Counted, not guessed. An account that names its own MT5 folder is
REM  OPENED AND SIGNED IN by the engine, so a terminal that is not
REM  running is not a reason to refuse - preflight.py knows which case
REM  this config is and says so in words.
set "TERMINALS=0"
for /f %%c in ('tasklist /fi "imagename eq terminal64.exe" /nh 2^>nul ^| find /c /i "terminal64.exe"') do set "TERMINALS=%%c"

REM  An older clone has no preflight.py. Skipping it is right: it is a
REM  CHECK, and a missing check must not be the thing that stops a
REM  trader working. The safety tests below are the gate that matters.
if not exist deploy\preflight.py (
  echo   [i] No preflight in this copy - skipping the connection check.
) else (
  %PY% deploy\preflight.py --config config.json --terminals-running %TERMINALS%
  if errorlevel 1 goto :refused
)
goto :tests

:refused
echo.
echo   [X] The engine has NOT been started - see above.
echo.
pause
exit /b 1

:tests

REM --- 6. The safety tests ---------------------------------------------
echo   Running the safety tests (about 20 seconds)...
%PY% -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   [X] THE SAFETY TESTS FAILED. The engine has NOT been started.
  echo.
  if "%UPDATED%"=="yes" (
    echo       This machine updated itself a moment ago, so the fault
    echo       most likely arrived with that update.
    echo.
  )
  echo       Do not trade on this build. Send the lines above to whoever
  echo       maintains it.
  echo.
  pause
  exit /b 1
)

REM --- 7. Go ------------------------------------------------------------
echo.
echo   All checks passed. Starting the engine and opening the ladders.
echo   Leave this window open - closing it stops trading.
echo.
%PY% start.py --config config.json

echo.
echo   The engine has stopped. Any positions you had are still at the
echo   broker; open the terminals to see them.
pause
