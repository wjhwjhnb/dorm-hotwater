@echo off
rem Shared launcher. ASCII-only content.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PY="
for %%C in (
  "%~dp0.venv\Scripts\python.exe"
  "%~dp0venv\Scripts\python.exe"
) do (
  if not defined PY if exist "%%~C" set "PY=%%~C"
)
if not defined PY (
  py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
      echo %%P | find /i "WindowsApps" >nul || set "PY=%%P"
    )
  )
)
if not defined PY (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  ) do (
    if not defined PY if exist "%%~D" set "PY=%%~D"
  )
)
if not defined PY (
  echo.
  echo   Python not found - starting first-time setup ...
  echo.
  call "%~dp0SETUP.bat"
  exit /b %ERRORLEVEL%
)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
%PY% "%~dp0cli.py" %*
echo.
pause
