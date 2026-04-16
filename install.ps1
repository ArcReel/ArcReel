# ============================================================================
# ArcReel — Universal Installer (Windows PowerShell)
# Tự động kiểm tra prerequisites, cài đặt toàn bộ hệ thống
# ============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

# ─── Config ──────────────────────────────────────────────────────────────────
$REPO_URL = "https://github.com/ArcReel/ArcReel.git"
$INSTALL_DIR = if ($env:ARCREEL_INSTALL_DIR) { $env:ARCREEL_INSTALL_DIR } else { Join-Path (Get-Location) "arcreel" }
$COMPOSE_FILE = "deploy/production/docker-compose.yml"
$APP_PORT = if ($env:ARCREEL_PORT) { $env:ARCREEL_PORT } else { "1241" }

# ─── Helper Functions ────────────────────────────────────────────────────────
function Write-Header {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════╗" -ForegroundColor Blue
    Write-Host "║     🎬 ArcReel — AI Video Creation Platform    ║" -ForegroundColor Cyan
    Write-Host "║     Universal Installer v1.0 (Windows)         ║" -ForegroundColor White
    Write-Host "╚════════════════════════════════════════════════╝" -ForegroundColor Blue
    Write-Host ""
}

function Write-Step($msg) {
    Write-Host "🔍 $msg" -ForegroundColor Yellow
}

function Write-OK($msg) {
    Write-Host "✅ $msg" -ForegroundColor Green
}

function Write-Err($msg) {
    Write-Host "❌ $msg" -ForegroundColor Red
}

function Write-Warn($msg) {
    Write-Host "⚠  $msg" -ForegroundColor Yellow
}

# ─── Check Docker ────────────────────────────────────────────────────────────
function Test-Docker {
    Write-Step "Kiem tra Docker / Checking Docker..."

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Err "Docker chua duoc cai dat / Docker not installed"
        Write-Host ""
        Write-Host "📦 Cai Docker Desktop cho Windows:" -ForegroundColor Yellow
        Write-Host "   https://docs.docker.com/desktop/install/windows-install/" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Vui long cai Docker roi chay lai script nay." -ForegroundColor Red
        Write-Host "Please install Docker and re-run this script." -ForegroundColor Red
        exit 1
    }

    $version = docker --version 2>$null
    Write-OK "Docker da cai dat / Docker installed: $version"

    # Check Docker Compose
    try {
        $composeVer = docker compose version --short 2>$null
        Write-OK "Docker Compose: $composeVer"
    } catch {
        Write-Err "Docker Compose chua duoc cai dat / Docker Compose not installed"
        Write-Host "   Vui long cap nhat Docker Desktop" -ForegroundColor Yellow
        exit 1
    }

    # Check Docker daemon
    try {
        docker info 2>$null | Out-Null
        Write-OK "Docker daemon dang chay / Docker daemon running"
    } catch {
        Write-Err "Docker daemon chua chay / Docker daemon not running"
        Write-Host "   Vui long mo Docker Desktop / Please start Docker Desktop" -ForegroundColor Yellow
        exit 1
    }
}

# ─── Check Git ───────────────────────────────────────────────────────────────
function Test-Git {
    Write-Step "Kiem tra Git / Checking Git..."

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Err "Git chua duoc cai dat / Git not installed"
        Write-Host "   Tai Git tai: https://git-scm.com/download/win" -ForegroundColor Yellow
        exit 1
    }

    $version = git --version 2>$null
    Write-OK "Git da cai dat / Git installed: $version"
}

# ─── Setup Repo ──────────────────────────────────────────────────────────────
function Setup-Repo {
    Write-Host ""
    Write-Step "Thiet lap repository / Setting up repository..."

    if (Test-Path (Join-Path $INSTALL_DIR ".git")) {
        Write-OK "Repository da ton tai tai / Repository exists at: $INSTALL_DIR"
        Write-Warn "Dang cap nhat / Updating..."
        Set-Location $INSTALL_DIR
        try { git pull --ff-only 2>$null } catch { Write-Warn "Khong the auto-pull, dung ban hien tai" }
    } else {
        Write-Host "   Dang clone tu / Cloning from: $REPO_URL" -ForegroundColor Cyan
        Write-Host "   Den / To: $INSTALL_DIR" -ForegroundColor Cyan
        git clone $REPO_URL $INSTALL_DIR
        Set-Location $INSTALL_DIR
    }

    Write-OK "Repository san sang / Repository ready"
}

# ─── Generate .env ───────────────────────────────────────────────────────────
function Setup-Env {
    Write-Host ""
    Write-Step "Thiet lap cau hinh / Setting up configuration..."

    $envFile = Join-Path $INSTALL_DIR ".env"

    if (Test-Path $envFile) {
        Write-OK "File .env da ton tai, giu nguyen / .env exists, keeping it"
        return
    }

    # Generate random passwords
    $dbPass = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
    $authPass = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    $jwtSecret = -join ((48..57) + (97..102) | Get-Random -Count 64 | ForEach-Object { [char]$_ })

    $envContent = @"
# ============================================================================
# ArcReel Configuration — Auto-generated by installer
# Cau hinh ArcReel — Tu dong tao boi trinh cai dat
# ============================================================================

# Database (PostgreSQL Docker local)
DATABASE_URL=postgresql+asyncpg://arcreel:${dbPass}@localhost:5432/arcreel

# Authentication
AUTH_USERNAME=admin
AUTH_PASSWORD=${authPass}
AUTH_TOKEN_SECRET=${jwtSecret}

# Server
HOST=0.0.0.0
PORT=${APP_PORT}
"@

    Set-Content -Path $envFile -Value $envContent -Encoding UTF8
    Write-OK "Da tao .env voi mat khau ngau nhien / .env created with random passwords"
    Write-Host "   📄 Xem cau hinh tai / View config at: $envFile" -ForegroundColor Cyan
}

# ─── Start Docker Compose ───────────────────────────────────────────────────
function Start-Services {
    Write-Host ""
    Write-Step "Khoi dong dich vu Docker / Starting Docker services..."

    Set-Location $INSTALL_DIR

    if (Test-Path $COMPOSE_FILE) {
        docker compose -f $COMPOSE_FILE up -d --build
    } else {
        Write-Warn "Khong tim thay $COMPOSE_FILE"
        Write-Host "   Dang tao PostgreSQL container..." -ForegroundColor Yellow

        # Start standalone PostgreSQL
        docker run -d `
            --name arcreel-postgres `
            -e POSTGRES_USER=arcreel `
            -e POSTGRES_PASSWORD=arcreel_pass `
            -e POSTGRES_DB=arcreel `
            -p 5432:5432 `
            --restart unless-stopped `
            postgres:16-alpine

        Write-OK "PostgreSQL container da khoi dong"
    }

    Write-OK "Dich vu Docker da khoi dong / Docker services started"
}

# ─── Wait for PostgreSQL ────────────────────────────────────────────────────
function Wait-ForPostgres {
    Write-Host ""
    Write-Step "Doi PostgreSQL san sang / Waiting for PostgreSQL..."

    $retries = 30
    while ($retries -gt 0) {
        try {
            $result = docker exec (docker ps -q --filter "name=postgres" | Select-Object -First 1) pg_isready -U arcreel 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-OK "PostgreSQL san sang / PostgreSQL ready"
                return
            }
        } catch {}

        $retries--
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 2
    }

    Write-Host ""
    Write-Warn "PostgreSQL mat thoi gian khoi dong, tiep tuc... / PostgreSQL taking time, continuing..."
}

# ─── Print Success ───────────────────────────────────────────────────────────
function Write-Success {
    $envFile = Join-Path $INSTALL_DIR ".env"
    $authPass = ""
    if (Test-Path $envFile) {
        $match = Select-String -Path $envFile -Pattern "^AUTH_PASSWORD=(.+)$" -ErrorAction SilentlyContinue
        if ($match) { $authPass = $match.Matches[0].Groups[1].Value }
    }

    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║     ✅ ArcReel da cai dat thanh cong!           ║" -ForegroundColor Cyan
    Write-Host "║     ✅ ArcReel installed successfully!           ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-Host "🌐 Truy cap / Access:   http://localhost:${APP_PORT}" -ForegroundColor Green
    Write-Host "👤 Tai khoan / User:    admin" -ForegroundColor Green
    if ($authPass) {
        Write-Host "🔑 Mat khau / Password: $authPass" -ForegroundColor Green
    } else {
        Write-Host "🔑 Mat khau / Password: (xem .env / see .env)" -ForegroundColor Yellow
    }
    Write-Host "📂 Thu muc / Directory: $INSTALL_DIR" -ForegroundColor Green
    Write-Host ""
    Write-Host "🌍 Ho tro da ngon ngu / Multi-language support:" -ForegroundColor Blue
    Write-Host "   🇻🇳 Tieng Viet  |  🇺🇸 English  |  🇨🇳 中文" -ForegroundColor Green
    Write-Host ""
}

# ─── Main ────────────────────────────────────────────────────────────────────
Write-Header
Test-Docker
Test-Git
Setup-Repo
Setup-Env
Start-Services
Wait-ForPostgres
Write-Success

Write-Host "Nhan phim bat ky de dong / Press any key to close..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
