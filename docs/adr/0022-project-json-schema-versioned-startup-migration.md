---
status: accepted
---

# File-level schema versions for project.json with incremental, idempotent startup migration

> This ADR originally delegated recovery from a forcibly killed process during the directory-swap window to a startup scan (the runner claimed rollback directories before iterating projects and renamed them back when the project directory was missing). That recovery path has been removed: a forced kill is among the exceptional conditions listed under "Defend only against normally reachable scenarios" in `docs/standards/general.md`, so no fallback is added for it. It also does not meet that standard's admission conditions: claiming introduces a branch beyond the single boundary. For manual recovery at the site, see the deployment documentation's "项目结构迁移" section.

Leaving files unversioned and accommodating old fields immediately on read has no migration cost, but retains old field shapes indefinitely and accumulates branches in read paths. Introduce a top-level `schema_version` in project.json and a `lib/project/project_migrations` registry. At startup, scan projects and run pure-function migrators incrementally (back up before migration, write back atomically, and cascade script migrations), isolating individual project failures without interrupting startup. Explicit versioning buys convergence of data shapes and one-time rewriting, preferable to indefinitely accumulating compatibility branches in read paths.

## Consequences

- This adds complexity for backups / atomic writes / failure isolation / idempotence, and migrations irreversibly rewrite files in place (for example, v0→v1 splits clue into scene/prop and removes importance; v1→v2 normalizes provider).
- The same migration chain runs beyond startup: the project-archive **import** path also reuses `migrate_project_dir`, because the startup runner covers only projects present at startup; old archives imported afterward need the complete chain at the import entry point.
- Migrations cascading across multiple files cannot rely solely on single-file atomic writes to `project.json`. Schema v6 first rewrites asset names, script/draft references, media, and version history in a sibling staging tree, replacing the whole directory only after everything succeeds. Failure at any stage preserves the original project tree.
- Directory replacement consists of two renames (original directory → `.<项目名>.v6-rollback-<hex>`, staging tree `.<项目名>.v6-<hex>` → project directory). On an in-process migration failure, rename the original directory back. If the process is forcibly killed during the window, the project directory is absent and the rollback directory remains; recovery is manual, by renaming the rollback directory to the project name, without automatic restoration.
- A single module generates and parses both intermediate-directory names, with matching suffix structures; separating naming from parsing would make cleanup silently stop working.
- When the project directory exists, the rollback directory is only a backup and is cleaned up along with leftover staging trees under the age gate for versioned backups. Do not clean it up while the project directory is absent: it is the only project data. This constraint prevents cleanup code from deleting data itself, rather than defending against forced kills.
- The staging tree copies the entire project directory, so v5→v6 includes a read-only disk-capacity preflight based on project size. The runner performs the migrator's read-only preflight before writing a versioned backup; rejection by the preflight leaves every byte of the project directory untouched.
