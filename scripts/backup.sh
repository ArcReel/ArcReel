#!/usr/bin/env bash
# =============================================================================
# ArcReel Backup Script
# Sao lưu tự động PostgreSQL + thư mục projects
# Auto backup PostgreSQL + projects directory
# 自动备份 PostgreSQL + projects 目录
#
# Cách dùng / Usage / 用法:
#   chmod +x scripts/backup.sh
#   ./scripts/backup.sh                    # Backup thủ công
#   crontab -e → 0 3 * * * /path/to/scripts/backup.sh  # Daily 3AM
#
# Cấu hình qua biến môi trường / Configuration via env vars:
#   BACKUP_DIR        — Thư mục lưu backup (default: ./backups)
#   BACKUP_KEEP_DAYS  — Giữ backup bao nhiêu ngày (default: 7)
#   POSTGRES_HOST     — PostgreSQL host (default: localhost)
#   POSTGRES_PORT     — PostgreSQL port (default: 5433)
#   POSTGRES_USER     — PostgreSQL user (default: arcreel)
#   POSTGRES_DB       — PostgreSQL database (default: arcreel)
# =============================================================================

set -euo pipefail

# Defaults
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5433}"
POSTGRES_USER="${POSTGRES_USER:-arcreel}"
POSTGRES_DB="${POSTGRES_DB:-arcreel}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Create backup dir
mkdir -p "$BACKUP_DIR"

log_info "=== ArcReel Backup — $TIMESTAMP ==="
log_info "Backup dir: $BACKUP_DIR"

# --- 1. PostgreSQL Backup ---
DB_BACKUP_FILE="$BACKUP_DIR/db_${TIMESTAMP}.sql.gz"
log_info "Backing up PostgreSQL ($POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB)..."

if command -v pg_dump &>/dev/null; then
    pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$POSTGRES_DB" \
        | gzip > "$DB_BACKUP_FILE" 2>/dev/null
    DB_SIZE=$(du -sh "$DB_BACKUP_FILE" | cut -f1)
    log_info "Database backup: $DB_BACKUP_FILE ($DB_SIZE)"
elif docker ps --format '{{.Names}}' | grep -q "postgres"; then
    # Fallback: pg_dump via Docker container
    CONTAINER=$(docker ps --format '{{.Names}}' | grep "postgres" | head -1)
    docker exec "$CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
        | gzip > "$DB_BACKUP_FILE" 2>/dev/null
    DB_SIZE=$(du -sh "$DB_BACKUP_FILE" | cut -f1)
    log_info "Database backup (via Docker): $DB_BACKUP_FILE ($DB_SIZE)"
else
    log_warn "pg_dump not found and no Docker postgres container running. Skipping DB backup."
fi

# --- 2. Projects Directory Backup ---
PROJECTS_DIR="$PROJECT_ROOT/projects"
if [ -d "$PROJECTS_DIR" ]; then
    PROJECTS_BACKUP_FILE="$BACKUP_DIR/projects_${TIMESTAMP}.tar.gz"
    log_info "Backing up projects directory..."
    tar -czf "$PROJECTS_BACKUP_FILE" -C "$PROJECT_ROOT" projects 2>/dev/null
    PROJ_SIZE=$(du -sh "$PROJECTS_BACKUP_FILE" | cut -f1)
    log_info "Projects backup: $PROJECTS_BACKUP_FILE ($PROJ_SIZE)"
else
    log_warn "No projects directory found at $PROJECTS_DIR. Skipping."
fi

# --- 3. .env Backup (nếu có / if exists) ---
if [ -f "$PROJECT_ROOT/.env" ]; then
    cp "$PROJECT_ROOT/.env" "$BACKUP_DIR/env_${TIMESTAMP}.bak"
    log_info "Config backup: env_${TIMESTAMP}.bak"
fi

# --- 4. Cleanup old backups ---
if [ "$BACKUP_KEEP_DAYS" -gt 0 ]; then
    OLD_COUNT=$(find "$BACKUP_DIR" -name "db_*" -o -name "projects_*" -o -name "env_*" \
        -mtime +"$BACKUP_KEEP_DAYS" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$OLD_COUNT" -gt 0 ]; then
        find "$BACKUP_DIR" -name "db_*" -o -name "projects_*" -o -name "env_*" \
            -mtime +"$BACKUP_KEEP_DAYS" -delete 2>/dev/null
        log_info "Cleaned up $OLD_COUNT old backup(s) (older than $BACKUP_KEEP_DAYS days)"
    fi
fi

# --- Summary ---
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR" | wc -l | tr -d ' ')
log_info "=== Backup complete ==="
log_info "Total backups: $BACKUP_COUNT files ($TOTAL_SIZE)"
log_info "Retention: $BACKUP_KEEP_DAYS days"
