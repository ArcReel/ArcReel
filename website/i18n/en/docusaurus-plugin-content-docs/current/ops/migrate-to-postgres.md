---
id: migrate-to-postgres
title: Migrate from SQLite to PostgreSQL
sidebar_position: 2
---

# Migrate from SQLite to PostgreSQL {#migrate-to-postgres}

This guide is for ArcReel deployments currently using the default SQLite configuration that need to switch to PostgreSQL.

## Prerequisites {#prerequisites}

- Docker and Docker Compose are installed
- ArcReel is currently running with SQLite (the database file is located at `projects/.arcreel.db`)

## Migration Steps {#migration-steps}

### 1. Stop the ArcReel services {#stop-services}

```bash
# 如果通过 Docker 运行
docker compose down

# 如果通过命令行直接运行，停止 uvicorn 进程
```

### 2. Back up the SQLite database {#backup-sqlite}

```bash
cp projects/.arcreel.db projects/.arcreel.db.bak
```

### 3. Configure environment variables {#configure-env}

Add the following variable to `.env` (used to initialize the PostgreSQL container in docker-compose):

```env
POSTGRES_PASSWORD=你的数据库密码
```

> You do not need to set `DATABASE_URL` manually. It is assembled automatically in `docker-compose.yml` from `POSTGRES_PASSWORD`.

### 4. Start PostgreSQL {#start-postgresql}

Start only the database service first:

```bash
docker compose up -d postgres
```

Wait for the health check to pass:

```bash
docker compose ps  # 确认 postgres 状态为 healthy
```

### 5. Migrate the data {#migrate-data}

Use pgloader inside the ArcReel container to migrate the SQLite data directly to PostgreSQL:

```bash
docker compose run --rm arcreel bash -c "
  apt-get update && apt-get install -y --no-install-recommends pgloader &&
  pgloader sqlite:///app/projects/.arcreel.db \
           postgresql://arcreel:\${POSTGRES_PASSWORD}@postgres:5432/arcreel
"
```

> pgloader automatically handles type and syntax differences between SQLite and PostgreSQL (Booleans, time formats, and so on),
> and skips existing table structures, importing only the data.

### 6. Verify the data {#verify-data}

```bash
docker compose exec postgres psql -U arcreel -d arcreel -c "
  SELECT 'tasks' AS tbl, COUNT(*) FROM tasks
  UNION ALL
  SELECT 'api_calls', COUNT(*) FROM api_calls
  UNION ALL
  SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
  UNION ALL
  SELECT 'api_keys', COUNT(*) FROM api_keys;
"
```

Compare the record counts in SQLite:

```bash
sqlite3 projects/.arcreel.db "
  SELECT 'tasks', COUNT(*) FROM tasks
  UNION ALL
  SELECT 'api_calls', COUNT(*) FROM api_calls
  UNION ALL
  SELECT 'agent_sessions', COUNT(*) FROM agent_sessions
  UNION ALL
  SELECT 'api_keys', COUNT(*) FROM api_keys;
"
```

### 7. Start all services {#start-all-services}

```bash
docker compose up -d
```

Visit `http://<你的IP>:1241` and verify that the service is working.

---

## Roll Back to SQLite {#rollback-to-sqlite}

If you need to roll back:

1. Stop the services: `docker compose down`
2. Restore the backup: `cp projects/.arcreel.db.bak projects/.arcreel.db`
3. From `.env`, remove `POSTGRES_PASSWORD`, then start without the PostgreSQL configuration in `docker-compose.yml`
