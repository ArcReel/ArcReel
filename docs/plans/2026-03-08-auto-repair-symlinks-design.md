# Design: Auto-Repair agent_runtime Symlinks on Startup

**Date**: 2026-03-08
**Status**: Approved

## Background

Each video project directory needs two symlinks so the Claude Agent SDK can discover skill/agent configuration:

- `.claude` → `../../agent_runtime_profile/.claude`
- `CLAUDE.md` → `../../agent_runtime_profile/CLAUDE.md`

`ProjectManager._create_claude_symlink()` creates these automatically when a new project is created. However, existing projects have two types of issues:

1. **Missing**: Projects created before the `agent_runtime_profile` mechanism was introduced have never had symlinks
2. **Broken**: Symlinks exist (`is_symlink()=True`) but the target is unreachable (`exists()=False`), for example when the `agent_runtime_profile` directory was deleted and recreated

Additionally, `scripts/migrate_claude_symlinks.py` has a bug: broken symlinks are skipped instead of repaired.

## Goal

- On every server startup, automatically ensure all project symlinks are correct
- Repair strategy: broken → delete and recreate; missing → create; normal → skip
- Does not affect any existing API behavior

## Architecture Changes

### 1. `lib/project_manager.py`

Upgrade `_create_claude_symlink()` to `repair_claude_symlink(project_dir)`, handling three states:

```
For each of .claude and CLAUDE.md:
  - is_symlink() and not exists()  → unlink + symlink_to (repair broken)
  - not exists() and not is_symlink() → symlink_to (create missing)
  - exists()                       → skip (normal, whether symlink or real file)
```

Add `repair_all_symlinks() -> dict`, scans all non-hidden subdirectories under `projects/`, returns statistics:

```python
{"repaired": int, "created": int, "skipped": int, "errors": int}
```

### 2. `server/app.py`

After `init_db()` in the `lifespan` startup, insert the following call:

```python
from lib.project_manager import ProjectManager
pm = ProjectManager(PROJECT_ROOT / "projects")
stats = pm.repair_all_symlinks()
logger.info("Symlink repair complete: %s", stats)
```

### 3. `scripts/migrate_claude_symlinks.py` (fix bug while at it)

Change the skip logic on line 53 so that broken symlinks are also repaired, keeping the script independently usable.

## Data Flow

```
lifespan startup
  └── init_db()
  └── repair_all_symlinks()        ← new
        └── for each project_dir in projects/
              └── repair_claude_symlink(project_dir)
                    ├── .claude: broken → unlink + symlink_to → repaired++
                    ├── .claude: missing → symlink_to → created++
                    ├── .claude: normal → skipped++
                    └── CLAUDE.md: same
  └── assistant_service.startup()
  └── GenerationWorker.start()
```

## Impact Scope

| File | Change Type |
|------|---------|
| `lib/project_manager.py` | Upgrade `_create_claude_symlink` → `repair_claude_symlink`, add `repair_all_symlinks` |
| `server/app.py` | Insert one-line call in lifespan startup |
| `scripts/migrate_claude_symlinks.py` | Fix skip bug |

Does not affect any existing API endpoints or tests.
