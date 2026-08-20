---
id: migrate-to-mysql
title: 从 SQLite / PostgreSQL 迁移到 MySQL
sidebar_position: 3
---

# 从 SQLite / PostgreSQL 迁移到 MySQL {#migrate-to-mysql}

本文档适用于已使用 SQLite 或 PostgreSQL 部署 ArcReel、希望切换到 **MySQL 8.0+** 的场景。
如果是全新部署，可直接跳到 [3. 环境变量配置](#configure-env) 并参考 [部署与运维](./deployment.md#mysql-deployment)。

三方言支持的架构决策、字段长度、去重生成列等设计细节见 [ADR-0061](https://github.com/ArcReel/ArcReel/blob/main/docs/adr/0061-three-dialect-database-support.md)。

:::note 版本要求
MySQL **8.0** 及以上（含 8.4 LTS）。
MySQL 5.7 与 MariaDB 未经 CI 覆盖，且缺少 `caching_sha2_password` 与持久化生成列的部分兼容性，不建议使用。
字符集要求 **`utf8mb4`**，排序规则建议 `utf8mb4_unicode_ci`。
:::

## 前置条件 {#prerequisites}

迁移路径不同，前置条件略有差异：

| 场景 | 需要 |
|------|------|
| 从 SQLite 迁移到 MySQL（同机） | Docker 或 `mysqldump` + `sqlite3` 命令行 |
| 从 PostgreSQL 迁移到 MySQL（同机） | Docker 或 `pg_dump` + `mysql` 命令行 |
| 使用已有 MySQL 集群 | 需要一个可建库、建表、建索引的账号，并在创建库时显式 `CHARACTER SET utf8mb4` |

> 迁移前 **必须先** 将 ArcReel 升级到最新版本，保证 `migrate-to-mysql` 指南对应的迁移脚本 (`alembic/versions/7a8b9c0d1e2f` 及以后) 已在代码中。如果使用官方 Docker 镜像，只要拉取的是 `ghcr.io/arcreel/arcreel:latest` 即可。

## 迁移步骤（从 SQLite → MySQL） {#sqlite-to-mysql}

### 1. 停止服务 {#stop-services}

```bash
# 如果通过 Docker 运行
docker compose down

# 如果通过命令行直接运行，停止 uvicorn 进程
```

### 2. 备份 SQLite 数据库 {#backup-sqlite}

```bash
cp projects/.arcreel.db projects/.arcreel.db.bak
```

### 3. 准备 MySQL 实例 {#prepare-mysql}

#### 方式 A：使用 ArcReel 自带的 `docker-compose.mysql.yml`

```bash
cd deploy/production
cp ../../.env.example .env  # 如已有可跳过
```

在 `.env` 中新增 MySQL 相关变量：

```dotenv
# MySQL 根密码（仅用于容器启动初始化）
MYSQL_ROOT_PASSWORD=请设置强密码
# ArcReel 专用账号密码
MYSQL_PASSWORD=请设置强密码
# 时区
TZ=Asia/Shanghai
```

只启动 MySQL 容器，等待健康检查通过：

```bash
docker compose -f docker-compose.mysql.yml up -d mysql
docker compose -f docker-compose.mysql.yml ps   # 确认 mysql 状态为 healthy
```

#### 方式 B：使用企业已部署的 MySQL 集群

在已有的 MySQL 上手动创建数据库：

```sql
CREATE DATABASE arcreel
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 创建专用账号（可选，推荐）
CREATE USER 'arcreel'@'%' IDENTIFIED BY '请设置强密码';
GRANT ALL PRIVILEGES ON arcreel.* TO 'arcreel'@'%';
FLUSH PRIVILEGES;
```

然后在 `.env` 中设置 `DATABASE_URL`：

```dotenv
DATABASE_URL=mysql+aiomysql://arcreel:请设置强密码@mysql-host:3306/arcreel?charset=utf8mb4
```

> 密码中的特殊字符（`%`、`@`、`:`、`/`、`?`、`#`）需要做 URL 百分号编码，例如：
> `abc%def@ghi` → `abc%25def%40ghi`。

### 4. 先在空库上跑 alembic 建表 {#build-schema-first}

迁移工具（pgloader / etl）直接导入数据时经常误把 MySQL 的表类型、字符集一并沿用源库的设置，无法生成 ArcReel 需要的去重生成列（`active_dedupe_key` / `dedupe_uuid` 等）。所以必须**先让 Alembic 在新库上跑完 schema**，再导入数据。

```bash
# 如果方式 A：在 arcreel 容器内执行（此时 mysql 已 healthy）
docker compose -f docker-compose.mysql.yml run --rm arcreel \
  uv run alembic upgrade head

# 如果方式 B：在源码目录下设置好 DATABASE_URL 执行
DATABASE_URL=mysql+aiomysql://... uv run alembic upgrade head
```

**校验 alembic 成功**：`alembic current` 输出 `head`，且 MySQL 中可以看到 `alembic_version` 表。

### 5. 迁移数据 {#migrate-sqlite-data}

推荐 `pgloader`，它会自动处理类型差异、跳过已存在的表结构、只导入数据。

```bash
# 在 arcreel 容器中一次性安装并执行
docker compose -f docker-compose.mysql.yml run --rm arcreel bash -c "
  apt-get update && apt-get install -y --no-install-recommends pgloader &&
  pgloader sqlite:///app/projects/.arcreel.db \
           mysql://arcreel:${MYSQL_PASSWORD}@mysql:3306/arcreel
"
```

#### 备选：无 pgloader 时用 Python 导

把以下脚本保存为 `_migrate_sqlite_to_mysql.py`，然后 `uv run python _migrate_sqlite_to_mysql.py`：

```python
"""一次性脚本：SQLite → MySQL 数据迁移（已存在表结构时只导数据）。
使用前在环境中设置 DATABASE_URL 指向目标 MySQL，并将源 SQLite 作为
SOURCE_DB_URL 传入。"""

from __future__ import annotations

import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

# 与 head schema 对齐（models 实际注册的全部业务表）；task_events 已在早期迁移删除
TABLES = [
    "users",
    "api_keys",
    "provider_config",
    "provider_credential",
    "provider_model",
    "custom_provider",
    "custom_provider_model",
    "agent_credentials",
    "agent_anthropic_credentials",
    "assets",
    "system_setting",
    "tasks",
    "worker_lease",
    "api_calls",
    "agent_sessions",
    "agent_session_entries",
    "agent_session_summaries",
    "agent_session_event_log",
    "agent_session_user_message_links",
]


async def main() -> int:
    src = create_async_engine(os.environ["SOURCE_DB_URL"])  # sqlite+aiosqlite:///...
    dst = create_async_engine(os.environ["DATABASE_URL"])  # mysql+aiomysql://...
    async with src.begin() as sconn, dst.begin() as dconn:
        for tbl_name in TABLES:

            def read(sync_conn, name=tbl_name):
                md = sa.MetaData()
                md.reflect(sync_conn, only=[name])
                t = md.tables[name]
                return list(sync_conn.execute(sa.select(t)))

            rows = await sconn.run_sync(read)
            if not rows:
                print(f"[SKIP] {tbl_name}: 0 rows")
                continue

            def write(sync_conn, rs=rows, name=tbl_name):
                md = sa.MetaData()
                md.reflect(sync_conn, only=[name])
                t = md.tables[name]
                sync_conn.execute(sa.insert(t).values(rs))

            await dconn.run_sync(write)
            print(f"[OK]   {tbl_name}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

### 6. 校验数据 {#verify-data}

```bash
docker compose -f docker-compose.mysql.yml exec mysql \
  mysql -uarcreel -p${MYSQL_PASSWORD} -Darcreel -e "
    SELECT 'tasks' AS tbl, COUNT(*) FROM tasks
    UNION ALL SELECT 'api_calls', COUNT(*) FROM api_calls
    UNION ALL SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
    UNION ALL SELECT 'api_keys', COUNT(*) FROM api_keys
    UNION ALL SELECT 'provider_credential', COUNT(*) FROM provider_credential;
  "
```

与 SQLite 记录数对比：

```bash
sqlite3 projects/.arcreel.db "
  SELECT 'tasks', COUNT(*) FROM tasks
  UNION ALL SELECT 'api_calls', COUNT(*) FROM api_calls
  UNION ALL SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
  UNION ALL SELECT 'api_keys', COUNT(*) FROM api_keys
  UNION ALL SELECT 'provider_credential', COUNT(*) FROM provider_credential;
"
```

**完全一致后再启动 ArcReel**。不一致时先不要启动，回滚到 SQLite 备份，检查迁移工具输出。

### 7. 启动完整服务 {#start-all}

```bash
docker compose -f docker-compose.mysql.yml up -d
```

访问 `http://<你的IP>:1241` 验证服务正常。

---

## 迁移步骤（从 PostgreSQL → MySQL） {#postgres-to-mysql}

PostgreSQL 与 MySQL 在大型对象（`tasks.payload_json` 等）、Boolean（PG `BOOLEAN` vs MySQL `TINYINT(1)`）、时间戳精度（PG microsecond vs MySQL fractional-seconds）上都有差异。**最稳妥的路径是通过 Alembic 在 MySQL 端先建表结构，再用业务表逐一导出/导入。**

### 1. 停止服务、备份源库

```bash
docker compose down
pg_dump -U arcreel -d arcreel --format=c -f /tmp/arcreel_pg.dump
```

### 2. 按上面 [3. 准备 MySQL 实例](#prepare-mysql) + [4. 先在空库上跑 alembic 建表](#build-schema-first) 完成新库准备

### 3. 用 `pgloader` 做异构迁移

`pgloader` 原生支持 PostgreSQL → MySQL，且会自动做 BOOLEAN / UUID / TIMESTAMP 类型转换：

```bash
docker compose -f docker-compose.mysql.yml run --rm arcreel bash -c "
  apt-get update && apt-get install -y --no-install-recommends pgloader &&
  pgloader postgresql://arcreel:${PG_PASSWORD}@pg-host:5432/arcreel \
           mysql://arcreel:${MYSQL_PASSWORD}@mysql:3306/arcreel
"
```

### 4. 校验并启动（同 SQLite 路径的 [6](#verify-data)、[7](#start-all)）

---

## 回滚 {#rollback}

迁移失败需要回滚时：
1. **停止服务** `docker compose down`（或停止 uvicorn 进程）。
2. **恢复源库备份**：SQLite 用 `cp projects/.arcreel.db.bak projects/.arcreel.db`；PostgreSQL 用 `pg_restore`。
3. **移除新的数据库相关环境变量**：删除 `.env` 中的 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`，并将 `DATABASE_URL` 改回原来的 SQLite/PG 配置（或删除 `DATABASE_URL` 以恢复默认 SQLite）。
4. 用原来的 `docker-compose.yml` 或命令行重新启动。

---

## 幂等性验证（开发/CI 开发者必看） {#idempotency}

新增迁移脚本或修改历史迁移后，在真正的 MySQL 实例上至少跑一轮以下操作：

```bash
# 1. 全新升级到最新版
DATABASE_URL=mysql+aiomysql://user:pass@host:3306/testdb uv run alembic upgrade head

# 2. 回退一个版本（验证 downgrade 可执行）
uv run alembic downgrade -1

# 3. 再次升级到最新版（验证幂等性：不会出现引号累加、主键 nullable 漂移等错误）
uv run alembic upgrade head
```

幂等性失败的典型表现（都属于迁移脚本 bug，必须修）：

| 症状 | 根因 | 修复方式 |
|------|------|----------|
| `currency` 默认值从 `'USD'` 变成 `'''USD'''` 再变成 `'''''USD'''''` | `existing_server_default` 传裸字符串，SQLAlchemy 每轮再加一层引号 | 反射默认值用 `sa.text(raw_default)` 包装 |
| 第二次 upgrade 报 1171「PRIMARY KEY must be NOT NULL」 | `alter_column(type_=...)` 没传 `existing_nullable`，MySQL MODIFY COLUMN 默认 nullable=True | 每次改 type 都从 `sa.inspect().get_columns()` 反射 nullable 与 server_default 传入 |
| 「All MySQL CHANGE/MODIFY COLUMN operations require the existing type」 | `alter_column` 只改 nullable 没给 `existing_type` | 每个 alter_column 显式给出 existing_type |

---

## 常见问题 {#faq}

### Q1. 连接 MySQL 报 `OperationalError: (2003, "Can't connect to MySQL server")`

- 检查 `DATABASE_URL` 的 host、port 是否可达；
- 企业防火墙或云安全组是否放行 3306；
- 如果是 localhost 连接，URL 里写 `127.0.0.1` 而不是 `localhost`（`localhost` 在 MySQL 语义里走 UNIX socket，而 `aiomysql` 默认走 TCP）。

### Q2. `sqlalchemy.exc.ProgrammingError: (1064, You have an error in your SQL syntax ... 'key' IN (...) at line 1)`

`key` 是 MySQL 保留字。出现这个报错说明有迁移脚本或自定义 SQL 里写了裸 `key`。

- 仓库内迁移脚本：用 `lib.db.migration_compat.qident("key")` 获取正确的标识符引号（MySQL 反引号，其他方言不加）。
- 业务层 ORM：SQLAlchemy 会自动处理，不用手改。

### Q3. `Specified key was too long; max key length is 3072 bytes`

InnoDB + `utf8mb4` 单个字符 4 字节，3072/4=768 字符上限。拼接型去重生成列（如 `active_dedupe_key`）在 ArcReel 中存的是拼接键的 **SHA-256 hex**（恒 64 字符），不受源列长度影响。
如果你自定义了索引，请对超长拼接键改存哈希或限制参与列长度。

### Q4. `pool_recycle` 为什么是 300s？可以改吗？

MySQL 默认 `wait_timeout=28800`（8 小时）但企业部署常见主动断连 5 分钟或 10 分钟。
`pool_recycle=300` 是保守值，保证连接池中的连接最多使用 5 分钟就主动回收。如果你确认连接寿命更长，可以在 `.env` 自定义 `DATABASE_URL` 的 engine 参数，但不建议改小（会增加建连开销）。
