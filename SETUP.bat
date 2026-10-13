@echo off
rem ============================================================
rem  One-click setup launcher  -  double-click this file.
rem
rem  1. find an existing Python
rem  2. if none found, download and install Python 3.13 per-user
rem     (InstallAllUsers=0 -> no admin rights, no UAC prompt)
rem  3. hand over to setup.py, which prints everything in Chinese
rem
rem  ------------------------------------------------------------------
rem  ASCII-ONLY ON PURPOSE.  READ THIS BEFORE EDITING.
rem
rem  cmd parses .bat files using the system OEM codepage (GBK on zh-CN).
rem  Any non-ASCII byte in here gets garbled and turns into bogus
rem  commands, which aborts the whole script.  Chinese belongs in the
rem  FILENAME and in setup.py (which is UTF-8 and handles it fine).
rem
rem  ALSO: never put a bare "(" or ")" inside an "if ( ... )" block.
rem  cmd uses ")" to find the end of the block, so an unescaped paren
rem  in an echo line silently truncates it.  Use "^(" / "^)" if you
rem  really need one.
rem  ------------------------------------------------------------------
rem
rem  NOTE: the Microsoft Store "python.exe" stub lives in
rem    %LOCALAPPDATA%\Microsoft\WindowsApps
rem  and cannot install packages.  We deliberately exclude that path.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
chcp 65001 >nul

set "PYEXE="

rem ---------- 1a. py launcher: most reliable ----------
for %%V in (3.13 3.12 3) do (
  if not defined PYEXE (
    py -%%V -c "import sys" >nul 2>nul && set "PYEXE=py -%%V"
  )
)

rem ---------- 1b. python on PATH, but NOT the Store stub ----------
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      echo %%P | find /i "WindowsApps" >nul || set "PYEXE=%%P"
    )
  )
)

rem ---------- 1c. common install locations ----------
if not defined PYEXE (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
  ) do (
    if not defined PYEXE if exist "%%~D" set "PYEXE=%%~D"
  )
)

rem ---------- 2. install Python if still missing ----------
rem  no bare parens in the echoes below (see the header note)
if not defined PYEXE (
  echo ============================================================
  echo   Python not found - installing Python 3.13 for you
  echo   current user only, no admin rights needed
  echo ============================================================
  echo.
  call :install_python
)

if not defined PYEXE (
  echo.
  echo [ERROR] Could not find or install Python.
  echo   Please install Python 3.13 from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH", then run this again.
  echo.
  pause
  exit /b 1
)

rem ---------- 3. hand over to setup.py ----------
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost

%PYEXE% "%~dp0setup.py" %*
echo.
pause
exit /b 0


rem ============================================================
rem  :install_python  -  download + silent per-user install
rem ============================================================
:install_python
set "TMPEXE=%TEMP%\lxt_py313_setup.exe"

for %%V in (3.13.15 3.13.14 3.13.12 3.12.10) do (
  if not defined PYEXE (
    echo   downloading Python %%V ...
    call :download "https://www.python.org/ftp/python/%%V/python-%%V-amd64.exe" "%TMPEXE%"

    if exist "%TMPEXE%" (
      echo   installing Python %%V ^(current user^) ...
      "%TMPEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
      del "%TMPEXE%" >nul 2>nul

      for %%D in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
      ) do (
        if not defined PYEXE if exist "%%~D" set "PYEXE=%%~D"
      )
      if not defined PYEXE (
        py -3.13 -c "import sys" >nul 2>nul && set "PYEXE=py -3.13"
      )
      if not defined PYEXE (
        py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
      )
    )
  )
)
if not defined PYEXE exit /b 1
echo   Python ready: %PYEXE%
exit /b 0


rem ============================================================
rem  :download <url> <dest>  -  curl first, PowerShell as backup
rem ============================================================
:download
set "URL=%~1"
set "DST=%~2"
del "%DST%" >nul 2>nul

where curl >nul 2>nul
if %ERRORLEVEL%==0 (
  curl -L --ssl-no-revoke --fail --silent --show-error -o "%DST%" "%URL%"
  if exist "%DST%" exit /b 0
)

echo   curl unavailable or failed, trying PowerShell ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%URL%' -OutFile '%DST%' -UseBasicParsing } catch { exit 1 }"
if exist "%DST%" exit /b 0
exit /b 1
