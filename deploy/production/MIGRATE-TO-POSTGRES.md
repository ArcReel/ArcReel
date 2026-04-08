# Migrating from SQLite to PostgreSQL

This document applies to users who have deployed ArcReel with the default SQLite setup and wish to switch to PostgreSQL.

## Prerequisites

- Docker and Docker Compose installed
- ArcReel currently running with SQLite (database file at `projects/.arcreel.db`)

## Migration Steps

### 1. Stop ArcReel Services

```bash
# If running via Docker
docker compose down

# If running directly from the command line, stop the uvicorn process
```

### 2. Back Up the SQLite Database

```bash
cp projects/.arcreel.db projects/.arcreel.db.bak
```

### 3. Configure Environment Variables

Add the following variable to `.env` (used for PostgreSQL container initialization in docker-compose):

```env
POSTGRES_PASSWORD=your_database_password
```

> `DATABASE_URL` does not need to be set manually — it is automatically assembled from `POSTGRES_PASSWORD` in `docker-compose.yml`.

### 4. Start PostgreSQL

Start only the database service first:

```bash
docker compose up -d postgres
```

Wait for the health check to pass:

```bash
docker compose ps  # confirm postgres status is healthy
```

### 5. Migrate Data

Use pgloader inside the ArcReel container to migrate data directly from SQLite to PostgreSQL:

```bash
docker compose run --rm arcreel bash -c "
  apt-get update && apt-get install -y --no-install-recommends pgloader &&
  pgloader sqlite:///app/projects/.arcreel.db \
           postgresql://arcreel:\${POSTGRES_PASSWORD}@postgres:5432/arcreel
"
```

> pgloader automatically handles type and syntax differences between SQLite and PostgreSQL (booleans, timestamp formats, etc.)
> and skips existing table schemas, importing only the data.

### 6. Verify Data

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

Compare against the SQLite record counts:

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

### 7. Start All Services

```bash
docker compose up -d
```

Visit `http://<your-IP>:1241` to verify the service is running correctly.

---

## Rolling Back to SQLite

If you need to revert:

1. Stop services: `docker compose down`
2. Restore the backup: `cp projects/.arcreel.db.bak projects/.arcreel.db`
3. Remove `POSTGRES_PASSWORD` from `.env` and start without the PostgreSQL configuration in `docker-compose.yml`
