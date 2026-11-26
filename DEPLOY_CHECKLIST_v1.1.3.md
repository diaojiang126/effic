# 部署清单 — 设备库存管理系统 v1.1.3

> 适用环境：Windows Server 2022 + IIS（站点名 `luosen`）
> 站点根目录：`C:\luosen\effic`　Python：`C:\Python39\python.exe`
> 对外访问：`http://1.94.99.61:5050/api/`
> 本次发布日期：2026-09-29

---

## 一、本次发布内容

| 模块 | 变更 |
|---|---|
| 后端 API | 新增 `/api/reports/summary`（报表汇总）、`/api/reports/recent`（近 N 天出入库汇总+明细）、`/export/records`（按日期范围导出 Excel/PDF）、`/api/inventory/stock`（各型号实时可用库存）；修复统计接口引用不存在字段导致数据全为 0 的问题；批量出入库接口加固（参数校验、条数上限 200、异常返回 JSON） |
| Web 报表页 | 日期范围查询、快捷范围（今日/近 7 天/近 30 天/本月）、记录类型筛选、汇总卡、按天/按品类汇总、出入库明细、Excel/PDF 导出（**入库与出库分开成表/分段**，明细一定带出） |
| Web 首页 | 系统版本号更新为 v1.1.3 |
| 安卓 App | 出库/批量出库页面**实时显示每个设备的剩余（可用）库存**；出入库「添加/批量」由弹窗改为**独立全屏页面**（表单分组、控件与间距优化、底部固定操作栏）；开始使用日期改为日历选择器（必填）；报表页新增近 7 日出入库汇总 + 明细列表；全局 UI 美化与响应式适配；修复服务器地址解析与 release 混淆导致无法登录的问题 |
| 数据库 | **无结构变更，无需执行任何 SQL** |

---

## 二、部署前准备

1. **备份现有文件**（PowerShell，管理员）
   ```powershell
   $ts = Get-Date -Format "yyyyMMdd_HHmmss"
   Copy-Item C:\luosen\effic\app.py "C:\luosen\effic\app.py.bak_$ts"
   Copy-Item C:\luosen\effic\templates "C:\luosen\effic\templates.bak_$ts" -Recurse
   Get-ChildItem C:\luosen\effic -Filter "*.bak_*" | Select-Object Name, LastWriteTime
   ```
2. **备份数据库**（可选但建议）
   ```powershell
   $ts = Get-Date -Format "yyyyMMdd_HHmmss"
   & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe" -u ls -pls1213456 device_inventory_system > "C:\luosen\backup_device_inventory_$ts.sql"
   ```
   （mysqldump 路径按服务器实际 MySQL 安装位置调整；也可在系统内使用「系统设置 → 数据备份」下载备份文件）
3. **安装导出组件 reportlab**（本次 PDF 导出依赖，若已安装可跳过）
   ```powershell
   C:\Python39\python.exe -m pip install reportlab
   C:\Python39\python.exe -c "import reportlab, openpyxl, pandas; print('reportlab', reportlab.Version)"
   ```
   *无外网时*：在联网机器执行 `pip download reportlab -d C:\wheels`（含依赖 Pillow），拷贝 `C:\wheels` 到服务器后执行
   `C:\Python39\python.exe -m pip install --no-index --find-links=C:\wheels reportlab`

---

## 三、需要部署的文件

从开发机（本仓库）复制到服务器站点根目录：

| # | 源文件（本仓库） | 服务器目标路径 | 说明 |
|---|---|---|---|
| 1 | `app.py` | `C:\luosen\effic\app.py` | 整份覆盖（含上一轮修复，务必用最新版） |
| 2 | `templates\report.html` | `C:\luosen\effic\templates\report.html` | 报表页（日期范围 + 导出） |
| 3 | `templates\index.html` | `C:\luosen\effic\templates\index.html` | 首页版本号 v1.1.3 |
| 4 | `requirements.txt` | `C:\luosen\effic\requirements.txt` | 仅作记录，已含 reportlab |
| 5 | `android_app\dist\effic-inventory-v1.1.3-arm64-v8a.apk` | 分发到手机 | 安卓客户端（推荐） |
| 6 | `android_app\dist\effic-inventory-v1.1.3-universal.apk` | 备用 | 全架构通用包 |

**不需要部署**：`web.config`（配置未变）、数据库、其他模板与静态资源。
（本清单文件 `DEPLOY_CHECKLIST_v1.1.3.md` 属运维文档，如需留档可一并复制到 `C:\luosen\effic\`）

---

## 四、部署步骤

1. 停止站点（避免文件占用与请求中途报错）
   ```powershell
   Stop-Website luosen
   ```
2. 复制上述第 1–4 个文件覆盖到 `C:\luosen\effic\`
3. 安装/确认 reportlab（见「二、3」）
4. 启动站点
   ```powershell
   Start-Website luosen
   # 如站点状态异常，可回收应用池（池名用 Get-Item 查询）：
   # (Get-Item "IIS:\Sites\luosen").applicationPool
   # Restart-WebAppPool -Name <池名>
   ```
   > `web.config` 中 `WSGI_RESTART_FILE_REGEX` 已包含 `.py`，改动 app.py 后 wfastcgi 会自动重启进程；模板有 `TEMPLATES_AUTO_RELOAD=True`，无需重启即可生效。
5. 分发 APK 到手机（覆盖安装，签名与旧包一致，**无需卸载**）

---

## 五、验证清单

### 5.1 后端接口（服务器本机执行）

```powershell
# ① 新接口是否已生效：未带 token 访问应返回 401（旧代码会 404）
try {
  Invoke-WebRequest "http://127.0.0.1:5050/api/reports/recent?days=7" -UseBasicParsing -TimeoutSec 10 | Out-Null
  "异常：返回了 200"
} catch { "status = " + $_.Exception.Response.StatusCode.value__ }   # 期望 401

# ② 公司列表接口正常
(Invoke-WebRequest "http://127.0.0.1:5050/api/public/companies" -UseBasicParsing -TimeoutSec 10).StatusCode  # 期望 200

# ③ 导出接口需登录：未登录应 302 跳登录页
try {
  Invoke-WebRequest "http://127.0.0.1:5050/export/records?format=xlsx" -UseBasicParsing -MaximumRedirection 0
} catch { "status = " + $_.Exception.Response.StatusCode.value__ }   # 期望 302
```

| 检查项 | 期望结果 |
|---|---|
| `/api/reports/recent?days=7`（无 token） | 401 |
| `/api/public/companies` | 200 且返回公司列表 |
| `/export/records`（未登录） | 302 重定向登录页 |
| `C:\Python39\python.exe -c "import reportlab"` | 无报错并输出版本号 |
| `C:\luosen\effic\logs\wsgi.log` | 无 ImportError / SyntaxError |

### 5.2 Web 端（浏览器）

1. 登录 → 首页「系统信息」显示 **v1.1.3**
2. 报表统计页：
   - 日期范围默认为近 7 天；点击「今日 / 近 7 天 / 近 30 天 / 本月」日期框正确联动
   - 点「查询」后 URL 带 `start/end/type`，汇总卡、按天汇总、按品类汇总、明细表均有数据（数据为空说明该区间确实无记录，换有数据的区间复测）
   - 记录类型切「仅入库」，明细「类型」列应全为「入库」
   - 点「导出Excel」：右上角绿色提示「导出完成：出入库记录_xxxx.xlsx」，打开后应有 **「汇总」「入库明细」「出库明细」三个工作表**：
     - 「汇总」含统计区间、入库/出库总量与笔数、**入库明细条数、出库明细条数**、按天、按品类
     - 「入库明细」「出库明细」各自独立成表（入库与出库不再混在一张表）；某类无数据时该表保留表头并显示「该时间段内无记录」
   - 点「导出PDF」：标题下方为「一、入库明细（N 条）」与「二、出库明细（M 条）」两段，段内各自成表
   - 记录类型选「仅入库/仅出库」时，导出文件只含对应类型的明细表
   - 填「开始日期晚于结束日期」或「区间超过 366 天」时点导出：出现黄色提示，页面不跳转、不下载
3. 数据一致性抽查：报表页「入库总量/出库总量」应与「入库管理/出库管理」列表内同区间记录相符

### 5.3 安卓端

| 步骤 | 期望结果 |
|---|---|
| 安装 `effic-inventory-v1.1.3-arm64-v8a.apk` 并打开 | 默认服务器地址 `http://1.94.99.61:5050/api/`，能加载公司列表 |
| 登录（admin/admin123） | 成功进入首页，底部显示 **v1.1.3** |
| 出库列表右下「+ 添加出库」 | 跳转到**独立添加出库页面**（非弹窗），顶部返回、底部「取消/保存出库」固定操作栏 |
| 添加出库页 → 点「开始使用日期」 | 弹出日历，选择后回填 `yyyy-MM-dd`；未选时「保存出库」不可点 |
| 出库列表右上「批量出库」 | 跳转到**批量出库页面**，表单分组清晰，设备列表占满整屏可滚动，填数量的型号行高亮 |
| 表单页保存成功 | 自动返回列表页，并在列表页显示绿色提示（如「出库记录添加成功」/「批量出库成功，共 N 项」） |
| 表单页返回 | 顶部返回或底部「取消」均可回到列表，不提交任何数据 |
| 添加出库页 → 选择设备型号 | 下拉列表中每个型号显示「可用库存：N」；选中后输入框下方也显示当前可用库存 |
| 添加出库页 → 数量填超过可用库存 | 数量框变红并提示「超出可用库存（当前可出 N）」（最终仍以服务端校验为准） |
| 批量出库页 | 每个设备行下方显示「剩余库存 N」；填的数量超出时该行变红提示；可点右上「刷新库存」重新拉取 |
| 报表页 | 显示今日出入库、趋势图、**近 7 天出入库明细（汇总 + 明细列表）**、品类明细、库存概况 |
| 报表页切换「近 14/30 天」「仅入库/仅出库」 | 趋势图与明细列表联动刷新 |

---

## 六、回滚方案

1. **后端/前端**：还原备份文件并重启站点
   ```powershell
   Stop-Website luosen
   Copy-Item C:\luosen\effic\app.py.bak_<时间戳> C:\luosen\effic\app.py -Force
   Copy-Item C:\luosen\effic\templates.bak_<时间戳>\* C:\luosen\effic\templates\ -Recurse -Force
   Start-Website luosen
   ```
2. **安卓**：直接安装上一版本的 APK 覆盖即可（同一签名密钥，无需卸载，本地登录状态保留）
3. **数据库**：本次无结构变更，通常无需回滚；如确需恢复，使用第五节第 2 步的备份文件

---

## 七、已知限制与注意事项

- 导出区间上限 **366 天**；PDF 明细最多 **3000 条**（超出请导 Excel）；Web 明细最多展示 **500 条**，App 明细最多展示 **100 条**（完整数据请用 Web 导出）。
- Excel 导出的明细分「入库明细」「出库明细」两张表；若某类在该区间无记录，该表只保留表头并提示「该时间段内无记录」。
- 安卓 release 包已**关闭 R8 混淆**（此前混淆会剥离泛型签名导致 Retrofit 崩溃），因此包体较大（arm64 约 18.8 MB）；后续如需恢复混淆须单独解决签名保留问题。
- App 与 Web 共用「数据库记录时间」判定区间（按服务器时区 Asia/Shanghai 的自然日）。
- 若报表页数据为 0 而列表有记录，请先确认服务器 `app.py` 已是最新版（旧版统计接口存在字段缺陷，见「一、本次发布内容」）。
- 建议部署后在 `C:\luosen\effic\logs\wsgi.log` 观察 5–10 分钟，确认无异常堆栈。
