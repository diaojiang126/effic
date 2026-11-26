@echo off
title Flask Service Restart - effic

echo ============================================
echo   Flask Service Restart - effic
echo   Port: 5050
echo ============================================
echo.

REM ---- Config (modify to match your server) ----
set "EFFIC_DIR=C:\luosen\effic"
set "PORT=5050"
set "PYTHON_EXE=C:\Python312\python.exe"
REM ----------------------------------------------

cd /d "%EFFIC_DIR%" 2>nul
if errorlevel 1 (
    echo [ERROR] Working directory not found: %EFFIC_DIR%
    echo Please edit EFFIC_DIR at the top of this script.
    pause
    exit /b 1
)

echo [1/4] Checking for process on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    set "OLD_PID=%%a"
)
if not defined OLD_PID (
    echo       No process found on port %PORT%, may be first start or already stopped.
    goto :START
)

echo       Found old process PID: %OLD_PID%
echo [2/4] Stopping old process...
taskkill /PID %OLD_PID% /F >nul 2>&1
if errorlevel 1 (
    echo       [WARNING] Failed to stop, may need administrator privileges.
    echo       Please right-click and Run as administrator.
    pause
    exit /b 1
)
echo       Process %OLD_PID% terminated.

timeout /t 2 /nobreak >nul

:START
echo [3/4] Starting Flask service...
echo       Working dir: %EFFIC_DIR%
echo       Python:      %PYTHON_EXE%
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    echo Please edit PYTHON_EXE at the top of this script.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%EFFIC_DIR%\app.py"

echo.
echo ============================================
echo   Service stopped (check above for errors if unexpected)
echo ============================================
pause
