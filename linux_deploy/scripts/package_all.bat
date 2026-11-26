@echo off
REM ============================================================
REM Full migration package script (run on Windows BEFORE OS reinstall)
REM Output: effic_migration_YYYYMMDD_HHMMSS.zip on Desktop
REM Contains: app code + MySQL backup + express.db + uploads + linux_deploy
REM Transfer: copy the zip to USB drive / LAN share / cloud storage,
REM           then unzip and deploy after Ubuntu reinstall
REM ============================================================

setlocal EnableDelayedExpansion

REM ---- Config (edit if needed) ----
set "EFFIC_ROOT=%~dp0..\.."
set "MYSQL_USER=effic"
set "MYSQL_PWD="
set "MYSQL_DB=device_inventory_system"
set "OUT_DIR=%USERPROFILE%\Desktop"
REM ---------------------------------
REM MYSQL_PWD 必须在这里填入运行机的真实应用数据库密码，脚本会用它执行 mysqldump。
REM 或在运行前设置环境变量:  set MYSQL_PWD=your_password && package_all.bat
if "%MYSQL_PWD%"=="" (
    echo [ERROR] MYSQL_PWD 未设置。请编辑本脚本填入应用数据库密码，或设置环境变量后重新运行。
    pause
    exit /b 1
)

REM ---- Locate mysqldump.exe (PATH first, then common install dirs) ----
set "MYSQLDUMP=mysqldump"
where mysqldump >nul 2>&1
if errorlevel 1 (
    for /d %%D in ("C:\Program Files\MySQL\MySQL Server*") do (
        if exist "%%D\bin\mysqldump.exe" set "MYSQLDUMP=%%D\bin\mysqldump.exe"
    )
    for /d %%D in ("C:\Program Files (x86)\MySQL\MySQL Server*") do (
        if exist "%%D\bin\mysqldump.exe" set "MYSQLDUMP=%%D\bin\mysqldump.exe"
    )
    if exist "D:\MySQL\bin\mysqldump.exe" set "MYSQLDUMP=D:\MySQL\bin\mysqldump.exe"
    if exist "C:\mysql\bin\mysqldump.exe"   set "MYSQLDUMP=C:\mysql\bin\mysqldump.exe"
)
echo Using mysqldump: %MYSQLDUMP%
"%MYSQLDUMP%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] mysqldump.exe not found. Please add MySQL bin dir to PATH,
    echo         or edit this script and set MYSQLDUMP manually.
    pause
    exit /b 1
)

for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value ^| find "="') do set "DT=%%a"
set "STAMP=%DT:~0,8%_%DT:~8,6%"
set "PACK_DIR=%TEMP%\effic_migration_%STAMP%"
set "OUT_ZIP=%OUT_DIR%\effic_migration_%STAMP%.zip"

echo ============================================
echo   effic full migration package
echo   Output: %OUT_ZIP%
echo ============================================

echo [1/6] Creating temp pack directory ...
if exist "%PACK_DIR%" rmdir /s /q "%PACK_DIR%"
mkdir "%PACK_DIR%"
mkdir "%PACK_DIR%\data"
mkdir "%PACK_DIR%\data\uploads"
mkdir "%PACK_DIR%\data\kdd_uploads"
mkdir "%PACK_DIR%\data\kdd_exports"

echo [2/6] Dumping MySQL database %MYSQL_DB% ...
"%MYSQLDUMP%" -u%MYSQL_USER% -p%MYSQL_PWD% --single-transaction --routines --triggers --events --default-character-set=utf8mb4 %MYSQL_DB% > "%PACK_DIR%\data\mysql_backup.sql"
if errorlevel 1 (
    echo [ERROR] mysqldump failed. Check MySQL is running and credentials are correct.
    rmdir /s /q "%PACK_DIR%"
    pause
    exit /b 1
)
echo       MySQL backup done.

echo [3/6] Copying SQLite and uploaded files ...
copy /y "%EFFIC_ROOT%\kdd\express.db" "%PACK_DIR%\data\express.db" >nul
xcopy /e /i /y /q "%EFFIC_ROOT%\static\uploads\*" "%PACK_DIR%\data\uploads\" >nul 2>&1
xcopy /e /i /y /q "%EFFIC_ROOT%\kdd\uploads\*"    "%PACK_DIR%\data\kdd_uploads\" >nul 2>&1
xcopy /e /i /y /q "%EFFIC_ROOT%\kdd\exports\*"    "%PACK_DIR%\data\kdd_exports\" >nul 2>&1

echo [4/6] Copying application code (excluding runtime artifacts) ...
mkdir "%PACK_DIR%\code"
xcopy /e /i /y /q "%EFFIC_ROOT%\*" "%PACK_DIR%\code\" /exclude:%~dp0package_exclude.txt
if errorlevel 1 (
    echo [ERROR] Code copy failed.
    rmdir /s /q "%PACK_DIR%"
    pause
    exit /b 1
)
REM xcopy /exclude uses substring match and cannot target root .env precisely,
REM so delete it explicitly here.
if exist "%PACK_DIR%\code\.env" del /q "%PACK_DIR%\code\.env"

echo [5/6] Writing pack info ...
echo Pack time: %date% %time% > "%PACK_DIR%\PACK_INFO.txt"
echo Source dir: %EFFIC_ROOT% >> "%PACK_DIR%\PACK_INFO.txt"
echo Database: %MYSQL_DB% >> "%PACK_DIR%\PACK_INFO.txt"
ver >> "%PACK_DIR%\PACK_INFO.txt"

echo [6/6] Compressing to zip ...
REM Use bsdtar (tar.exe, built into Windows 10 1803+).
REM PowerShell 5.1 Compress-Archive writes backslash path separators,
REM which Linux unzip cannot read (it creates files literally named
REM "code\app.py"). bsdtar writes proper forward-slash entries.
where tar >nul 2>&1
if errorlevel 1 (
    echo [ERROR] tar.exe not found. Requires Windows 10 1803 or later.
    echo         Alternative: install 7-Zip and run 7z a "%OUT_ZIP%" "%PACK_DIR%\*"
    pause
    exit /b 1
)
if exist "%OUT_ZIP%" del /q "%OUT_ZIP%"
tar -a -c -f "%OUT_ZIP%" -C "%PACK_DIR%" .
if errorlevel 1 (
    echo [ERROR] Compression failed.
    pause
    exit /b 1
)

rmdir /s /q "%PACK_DIR%"

echo.
echo ============================================
echo   Package done: %OUT_ZIP%
echo ============================================
echo.
echo Next steps:
echo   1. Copy the zip to USB drive / LAN share / cloud storage
echo   2. Reinstall the server with Ubuntu
echo   3. On Ubuntu, unzip and deploy:
echo      unzip effic_migration_*.zip -d /tmp/migration_pack
echo      sudo cp -a /tmp/migration_pack/code/* /opt/effic/
echo      sudo bash /opt/effic/linux_deploy/scripts/install_ubuntu.sh
pause
endlocal
