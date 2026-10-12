@echo off
REM ===================================================================
REM  Log in to 乐校通 and save the session token
REM  (ASCII-only: all Chinese output comes from Python)
REM ===================================================================
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  ) do (
    if not defined PY if exist "%%~D" set "PY=%%~D"
  )
)
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
      echo %%i | find /i "WindowsApps" >nul
      if errorlevel 1 set "PY=%%i"
    )
  )
)
if not defined PY (
  echo [X] Python not found. See 安装配置.bat for instructions.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   Login to 乐校通  (saves the session token locally)
echo ============================================================
echo.
echo   A captcha image will pop up - type the characters you see.
echo   The password is NOT saved anywhere.
echo.

"!PY!" "src\lxt_login.py"
set RC=!ERRORLEVEL!

echo.
if "!RC!"=="0" (
  echo [OK] Login succeeded - token saved.
) else (
  echo [X] Login failed ^(exit !RC!^). Run again to retry.
)
echo.
pause
endlocal
exit /b %RC%
