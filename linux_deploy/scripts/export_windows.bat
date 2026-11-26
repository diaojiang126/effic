@echo off
REM ============================================================
REM Windows data export script (run on the OLD Windows server)
REM Output: migration_pack\ folder with MySQL backup + express.db + uploads
REM Then copy the whole migration_pack folder to the Linux server
REM ============================================================

setlocal EnableDelayedExpansion

set "EXPORT_DIR=%~dp0migration_pack"
set "MYSQL_USER=effic"
set "MYSQL_PWD="
set "MYSQL_DB=device_inventory_system"
set "EFFIC_ROOT=%~dp0..\.."

if "%MYSQL_PWD%"=="" (
    echo [ERROR] MYSQL_PWD 未设置。请编辑本脚本填入运行机的应用数据库密码，或先 set MYSQL_PWD=xxx 再运行。
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
    echo [ERROR] mysqldump.exe not found. Please add MySQL bin dir to PATH.
    exit /b 1
)

echo [1/4] Creating export directory: %EXPORT_DIR%
if exist "%EXPORT_DIR%" rmdir /s /q "%EXPORT_DIR%"
mkdir "%EXPORT_DIR%"
mkdir "%EXPORT_DIR%\uploads"
mkdir "%EXPORT_DIR%\kdd_uploads"
mkdir "%EXPORT_DIR%\kdd_exports"

echo [2/4] Dumping MySQL database %MYSQL_DB% ...
"%MYSQLDUMP%" -u%MYSQL_USER% -p%MYSQL_PWD% --single-transaction --routines --triggers --events --default-character-set=utf8mb4 %MYSQL_DB% > "%EXPORT_DIR%\mysql_backup.sql"
if errorlevel 1 (
    echo [ERROR] mysqldump failed.
    exit /b 1
)

echo [3/4] Copying SQLite express.db and uploaded files ...
copy /y "%EFFIC_ROOT%\kdd\express.db" "%EXPORT_DIR%\express.db" >nul
xcopy /e /i /y "%EFFIC_ROOT%\static\uploads\*" "%EXPORT_DIR%\uploads\" >nul
xcopy /e /i /y "%EFFIC_ROOT%\kdd\uploads\*" "%EXPORT_DIR%\kdd_uploads\" >nul
xcopy /e /i /y "%EFFIC_ROOT%\kdd\exports\*" "%EXPORT_DIR%\kdd_exports\" >nul

echo [4/4] Done. Copy the whole %EXPORT_DIR% folder to the Linux server (e.g. /tmp/migration_pack)
echo.
echo Hint: use scp or WinSCP:
echo   scp -r %EXPORT_DIR% user@linux-server:/tmp/
endlocal
