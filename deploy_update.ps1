<#
=====================================================================
 快递单识别模块 —— 增量更新脚本
 -------------------------------------------------------------------
 适用场景：服务器上已在运行，只需发布本轮改动的文件。

 【与 deploy_iis.ps1 的区别】
   本脚本只做增量更新，绝不执行：
     - 不重建 / 修改 IIS 站点与应用池配置
     - 不重新生成 web.config（避免上传限制等设置被覆盖）
     - 不重新生成 .env（避免 JWT 密钥轮换导致所有人掉线）
     - 不触碰 kdd\express.db（表结构迁移由服务启动时自动完成）

 【用法】在服务器上以管理员身份运行：
     Set-ExecutionPolicy Bypass -Scope Process -Force

     # 目标目录留空时会自动从 IIS 站点 [luosen] 读取
     .\deploy_update.ps1 -SourceDir . -DryRun
     .\deploy_update.ps1 -SourceDir .

     # 若站点名不同，或想显式指定站点目录：
     .\deploy_update.ps1 -SourceDir . -SiteName MySite
     .\deploy_update.ps1 -SourceDir . -TargetDir C:\luosen\effic

 注意：-SourceDir 是发布包目录，-TargetDir 是站点目录，两者必须不同。

 【回滚】脚本会打印带时间戳的备份目录，把里面文件原样拷回目标目录即可。
=====================================================================
#>

param(
    # 新文件所在目录（发布包，需保持与站点一致的相对结构，例如 <SourceDir>\kdd\ocr_engine.py）
    [Parameter(Mandatory = $true)]
    [string]$SourceDir,

    # 主站站点物理路径。留空时会自动从 IIS 站点 [$SiteName] 读取
    [string]$TargetDir = "",

    # IIS 站点名（同时作为应用池名的默认值）
    [string]$SiteName = "luosen",

    # 应用池名称，留空则使用站点名
    [string]$AppPoolName = "",

    # kdd 模块在服务器上的部署目录；不存在则自动跳过同步
    [string]$KddStandaloneDir = "C:\luosen\effic\kdd",

    # 跳过 pip 依赖安装
    [switch]$SkipPip,

    # 只打印将要执行的动作，不做任何修改
    [switch]$DryRun
)

if (-not $AppPoolName) { $AppPoolName = $SiteName }

$ErrorActionPreference = "Stop"

# ============ 待发布文件清单（相对路径）============
$FullManifest = @(
    "app.py"
    "express_routes.py"
    ".env.example"
    "static\js\express_app.js"
    "kdd\ocr_engine.py"
    "kdd\image_enhance.py"
    "kdd\metrics.py"
    "kdd\database.py"
    "kdd\config.py"
    "kdd\app.py"
    "kdd\requirements.txt"
    "kdd\web.config"
    "kdd\templates\index.html"
    "kdd\templates\metrics.html"
    "kdd\static\js\app.js"
)

# 服务器上原本不存在、必须新增的文件
$NewFiles = @("kdd\image_enhance.py", "kdd\metrics.py", "kdd\templates\metrics.html")

# 绝不允许被导入 / 覆盖的路径（防御性检查）
$NeverTouch = @("kdd\express.db", "kdd\uploads", "kdd\exports", "logs", "uploads", ".env", "web.config")

function Write-Step($text)    { Write-Host "[步骤] $text" -ForegroundColor Green }
function Write-Info($text)    { Write-Host "       $text" -ForegroundColor Gray }
function Write-Warn2($text)   { Write-Host "       $text" -ForegroundColor Yellow }
function Write-Err2($text)    { Write-Host "       $text" -ForegroundColor Red }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 快递单识别模块 - 增量更新" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
if ($DryRun) { Write-Host " 模式：DRY-RUN（不会修改任何文件）" -ForegroundColor Yellow }
Write-Host ""

# ---- 0. 管理员权限 ----
if (-not $DryRun) {
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Err2 "请以管理员身份运行 PowerShell 后重试。"
        exit 1
    }
}

# ---- 1. 参数与目录校验 ----
Write-Step "校验源目录与目标站点"

# 相对路径（含 "."）先归一化成绝对路径 —— 个别环境下 Test-Path 对 "." 的解析会失败
if (-not [System.IO.Path]::IsPathRooted($SourceDir)) {
    $SourceDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $SourceDir))
}
if ($SourceDir.Length -gt 3) { $SourceDir = $SourceDir.TrimEnd('\') }

if (-not (Test-Path -LiteralPath $SourceDir)) {
    Write-Err2 "源目录不存在: $SourceDir"
    Write-Err2 ("当前目录:     " + (Get-Location).Path)
    Write-Err2 ("脚本所在目录: " + $PSScriptRoot)
    Write-Err2 "可改用绝对路径，例如：-SourceDir C:\luosen\effic_release"
    exit 1
}

# 目标目录：优先取参数，否则自动从 IIS 站点读取
if (-not $TargetDir) {
    $siteList = @()
    try {
        Import-Module WebAdministration -ErrorAction Stop
        $siteList = @(Get-Website -ErrorAction SilentlyContinue)
    } catch {
        Write-Warn2 "读取 IIS 配置失败：$($_.Exception.Message)"
    }

    $site = $null
    if ($siteList.Count -gt 0) {
        $site = $siteList | Where-Object { $_.Name -eq $SiteName } | Select-Object -First 1
        if (-not $site) {
            # 站点名没命中：退一步找目录下存在 app.py 的站点
            $site = $siteList | Where-Object {
                $_.PhysicalPath -and (Test-Path (Join-Path $_.PhysicalPath.TrimEnd('\') 'app.py'))
            } | Select-Object -First 1
            if ($site) { Write-Info "站点名 [$SiteName] 未命中，改用 [$($site.Name)]（其目录含 app.py）" }
        }
        if ($site -and $site.PhysicalPath) {
            $TargetDir = $site.PhysicalPath.TrimEnd('\')
            Write-Info "已从 IIS 站点 [$($site.Name)] 识别站点目录"
        }
    }

    if (-not $TargetDir) {
        if ($siteList.Count -gt 0) {
            Write-Warn2 "IIS 站点清单（请用 -SiteName 或 -TargetDir 指定）："
            $siteList | ForEach-Object {
                Write-Warn2 ("  " + $_.Name.PadRight(22) + " -> " + $_.PhysicalPath)
            }
        } else {
            Write-Warn2 "未读取到任何 IIS 站点（可能未安装 IIS 管理模块，或当前不是管理员）。"
        }
    }
}
if (-not $TargetDir) {
    Write-Err2 "无法确定主站目录，请用 -TargetDir 显式指定，例如："
    Write-Err2 "  .\deploy_update.ps1 -SourceDir . -TargetDir C:\luosen\effic"
    Write-Err2 "查看站点实际路径：Get-Website | Select-Object Name, PhysicalPath"
    exit 1
}
if (-not [System.IO.Path]::IsPathRooted($TargetDir)) {
    $TargetDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $TargetDir))
}
if (-not (Test-Path -LiteralPath $TargetDir)) {
    Write-Err2 "目标目录不存在: $TargetDir"
    exit 1
}
$TargetDir = (Resolve-Path $TargetDir).Path

if (-not (Test-Path (Join-Path $TargetDir 'app.py'))) {
    Write-Err2 "目标目录里没有 app.py，它可能不是站点根目录："
    Write-Err2 "  $TargetDir"
    Write-Err2 "站点根目录应包含 app.py、web.config、kdd\ 等，请用 -TargetDir 指定正确路径。"
    exit 1
}

if ($SourceDir -eq $TargetDir) {
    Write-Err2 "源目录与目标目录是同一个目录，不能自我覆盖："
    Write-Err2 "  $SourceDir"
    Write-Err2 "请把发布包放在站点目录之外，或用 -TargetDir 显式指定站点目录。"
    exit 1
}

Write-Info "源目录:   $SourceDir"
Write-Info "目标目录: $TargetDir  (站点 $SiteName)"

$missing = @()
foreach ($rel in $FullManifest) {
    if (-not (Test-Path (Join-Path $SourceDir $rel))) { $missing += $rel }
}
if ($missing.Count -gt 0) {
    Write-Warn2 "源目录缺少以下文件（将跳过未提供项）："
    foreach ($item in $missing) { Write-Warn2 "  - $item" }
    if ($missing.Count -eq $FullManifest.Count) {
        Write-Err2 "源目录里一个待发布文件都没有，请确认 -SourceDir 是否正确。"
        exit 1
    }
} else {
    Write-Info "清单完整，共 $($FullManifest.Count) 个文件"
}

# ---- 2. 备份 ----
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupRoot = Join-Path $TargetDir "_deploy_backup_$stamp"
Write-Step "备份将被覆盖的文件 -> $backupRoot"
$backupCount = 0
foreach ($rel in $FullManifest) {
    $target = Join-Path $TargetDir $rel
    if (Test-Path $target) {
        if ($DryRun) {
            Write-Info "[DRY] 备份 $rel"
        } else {
            $dest = Join-Path $backupRoot $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item $target $dest -Force
        }
        $backupCount++
    }
}
Write-Info "已备份 $backupCount 个文件"

# ---- 3. 复制文件 ----
Write-Step "复制文件到主站目录"
$copied = 0
foreach ($rel in $FullManifest) {
    $src = Join-Path $SourceDir $rel
    if (-not (Test-Path $src)) { continue }
    foreach ($blocked in $NeverTouch) {
        if ($rel -eq $blocked -or $rel.StartsWith("$blocked\")) {
            Write-Err2 "清单中出现禁止覆盖的路径，已中止：$rel"
            exit 1
        }
    }
    $dst = Join-Path $TargetDir $rel
    $isNew = $rel -in $NewFiles
    if ($DryRun) {
        Write-Info ("[DRY] {0} {1}" -f $(if ($isNew) { "新增" } else { "更新" }), $rel)
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item $src $dst -Force
        Write-Info ("{0} {1}" -f $(if ($isNew) { "新增" } else { "更新" }), $rel)
    }
    $copied++
}
Write-Info "共处理 $copied 个文件"

# ---- 4. kdd 独立站同步（可选）----
$kddFiles = $FullManifest | Where-Object { $_.StartsWith("kdd\") }
$kddTarget = $null
if (Test-Path $KddStandaloneDir) { $kddTarget = (Resolve-Path $KddStandaloneDir).Path }

if ($kddTarget -and ($kddTarget -eq $SourceDir -or $kddTarget -eq $TargetDir -or $kddTarget.StartsWith("$TargetDir\"))) {
    Write-Step "kdd 目录位于站点目录内（$kddTarget），已由主流程覆盖，跳过单独同步"
} elseif ($kddTarget) {
    Write-Step "同步到 kdd 独立站: $kddTarget"
    $kddBackup = Join-Path $kddTarget "_deploy_backup_$stamp"
    foreach ($rel in $kddFiles) {
        $src = Join-Path $SourceDir $rel
        if (-not (Test-Path $src)) { continue }
        $subPath = $rel.Substring(4)   # 去掉 "kdd\"
        $dst = Join-Path $kddTarget $subPath
        if ($DryRun) {
            Write-Info "[DRY] $subPath"
        } else {
            if (Test-Path $dst) {
                $bDest = Join-Path $kddBackup $subPath
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bDest) | Out-Null
                Copy-Item $dst $bDest -Force
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item $src $dst -Force
            Write-Info "$subPath"
        }
    }
    if (-not $DryRun) { Write-Info "独立站备份目录: $kddBackup" }
} else {
    Write-Step "未发现 kdd 独立站目录（$KddStandaloneDir），跳过同步"
}

# ---- 5. 安装依赖 ----
if ($SkipPip) {
    Write-Step "已跳过依赖安装（-SkipPip）"
} else {
    Write-Step "安装 Python 依赖（opencv-python-headless / numpy 等）"
    $pythonCandidates = @(
        "C:\Python313\python.exe", "C:\Python312\python.exe", "C:\Python311\python.exe",
        "C:\Program Files\Python313\python.exe", "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    $python = $null
    foreach ($candidate in $pythonCandidates) {
        if (Test-Path $candidate) { $python = $candidate; break }
    }
    if (-not $python) {
        $found = Get-Command python -ErrorAction SilentlyContinue
        if ($found) { $python = $found.Source }
    }
    if (-not $python) {
        Write-Warn2 "未找到 python.exe，请手动执行: pip install -r <站点>\kdd\requirements.txt"
    } else {
        Write-Info "Python: $python"
        $reqFile = Join-Path $TargetDir "kdd\requirements.txt"
        if (Test-Path $reqFile) {
            if ($DryRun) {
                Write-Info "[DRY] & `"$python`" -m pip install -r `"$reqFile`""
            } else {
                & $python -m pip install -r $reqFile
                if ($LASTEXITCODE -ne 0) {
                    Write-Warn2 "依赖安装返回非零码，请手动检查：$python -m pip install -r $reqFile"
                } else {
                    Write-Info "依赖安装完成"
                }
            }
        }
    }
}

# ---- 6. 冒烟测试（导入检查）----
Write-Step "冒烟测试：检查新模块能否正常导入"
$kddDir = Join-Path $TargetDir "kdd"
if ($DryRun) {
    Write-Info "[DRY] python -c `"import image_enhance, metrics, ocr_engine, database, config`""
} else {
    if (-not $python) {
        $found = Get-Command python -ErrorAction SilentlyContinue
        if ($found) { $python = $found.Source }
    }
    if ($python) {
        $check = "import sys; sys.path.insert(0, r'$kddDir'); import config, image_enhance, metrics, ocr_engine, database; print('IMPORT_OK')"
        # Python 会把无害告警（如 RequestsDependencyWarning）写到 stderr，
        # 在本脚本 $ErrorActionPreference='Stop' 下经 2>&1 重定向会被误判为致命错误，
        # 因此这里临时放宽，改以退出码 + IMPORT_OK 标记判定成败。
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & $python -c $check 2>&1 | Out-String
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        if ($exitCode -eq 0 -and $output -match "IMPORT_OK") {
            Write-Info "导入检查通过"
        } else {
            Write-Err2 "导入检查失败，服务可能起不来！输出如下："
            Write-Err2 $output.Trim()
            Write-Warn2 "可先把备份目录里的文件拷回目标目录回滚。"
            exit 1
        }
    } else {
        Write-Warn2 "未找到 python.exe，跳过导入检查"
    }
}

# ---- 7. 回收应用池，让新代码生效 ----
Write-Step "回收 IIS 应用池：$AppPoolName"
if ($DryRun) {
    Write-Info "[DRY] Restart-WebAppPool $AppPoolName"
} else {
    try {
        Import-Module WebAdministration -ErrorAction Stop
        $pool = Get-Item "IIS:\AppPools\$AppPoolName" -ErrorAction SilentlyContinue
        if ($pool) {
            Restart-WebAppPool $AppPoolName
            Write-Info "应用池已回收"
        } else {
            Write-Warn2 "未找到应用池 [$AppPoolName]。若站点名不同，请用 -AppPoolName 指定；"
            Write-Warn2 "或手动重启 IIS：iisreset /noforce"
        }
    } catch {
        Write-Warn2 "回收应用池失败（可能未安装 IIS 管理模块）：$_"
        Write-Warn2 "请手动执行：iisreset /noforce"
    }
}

# ---- 8. 汇总 ----
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host $(if ($DryRun) { " DRY-RUN 结束（未做任何修改）" } else { " 更新完成" }) -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
if (-not $DryRun) {
    Write-Host "主站备份目录: $backupRoot" -ForegroundColor Yellow
    Write-Host "回滚方式:     把备份目录内文件按相同相对路径拷回 $TargetDir 即可" -ForegroundColor Gray
}
Write-Host ""
Write-Host "部署后自检:" -ForegroundColor Cyan
Write-Host "  1. 启动日志不应出现「更新admin用户密码」" -ForegroundColor Gray
Write-Host "  2. 上传顺丰运单截图，确认寄件/收件姓名、电话、地址各自正确且不重复" -ForegroundColor Gray
Write-Host "  3. 单张识别耗时应在 1 秒左右（优化前约 2.5 秒）" -ForegroundColor Gray
Write-Host "  4. 管理员访问 /express/api/metrics，应返回各阶段 P50/P95" -ForegroundColor Gray
Write-Host "  5. 数据库表结构迁移（client_token 列与索引）由服务启动时自动完成" -ForegroundColor Gray
Write-Host ""
