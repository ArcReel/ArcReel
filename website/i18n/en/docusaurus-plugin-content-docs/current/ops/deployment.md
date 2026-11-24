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
| `deploy/projects/` | `/app/projects` | Data root: projects, generated assets, the default SQLite database, logs, Vertex AI credentials, and all other runtime data |
| `deploy/claude_data/` | `/root/.claude` | Agent runtime data |

`deploy/projects/` is ArcReel's data root (`ARCREEL_DATA_DIR`, `/app/projects` inside the container). Projects live in its `projects/` subdirectory, and all other runtime data sits alongside it:

```text
deploy/projects/               data root
├── projects/<project-name>/   projects and generated assets
├── deleted_projects/          deleted projects, kept for 30 days
├── global_assets/             global asset library
├── users/<user_id>/memory/    Agent user memory
├── arcreel.db                 default SQLite database (with -wal / -shm)
├── logs/                      application logs
├── vertex_keys/               Vertex AI credential files
├── trial_runs/                output of endpoint "Test connection" runs
└── runtime/                   migration markers, generation admission locks, image thumbnail cache, and other runtime state
```

Only directories under `projects/` whose names contain letters, digits, or hyphens and that contain a `project.json` file count as projects. No other entry in the data root appears in the project list. The diagnostic logs downloaded from Settings → About list the actual locations of the data root and each kind of data above.

> Back up the entire data root, not just the database. The database stores tasks, configuration, and index information, while the project directories store source media and generated files. They must remain consistent.

The project trash is stored in `projects/<project-name>/.arcreel/trash/`. A full data-root backup keeps its deletion records; a project ZIP export does not include it. Deletion records are kept for 30 days from deletion and are cleared at application startup and periodically while it runs. Restoring a backup does not reset a record's retention period.

A deleted project moves as a whole, together with its project trash, to `deleted_projects/<project-name>#deleted-<deletion-time>/`. It is also kept for 30 days and cleared the same way. You can restore or permanently delete it from Deleted projects on the project list page. If the original name is already taken, the project is restored under a new name with a suffix.

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

ArcReel runs Alembic at startup to upgrade the database schema. You can also run `alembic upgrade head` before starting the app. The migration records the previous database version first so ArcReel can [preserve the models in use](#project-schema-migrations). If the write fails, repair database access and retry. If it fails on SQLite just after creating the global settings table for the first time, a retry can proceed with the previous version treated as unknown. Check and reselect models in Global Settings → Default Models. Run the migration against the live database. The offline `alembic upgrade --sql` mode is unsupported.

[Back up the database and data root](#backup-and-restore) before upgrading.

## 3. Environment Variables {#environment-variables}

The default deployment examples currently include these core variables:

| Variable | Default | Recommendation |
|---|---|---|
| `AUTH_USERNAME` | `admin` | Change the administrator username if needed |
| `AUTH_PASSWORD` | Empty | Explicitly set a strong password for production deployments |
| `AUTH_TOKEN_SECRET` | Empty | Set a fixed, long-lived random value; if left empty, ArcReel generates one, saves it to `runtime/auth-token-secret` under the data root, and reuses it after restarts |
| `AUTH_ENABLED` | `true` | Never disable for remote deployments; `false` leaves every management endpoint unauthenticated |
| `LOG_LEVEL` | `INFO` | Temporarily change to `DEBUG` while troubleshooting, then restore it |
| `POSTGRES_PASSWORD` | None | Required and must be set only for production deployments |
| `TZ` | `Asia/Shanghai` | Can be overridden in the Compose environment |
| `DATABASE_URL` | Default SQLite path | Production Compose sets the PostgreSQL URL automatically |
| `ARCREEL_DATA_DIR` | `projects` | Data root; projects, the default SQLite database, logs, and Vertex credentials all live under it |
| `CORS_ORIGINS` | Wildcard | When narrowed to an allowlist, browser MCP client origins must be listed too |
| `ARCREEL_OFFICIAL_SERVICE_URL` | Built-in official service address | The official service behind market install counts, ratings, and submissions; set it to empty to turn it off entirely, leaving the market to read market sources only |

Notes:

- Changing `AUTH_TOKEN_SECRET` invalidates existing login tokens. A value you set takes precedence, and the secret file generated under the data root is no longer used. When no value is explicitly set, deleting the secret file and restarting generates a new secret and invalidates existing logins.
- After editing `.env`, run `docker compose up -d` in the Compose directory to recreate the container. `docker compose restart` and automatic container restarts keep the environment variables captured when the container was created and do not reload `.env`. If startup finds that a process environment variable differs from the mounted `.env`, the log shows one WARNING that lists only the variable names.
- `AUTH_ENABLED=false` is only for a local machine protected by its own network boundary. Compose publishes port `1241` on all host interfaces by default, so never disable authentication for remote deployments. While authentication is off, startup logs a WARNING.
- `.env` may contain secrets. Do not commit it to version control.
- Vertex credential files should be readable only by the user who runs ArcReel.
- File logs are always written to `logs/` under the data root. The old `ARCREEL_LOG_DIR` variable no longer takes effect; if it is still set, startup logs a one-time notice. Set `ARCREEL_LOG_FILE_DISABLED=true` if you only want logs on stdout.
- Third-party model API keys are normally managed on the ArcReel Settings page. Do not include them in public documentation.

The remote MCP endpoint is `/mcp` and always requires an API Key with an `arc-` prefix; it never permits anonymous access, even when `AUTH_ENABLED=false`. Remote access needs no extra `MCP_*` configuration: paste the endpoint URL shown under "External agent access" in Settings, together with an API Key created under "Access tokens", into your client, and connect through an HTTPS reverse proxy, VPN, or secure tunnel that preserves long-lived SSE connections.

The server does not validate the request `Host` header; the endpoint boundary rests on the API Key enforced on every request. Host ownership belongs to the deployment: restrict `server_name` (Nginx) or the equivalent rule on your reverse proxy so only requests for the expected domain reach ArcReel.

Browser MCP clients need no configuration either while `CORS_ORIGINS` stays at its permissive default; once you narrow it to an allowlist, add the client Origin to it as well. That single allowlist governs both the application API and the MCP endpoint; there is no second MCP-specific list.

Remote MCP offers no OAuth authorization and publishes no OAuth protected-resource metadata (RFC 9728). Clients that only support OAuth discovery and cannot be given a Bearer credential cannot connect.

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

This endpoint requires no authentication. In official images, the response includes a `commit` field holding the commit sha the image was built from, which tells you which version the instance is running; when run directly from source, it is `null`.

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

The bundled Compose files set a default 60-second total container stop grace period (`stop_grace_period: 60s`), covering HTTP connection draining, parallel shutdown of in-flight tasks and resident Agent sessions, and database teardown. On receiving a stop signal, the service immediately stops claiming new queued tasks, proactively closes active browser project-event and Agent-session SSE streams (clients reconnect automatically once the new container is up), and waits within the grace period for short in-flight tasks and Agent sessions to finish cleanly. Video tasks still `running` past the grace period automatically attempt to resume and download their results after restart when their provider job ID and valid checkpoint are recorded on a resumable backend, without submitting duplicate paid requests. Increase `stop_grace_period` in Compose if you want a longer drain window for local renders or slower image tasks.

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

1. **Start the new version with the old volume mounts in place.** If you pinned the ArcReel image tag, change it to the target version first. Keep the existing volume mounts and run `docker compose pull` and `docker compose up -d` as described in [Upgrade the Default Deployment](#upgrade-sqlite-deployment) or [Upgrade the Production Deployment](#upgrade-postgresql-deployment). Both old volumes are still mounted, so the first startup merges their files into the data root. Confirm that the startup log contains the data root layout migration completion message (「数据根布局迁移完成」), and that the original log and credential files now appear under `deploy/projects/logs/` and `deploy/projects/vertex_keys/` (credential files may have been renamed to `vertex_cred_<credential-id>.json`).
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

ArcReel upgrades project data automatically at startup. [Back up the data root and database](#backup-and-restore) before upgrading, and leave space for the migration. A large project may need a full directory copy, and the first startup can take longer.

When a parent directory of the project or temporary archive extraction directory is accessed through a symbolic link, migration still assigns legacy scripts to the correct episodes and preserves their existing media content.

**Review migration results**

- The project message about items adjusted during the upgrade opens a read-only migration report. “Adjusted” lists structural changes and kept copies. “Could not import” lists files preserved in place but left unregistered. Archive imports with these adjustments prompt for confirmation before completing. Cancel to discard that import.
- After upgrading from an older release, check the global and project default models. ArcReel preserves the previously used models when it can reconstruct the choices. Select a model in Settings if the old choice could not be recovered. When importing an old project ZIP, ArcReel uses the recommendations at import time to fill in old selections. Check the project settings after import.
- Project-level migration backups sit beside the modified files and end in `.bak.v<starting-version>-<timestamp>`. Copies kept to resolve conflicts end in `.kept.v<starting-version>-<timestamp>`. Project-level backups cover the files involved in the migration. Use the full pre-upgrade backup to restore the entire instance.

**Recover from a project migration failure**

1. Keep the project directory, backups, and saved copies. Check the startup log for disk space, permissions, or damaged files. Leave the schema version as recorded.
2. Resolve the problem and restart the service. ArcReel retries the migration.
3. If the project directory disappears after a power loss or forced container stop, stop the service. Look next to the project directory for a directory starting with `.<project-name>.v6-rollback-`. If there are several, select the newest by modification time. Rename it to the original project name and start the service. Keep the hidden directories and avoid creating a project with that name until recovery is complete.

See the [project data migration reference](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/project-migrations.md) for version-specific behavior.

### 5.6 Pin a Version {#pin-version}

`latest` is suitable for a quick evaluation, but pinning a release tag is a better choice for production.

Change the image in Compose to:

```yaml
image: arcreel/arcreel:X.Y.Z
```

Explicitly changing the version when upgrading reduces the risk of unintentionally pulling a new version.

## 6. Backup and Restore {#backup-and-restore}

After the whole site is moved to a different absolute path, the application relocates the storage of existing Agent sessions to match the current project directories at startup, so conversations can continue with their context. Sessions of deleted projects follow the new name the project is restored under. With `ARCREEL_SDK_SESSION_STORE=off`, sessions are stored only in the SDK's local directory, so keep that directory when migrating (`claude_data/` for Docker, or `CLAUDE_CONFIG_DIR` or the default `~/.claude` for native deployments). When the same session has records in several source locations, or the destination already has a record, they are not merged automatically; a failed move logs a warning, and the service still starts.

After object storage is enabled under **Save new images to**, the content of remote images lives only in your TOS bucket; backups of the data root and the database contain only their records and a local cache that can be cleaned up. When restoring a backup, the connection must still point to the original bucket, and the objects in the bucket must be kept as well. ArcReel deletes image objects that are no longer used, so objects of projects or versions deleted after the backup may already be gone from the bucket, and those images cannot be read after restoring an older backup. If you need to restore to an earlier point in time, back up the objects under `arcreel-materials/` while writes are stopped (or enable versioning on the bucket), and put them back into the original bucket under the same keys when restoring; see [Save New Images to Object Storage](../guide/asset-storage.md#remote-image-storage).

Before backing up, use `docker compose config` to confirm which database and data root ArcReel actually uses. The `tar` examples use the default `docker-compose.yml`. Include every Compose file in use in the configuration archive, including automatically loaded override files and files selected by `COMPOSE_FILE` or `-f`. Preserve relative paths. After extracting the archive into the restore directory, update any `COMPOSE_FILE` or `-f` paths that point to the original directory. Load the restored files in their original order. See [Merge Compose files](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/).

### 6.1 Back Up a SQLite Deployment {#backup-sqlite}

Back up the entire data root, including projects, the default SQLite database, logs, and Vertex credentials. Also archive the active Compose files, `.env`, and `claude_data/`.

Run the commands from the active Compose directory. The initial deployment uses `deploy/` and its `projects/` subdirectory by default. Find the actual data root on the host, then stop writes. For a custom mount with `ARCREEL_DATA_DIR`, set `data_dir` to the corresponding absolute host path:

```bash
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
  docker-compose.yml .env claude_data

tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .
```

Archiving the entire `data_dir` after the service has stopped keeps `arcreel.db` and project assets at the same point in time. The configuration and data archives must use the same timestamp and be stored together. `umask 077` and backup directory mode `0700` restrict access to the credentials and project assets they contain. Do not copy only `arcreel.db` while ArcReel is writing to it. In WAL mode, committed transactions may still reside in `arcreel.db-wal`; losing or mismatching the WAL can cause data loss or corruption. If downtime is not possible, use the SQLite Online Backup API, such as the `sqlite3` `.backup` command, or `VACUUM INTO` to create a consistent snapshot instead of copying the main database file directly with `cp`.

Restart the service:

```bash
docker compose start arcreel
```

To restore:

1. Stop ArcReel and keep the original deployment directory for rollback.
2. Extract the configuration archive into a new deployment directory. If any custom mounts still point to the original deployment's directories, update them to the new paths. Confirm that the data root mount points to a new, empty directory, then extract the full data archive into that empty directory.
3. Run `docker compose up -d --wait arcreel` from the restore directory. Check `/health` and open several projects to verify their images, videos, and version history.

### 6.2 Back Up a PostgreSQL Deployment {#backup-postgresql}

These database commands apply only when ArcReel connects to the `postgres` service in the same Compose project. For any other PostgreSQL deployment, see [Back Up and Restore External PostgreSQL](#external-postgresql-backup-restore). The examples use `arcreel` as both the database name and user, and read the password from `POSTGRES_PASSWORD` in the PostgreSQL container. If you changed the database name or user, adjust `-d`, `-U`, and authentication in the backup and restore commands to match.

Run these commands from the active production Compose directory. The initial deployment uses `deploy/production/` by default. Stop ArcReel and leave PostgreSQL running so the database and project files represent the same point in time. Archive the data root actually mounted by Compose. For a custom mount, set `data_dir` to the absolute host path.

```bash
data_dir="$(pwd -P)/projects"
# Custom data directory example: data_dir="/srv/arcreel/projects"
umask 077
mkdir -p backups
chmod 700 backups
docker compose stop arcreel

backup_stamp="$(date +%Y%m%d-%H%M%S)"

docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h 127.0.0.1 -U arcreel -d arcreel' \
  > "backups/arcreel-db-${backup_stamp}.sql"

tar -czf "backups/arcreel-files-${backup_stamp}.tar.gz" \
  .env docker-compose.yml claude_data
tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .

docker compose start arcreel
```

Keep and restore the SQL dump, configuration archive, and data root archive with the same timestamp. The data root includes logs and Vertex credentials. The archived `docker-compose.yml` and `.env` preserve any custom `DATABASE_URL`. `umask 077` and backup directory mode `0700` ensure that the host-created SQL file and file archive are readable and writable only by the current user.

`pg_dump` reads `PGPASSWORD` through libpq. The command above sets it only for that `pg_dump` process inside the PostgreSQL container, allowing `docker compose exec -T` to run non-interactively without expanding the password into the host command line. For long-running host-side backup automation, use a PostgreSQL password file with `0600` permissions instead. Never put the password in a script or backup filename.

If `tar` reports `Permission denied`, the mounted directory contains files created by the container's root user that the current host user cannot read. Rerun the corresponding `tar` command with `sudo`, then restrict read access to the backup file when finished.

### 6.3 Restore PostgreSQL {#restore-postgresql}

This section restores the `postgres` service managed by the same Compose project. For an external database, see [Back Up and Restore External PostgreSQL](#external-postgresql-backup-restore).

Use the [archives and SQL dump with the same timestamp](#backup-postgresql). Start in the active production Compose directory and restore into a new sibling directory. Keep the original database and files for rollback. The steps use the default relative mount paths. For custom mounts, adjust the restored configuration to mount the extracted `projects/` and `claude_data/` in the new deployment directory, and point PostgreSQL to a new `pgdata/` path that does not yet exist in that directory. If you use image object storage, prepare [the objects from the same backup point](#backup-and-restore). The restored connection needs access to the matching images in the original bucket.

Extract the archives into an empty directory and check the restored configuration and credentials. If the file archive already contains `projects/`, use that data root. Replace the timestamp below and run all steps in the same shell.

```bash
set -euo pipefail

active_dir="$(pwd -P)"
backup_stamp=YYYYMMDD-HHMMSS
archive="${active_dir}/backups/arcreel-files-${backup_stamp}.tar.gz"
projects_archive="${active_dir}/backups/arcreel-projects-${backup_stamp}.tar.gz"
db_dump="${active_dir}/backups/arcreel-db-${backup_stamp}.sql"
test -s "${archive}" && test -s "${db_dump}" || exit 1

restore_dir="$(mktemp -d "$(dirname "${active_dir}")/arcreel-restored-XXXXXX")"
tar -xzf "${archive}" -C "${restore_dir}"
test -f "${restore_dir}/.env"
test -f "${restore_dir}/docker-compose.yml"
if [ ! -d "${restore_dir}/projects" ]; then
  mkdir "${restore_dir}/projects"
  tar -xzf "${projects_archive}" -C "${restore_dir}/projects"
fi
test -d "${restore_dir}/claude_data"
test ! -e "${restore_dir}/pgdata" && test ! -L "${restore_dir}/pgdata" || exit 1
```

Run `docker compose config` in the restore directory. Confirm that the mounts use the new paths and that ArcReel’s `DATABASE_URL` points to the new deployment’s `postgres` service. Return to the original deployment directory and stop its stack. Then switch back to the restore directory and start only PostgreSQL.

```bash
cd "${active_dir}"
docker compose down
cd "${restore_dir}"
docker compose up -d postgres
docker compose ps postgres
```

Once PostgreSQL is healthy, import the SQL into the new, empty database. If you use a custom database name or user, create the empty database and the required roles in the restore instance first. The import runs in one transaction and rolls back automatically if it fails. Resolve the error before retrying, and start ArcReel only after the import succeeds.

```bash
docker compose exec -T postgres psql -X --single-transaction -v ON_ERROR_STOP=1 -U arcreel -d arcreel -f - < "${db_dump}"
```

Once the database and object storage are ready, create the ArcReel container from the restored configuration. Sign in and check projects, images, videos, version history, and important sessions.

```bash
docker compose up -d --wait arcreel
curl -f http://localhost:1241/health
```

After verification, run future Compose and backup commands from the restore directory. To roll back, run `docker compose down` there, then run `docker compose up -d` in the original directory.

### 6.4 Back Up and Restore External PostgreSQL {#external-postgresql-backup-restore}

1. Stop ArcReel writes. Identify the source database from the effective `DATABASE_URL` and back it up with your hosting provider’s tools or [`pg_dump`](https://www.postgresql.org/docs/18/app-pgdump.html). Archive the configuration and data root at the same point in time using the steps above. Skip the `pg_dump` command that runs inside Compose. Resume the original service after all backups finish.
2. Extract the configuration and data root into the new location, then update the host mounts in the restored configuration to use the new paths. Use your hosting provider’s restore tools, or [restore the SQL dump](https://www.postgresql.org/docs/18/backup-dump.html#BACKUP-DUMP-RESTORE) into a separate, empty database. Keep the original database for rollback.
3. Update the restored deployment’s `DATABASE_URL` and verify with `docker compose config` that it points to the restored database. Check the mounts and object storage. If both deployments use the same host port, run `docker compose stop arcreel` in the original deployment directory first. Then start ArcReel from the restore directory, check `/health`, and verify your project contents.

To roll back, run `docker compose stop arcreel` in the restore directory. Then run `docker compose up -d arcreel` in the original deployment directory.

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

ArcReel handles cache headers and compression itself, so the reverse proxy needs no extra configuration for them:

- Media URLs with a version key (`?v=`, `?fp=`) or pointing to a `versions/` snapshot, and the content-hashed frontend build output under `/assets/`, return `Cache-Control: public, max-age=31536000, immutable`, so browsers reuse their local copy directly;
- Media URLs without a version key return `Cache-Control: no-cache`, so browsers revalidate with the server before each use and receive `304` when the file has not changed;
- The page entry HTML returns `no-store`, so browsers load the new frontend after an upgrade;
- Text responses such as JSON, JS, CSS, and HTML are gzip-compressed; SSE event streams, images, audio, and video are not compressed.

The reverse proxy should pass these response headers through unchanged. Do not override them with `expires`, `add_header Cache-Control`, or `proxy_hide_header`.

Nginx example. The example splits out the media file paths: SSE and regular API traffic run with proxy buffering off so events reach the browser in real time, while `/api/v1/files/` and `/api/v1/global-assets/` run with proxy buffering on so Nginx receives images and videos first and releases the backend connection early.

```nginx
server {
    listen 443 ssl http2;
    server_name arcreel.example.com;

    ssl_certificate /etc/letsencrypt/live/arcreel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arcreel.example.com/privkey.pem;

    client_max_body_size 2g;

    # Compress text types only. Nginx does not recompress responses ArcReel has already compressed.
    # Do not add text/event-stream: compression buffering delays SSE events. Images, audio, and video are already compressed; do not add them either
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/markdown application/json application/javascript image/svg+xml;

    # Project media and global assets: read-only GET, buffering on
    location ~ ^/api/v1/(files|global-assets)/ {
        proxy_pass http://127.0.0.1:1241;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering on;
    }

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
- Check whether you ran `docker compose up -d` after editing `.env`, and whether the startup log has a WARNING that process environment variables differ from `.env`;
- Log in again after changing `AUTH_TOKEN_SECRET`.

### Agent Requests Fail {#agent-request-fails}

- Verify the Agent credentials;
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

Do not directly delete files referenced by current projects. To reduce disk usage, archive projects, remove unused projects, and keep only the versions you need.

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
