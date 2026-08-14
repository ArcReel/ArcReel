---
id: migrate-to-postgres
title: 从 SQLite 迁移到 PostgreSQL
sidebar_position: 2
---

# 从 SQLite 迁移到 PostgreSQL {#migrate-to-postgres}

本文档适用于已使用默认 Docker + SQLite 部署 ArcReel、希望切换到 `deploy/production/` PostgreSQL 部署的场景。以下命令都从 ArcReel 仓库根目录执行。

迁移前先区分三类数据：

| 路径 | 用途 | 迁移处理 |
|---|---|---|
| `deploy/projects/.arcreel.db` | 默认部署的 SQLite 数据库 | 由 pgloader 导入 PostgreSQL |
| `deploy/projects/` 中的其他文件 | 项目元数据和媒体资产 | 复制到 `deploy/production/projects/` |
| `deploy/production/pgdata/` | PostgreSQL 集群数据 | 由 PostgreSQL 初始化，不放项目文件或 SQLite 文件 |

如果通过 `ARCREEL_DATA_DIR` 自定义了数据根目录，请把本文中的 `deploy/projects/` 替换为实际源目录。

## 前置条件 {#prerequisites}

- 已安装 Docker 和 Docker Compose
- 已安装 `sqlite3` 命令行工具（先运行 `sqlite3 --version` 确认）
- ArcReel 当前使用默认 SQLite 部署，数据库位于 `deploy/projects/.arcreel.db`
- `deploy/production/pgdata/` 与 `deploy/production/projects/` 尚未存放需要保留的生产数据

## 迁移步骤 {#migration-steps}

### 1. 停止 ArcReel 服务 {#stop-services}

```bash
cd "$(git rev-parse --show-toplevel)"
docker compose -f deploy/docker-compose.yml down
```

从此到迁移验证完成前，不要重新启动默认部署，以免 SQLite 数据库与项目资产继续发生写入。

### 2. 生成一致备份 {#backup-sqlite}

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p deploy/backups

sqlite3 deploy/projects/.arcreel.db \
  ".backup 'deploy/backups/arcreel-sqlite-${backup_stamp}.db'"

sqlite3 "deploy/backups/arcreel-sqlite-${backup_stamp}.db" \
  "PRAGMA quick_check;"

tar -czf "deploy/backups/arcreel-source-${backup_stamp}.tar.gz" \
  -C deploy .env projects
```

`PRAGMA quick_check;` 必须输出 `ok`。`sqlite3 .backup` 通过 SQLite 备份 API 生成包含已提交 WAL 内容的一致快照；不要在服务运行时只用 `cp` 复制 `.arcreel.db`。SQLite 的 `.arcreel.db-wal` 可能保存已提交但尚未 checkpoint 的交易，与主文件分离可能丢数据或损坏备份。配套的 tar 归档用于恢复原 `.env`、数据库边车文件和项目资产。

### 3. 准备 PostgreSQL 部署 {#configure-env}

创建生产配置：

```bash
test ! -e deploy/production/.env
cp deploy/production/.env.example deploy/production/.env
```

如果第一条命令失败，说明生产配置已存在；先核对并保留其中的有效设置，不要直接覆盖。

编辑 `deploy/production/.env`，设置认证参数与 PostgreSQL 密码：

```env
AUTH_USERNAME=admin
AUTH_PASSWORD=请设置强密码
AUTH_TOKEN_SECRET=请设置长期固定的随机密钥
POSTGRES_PASSWORD=请设置只含字母和数字的数据库密码
```

`DATABASE_URL` 已在生产 Compose 中自动拼接。本迁移命令也会把密码放入 pgloader 的 PostgreSQL URI，因此建议用 `openssl rand -hex 16` 生成 URL-safe 密码。如果必须使用特殊字符，需按[部署指南的说明](./deployment.md#postgresql-start)分开保存原始密码与百分号编码后的 URI 密码，不能把编码值直接当作 `POSTGRES_PASSWORD`。下面的 pgloader 命令在存在 `POSTGRES_PASSWORD_URLENCODED` 时优先使用它。

确认以下命令没有输出；如果目标目录已有内容，先停止迁移，不要覆盖：

```bash
find deploy/production/projects -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null
test ! -e deploy/production/pgdata/PG_VERSION
```

将项目和媒体资产复制到生产目录，但不把 SQLite 数据库复制进去：

```bash
mkdir -p deploy/production/projects
tar -C deploy/projects --exclude='.arcreel.db*' -cf - . | \
  tar -C deploy/production/projects -xf -
```

### 4. 启动 PostgreSQL {#start-postgresql}

先只启动数据库服务：

```bash
docker compose -f deploy/production/docker-compose.yml up -d postgres
```

等待健康检查通过：

```bash
docker compose -f deploy/production/docker-compose.yml ps
```

### 5. 迁移数据 {#migrate-data}

在 ArcReel 容器内使用 pgloader 将原 SQLite 数据库直接迁移到 PostgreSQL：

```bash
source_projects="$(cd deploy/projects && pwd)"

docker compose -f deploy/production/docker-compose.yml run --rm \
  -v "${source_projects}:/migration-source:ro" \
  arcreel bash -c '
    apt-get update &&
    apt-get install -y --no-install-recommends pgloader &&
    pgloader sqlite:///migration-source/.arcreel.db \
             "postgresql://arcreel:${POSTGRES_PASSWORD_URLENCODED:-$POSTGRES_PASSWORD}@postgres:5432/arcreel"
  '
```

:::danger

**不要对已有数据的目标重复执行。** pgloader 的 SQLite 默认选项包含 `include drop`：它会用 `CASCADE` 删除目标中与源数据库同名的表，再重建结构和导入数据。这不是“跳过现有表”。只对本流程刚初始化的空 `arcreel` 数据库执行一次。如果迁移失败，先确认目标没有需要保留的数据，重建空目标后再重试；不要在 ArcReel 已经向 PostgreSQL 写入数据后重跑。

:::

pgloader 会自动处理 SQLite 与 PostgreSQL 之间的常见类型和语法差异，并重置导入表的序列。

### 6. 验证数据 {#verify-data}

```bash
docker compose -f deploy/production/docker-compose.yml \
  exec postgres psql -U arcreel -d arcreel -c "
  SELECT 'tasks' AS tbl, COUNT(*) FROM tasks
  UNION ALL
  SELECT 'api_calls', COUNT(*) FROM api_calls
  UNION ALL
  SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
  UNION ALL
  SELECT 'api_keys', COUNT(*) FROM api_keys;
"
```

对比 SQLite 中的记录数：

```bash
sqlite3 deploy/projects/.arcreel.db "
  SELECT 'tasks', COUNT(*) FROM tasks
  UNION ALL
  SELECT 'api_calls', COUNT(*) FROM api_calls
  UNION ALL
  SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
  UNION ALL
  SELECT 'api_keys', COUNT(*) FROM api_keys;
"
```

### 7. 启动完整服务 {#start-all-services}

```bash
docker compose -f deploy/production/docker-compose.yml up -d
docker compose -f deploy/production/docker-compose.yml ps
curl -f http://localhost:1241/health
```

访问 `http://<你的IP>:1241` 验证服务正常。

---

## 回滚到 SQLite {#rollback-to-sqlite}

以上迁移流程不会改写原 `deploy/projects/` 和 `deploy/.env`，因此正常回滚应重新启动默认 Compose，而不是修改生产 `.env`：

1. 停止 PostgreSQL 生产部署：

   ```bash
   cd "$(git rev-parse --show-toplevel)"
   docker compose -f deploy/production/docker-compose.yml down
   ```

2. 确认 `deploy/projects/.arcreel.db` 和 `deploy/.env` 仍在。如果原目录被修改或损坏，先保留当前副本，再从第 2 步的 `arcreel-source-YYYYMMDD-HHMMSS.tar.gz` 恢复 `.env` 与整个 `projects/`；不要只覆盖主 SQLite 文件而留下不匹配的 `-wal` 或 `-shm` 文件。

3. 重新启动 SQLite 默认部署：

   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   docker compose -f deploy/docker-compose.yml ps
   curl -f http://localhost:1241/health
   ```

4. 登录后抽查项目、图片、视频和任务记录，并用第 6 步的 SQLite 查询复核记录数。

5. 在回滚验证完成前，保留 `deploy/production/pgdata/`、`deploy/production/projects/` 和迁移备份以便排查，不要删除。`POSTGRES_PASSWORD` 位于独立的 `deploy/production/.env`，无需从 `deploy/.env` 移除。

如果原来不是默认 Compose，还需将启动环境中的 `DATABASE_URL` 恢复为 SQLite URL 或取消设置，并确认 `ARCREEL_DATA_DIR` 指回原数据目录后再启动。
