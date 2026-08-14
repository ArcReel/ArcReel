---
id: migrate-to-postgres
title: Migrate from SQLite to PostgreSQL
sidebar_position: 2
---

# Migrate from SQLite to PostgreSQL {#migrate-to-postgres}

This guide is for ArcReel deployments currently using the default Docker + SQLite configuration that need to switch to the PostgreSQL deployment under `deploy/production/`. Run every command below from the root of the ArcReel repository.

Before migrating, distinguish the three types of data involved:

| Path | Purpose | Migration Action |
|---|---|---|
| `deploy/projects/.arcreel.db` | SQLite database used by the default deployment | Import into PostgreSQL with pgloader |
| Other files under `deploy/projects/` | Project metadata and media assets | Copy to `deploy/production/projects/` |
| `deploy/production/pgdata/` | PostgreSQL cluster data | Initialize with PostgreSQL; never place project or SQLite files here |

The commands below consistently use the shell variable `source_projects` for the source data root on the host. The default deployment sets it to the absolute path of `deploy/projects/`. If you changed the container data directory with `ARCREEL_DATA_DIR` and a custom mount, set `source_projects` in step 1 to the corresponding absolute host path. The container and host paths may differ, so this guide does not derive that value directly from `.env`. Keep this variable in the same shell throughout the migration.

## Prerequisites {#prerequisites}

- Docker and Docker Compose are installed
- The `sqlite3` command-line tool is installed; run `sqlite3 --version` to confirm
- ArcReel currently uses the default SQLite deployment, with the database at `deploy/projects/.arcreel.db`
- `deploy/production/pgdata/` and `deploy/production/projects/` do not contain production data that must be preserved

## Migration Steps {#migration-steps}

### 1. Stop the ArcReel services {#stop-services}

```bash
cd "$(git rev-parse --show-toplevel)"

source_projects="$(cd deploy/projects && pwd)"
# Custom data directory example: source_projects="/srv/arcreel/projects"

if [ ! -f "${source_projects}/.arcreel.db" ]; then
  echo "Error: ${source_projects}/.arcreel.db does not exist" >&2
  exit 1
fi

docker compose -f deploy/docker-compose.yml down
```

Do not restart the default deployment until migration verification is complete. Otherwise, the SQLite database and project assets may continue to receive writes.

### 2. Create a Consistent Backup {#backup-sqlite}

```bash
set -euo pipefail

backup_stamp="$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p deploy/backups
chmod 700 deploy/backups

sqlite3 "${source_projects}/.arcreel.db" \
  ".backup 'deploy/backups/arcreel-sqlite-${backup_stamp}.db'"

check_result="$(sqlite3 "deploy/backups/arcreel-sqlite-${backup_stamp}.db" \
  "PRAGMA quick_check;")"

if [ "${check_result}" != "ok" ]; then
  echo "Error: SQLite backup quick_check failed" >&2
  exit 1
fi

tar -czf "deploy/backups/arcreel-source-${backup_stamp}.tar.gz" \
  -C "${source_projects}" .

cp deploy/.env "deploy/backups/arcreel-source-${backup_stamp}.env"
```

The guard accepts only an exact `ok` response from `PRAGMA quick_check;`. Any backup, check, archive, or configuration-copy failure stops the migration immediately. `sqlite3 .backup` uses the SQLite backup API to create a consistent snapshot that includes committed WAL content. Do not copy only `.arcreel.db` with `cp` while the service is running. `.arcreel.db-wal` may contain committed transactions that have not yet been checkpointed, so separating it from the main file can lose data or corrupt the backup. The paired tar archive stores the complete contents of `source_projects`, while the adjacent `.env` copy stores the default deployment configuration. Restore only artifacts with the same timestamp. `umask 077` and directory mode `0700` restrict access to the credentials and project assets they contain.

### 3. Prepare the PostgreSQL Deployment {#configure-env}

Create the production configuration:

```bash
if [ -e deploy/production/.env ]; then
  echo "Error: deploy/production/.env already exists; preserve and review it instead of overwriting it" >&2
  exit 1
fi

cp deploy/production/.env.example deploy/production/.env
```

If a production configuration already exists, the guard above prints an error and stops the current shell. Review and preserve its valid settings instead of overwriting it.

Edit `deploy/production/.env` and set the authentication values and PostgreSQL password:

```env
AUTH_USERNAME=admin
AUTH_PASSWORD=set a strong password
AUTH_TOKEN_SECRET=set a long-lived random secret
POSTGRES_PASSWORD=set a database password containing only letters and numbers
```

Production Compose assembles `DATABASE_URL` automatically. The migration command also embeds the password in pgloader's PostgreSQL URI, so use `openssl rand -hex 16` to generate a URL-safe password. If special characters are required, follow the [deployment guide](./deployment.md#postgresql-start) to store the raw password separately from the percent-encoded URI password. Never use the encoded value itself as `POSTGRES_PASSWORD`. The pgloader command below prefers `POSTGRES_PASSWORD_URLENCODED` when it is present.

Run the directory guard. It produces no output when the target directories are absent or empty. If it finds any entry, including a hidden file, it prints an error and stops the current shell:

```bash
for target_dir in deploy/production/projects deploy/production/pgdata; do
  if [ -e "${target_dir}" ] && [ ! -d "${target_dir}" ]; then
    echo "Error: ${target_dir} exists and is not a directory; stopping migration" >&2
    exit 1
  fi

  if [ -d "${target_dir}" ]; then
    first_entry="$(find "${target_dir}" -mindepth 1 -maxdepth 1 -print -quit)" || exit 1
    if [ -n "${first_entry}" ]; then
      echo "Error: ${target_dir} is not empty; stopping migration without overwriting it" >&2
      exit 1
    fi
  fi
done
```

Copy project and media assets to the production directory without copying the SQLite database:

```bash
set -euo pipefail

mkdir -p deploy/production/projects
tar -C "${source_projects}" --exclude='.arcreel.db*' -cf - . | \
  tar -C deploy/production/projects -xf -
```

Strict mode stops immediately if directory creation or either side of the pipeline fails, preventing the migration from using an incomplete asset copy.

### 4. Start PostgreSQL {#start-postgresql}

Start only the database service first:

```bash
docker compose -f deploy/production/docker-compose.yml up -d postgres
```

Wait for the health check to pass:

```bash
docker compose -f deploy/production/docker-compose.yml ps
```

### 5. Migrate the data {#migrate-data}

Use pgloader inside the ArcReel container to migrate the original SQLite database directly to PostgreSQL:

```bash
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

**Do not run this command repeatedly against a target that contains data.** pgloader's default SQLite options include `include drop`: it uses `CASCADE` to drop target tables whose names match the source database, then recreates the schema and imports the data. It does not skip existing tables. Run it once, only against the empty `arcreel` database initialized by this procedure. If migration fails, first verify that the target contains no data you need to preserve, recreate an empty target, and then retry. Never rerun pgloader after ArcReel has started writing to PostgreSQL.

:::

pgloader handles common type and syntax differences between SQLite and PostgreSQL and resets the sequences for imported tables.

### 6. Verify the data {#verify-data}

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

Compare the record counts in SQLite:

```bash
sqlite3 "${source_projects}/.arcreel.db" "
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
docker compose -f deploy/production/docker-compose.yml up -d
docker compose -f deploy/production/docker-compose.yml ps
curl -f http://localhost:1241/health
```

Visit `http://<your-ip>:1241` and verify that the service is working.

---

## Roll Back to SQLite {#rollback-to-sqlite}

The migration procedure above does not modify the source data directory referenced by `source_projects` or `deploy/.env`. A normal rollback therefore restarts the original SQLite deployment instead of changing the production `.env`. If you roll back from a new shell, first set `source_projects` again as described in step 1:

1. Stop the PostgreSQL production deployment:

   ```bash
   cd "$(git rev-parse --show-toplevel)"
   docker compose -f deploy/production/docker-compose.yml down
   ```

2. Confirm that `${source_projects}/.arcreel.db` and `deploy/.env` still exist. If the source directory was changed or damaged, first select the two backup files from step 2 that have the same timestamp, then run:

   ```bash
   set -euo pipefail

   archive="deploy/backups/arcreel-source-YYYYMMDD-HHMMSS.tar.gz"
   env_backup="deploy/backups/arcreel-source-YYYYMMDD-HHMMSS.env"
   preserved="${source_projects}.before-rollback-$(date +%Y%m%d-%H%M%S)"

   mv -- "${source_projects}" "${preserved}"
   mkdir -p "${source_projects}"
   tar -xzf "${archive}" -C "${source_projects}"
   cp "${env_backup}" deploy/.env
   ```

   This moves the old source directory out of the way before creating an empty directory and extracting the archive, so files absent from the archive cannot remain. It restores `.env` only after extraction succeeds. Do not overwrite only the main SQLite file while leaving mismatched `-wal` or `-shm` files behind. Keep `${preserved}` until rollback verification is complete.

3. Restart the default SQLite deployment:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   docker compose -f deploy/docker-compose.yml ps
   curl -f http://localhost:1241/health
   ```

4. Sign in and inspect several projects, images, videos, and task records. Run the SQLite query from step 6 again to verify record counts.

5. Keep `deploy/production/pgdata/`, `deploy/production/projects/`, and the migration backups until rollback verification is complete. Do not delete them. `POSTGRES_PASSWORD` is stored in the separate `deploy/production/.env` and does not need to be removed from `deploy/.env`.

If the original deployment used custom Compose configuration or a custom data mount, also restore or unset `DATABASE_URL` so it selects SQLite, confirm that `ARCREEL_DATA_DIR` still points to the original directory inside the container, and verify that the mount maps it to `${source_projects}` on the host before restarting ArcReel.
