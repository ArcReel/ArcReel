#!/usr/bin/env bash
# ============================================================================
# ArcReel — Universal Installer (macOS / Linux)
# Tự động nhận diện hệ điều hành, kiểm tra prerequisites, cài đặt toàn bộ
# ============================================================================
set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ─── Config ──────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/ArcReel/ArcReel.git"
INSTALL_DIR="${ARCREEL_INSTALL_DIR:-$(pwd)/arcreel}"
COMPOSE_FILE="deploy/production/docker-compose.yml"
APP_PORT="${ARCREEL_PORT:-1241}"

# ─── Detect OS ───────────────────────────────────────────────────────────────
detect_os() {
    local uname_s
    uname_s="$(uname -s)"
    case "$uname_s" in
        Darwin)  OS="macos" ;;
        Linux)
            if grep -qEi "(Microsoft|WSL)" /proc/version 2>/dev/null; then
                OS="wsl"
            else
                OS="linux"
            fi
            ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"
            ;;
        *)
            echo -e "${RED}❌ Hệ điều hành không được hỗ trợ: $uname_s${NC}"
            echo -e "${RED}❌ Unsupported OS: $uname_s${NC}"
            exit 1
            ;;
    esac
    echo -e "${CYAN}🖥  Hệ điều hành / OS: ${OS}${NC}"
}

# ─── Print Banner ────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     ${CYAN}🎬 ArcReel — AI Video Creation Platform${BLUE}    ║${NC}"
    echo -e "${BLUE}║     ${NC}Universal Installer v1.0${BLUE}                    ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════╝${NC}"
    echo ""
}

# ─── Check Docker ────────────────────────────────────────────────────────────
check_docker() {
    echo -e "${YELLOW}🔍 Kiểm tra Docker / Checking Docker...${NC}"
    if command -v docker &>/dev/null; then
        local docker_version
        docker_version=$(docker --version 2>/dev/null || echo "unknown")
        echo -e "${GREEN}✅ Docker đã cài đặt / Docker installed: ${docker_version}${NC}"
    else
        echo -e "${RED}❌ Docker chưa được cài đặt / Docker not installed${NC}"
        echo ""
        case "$OS" in
            macos)
                echo -e "${YELLOW}📦 Cài Docker Desktop cho macOS:${NC}"
                echo -e "   ${CYAN}https://docs.docker.com/desktop/install/mac-install/${NC}"
                echo ""
                echo -e "${YELLOW}Hoặc sử dụng Homebrew:${NC}"
                echo -e "   ${CYAN}brew install --cask docker${NC}"
                ;;
            linux|wsl)
                echo -e "${YELLOW}📦 Cài Docker cho Linux:${NC}"
                echo -e "   ${CYAN}curl -fsSL https://get.docker.com | sh${NC}"
                echo -e "   ${CYAN}sudo usermod -aG docker \$USER${NC}"
                echo -e "   ${CYAN}newgrp docker${NC}"
                ;;
            *)
                echo -e "${YELLOW}📦 Tải Docker Desktop:${NC}"
                echo -e "   ${CYAN}https://docs.docker.com/get-docker/${NC}"
                ;;
        esac
        echo ""
        echo -e "${RED}Vui lòng cài Docker rồi chạy lại script này.${NC}"
        echo -e "${RED}Please install Docker and re-run this script.${NC}"
        exit 1
    fi

    # Check Docker Compose
    if docker compose version &>/dev/null; then
        local compose_version
        compose_version=$(docker compose version --short 2>/dev/null || echo "unknown")
        echo -e "${GREEN}✅ Docker Compose: ${compose_version}${NC}"
    elif command -v docker-compose &>/dev/null; then
        echo -e "${GREEN}✅ Docker Compose (standalone) đã cài${NC}"
        # Create alias
        DOCKER_COMPOSE="docker-compose"
    else
        echo -e "${RED}❌ Docker Compose chưa được cài đặt${NC}"
        echo -e "${YELLOW}   Vui lòng cập nhật Docker Desktop hoặc cài docker-compose-plugin${NC}"
        exit 1
    fi

    # Check Docker daemon
    if ! docker info &>/dev/null; then
        echo -e "${RED}❌ Docker daemon chưa chạy / Docker daemon not running${NC}"
        case "$OS" in
            macos)
                echo -e "${YELLOW}   Vui lòng mở Docker Desktop / Please start Docker Desktop${NC}" ;;
            linux)
                echo -e "${YELLOW}   Chạy: sudo systemctl start docker${NC}" ;;
        esac
        exit 1
    fi
    echo -e "${GREEN}✅ Docker daemon đang chạy / Docker daemon running${NC}"
}

# ─── Check Git ───────────────────────────────────────────────────────────────
check_git() {
    echo -e "${YELLOW}🔍 Kiểm tra Git / Checking Git...${NC}"
    if command -v git &>/dev/null; then
        local git_version
        git_version=$(git --version 2>/dev/null || echo "unknown")
        echo -e "${GREEN}✅ Git đã cài đặt / Git installed: ${git_version}${NC}"
    else
        echo -e "${RED}❌ Git chưa được cài đặt / Git not installed${NC}"
        case "$OS" in
            macos)
                echo -e "${YELLOW}   Chạy: xcode-select --install${NC}"
                echo -e "${YELLOW}   Hoặc: brew install git${NC}"
                ;;
            linux)
                echo -e "${YELLOW}   Chạy: sudo apt install git   (Debian/Ubuntu)${NC}"
                echo -e "${YELLOW}         sudo yum install git   (CentOS/RHEL)${NC}"
                ;;
        esac
        exit 1
    fi
}

# ─── Clone or Update Repo ───────────────────────────────────────────────────
setup_repo() {
    echo ""
    echo -e "${YELLOW}📥 Thiết lập repository / Setting up repository...${NC}"

    if [ -d "$INSTALL_DIR/.git" ]; then
        echo -e "${GREEN}📂 Repository đã tồn tại tại / Repository exists at: ${INSTALL_DIR}${NC}"
        echo -e "${YELLOW}   Đang cập nhật / Updating...${NC}"
        cd "$INSTALL_DIR"
        git pull --ff-only 2>/dev/null || echo -e "${YELLOW}⚠  Không thể auto-pull, dùng bản hiện tại${NC}"
    else
        echo -e "${CYAN}   Đang clone từ / Cloning from: ${REPO_URL}${NC}"
        echo -e "${CYAN}   Đến / To: ${INSTALL_DIR}${NC}"
        git clone "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi

    echo -e "${GREEN}✅ Repository sẵn sàng / Repository ready${NC}"
}

# ─── Generate .env ───────────────────────────────────────────────────────────
setup_env() {
    echo ""
    echo -e "${YELLOW}⚙️  Thiết lập cấu hình / Setting up configuration...${NC}"

    local env_file="$INSTALL_DIR/.env"

    if [ -f "$env_file" ]; then
        echo -e "${GREEN}📄 File .env đã tồn tại, giữ nguyên / .env exists, keeping it${NC}"
        return
    fi

    # Generate random passwords
    local db_pass
    local auth_pass
    local jwt_secret
    db_pass=$(openssl rand -base64 16 | tr -d '=/+' | head -c 20)
    auth_pass=$(openssl rand -base64 16 | tr -d '=/+' | head -c 16)
    jwt_secret=$(openssl rand -hex 32)

    cat > "$env_file" << EOF
# ============================================================================
# ArcReel Configuration — Auto-generated by installer
# Cấu hình ArcReel — Tự động tạo bởi trình cài đặt
# ============================================================================

# Database (PostgreSQL Docker local)
DATABASE_URL=postgresql+asyncpg://arcreel:${db_pass}@localhost:5432/arcreel

# Authentication
AUTH_USERNAME=admin
AUTH_PASSWORD=${auth_pass}
AUTH_TOKEN_SECRET=${jwt_secret}

# Server
HOST=0.0.0.0
PORT=${APP_PORT}
EOF

    echo -e "${GREEN}✅ Đã tạo .env với mật khẩu ngẫu nhiên / .env created with random passwords${NC}"
    echo -e "${CYAN}   📄 Xem cấu hình tại / View config at: ${env_file}${NC}"
}

# ─── Start Docker Compose ───────────────────────────────────────────────────
start_services() {
    echo ""
    echo -e "${YELLOW}🐳 Khởi động dịch vụ Docker / Starting Docker services...${NC}"

    cd "$INSTALL_DIR"

    local compose_cmd="docker compose"
    if [ "${DOCKER_COMPOSE:-}" = "docker-compose" ]; then
        compose_cmd="docker-compose"
    fi

    # Check if compose file exists
    if [ ! -f "$COMPOSE_FILE" ]; then
        echo -e "${YELLOW}⚠  Không tìm thấy ${COMPOSE_FILE}, sử dụng Dockerfile trực tiếp${NC}"
        echo -e "${YELLOW}⚠  ${COMPOSE_FILE} not found, using Dockerfile directly${NC}"

        # Build and run with inline compose
        $compose_cmd -f - up -d --build << 'COMPOSE_EOF'
version: '3.8'
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: arcreel
      POSTGRES_PASSWORD: ${DB_PASS:-arcreel_pass}
      POSTGRES_DB: arcreel
    ports:
      - "5432:5432"
    volumes:
      - arcreel_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U arcreel"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  arcreel_pgdata:
COMPOSE_EOF
    else
        $compose_cmd -f "$COMPOSE_FILE" up -d --build
    fi

    echo -e "${GREEN}✅ Dịch vụ Docker đã khởi động / Docker services started${NC}"
}

# ─── Wait for PostgreSQL ────────────────────────────────────────────────────
wait_for_postgres() {
    echo ""
    echo -e "${YELLOW}⏳ Đợi PostgreSQL sẵn sàng / Waiting for PostgreSQL...${NC}"

    local retries=30
    while [ $retries -gt 0 ]; do
        if docker compose -f "$COMPOSE_FILE" exec -T postgres pg_isready -U arcreel &>/dev/null 2>&1 || \
           docker exec "$(docker ps -q --filter name=postgres)" pg_isready -U arcreel &>/dev/null 2>&1; then
            echo -e "${GREEN}✅ PostgreSQL sẵn sàng / PostgreSQL ready${NC}"
            return 0
        fi
        retries=$((retries - 1))
        echo -n "."
        sleep 2
    done

    echo ""
    echo -e "${YELLOW}⚠  PostgreSQL mất thời gian khởi động, tiếp tục... / PostgreSQL taking time, continuing...${NC}"
}

# ─── Run Migrations ─────────────────────────────────────────────────────────
run_migrations() {
    echo ""
    echo -e "${YELLOW}📦 Chạy database migrations / Running database migrations...${NC}"

    cd "$INSTALL_DIR"

    # Try running alembic in Docker
    if docker compose -f "$COMPOSE_FILE" exec -T app alembic upgrade head 2>/dev/null; then
        echo -e "${GREEN}✅ Migrations hoàn tất / Migrations complete${NC}"
    else
        echo -e "${YELLOW}⚠  Migrations sẽ được chạy khi app khởi động / Migrations will run on app startup${NC}"
    fi
}

# ─── Print Success ───────────────────────────────────────────────────────────
print_success() {
    local env_file="$INSTALL_DIR/.env"
    local auth_user="admin"

    # Try to read auth password from .env
    local auth_pass=""
    if [ -f "$env_file" ]; then
        auth_pass=$(grep "^AUTH_PASSWORD=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "")
    fi

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║     ${CYAN}✅ ArcReel đã cài đặt thành công!${GREEN}          ║${NC}"
    echo -e "${GREEN}║     ${CYAN}✅ ArcReel installed successfully!${GREEN}          ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}🌐 Truy cập / Access:   ${GREEN}http://localhost:${APP_PORT}${NC}"
    echo -e "${CYAN}👤 Tài khoản / User:    ${GREEN}${auth_user}${NC}"
    if [ -n "$auth_pass" ]; then
        echo -e "${CYAN}🔑 Mật khẩu / Password: ${GREEN}${auth_pass}${NC}"
    else
        echo -e "${CYAN}🔑 Mật khẩu / Password: ${YELLOW}(xem .env / see .env)${NC}"
    fi
    echo -e "${CYAN}📂 Thư mục / Directory: ${GREEN}${INSTALL_DIR}${NC}"
    echo ""
    echo -e "${YELLOW}📋 Các lệnh hữu ích / Useful commands:${NC}"
    echo -e "   ${CYAN}cd ${INSTALL_DIR}${NC}"
    echo -e "   ${CYAN}docker compose -f ${COMPOSE_FILE} logs -f       ${NC}# Xem logs / View logs"
    echo -e "   ${CYAN}docker compose -f ${COMPOSE_FILE} restart      ${NC}# Khởi động lại / Restart"
    echo -e "   ${CYAN}docker compose -f ${COMPOSE_FILE} down         ${NC}# Dừng / Stop"
    echo -e "   ${CYAN}docker compose -f ${COMPOSE_FILE} down -v      ${NC}# Xóa toàn bộ / Remove all"
    echo ""
    echo -e "${BLUE}🌍 Hỗ trợ đa ngôn ngữ / Multi-language support:${NC}"
    echo -e "   ${GREEN}🇻🇳 Tiếng Việt  |  🇺🇸 English  |  🇨🇳 中文${NC}"
    echo ""
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    print_banner
    detect_os
    echo ""
    check_docker
    check_git
    setup_repo
    setup_env
    start_services
    wait_for_postgres
    run_migrations
    print_success
}

main "$@"
