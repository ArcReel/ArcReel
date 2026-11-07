# MySQL 8 compatibility research

> Status: research. The gap inventory is a work list, not a completion report.
> Related: [#1983](https://github.com/ArcReel/ArcReel/issues/1983) (proposal), [#1985](https://github.com/ArcReel/ArcReel/pull/1985) (first migration/deployment/documentation implementation batch)

## Background

ArcReel currently supports SQLite/PostgreSQL dialects. Evaluate gaps/paths for MySQL 8.0+ as an optional self-hosted backend.

## Current status

- [x] Research complete; compatibility gaps documented here
- [ ] Engine adaptation (`lib/db/engine.py`)
- [ ] Alembic migration fixes
- [ ] CI mysql-compat job
- [ ] Documentation (`.env.example` + docs)

## Compatibility gaps

Evidence labels: **reproduced** means actually observed on SQLite/PG; **to verify** means inferred from MySQL documentation without real MySQL 8 tests.

### 1. Expression indexes (reproduced on SQLite only)

- SQLite table reconstruction during rollback loses expression indexes ([#1807](https://github.com/ArcReel/ArcReel/issues/1807)).
- Whether MySQL 8 shares it: **to verify**. #1807 covers SQLite only, not MySQL failures. Record MySQL version, index definition, revision, expected state, and actual results.

### 2. Partial unique indexes (known unsupported in MySQL; replacement undecided)

Two places depend on `sqlite_where` / `postgresql_where` partial uniqueness:

- `idx_tasks_dedupe_active` in `lib/db/models/task.py` (active-task deduplication: `COALESCE` expression columns + conditions)
- `uq_provider_credential_one_active` in `alembic/versions/2c57c41eca74_add_provider_credential_table.py` (one active credential per provider)

MySQL 8 lacks partial indexes; direct migration creates **unconditional unique indexes**, breaking deduplication or migrations for the first and rejecting existing multiple inactive credentials for the second. Candidate: virtual generated columns + ordinary unique indexes. Design/test each separately.

### 3. Runtime DML dialect branches (missing)

Bulk insertion in `lib/agent/agent_session_store/store.py` branches only postgresql/sqlite; MySQL/others use SQLite `on_conflict_do_nothing()`, failing MySQL compilation. Add MySQL strategies (`INSERT IGNORE` or `ON DUPLICATE KEY`) and tests during engine adaptation.

### 4. JSON types (to verify)

Verify all read/write paths for SQLite text, PostgreSQL native JSON, and MySQL 8 native JSON.

### 5. server_default (no initial difference; systematic review pending)

Initial review found no `text("NOW()")`; migrations consistently use `sa.func.now()`. Systematically record each migration's actual `func.now()` DDL (`CURRENT_TIMESTAMP` variants) across dialects and precision differences (MySQL `DATETIME` defaults to seconds versus required microseconds).

## Migration validation plan

Every affected revision must satisfy offline/online standards with independent round trips and idempotent upgrades.

> `URL_ENCODED_PASSWORD` below is a placeholder without shell metacharacters such as `<>`, allowing copied single-quoted URLs. Replace before use; URL-encode reserved password characters such as `@ : / ?`. Never write real passwords here.

### Offline (generate SQL per revision without database connections)

```text
# Generate/check SQL separately for each revision
# Syntax: alembic upgrade <from_revision>:<to_revision> --sql
DATABASE_URL='mysql+aiomysql://arcreel:URL_ENCODED_PASSWORD@mysql:3306/arcreel?charset=utf8mb4' \
  alembic upgrade f6a41746c0de:7a8b9c0d1e2f --sql > upgrade_7a8b9c0d1e2f.sql
DATABASE_URL='mysql+aiomysql://arcreel:URL_ENCODED_PASSWORD@mysql:3306/arcreel?charset=utf8mb4' \
  alembic upgrade f6a41746c0de:bcaaa615ff38 --sql > upgrade_bcaaa615ff38.sql
DATABASE_URL='mysql+aiomysql://arcreel:URL_ENCODED_PASSWORD@mysql:3306/arcreel?charset=utf8mb4' \
  alembic downgrade 7a8b9c0d1e2f:f6a41746c0de --sql > downgrade_7a8b9c0d1e2f.sql
DATABASE_URL='mysql+aiomysql://arcreel:URL_ENCODED_PASSWORD@mysql:3306/arcreel?charset=utf8mb4' \
  alembic downgrade bcaaa615ff38:7a8b9c0d1e2f --sql > downgrade_bcaaa615ff38.sql
# Check dialect differences, type conversions, and downgrade/rollback paths
```

### Online (real MySQL, independent revision round trips + idempotence)

```text
export DATABASE_URL='mysql+aiomysql://arcreel:URL_ENCODED_PASSWORD@mysql:3306/arcreel?charset=utf8mb4'

# 7a8b9c0d1e2f independent round trip
alembic upgrade 7a8b9c0d1e2f
alembic downgrade 7a8b9c0d1e2f~1      # Return to this migration's down_revision
alembic upgrade 7a8b9c0d1e2f          # Idempotent upgrade: schema matches first upgrade

# bcaaa615ff38 independent round trip
alembic upgrade bcaaa615ff38
alembic downgrade bcaaa615ff38~1      # Return to this migration's down_revision
alembic upgrade bcaaa615ff38          # Idempotent upgrade

# Head idempotence: upgrading an already-head DB has no side effects
alembic upgrade head                  # First run
alembic upgrade head                  # Second run (must be no-op)
# Compare schemas after both upgrade head calls (use alembic current + DDL dumps)
```

> `f6a41746c0de` above is a parent-revision placeholder at research time; `b3f9c07ae214` is PR #1985's current head. Replace both downgrade commands with actual migration `down_revision` values when implementing (check via `grep down_revision alembic/versions/<file>.py`).

| Migration | Revision | Changes | Validation |
|---|---|---|---|
| 7a8b9c0d1e2f | unify_partial_indexes_and_string_lengths | Generated columns + field lengths | MySQL: valid generated-column syntax/equivalent unique semantics; PG/SQLite: intact partial indexes |
| bcaaa615ff38 | mysql_varchar_to_datetime | VARCHAR→DATETIME(6) | MySQL: precision-preserving conversion; PG/SQLite: no-op |

| Mode | Definition | Acceptance |
|---|---|---|
|| offline | Render SQL without connections | Valid upgrade/downgrade syntax and reversible downgrade paths |
|| online | Execute against real MySQL | Every revision's `upgrade → downgrade → upgrade` succeeds with schemas matching first upgrades; after all round trips, two `upgrade head` calls leave identical schemas and second is no-op |
|| online (data) | Execute after representative legacy-data loading | Preserve semantics, such as coexistence of multiple inactive provider_credential records |


## Next steps

After converting all gaps into implementation work, split PRs engine → migrations → CI → documentation. First batch (VARCHAR→DATETIME / deployment templates / operations docs): #1985.
