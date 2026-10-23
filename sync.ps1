# ============================================================
#  同步代码：主 worktree (main)  --->  当前 worktree
#
#  用法（两种都行）：
#    1. 双击 sync.bat
#    2. 在 PowerShell 里执行：  .\sync.ps1
#
#  做了三件事：
#    1. 在主 worktree 执行 git pull，把远端最新代码拉下来
#    2. 把当前 worktree 的代码对齐到 main
#    3. 询问是否重建 Docker 镜像并重启服务
# ============================================================

$ErrorActionPreference = "Continue"

# ===== 可修改的配置 =====
$MainDir = "E:\workspace\studio\ArcReel"   # 主 worktree 路径
$HealthUrl = "http://localhost:1241/health"
# ========================

$WtDir = $PSScriptRoot

function Write-Step($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Write-Info($text) {
    Write-Host $text
}

function Write-Warn($text) {
    Write-Host $text -ForegroundColor Yellow
}

function Write-Err($text) {
    Write-Host $text -ForegroundColor Red
}

function Write-Ok($text) {
    Write-Host $text -ForegroundColor Green
}

# 在指定目录执行 git，返回退出码；输出到控制台
function Invoke-Git($dir, $gitArgs) {
    Push-Location $dir
    try {
        & git @gitArgs
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

# 取 git 输出的某一行（短 hash 用）
function Get-GitLine($dir, $gitArgs) {
    Push-Location $dir
    try {
        $out = & git @gitArgs 2>$null
        if ($out) { return ($out | Select-Object -First 1).Trim() }
        return ""
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  同步代码：主 worktree (main)  --->  当前 worktree" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---------- 检查主 worktree 路径 ----------
if (-not (Test-Path (Join-Path $MainDir ".git"))) {
    Write-Err "[错误] 找不到主 worktree：$MainDir"
    Write-Host ""
    Write-Host "请打开 sync.ps1 修改顶部的 `$MainDir = `"$MainDir`" 这一行。"
    Write-Host "或者在命令行执行 git worktree list 查看真实路径。"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

Write-Step "主 worktree"
Write-Info "路径：$MainDir"
Write-Info "当前分支：$(Get-GitLine $MainDir @('branch','--show-current'))"

# ---------- 第 1 步：拉取远端最新代码 ----------
Write-Step "[1/3] 拉取远端最新代码"
Write-Info "这一步在主 worktree 里执行 git pull。"
Write-Host ""

$rc = Invoke-Git $MainDir @("pull")
if ($rc -ne 0) {
    Write-Host ""
    Write-Warn "[警告] git pull 失败。"
    Write-Warn "       常见原因是网络/代理问题（GitHub 在国内通常需要代理）。"
    Write-Warn "       你可以先在主 worktree 手动 pull 成功，再重新运行本脚本。"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

$MainHead = Get-GitLine $MainDir @("rev-parse","--short","main")
Write-Host ""
Write-Ok "[完成] main 分支现在是：$MainHead"

# ---------- 第 2 步：同步到当前 worktree ----------
Write-Step "[2/3] 把当前 worktree 对齐到 main"
Write-Info "路径：$WtDir"
Write-Host ""

$OldHead = Get-GitLine $WtDir @("rev-parse","--short","HEAD")
$HasChanges = $false

Push-Location $WtDir
try {
    & git diff --quiet HEAD
    if ($LASTEXITCODE -ne 0) { $HasChanges = $true }
    else {
        & git diff --cached --quiet HEAD
        if ($LASTEXITCODE -ne 0) { $HasChanges = $true }
    }
}
finally { Pop-Location }

$Stashed = $false
if ($HasChanges) {
    Write-Warn "你有还没提交的修改。"
    Write-Info "脚本会先把它们收进 Git 的暂存箱（就像放进抽屉），同步完自动取回。"
    Write-Host ""
    Invoke-Git $WtDir @("stash","push","-m","sync.ps1 自动暂存") | Out-Null
    $Stashed = $true
}

$rc = Invoke-Git $WtDir @("reset","--hard","main")
if ($rc -ne 0) {
    Write-Host ""
    Write-Err "[错误] 对齐失败。"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

if ($Stashed) {
    Write-Host ""
    Write-Info "正在取回刚才收起来的修改..."
    $rc = Invoke-Git $WtDir @("stash","pop")
    if ($rc -ne 0) {
        Write-Host ""
        Write-Warn "[注意] 取回时出现冲突。"
        Write-Warn "       你的修改仍然安全地存在 Git 里，没有丢失。"
        Write-Warn "       执行 git stash list 可以看到，git stash pop 可以重试。"
    }
}

$NewHead = Get-GitLine $WtDir @("rev-parse","--short","HEAD")
Write-Host ""
Write-Ok "[完成] 代码已同步：$OldHead ---> $NewHead"
Write-Info "你那些没被 Git 跟踪的本地文件（Dockerfile.local、.env 等）都原样保留。"

# ---------- 第 3 步：重建 Docker ----------
Write-Step "[3/3] 是否重建 Docker 镜像并重启服务？"
Write-Host ""
Write-Info "  docker compose build   大约 5 分钟（代码变了就需要重建）"
Write-Info "  docker compose up -d   启动约 1 分钟"
Write-Host ""

$answer = Read-Host "输入 y 重建，直接回车跳过"
if ($answer -ne "y" -and $answer -ne "Y") {
    Write-Host ""
    Write-Info "已跳过 Docker 重建。代码已同步，需要时手动执行："
    Write-Host "  cd `"$WtDir\deploy`""
    Write-Host "  docker compose build --pull=false"
    Write-Host "  docker compose up -d"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 0
}

$DeployDir = Join-Path $WtDir "deploy"
Push-Location $DeployDir
try {
    Write-Host ""
    Write-Info "正在构建镜像，请耐心等待..."
    Write-Host ""
    & docker compose build --pull=false
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Err "[错误] 镜像构建失败，上方是详细日志。"
        Write-Host ""
        Read-Host "按回车键退出"
        exit 1
    }

    Write-Host ""
    Write-Info "正在启动容器..."
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Err "[错误] 容器启动失败。"
        Write-Host ""
        Read-Host "按回车键退出"
        exit 1
    }
}
finally { Pop-Location }

Write-Host ""
Write-Info "等待服务就绪（最多约 3 分钟）..."
$ready = $false
for ($i = 1; $i -le 12; $i++) {
    Start-Sleep -Seconds 15
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 8 -UseBasicParsing -Proxy $null
        $code = $resp.StatusCode
    }
    catch {
        $code = 0
    }
    Write-Host "  等待中... [$i/12] HTTP $code"
    if ($code -eq 200) { $ready = $true; break }
}

Write-Host ""
if ($ready) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  全部完成！" -ForegroundColor Green
    Write-Host ""
    Write-Host "  访问地址：http://localhost:1241"
    Write-Host "  登录密码：见 deploy\.env 里的 AUTH_PASSWORD"
    Write-Host "============================================================" -ForegroundColor Green
}
else {
    Write-Warn "[注意] 等待超时，服务可能还在启动。"
    Write-Warn "       用 docker compose logs -f arcreel 查看日志。"
}

Write-Host ""
Read-Host "按回车键退出"
exit 0
