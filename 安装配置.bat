@echo off
REM ===================================================================
REM  Dorm Hot Water - One-click Setup
REM  ASCII-only on purpose: all Chinese output comes from Python, so
REM  this file works regardless of the Windows code page.
REM ===================================================================
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Dorm Hot Water - Setup
echo ============================================================
echo.

set "PY="

REM ---------- 1) the "py" launcher (most reliable) ----------
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)

REM ---------- 2) common per-user install locations ----------
if not defined PY (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
  ) do (
    if not defined PY if exist "%%~D" set "PY=%%~D"
  )
)

REM ---------- 3) whatever "python" is on PATH ----------
REM     skip the Microsoft Store alias (...\WindowsApps\python.exe):
REM     it is a stub that just opens the Store and cannot pip install.
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
      echo %%i | find /i "WindowsApps" >nul
      if errorlevel 1 set "PY=%%i"
    )
  )
)

REM ---------- sanity check ----------
if defined PY (
  "!PY!" -c "import sys" >nul 2>&1
  if errorlevel 1 set "PY="
)

if not defined PY (
  echo [X] No usable Python found.
  echo.
  echo     Please install Python 3.9 or newer:
  echo       https://www.python.org/downloads/
  echo     During install, TICK "Add python.exe to PATH".
  echo.
  echo     NOTE: the "python" from the Microsoft Store will NOT work.
  echo.
  pause
  exit /b 1
)

echo [OK] Python: !PY!
echo.

REM ---------- dependencies ----------
echo [..] Checking dependencies...
"!PY!" -c "import bleak, requests" >nul 2>&1
if errorlevel 1 (
  echo [..] Installing bleak and requests ...
  "!PY!" -m pip install --disable-pip-version-check --no-input bleak requests
  if errorlevel 1 (
    echo.
    echo [X] Install failed. Try manually:
    echo       "!PY!" -m pip install bleak requests
    echo.
    pause
    exit /b 1
  )
)
echo [OK] Dependencies ready.
echo.

REM ---------- run the wizard ----------
"!PY!" "src\setup_wizard.py"
set RC=!ERRORLEVEL!

echo.
if "!RC!"=="0" (
  echo [OK] Setup finished. Check the new icons on your Desktop.
) else (
  echo [X] Setup did not finish ^(exit !RC!^).
)
echo.
pause
endlocal
exit /b %RC%
