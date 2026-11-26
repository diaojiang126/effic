<#
===========================================================================
 EfficInventory - IIS Quick Deploy Script (Windows Server 2022 / Windows 10/11)
 Run as Administrator in PowerShell
 Key: Set-ExecutionPolicy Bypass -Scope Process -Force before running
===========================================================================
#>

#Requires -RunAsAdministrator

param(
    [string]$SiteName = "effic",
    [string]$SitePort = "5050",
    [string]$PythonPath = "",
    [string]$DbName = "device_inventory_system",
    [string]$DbUser = "root",
    [string]$DbPassword = "",       # MySQL root 密码（首次部署创建库/用户时使用）
    [string]$AppDbUser = "effic",   # 应用使用的数据库用户（新建时会授权）
    [string]$AppDbPassword = "",    # 应用使用的数据库用户密码（必须提供）
    [switch]$SkipFirewall = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$initScript = Join-Path $ProjectRoot "init_database.py"

# ---- 安全检查：密码必须显式提供，不再使用硬编码默认值 ----
if ([string]::IsNullOrWhiteSpace($AppDbPassword)) {
    Write-Host "[ERROR] 必须通过 -AppDbPassword 参数提供应用数据库用户的密码，禁止使用硬编码默认值。" -ForegroundColor Red
    Write-Host "        示例: .\deploy_iis.ps1 -DbPassword 'mysql-root-pwd' -AppDbPassword 'strong-app-db-pwd'" -ForegroundColor Gray
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " EfficInventory - IIS Deploy Script" -ForegroundColor Cyan
Write-Host " Target: Windows Server 2022 / Windows 11" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Project Path: $ProjectRoot" -ForegroundColor Gray
Write-Host ""

# ---- Step -1: Pre-check IIS features ----
Write-Host "[Pre-check] Verifying IIS and required features..." -ForegroundColor Green

$requiredFeatures = @(
    "Web-Server",
    "Web-CGI",
    "Web-Mgmt-Console"
)
$missingFeatures = @()
foreach ($feat in $requiredFeatures) {
    $installed = Get-WindowsFeature -Name $feat -ErrorAction SilentlyContinue
    if (-not $installed -or -not $installed.Installed) {
        $missingFeatures += $feat
    }
}
if ($missingFeatures.Count -gt 0) {
    Write-Host "      Missing IIS features: $($missingFeatures -join ', ')" -ForegroundColor Yellow
    Write-Host "      Installing IIS features (this may take a minute)..." -ForegroundColor Gray
    try {
        Install-WindowsFeature -Name $missingFeatures -IncludeManagementTools -ErrorAction Stop | Out-Null
        Write-Host "      IIS features installed successfully" -ForegroundColor Gray
    } catch {
        Write-Warning "Failed to install IIS features: $_"
        Write-Warning "Please install manually: Install-WindowsFeature Web-Server,Web-CGI,Web-Mgmt-Console"
    }
} else {
    Write-Host "      IIS features OK" -ForegroundColor Gray
}

# ---- Detect Python ----
if (-not $PythonPath) {
    $pythonCandidates = @(
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "C:\Python39\python.exe",
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python310\python.exe",
        "C:\Program Files\Python39\python.exe",
        "C:\Users\Dell\AppData\Local\Programs\Python\Python313\python.exe"
    )
    foreach ($candidate in $pythonCandidates) {
        if (Test-Path $candidate) {
            $PythonPath = $candidate
            break
        }
    }
    if (-not $PythonPath) {
        $pythonExe = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonExe) {
            $PythonPath = $pythonExe.Source
        }
    }
}

if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    Write-Error "Python not found. Install Python 3.9+ or use -PythonPath to specify path."
    exit 1
}

$pythonDir = Split-Path -Parent $PythonPath
$pythonVersion = & $PythonPath --version 2>&1
Write-Host "[Step 0] Python Version: $pythonVersion" -ForegroundColor Green
Write-Host "         Python Path: $PythonPath" -ForegroundColor Gray

# ---- Step 0: Initialize database FIRST (before anything else) ----
Write-Host "[Step 1] Initializing database..." -ForegroundColor Green
if (-not $DbPassword) {
    $securePwd = Read-Host "Enter MySQL root password" -AsSecureString
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePwd)
    $DbPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
}

if (Test-Path $initScript) {
    & $PythonPath $initScript "--password=$DbPassword"
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
        Write-Warning "Database init may have failed. Check MySQL connection."
    } else {
        Write-Host "      Database initialized" -ForegroundColor Gray
    }
} else {
    Write-Warning "init_database.py not found at $initScript"
    Write-Warning "Ensure MySQL database [$DbName] exists and user 'ls'@'localhost' has access."
}

# ---- 1. Install wfastcgi ----
Write-Host "[Step 2] Installing/Upgrading wfastcgi..." -ForegroundColor Green
try {
    & $PythonPath -m pip install wfastcgi --upgrade -q
    Write-Host "      wfastcgi installed successfully" -ForegroundColor Gray
} catch {
    Write-Error "wfastcgi install failed: $_"
    exit 1
}

# ---- 2. Register wfastcgi to IIS ----
Write-Host "[Step 3] Registering wfastcgi to IIS..." -ForegroundColor Green
$wfastcgiPath = Join-Path (Split-Path -Parent $PythonPath) "Lib\site-packages\wfastcgi.py"
if (-not (Test-Path $wfastcgiPath)) {
    $wfastcgiPath = Join-Path $pythonDir "Lib\site-packages\wfastcgi.py"
}
if (-not (Test-Path $wfastcgiPath)) {
    Write-Error "wfastcgi.py not found. Check Python installation."
    exit 1
}

try {
    & $PythonPath $wfastcgiPath /uninstall 2>&1 | Out-Null
    Write-Host "      Unregistered old config" -ForegroundColor Gray

    $job = Start-Job -ScriptBlock {
        param($p, $w)
        & $p $w /install 2>&1 | Out-Null
    } -ArgumentList $PythonPath, $wfastcgiPath

    if (Wait-Job $job -Timeout 30) {
        Receive-Job $job
        Write-Host "      FastCGI registered successfully" -ForegroundColor Gray
    } else {
        Stop-Job $job -PassThru | Remove-Job
        Write-Warning "wfastcgi registration timed out (30s). This is normal on some systems."
        Write-Warning "You can manually register later: $PythonPath $wfastcgiPath /install"
    }
    Write-Host "      wfastcgi path: $wfastcgiPath" -ForegroundColor Gray
} catch {
    Write-Warning "wfastcgi registration failed (may need manual config): $_"
}

# ---- 3. Install project dependencies ----
Write-Host "[Step 4] Installing project dependencies..." -ForegroundColor Green
$reqFile = Join-Path $ProjectRoot "requirements.txt"
$kddReqFile = Join-Path $ProjectRoot "kdd\requirements.txt"

if (Test-Path $reqFile) {
    & $PythonPath -m pip install -r $reqFile -q
    Write-Host "      Main system dependencies installed" -ForegroundColor Gray
} else {
    Write-Warning "requirements.txt not found"
}

if (Test-Path $kddReqFile) {
    & $PythonPath -m pip install -r $kddReqFile -q
    Write-Host "      Express subsystem dependencies installed" -ForegroundColor Gray
}

# ---- 4. Create required directories ----
Write-Host "[Step 5] Creating required directories..." -ForegroundColor Green
$logDir = Join-Path $ProjectRoot "logs"
$uploadDir = Join-Path $ProjectRoot "uploads"
$kddUploadDir = Join-Path $ProjectRoot "kdd\uploads"
$kddExportDir = Join-Path $ProjectRoot "kdd\exports"

foreach ($dir in @($logDir, $uploadDir, $kddUploadDir, $kddExportDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
        Write-Host "      Created directory: $dir" -ForegroundColor Gray
    }
}

try {
    $iusrs = "IIS_IUSRS"
    if (-not (Get-LocalGroup -Name $iusrs -ErrorAction SilentlyContinue)) {
        $iusrs = "IIS_IUSRS"
    }
    icacls $logDir /grant "${iusrs}:(CI)(OI)M" /q 2>&1 | Out-Null
    icacls $uploadDir /grant "${iusrs}:(CI)(OI)M" /q 2>&1 | Out-Null
    icacls $kddUploadDir /grant "${iusrs}:(CI)(OI)M" /q 2>&1 | Out-Null
    Write-Host "      Directory permissions configured" -ForegroundColor Gray
} catch {
    Write-Warning "Directory permission config failed, please set manually: $_"
}

# ---- 5. Generate web.config ----
Write-Host "[Step 6] Generating web.config..." -ForegroundColor Green
$scriptProcessor = $PythonPath, $wfastcgiPath -join '|'

$jwtSecretBytes = New-Object byte[] 64
[System.Security.Cryptography.RandomNumberGenerator]::Fill($jwtSecretBytes)
$jwtSecret = [Convert]::ToBase64String($jwtSecretBytes)
Write-Host "      JWT Secret generated: $jwtSecret" -ForegroundColor Gray

$webConfig = @(
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<configuration>',
    '    <system.webServer>',
    '        <handlers>',
    '            <add name="PythonFastCGI" path="*" verb="*" modules="FastCgiModule" scriptProcessor="' + $scriptProcessor + '" resourceType="Unspecified" requireAccess="Script" />',
    '        </handlers>',
    '        <staticContent>',
    '            <remove fileExtension=".json" />',
    '            <mimeMap fileExtension=".json" mimeType="application/json" />',
    '            <remove fileExtension=".woff" />',
    '            <mimeMap fileExtension=".woff" mimeType="font/woff" />',
    '            <remove fileExtension=".woff2" />',
    '            <mimeMap fileExtension=".woff2" mimeType="font/woff2" />',
    '            <remove fileExtension=".ttf" />',
    '            <mimeMap fileExtension=".ttf" mimeType="font/ttf" />',
    '            <remove fileExtension=".eot" />',
    '            <mimeMap fileExtension=".eot" mimeType="application/vnd.ms-fontobject" />',
    '        </staticContent>',
    '        <httpErrors errorMode="Detailed" />',
    '        <directoryBrowse enabled="false" />',
    '        <security>',
    '            <requestFiltering>',
    '                <requestLimits maxAllowedContentLength="16777216" />',
    '            </requestFiltering>',
    '        </security>',
    '        <modules>',
    '            <remove name="WebDAVModule" />',
    '        </modules>',
    '    </system.webServer>',
    '    <appSettings>',
    '        <add key="PYTHONPATH" value="' + $ProjectRoot + '" />',
    '        <add key="WSGI_HANDLER" value="app.app" />',
    '        <add key="WSGI_LOG" value="' + $logDir + '\wsgi.log" />',
    '        <add key="WSGI_RESTART_FILE_REGEX" value=".*\.((py)|(cfg)|(env))$" />',
    '        <add key="FLASK_ENV" value="production" />',
    '        <add key="DATABASE_NAME" value="' + $DbName + '" />',
    '        <add key="JWT_SECRET_KEY" value="' + $jwtSecret + '" />',
    '        <add key="SECRET_KEY" value="' + $jwtSecret + '" />',
    '        <add key="DB_HOST" value="localhost" />',
    '        <add key="DB_PORT" value="3306" />',
    '        <add key="DB_USER" value="' + $AppDbUser + '" />',
    '        <add key="DB_PASSWORD" value="' + $AppDbPassword + '" />',
    '        <add key="DB_DATABASE" value="' + $DbName + '" />',
    '    </appSettings>',
    '</configuration>'
) -join "`r`n"

$webConfigPath = Join-Path $ProjectRoot "web.config"
if (Test-Path $webConfigPath) {
    $backupPath = Join-Path $ProjectRoot "web.config.backup.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $webConfigPath $backupPath
    Write-Host "      Old web.config backed up to: $backupPath" -ForegroundColor Gray
}

$webConfig | Set-Content -Path $webConfigPath -Encoding UTF8
Write-Host "      web.config generated" -ForegroundColor Gray

# ---- Generate .env file ----
Write-Host "[Step 6b] Generating .env file..." -ForegroundColor Green

$secretBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($secretBytes)
$appSecretKey = -join ($secretBytes | ForEach-Object { $_.ToString("x2") })

$nowStr = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$envLines = @()
$envLines += "# $nowStr"
$envLines += ""
$envLines += "# DB"
$envLines += "DB_HOST=localhost"
$envLines += "DB_PORT=3306"
$envLines += "DB_USER=$AppDbUser"
$envLines += "DB_PASSWORD=$AppDbPassword"
$envLines += "DB_DATABASE=$DbName"
$envLines += ""
$envLines += "# Security"
$envLines += "SECRET_KEY=$appSecretKey"
$envLines += "JWT_SECRET_KEY=$jwtSecret"
$envLines += ""
$envLines += "# Server"
$envLines += "FLASK_ENV=production"
$envLines += "FLASK_DEBUG=False"
$envLines += "HOST=0.0.0.0"
$envLines += "PORT=$SitePort"
$envContent = $envLines -join "`r`n"

$envPath = Join-Path $ProjectRoot ".env"
if (Test-Path $envPath) {
    $envBackupPath = Join-Path $ProjectRoot ".env.backup.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $envPath $envBackupPath
    Write-Host "      Old .env backed up to: $envBackupPath" -ForegroundColor Gray
}

$envContent | Set-Content -Path $envPath -Encoding UTF8
Write-Host "      .env file generated" -ForegroundColor Gray
Write-Host "      App DB User: $AppDbUser" -ForegroundColor Gray

# ---- 6. Create IIS site ----
Write-Host "[Step 7] Creating/Updating IIS site..." -ForegroundColor Green

Import-Module WebAdministration -ErrorAction SilentlyContinue

if (-not (Get-Module WebAdministration)) {
    Write-Warning "IIS administration module not available. Please create site manually."
    Write-Host ""
    Write-Host "Manual IIS site creation steps:" -ForegroundColor Yellow
    Write-Host "  1. Open IIS Manager" -ForegroundColor Yellow
    Write-Host "  2. Right-click Sites -> Add Website" -ForegroundColor Yellow
    Write-Host "  3. Site name: $SiteName" -ForegroundColor Yellow
    Write-Host "  4. Physical path: $ProjectRoot" -ForegroundColor Yellow
    Write-Host "  5. Port: $SitePort" -ForegroundColor Yellow
} else {
    $site = Get-Website -Name $SiteName -ErrorAction SilentlyContinue
    if ($site) {
        Write-Host "      Site [$SiteName] already exists, updating..." -ForegroundColor Gray
        Set-ItemProperty "IIS:\Sites\$SiteName" -Name physicalPath -Value $ProjectRoot
        Set-ItemProperty "IIS:\Sites\$SiteName" -Name bindings -Value @{protocol="http";bindingInformation="*:${SitePort}:"}
    } else {
        Write-Host "      Creating new site [$SiteName]..." -ForegroundColor Gray
        New-Website -Name $SiteName -PhysicalPath $ProjectRoot -Port $SitePort -Force
    }

    $poolName = $SiteName
    if (-not (Test-Path "IIS:\AppPools\$poolName")) {
        New-WebAppPool -Name $poolName -Force
    }
    Set-ItemProperty "IIS:\AppPools\$poolName" -Name managedRuntimeVersion -Value ""
    Set-ItemProperty "IIS:\AppPools\$poolName" -Name enable32BitAppOnWin64 -Value $false
    Set-ItemProperty "IIS:\AppPools\$poolName" -Name processModel.identityType -Value ApplicationPoolIdentity

    Set-ItemProperty "IIS:\Sites\$SiteName" -Name applicationPool -Value $poolName

    Restart-WebAppPool $poolName -ErrorAction SilentlyContinue
    Start-Website $SiteName -ErrorAction SilentlyContinue

    Write-Host "      IIS site configured successfully" -ForegroundColor Gray
}

# ---- 7. Configure NTFS permissions for AppPoolIdentity ----
Write-Host "[Step 8] Configuring directory permissions..." -ForegroundColor Green
$poolIdentity = "IIS AppPool\$SiteName"
try {
    icacls $logDir /grant "${poolIdentity}:(OI)(CI)M" /q 2>&1 | Out-Null
    icacls $uploadDir /grant "${poolIdentity}:(OI)(CI)M" /q 2>&1 | Out-Null
    icacls $kddUploadDir /grant "${poolIdentity}:(OI)(CI)M" /q 2>&1 | Out-Null
    Write-Host "      AppPoolIdentity permissions granted" -ForegroundColor Gray
} catch {
    Write-Warning "AppPoolIdentity permission config failed: $_"
}

# ---- 8. Open firewall port ----
if (-not $SkipFirewall) {
    Write-Host "[Step 9] Configuring Windows Firewall..." -ForegroundColor Green
    $fwRuleName = "EfficInventory_Port_$SitePort"
    $existingFw = Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue
    if (-not $existingFw) {
        try {
            New-NetFirewallRule -DisplayName $fwRuleName `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $SitePort `
                -Action Allow `
                -Profile Any `
                -ErrorAction Stop | Out-Null
            Write-Host "      Firewall rule created: port $SitePort (TCP Inbound)" -ForegroundColor Gray
        } catch {
            Write-Warning "Firewall rule creation failed: $_"
            Write-Warning "Manually open port: netsh advfirewall firewall add rule name=`"$fwRuleName`" dir=in action=allow protocol=TCP localport=$SitePort"
        }
    } else {
        Write-Host "      Firewall rule `"$fwRuleName`" already exists" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Deployment Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Access URL: http://localhost:$SitePort" -ForegroundColor Yellow
Write-Host "Project Path: $ProjectRoot" -ForegroundColor Gray
Write-Host "Python:   $PythonPath" -ForegroundColor Gray
Write-Host "Log:      $logDir\wsgi.log" -ForegroundColor Gray
Write-Host ""
Write-Host "Login credentials: admin / admin123" -ForegroundColor Green
Write-Host ""
Write-Host "Troubleshooting:" -ForegroundColor Cyan
Write-Host "  1. If 500 error occurs, check log: $logDir\wsgi.log" -ForegroundColor Gray
Write-Host "  2. If static files fail to load, check IIS MIME types" -ForegroundColor Gray
Write-Host "  3. To change DB config, edit .env or re-run with -AppDbUser/-AppDbPassword" -ForegroundColor Gray
Write-Host "  4. To change port, re-run: .\deploy_iis.ps1 -SitePort 8080" -ForegroundColor Gray
Write-Host "  5. If login shows 'database connection failed', run: python init_database.py --password=YOUR_ROOT_PWD" -ForegroundColor Gray
Write-Host "  6. Config files: web.config + .env (restart app pool after editing)" -ForegroundColor Gray
Write-Host "  7. Server 2022: verify `"Web-CGI`" feature is installed" -ForegroundColor Gray
Write-Host ""
