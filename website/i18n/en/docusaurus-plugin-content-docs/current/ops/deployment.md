---
id: deployment
title: Deployment and Operations
sidebar_position: 1
---

# Deployment and Operations {#deployment}

This document covers ArcReel's default deployment, PostgreSQL production deployment, environment variables, data persistence, upgrades, backups, restoration, reverse proxies, and troubleshooting. See the [Security Policy](https://github.com/ArcReel/ArcReel/blob/main/SECURITY.md) for the official support boundaries and the [Security Threat Model](https://github.com/ArcReel/ArcReel/blob/main/docs/security/threat-model.md) for the complete trust boundaries.

## Choosing a Deployment Mode {#choose-deployment-mode}

| Scenario | Recommended Method | Database | Notes |
|---|---|---|---|
| First-time evaluation or light personal use | `deploy/` | SQLite | Minimal configuration and the fastest startup |
| Long-running, concurrent, or production service | `deploy/production/` | PostgreSQL | Better suited to concurrency, backups, and operations, but provides no user isolation |
| Local development | Run from source | SQLite or PostgreSQL | See the [Contributing Guide](../dev/contributing.md) |

Regardless of the method you choose, project images, videos, and other generated assets must be stored persistently.

ArcReel is currently designed for a single trusted operator and does not support sharing an instance among mutually untrusted users. A PostgreSQL production deployment does not add tenant isolation, role-based permissions, or per-user project authorization.

## 1. Default Deployment: SQLite {#sqlite-deployment}

### 1.1 Start {#sqlite-start}

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy

cp .env.example .env
```

Edit `.env`:

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=set a strong password
AUTH_TOKEN_SECRET=set a long-lived random secret
# LOG_LEVEL=INFO
```

Generate a random secret:

```bash
openssl rand -hex 32
```

Start the service:

```bash
docker compose up -d
```

Verify it:

```bash
docker compose ps
docker compose logs --tail=100 arcreel
curl http://localhost:1241/health
```

### 1.2 Persistent Directories {#sqlite-volumes}

The default Compose configuration mounts:

| Host Path | Container Path | Contents |
|---|---|---|
| `deploy/.env` | `/app/.env` | Authentication and runtime configuration |
| `deploy/projects/` | `/app/projects` | Data root: projects, generated assets, the default SQLite database, logs, Google Vertex AI credentials, and all other runtime data |
| `deploy/claude_data/` | `/root/.claude` | Agent runtime data |

`deploy/projects/` is ArcReel's data root (`ARCREEL_DATA_DIR`, `/app/projects` inside the container). Projects live in its `projects/` subdirectory, and all other runtime data sits alongside it:

```text
deploy/projects/               data root
├── projects/<project-name>/   projects and generated assets
├── global_assets/             global asset library
├── users/<user_id>/memory/    Agent user memory
├── arcreel.db                 default SQLite database (with -wal / -shm)
├── logs/                      application logs
├── vertex_keys/               Vertex AI credential files
├── trial_runs/                output of endpoint "Test connection" runs
└── runtime/                   migration markers, generation admission locks, and other runtime state
```

Only directories under `projects/` whose names contain letters, digits, or hyphens and that contain a `project.json` file count as projects. No other entry in the data root appears in the project list. The diagnostic logs downloaded from Settings → About list the actual locations of the data root and each kind of data above.

> Back up the entire data root, not just the database. The database stores tasks, configuration, and index information, while the project directories store source media and generated files. They must remain consistent.

## 2. Production Deployment: PostgreSQL {#postgresql-deployment}

### 2.1 Start {#postgresql-start}

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
cp .env.example .env
```

Edit `.env`:

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=set a strong password
AUTH_TOKEN_SECRET=set a long-lived random secret
POSTGRES_PASSWORD=set a database password
# LOG_LEVEL=INFO
```

Generate a password containing only hexadecimal characters where possible:

```bash
openssl rand -hex 16
```

The default Compose configuration passes the raw `POSTGRES_PASSWORD` to PostgreSQL and also interpolates it into the password segment of `DATABASE_URL`. If the password contains URL-reserved characters such as `@`, `:`, `/`, `?`, `#`, or `%`, the password in the connection URI must be percent-encoded. Do not put the encoded value directly in `POSTGRES_PASSWORD`: PostgreSQL needs the raw password, while only the URI needs the encoded form.

When special characters are required, keep the raw and encoded values separate in `.env`:

```dotenv
POSTGRES_PASSWORD='p@ss/word'
POSTGRES_PASSWORD_URLENCODED=p%40ss%2Fword
```

Then change only the password segment of `DATABASE_URL` in `deploy/production/docker-compose.yml` to `${POSTGRES_PASSWORD_URLENCODED}`; leave the PostgreSQL container's `POSTGRES_PASSWORD` unchanged. You can generate the encoded value with `urllib.parse.quote(raw_password, safe="")`. If you do not want to maintain this Compose customization, use the hexadecimal password described above.

Start the service:

```bash
docker compose up -d
```

Verify it:

```bash
docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=100 arcreel
curl http://localhost:1241/health
```

### 2.2 PostgreSQL Persistent Directories {#postgresql-volumes}

| Host Path | Contents |
|---|---|
| `deploy/production/pgdata/` | PostgreSQL data directory |
| `deploy/production/projects/` | Data root: projects, media assets, logs, Vertex AI credentials, and so on; same layout as the [default deployment](#sqlite-volumes), without the database |
| `deploy/production/claude_data/` | Agent runtime data |
| `deploy/production/.env` | Authentication and database configuration |

`pgdata/` stores only the PostgreSQL cluster, while `projects/` is the data root and stores project metadata, media assets, and the remaining runtime data. Both directories must be persisted and backed up together. The production deployment uses PostgreSQL through `DATABASE_URL` and does not use `deploy/production/projects/arcreel.db`. Do not copy SQLite files into `pgdata/`, and do not treat these two directories as interchangeable database backups.

### 2.3 Database Migrations {#database-migrations}

ArcReel runs Alembic migrations at application startup to upgrade the database schema to the current version.

At startup, ArcReel also converts historical call output paths to project-relative paths when it can identify the project root, so usage detail thumbnails can survive a data directory move. Ambiguous old paths remain unchanged and are logged. Queued legacy text tasks use the current data directory; any old directory in their payload is ignored.

You must still create a backup before upgrading. Automatic migration handles schema upgrades; it does not replace a rollback-capable data backup.

## 3. Environment Variables {#environment-variables}

The default deployment examples currently include these core variables:

| Variable | Default | Recommendation |
|---|---|---|
| `AUTH_USERNAME` | `admin` | Change the administrator username if needed |
| `AUTH_PASSWORD` | Empty | Explicitly set a strong password for production deployments |
| `AUTH_TOKEN_SECRET` | Empty | Set a fixed, long-lived random value for production deployments |
| `AUTH_ENABLED` | `true` | Never disable for remote deployments; `false` leaves every management endpoint unauthenticated |
| `LOG_LEVEL` | `INFO` | Temporarily change to `DEBUG` while troubleshooting, then restore it |
| `POSTGRES_PASSWORD` | None | Required and must be set only for production deployments |
| `TZ` | `Asia/Shanghai` | Can be overridden in the Compose environment |
| `DATABASE_URL` | Default SQLite path | Production Compose sets the PostgreSQL URL automatically |
| `ARCREEL_DATA_DIR` | `projects` | Data root; projects, the default SQLite database, logs, and Vertex credentials all live under it |
| `CORS_ORIGINS` | Wildcard | When narrowed to an allowlist, browser MCP client origins must be listed too |
| `MCP_PUBLIC_URL` | `http://localhost:1241/mcp` | Optional; only OAuth discovery-based MCP clients need it |

Notes:

- Changing `AUTH_TOKEN_SECRET` invalidates existing login tokens.
- `AUTH_ENABLED=false` is only for a local machine protected by its own network boundary. Compose publishes port `1241` on all host interfaces by default, so never disable authentication for remote deployments. While authentication is off, startup logs a WARNING.
- `.env` may contain secrets. Do not commit it to version control.
- Vertex credential files should be readable only by the user who runs ArcReel.
- File logs are always written to `logs/` under the data root. The old `ARCREEL_LOG_DIR` variable no longer takes effect; if it is still set, startup logs a one-time notice. Set `ARCREEL_LOG_FILE_DISABLED=true` if you only want logs on stdout.
- Third-party model API keys are normally managed on the ArcReel Settings page. Do not include them in public documentation.

The remote MCP endpoint is `/mcp` and always requires an API Key with an `arc-` prefix; it never permits anonymous access, even when `AUTH_ENABLED=false`. Remote access needs no extra `MCP_*` configuration: paste the endpoint URL and API Key shown in the Settings page's "External agent access" dialog into your client, and connect through an HTTPS reverse proxy, VPN, or secure tunnel that preserves long-lived SSE connections.

The server does not validate the request `Host` header; the endpoint boundary rests on the API Key enforced on every request. Host ownership belongs to the deployment: restrict `server_name` (Nginx) or the equivalent rule on your reverse proxy so only requests for the expected domain reach ArcReel.

Browser MCP clients need no configuration either while `CORS_ORIGINS` stays at its permissive default; once you narrow it to an allowlist, add the client Origin to it as well. That single allowlist governs both the application API and the MCP endpoint; there is no second MCP-specific list. `MCP_PUBLIC_URL` only fills the OAuth protected-resource metadata (RFC 9728) and the 401 challenge that discovery-based clients read; clients that connect with a Bearer token, such as Claude Code and codex, never use it and can leave it unset.

ArcReel's sandbox requires provider secrets to be absent from the parent process environment. If any of the following credential environment variables has a non-empty value, the service refuses to start and prompts you to move the credential to the Web UI Settings page:

- `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `DASHSCOPE_API_KEY` / `MINIMAX_API_KEY` / `AGNES_API_KEY` / `OPENAI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS` (upload Vertex credentials on the Settings page; they are stored in `vertex_keys/` under the data root)

Non-secret configuration such as `ANTHROPIC_BASE_URL` and model names does not independently cause startup to be rejected, but it is still best managed in the Web UI together with the corresponding credentials.

## 4. Health Checks and Logs {#health-and-logs}

### 4.1 Health Check {#health-check}

Compose uses:

```text
GET /health
```

To check manually:

```bash
curl -f http://localhost:1241/health
```

### 4.2 View Logs {#view-logs}

```bash
# Last 200 lines
docker compose logs --tail=200 arcreel

# Follow continuously
docker compose logs -f arcreel

# Production database logs
docker compose logs -f postgres
```

File logs are stored in `logs/` under the data root, which is `deploy/projects/logs/` in the default deployment.

Do not paste complete logs directly into a public issue. Remove the following before submitting them:

- API keys;
- Tokens;
- Credentials embedded in Base URLs;
- User input;
- Private information in local file paths.

## 5. Upgrades {#upgrade}

### 5.1 Before Upgrading {#before-upgrade}

1. Read the [CHANGELOG](https://github.com/ArcReel/ArcReel/blob/main/CHANGELOG.md) and the target release notes;
2. Check for breaking changes;
3. Back up the data root and the database; see [Backup and Restore](#backup-and-restore);
4. Record the current image version;
5. Perform the upgrade during an acceptable maintenance window.

> ArcReel does not support downgrades. An upgrade may rewrite the data root layout, the database schema, and project structures, and older versions cannot read the upgraded data. To go back to an older version, stop the service and restore the backup taken before the upgrade.

### 5.2 Upgrade the Default Deployment {#upgrade-sqlite-deployment}

When upgrading from a version released before the data root layout change, run the commands below with your existing Compose file first, then switch to the single-volume Compose file as described in [Data Root Layout Migration](#data-root-layout-migration).

From `deploy/`:

```bash
# Back up first; see below
docker compose pull
docker compose up -d

docker compose ps
docker compose logs --tail=100 arcreel
curl -f http://localhost:1241/health
```

### 5.3 Upgrade the Production Deployment {#upgrade-postgresql-deployment}

When upgrading from a version released before the data root layout change, likewise keep your existing Compose file first, then switch to the single-volume Compose file as described in [Data Root Layout Migration](#data-root-layout-migration).

From `deploy/production/`:

```bash
# Back up the database and the data root projects/ first
docker compose pull
docker compose up -d

docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=200 arcreel
curl -f http://localhost:1241/health
```

The application runs database migrations when it starts. Do not skip multiple versions and upgrade directly without a backup.

### 5.4 Data Root Layout Migration {#data-root-layout-migration}

When you upgrade from an older version in which projects, the database, and other data sat side by side at the top of the data root, the first startup automatically reorganizes the data root into the layout shown in [Persistent Directories](#sqlite-volumes). The data root stays the same directory you already configured and mounted, so no environment variables need to change:

- Project directories move into `projects/` under the data root. Directories with valid names but no `project.json` move there unchanged as well, but are not shown as projects; directories named after system directories such as `logs`, `users`, and `runtime` are the exception and stay where they are. A project named `projects` keeps its name after the move.
- `_global_assets/` is renamed to `global_assets/`, `.arcreel/users/` moves to `users/`, and runtime state such as migration markers moves into `runtime/`.
- Credentials in `vertex_keys/` one level above the data root (the `./vertex_keys` volume in Docker) are merged into `vertex_keys/` under the data root: files referenced by credential records are saved by credential ID as `vertex_cred_<credential-id>.json`, and other `.json` files keep their names.
- Existing Agent sessions, global assets, Vertex credentials, usage records, and queued tasks are rewritten accordingly and keep working after the upgrade.

These steps run one by one, and every step is safe to rerun: if the container is killed or the process crashes, the next startup finishes the job. When all steps are done, a completion marker is written to `runtime/`, and later startups only check that marker. If a step fails, startup logs an ERROR and the service stops starting instead of running on a half-migrated layout; fix the cause shown in the log and restart to resume. For example, if `projects/` under the data root already contains a directory with the same name as a project being moved, the migration stops and you need to decide manually which copy to keep. When a file with the same name already exists at the new location of global assets or user memory, the migration does not stop: the old file stays in its old location and the startup log records a warning.

Two more items are not governed by the completion marker and are checked on every startup:

- The default SQLite database `.arcreel.db` (with `-wal` / `-shm`) is renamed to `arcreel.db`. This happens when the database address is resolved, so the original database is used whether the application or Alembic runs first. If both the old and new files exist, nothing is renamed, `arcreel.db` stays in use, and the startup log records a warning. When `DATABASE_URL` is set (including PostgreSQL), the database is left untouched.
- Files in the old log directory under the code directory (the `./logs` volume in Docker) are merged into `logs/` under the data root; files whose names already exist at the new location stay where they are. Errors in this step are only logged and do not block startup. A custom log directory set through `ARCREEL_LOG_DIR` is not moved.

**Back up the entire data root before upgrading**, together with the old log and credential directories outside it (the `./logs` and `./vertex_keys` volumes in Docker; otherwise `logs/` under the code directory and `vertex_keys/` one level above the data root). Back up an external database separately. The migration moves files from those two directories into the data root. Older versions treat `projects/` as a project and do not recognize the new database file name; downgrading requires restoring that backup.

The new Compose files mount a single data volume, `./projects`, plus `.env` and `claude_data/`; they no longer mount `./logs` or `./vertex_keys`. Files in the old volumes are merged into the data root only while those volumes are still mounted at startup, and Vertex credentials are moved only once, during the layout migration; after the completion marker is written they are no longer processed. A Docker deployment therefore upgrades from an older version in two phases (the examples use `deploy/`; for the production deployment use `deploy/production/`):

1. **Upgrade with your existing Compose file (no configuration changes).** Leave the Compose file as it is and run `docker compose pull` and `docker compose up -d` as described in [Upgrade the Default Deployment](#upgrade-sqlite-deployment) or [Upgrade the Production Deployment](#upgrade-postgresql-deployment). Both old volumes are still mounted, so the first startup merges their files into the data root. Confirm that the startup log contains the data root layout migration completion message (「数据根布局迁移完成」), and that the original log and credential files now appear under `deploy/projects/logs/` and `deploy/projects/vertex_keys/` (credential files may have been renamed to `vertex_cred_<credential-id>.json`).
2. **Then switch to the single-volume Compose file.** Remove the `./logs` and `./vertex_keys` volume lines from the Compose file (or switch to the new Compose file), then run `docker compose up -d` again. You can then delete `deploy/logs/` and `deploy/vertex_keys/`; if files remain in them (for example because a file with the same name already existed in the data root, or because the old volume was mounted read-only), first confirm that you no longer need them.

If you already switched to the new Compose file but have not started the new version yet, temporarily add the two volume lines back before the first startup, then follow the two phases above.

If you already switched to the new Compose file and started the new version, files in the old volumes are no longer merged automatically and must be copied by hand. The container wrote the old files as root, so prefix the commands with `sudo` if needed:

```bash
# Run in deploy/
docker compose stop arcreel

# Vertex credentials uploaded in Settings are saved as vertex_cred_<credential-id>.json, and ArcReel reads them by that file name, so keep the names unchanged
cp -p vertex_keys/vertex_cred_*.json projects/vertex_keys/
chmod 600 projects/vertex_keys/vertex_cred_*.json

# Put the old logs in a subdirectory of logs/ so they do not overwrite the arcreel.log already written by the new version
mkdir -p projects/logs/pre-upgrade
cp -Rp logs/. projects/logs/pre-upgrade/

docker compose start arcreel
```

After confirming that the Vertex credentials work in Settings, delete `deploy/logs/` and `deploy/vertex_keys/`. Credential files in the old directory whose names do not follow the `vertex_cred_<credential-id>.json` pattern are not read; upload those credentials again in Settings.

### 5.5 Project Schema Migrations {#project-schema-migrations}

In addition to database migrations, application startup upgrades each project in the `projects/` directory under the data root. Only directories whose names contain letters, digits, or hyphens and that contain a `project.json` file count as projects for migration and stale backup cleanup. When upgrading to a version that introduces artifact-state records, ArcReel fully validates the project and its formal scripts, writes the complete artifact records atomically, and updates the schema version in `project.json` only after those steps succeed.

Before committing a migration, ArcReel creates adjacent backups with a `.bak.v<source-version>-<timestamp>` suffix for:

- `project.json`;
- Formal script files registered in `project.json`;
- An existing `.arcreel_artifacts.json`;
- `versions/versions.json`, for migrations that rewrite version records.

Project migration is safe to retry. If a previous startup was interrupted while creating backups or committing changes, the next startup validates the project again and ensures that at least one backup exactly matches the pre-migration content before continuing. Identical backups are kept only once, so repeated failures do not pile up copies. These automatically generated project-level backups exist only for migration recovery; they do not replace deployment-level backups of the database and the entire data root.

After a migration completes, ArcReel writes `.migration_report.json` to the project directory. It records how many artifacts the migration registered and which ones it skipped and why (for example, legacy narration audio that has no synthesis settings to project from). It is informational only and never blocks any operation; the production status API exposes it unchanged in the `migration_report` field.

Videos generated before the artifact-record mechanism existed (whose legacy version records have no typed provenance fields) get their provenance backfilled by the migration from the project state at that time, so they display and preview normally after the upgrade. Videos that cannot be backfilled are listed in the migration report.

For drama projects whose `project.json` has no aspect ratio field (projects created by very early versions, or imported ones), storyboard freshness is judged against the drama default ratio of 16:9, matching what generation actually uses. Storyboards in such projects that were previously recorded against 9:16 show as out of date after the upgrade; regenerate them as needed.

If a referenced asset was deleted or renamed, or a referenced character, scene, or prop has no registrable sheet at upgrade time (it was never generated, its file is missing, or its description is empty so the sheet itself is not registered), the storyboard is not registered: it shows as missing after the upgrade and is listed in the migration report. Product sheets are optional: a product with no declared sheet can use only its originals, or text alone if no originals are declared either. However, an unavailable declared product sheet or an unreadable declared product original also prevents storyboard registration. Follow the report to restore the asset registration or sheet, re-upload missing originals or clear their invalid fields, then regenerate the storyboard.

Asset sheets and derivative sheets are registered by the same rules used at generation time: if a character or product declares an original image that cannot be read, or the asset description is empty, its sheet is not registered; if a derivative's base sheet cannot be registered, the derivative sheet is not registered either. They show as missing after the upgrade and are listed in the migration report. Re-upload the original or clear the invalid original field, fill in the description, then regenerate the sheet; regenerate the derivative sheet once its base sheet is in place. After the upgrade, a character or product whose original image is lost can no longer generate an asset sheet until you do the same.

When upgrading to the version in which confirming content immediately produces the formal script, the migration takes over each episode according to its state:

- Episodes whose script plan was confirmed but that have no formal script yet get a formal script produced from the confirmed plan, with every shot marked as awaiting prompt writing. Episodes that cannot be converted are listed in the migration report; confirm the script plan again to fix them.
- Episodes that already have a formal script keep their content. Shots with an empty visual prompt are marked as awaiting writing, and missing visual adaptation descriptions for drama shots and missing source text for reference-to-video units are filled in from the script plan.
- Episodes that already have a formal script but never recorded a content confirmation use the current script plan as the confirmed baseline. Rerunning the script plan afterwards only returns that episode to awaiting confirmation; the existing formal script can still be used for production.
- An episode's script binding is recognized only when it is exactly `scripts/episode_N.json`. If an episode is bound to another name (for example `scripts/custom.json`), is written in a form that points to the same script but differs literally, such as `episode_N.json`, `./scripts/episode_N.json`, or `scripts\episode_N.json`, or if the same episode number appears more than once in `episodes`, the project fails to migrate and stays at its pre-upgrade version without a single byte of the project directory changed. The failure record names the episode numbers and bindings at fault. Use it to find the script: move it to `scripts/episode_N.json` if it is not there (leave the file alone if it is already there and only the binding is written differently), change `script_file` in `project.json` to exactly `scripts/episode_N.json` (keeping only one entry per duplicated episode number), and restart to continue.

One class of migration first copies the whole project next to its directory, rewrites the copy, and then swaps the directories. What that means for disk space and recovery:

- Free space is checked before the migration starts. If it cannot hold the copy, that project fails with a "disk space is insufficient" error and its directory is left untouched; free up space and restart to continue.
- If the process is killed during the swap (power loss, `kill -9`, an OOM-killed container), the project directory can be missing for a moment and a hidden directory starting with `.<project-name>.v6-` is left alongside it. The next startup reclaims it and brings the project back, so do not delete these directories by hand, and do not rush to recreate a project that disappeared from the list.
- Once the project is back in place, those hidden directories are cleaned up automatically under the same retention window as the backups above.

If a project migration fails, preserve the files and inspect the startup logs. Do not manually change the schema version or delete backup files. Repair the damaged project references or permissions, then restart the service.

### 5.6 Pin a Version {#pin-version}

`latest` is suitable for a quick evaluation, but pinning a release tag is a better choice for production.

Change the image in Compose to:

```yaml
image: arcreel/arcreel:X.Y.Z
```

Explicitly changing the version when upgrading reduces the risk of unintentionally pulling a new version.

## 6. Backup and Restore {#backup-and-restore}

### 6.1 Back Up a SQLite Deployment {#backup-sqlite}

The data root already contains projects, the default SQLite database, logs, and Vertex credentials, so a file-system backup only needs to archive the data root plus `.env` and `claude_data/`.

First identify the actual data root on the host, then stop writes. Default Compose uses `deploy/projects/`. If you changed the container path with `ARCREEL_DATA_DIR` and a custom mount, set `data_dir` to the corresponding absolute host path:

```bash
cd deploy

data_dir="$(cd projects && pwd)"
# Custom data directory example: data_dir="/srv/arcreel/projects"

docker compose stop arcreel
```

Create the backup:

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p backups
chmod 700 backups

tar -czf "backups/arcreel-config-${backup_stamp}.tar.gz" \
  .env claude_data

tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .
```

Archiving the entire `data_dir` after the service has stopped keeps `arcreel.db` and project assets at the same point in time. The configuration and data archives must use the same timestamp and be stored together. `umask 077` and backup directory mode `0700` restrict access to the credentials and project assets they contain. Do not copy only `arcreel.db` while ArcReel is writing to it. In WAL mode, committed transactions may still reside in `arcreel.db-wal`; losing or mismatching the WAL can cause data loss or corruption. If downtime is not possible, use the SQLite Online Backup API, such as the `sqlite3` `.backup` command, or `VACUUM INTO` to create a consistent snapshot instead of copying the main database file directly with `cp`.

Restart the service:

```bash
docker compose start arcreel
```

To restore:

1. Stop ArcReel;
2. Back up the current directory so you can roll back if files are overwritten;
3. Restore `.env` and `claude_data/` from the configuration archive, then extract the paired data archive completely into an empty `data_dir`;
4. Start the service and check `/health`;
5. Open several projects and verify their images, videos, and version history.

### 6.2 Back Up a PostgreSQL Deployment {#backup-postgresql}

Stop the ArcReel application first, but leave PostgreSQL running, so no new writes occur while you back up the database and project files:

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
umask 077
mkdir -p backups
chmod 700 backups
docker compose stop arcreel

backup_stamp="$(date +%Y%m%d-%H%M%S)"

docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h 127.0.0.1 -U arcreel -d arcreel' \
  > "backups/arcreel-db-${backup_stamp}.sql"

tar -czf "backups/arcreel-files-${backup_stamp}.tar.gz" \
  .env docker-compose.yml projects claude_data

docker compose start arcreel
```

The database and file backups use the same timestamp and must be stored and restored together. The `projects/` directory in the file backup is the data root and already includes logs and Vertex credentials. The file backup includes the active `docker-compose.yml`, so any `DATABASE_URL` customization required by a special-character password is restored together with `.env`. `umask 077` and backup directory mode `0700` ensure that the host-created SQL file and file archive are readable and writable only by the current user.

`pg_dump` reads `PGPASSWORD` through libpq. The command above sets it only for that `pg_dump` process inside the PostgreSQL container, allowing `docker compose exec -T` to run non-interactively without expanding the password into the host command line. For long-running host-side backup automation, use a PostgreSQL password file with `0600` permissions instead. Never put the password in a script or backup filename.

If `tar` reports `Permission denied`, the mounted directory contains files created by the container's root user that the current host user cannot read. Rerun the corresponding `tar` command with `sudo`, then restrict read access to the backup file when finished.

### 6.3 Restore PostgreSQL {#restore-postgresql}

Before restoring, stop ArcReel but leave PostgreSQL running:

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
docker compose stop arcreel

backup_stamp=YYYYMMDD-HHMMSS
tar -xzf "backups/arcreel-files-${backup_stamp}.tar.gz"
```

The file archive restores `.env`, `docker-compose.yml`, the data root `projects/`, and `claude_data/`. The following procedure also deletes the existing data in the target `arcreel` database. First verify that the database and file backups with the same `backup_stamp` are complete, and rehearse the restoration procedure in an isolated environment.

Recreate an empty database before importing to avoid conflicts with existing schemas or data:

```bash
docker compose exec -T postgres \
  dropdb -U arcreel --maintenance-db=postgres --if-exists --force arcreel

docker compose exec -T postgres \
  createdb -U arcreel --maintenance-db=postgres -O arcreel arcreel

cat "backups/arcreel-db-${backup_stamp}.sql" | \
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U arcreel -d arcreel
```

After the database import succeeds, restart the application:

```bash
docker compose start arcreel
curl -f http://localhost:1241/health
```

> The restoration strategy depends on whether you are overwriting an existing database, restoring across versions, and whether the service was still accepting writes when the backup was created. Production environments should regularly perform real restoration drills, not merely verify that backup files exist.

## 7. Reverse Proxy and HTTPS {#reverse-proxy-and-https}

ArcReel does not currently support direct exposure to the public Internet. Private remote deployments must enable authentication and protect traffic with TLS, a VPN, or a secure tunnel. Do not publish port `1241` directly to an untrusted network. Recommended practices:

- Use Nginx, Caddy, Traefik, or a cloud load balancer;
- Configure HTTPS;
- Allow only the proxy server to access the ArcReel container port;
- Preserve long-lived SSE connections;
- Set sufficiently large upload limits and read timeouts.

The official Compose files use `1241:1241` by default, which publishes the backend port on every host network interface. Adding a reverse proxy alone does not close this direct access path. When the reverse proxy runs on the same host, change the `arcreel` service's port mapping before startup so it listens only on loopback:

```yaml
ports:
  - "127.0.0.1:1241:1241"
```

If the reverse proxy runs on a container network or another host, remove any unnecessary host port publishing and use the container network, host firewall, or an equivalent network policy to ensure only the proxy can access the ArcReel backend.

Nginx example:

```nginx
server {
    listen 443 ssl http2;
    server_name arcreel.example.com;

    ssl_certificate /etc/letsencrypt/live/arcreel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arcreel.example.com/privkey.pem;

    client_max_body_size 2g;

    location / {
        proxy_pass http://127.0.0.1:1241;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ArcReel uses SSE to push Agent replies and project events
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Certificate configuration depends on your infrastructure. You can use ACME/Let's Encrypt or certificates managed by your cloud platform.

## 8. Container Permissions and the Agent Sandbox {#container-permissions-and-sandbox}

ArcReel strictly checks the Agent sandbox at startup on Linux and macOS and refuses to start if the required tools are missing or unavailable. Native Windows does not provide `bwrap`, so ArcReel automatically falls back to a restricted Bash command allowlist. This mode supports only project creation and basic workflows; use WSL2 or Docker Desktop for production deployments.

| Environment | Tool | Installation |
|---|---|---|
| macOS | `sandbox-exec` | Included with the operating system; no additional installation required |
| Local development on Linux | `bwrap` + `socat` | Ubuntu/Debian: `sudo apt install bubblewrap socat`; Fedora: `sudo dnf install bubblewrap socat`; Arch: `sudo pacman -S bubblewrap socat` |
| Docker | `bwrap` + `socat` | Included in the official image |
| Native Windows | No `bwrap` sandbox | Automatically falls back to a Bash command allowlist; WSL2 / Docker Desktop recommended |

The official Compose configuration gives the Agent Bash sandbox:

- `seccomp:unconfined`
- `apparmor:unconfined`
- `NET_ADMIN`

These settings support `bwrap` isolation and nested network namespaces inside the container, but also give the container more privileges than a typical web application.

Production recommendations:

- Use a dedicated host or, at minimum, a well-isolated runtime environment;
- Do not mount the Docker socket into the container;
- Do not mount any additional, unnecessary host directories;
- Restrict access to administrative pages;
- Keep ArcReel and the base image up to date;
- Give the Agent only the network and file access it needs;
- Treat project input from unknown sources with caution.

Although the Docker image includes `bwrap` and `socat`, user namespace or AppArmor policies on the host may still prevent the sandbox from starting. If startup fails, resolve the `SANDBOX_*` diagnostics shown in the service output. Do not bypass the checks by switching to privileged mode, and do not remove the official Compose sandbox configuration without understanding the consequences.

## 9. Monitoring Recommendations {#monitoring}

At minimum, monitor:

- Whether `/health` is available;
- Whether containers restart frequently;
- Available disk space;
- The growth rate of `projects/`;
- The size of the PostgreSQL data directory;
- Task failure rates;
- Provider rate limits and insufficient quotas;
- The most recent successful backup time.

Media assets usually grow faster than the database. Prioritize capacity alerts for the project directory.

## 10. Common Problems {#troubleshooting}

### Service Fails to Start {#service-wont-start}

```bash
docker compose ps
docker compose logs --tail=300 arcreel
```

Check:

- Whether `.env` exists;
- Whether port `1241` is already in use;
- Whether the image was pulled successfully;
- Whether the mounted directories are writable;
- Whether `POSTGRES_PASSWORD` is set for production deployments.

### Health Check Fails {#health-check-fails}

```bash
curl -v http://localhost:1241/health
docker compose logs --tail=300 arcreel
```

If the container has just started, check whether database migrations are still running.

### Cannot Log In {#cannot-log-in}

- Check `AUTH_USERNAME`;
- Check `AUTH_PASSWORD` in `.env`;
- If the password was left empty on first startup, check whether it was written back to the file;
- Log in again after changing `AUTH_TOKEN_SECRET`.

### Agent Requests Fail {#agent-request-fails}

- Verify the AI assistant credentials;
- Check the Base URL and model name;
- Check the network and proxy;
- Check whether the provider is rate-limiting requests;
- Use a small amount of content for verification. Do not run a complete novel through the whole pipeline on the first attempt.

### Tasks Remain Queued {#tasks-stuck-in-queue}

- Review the image, video, and audio concurrency settings;
- Check for abnormal tasks that have remained running for an extended period;
- Check the provider's RPM quota;
- Check whether a preceding task is still incomplete.

### Rapid Disk Growth {#disk-growth}

From the Compose directory, check how much space each part of the data root uses:

```bash
du -sh projects/*
find projects/projects -type f -size +500M
```

Do not directly delete files referenced by current projects. Prefer archiving projects, removing unused projects, and retaining only the necessary space for version history.

## 11. Go-Live Checklist {#go-live-checklist}

- [ ] Use PostgreSQL;
- [ ] Pin the release image version;
- [ ] Set a strong `AUTH_PASSWORD`;
- [ ] Set a fixed `AUTH_TOKEN_SECRET`;
- [ ] Configure HTTPS;
- [ ] Do not expose `1241` directly;
- [ ] Verify that SSE works correctly;
- [ ] Back up the database and the data root;
- [ ] Complete a restoration drill;
- [ ] Configure disk space and health-check alerts;
- [ ] Confirm that model API keys do not appear in logs or the repository;
- [ ] Read the license and `NOTICE`.
