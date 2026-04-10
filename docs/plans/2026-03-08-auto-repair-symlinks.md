# Auto-Repair Agent Runtime Symlinks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically repair all broken or missing `.claude` / `CLAUDE.md` symlinks in project directories on server startup.

**Architecture:** Add `repair_claude_symlink()` and `repair_all_symlinks()` methods to `ProjectManager`, handling three states (broken/missing/normal); call `repair_all_symlinks()` in the lifespan startup in `server/app.py`; also fix the skip bug in `scripts/migrate_claude_symlinks.py`.

**Tech Stack:** Python 3.12, pathlib, FastAPI lifespan, pytest

---

### Task 1: Write failing tests for `repair_claude_symlink`

**Files:**
- Modify: `tests/test_project_manager_symlink.py`

**Step 1: Append two new test classes to the end of the test file**

```python
class TestRepairClaudeSymlink:
    def _make_env(self, tmp_path):
        """Create standard test environment: projects/ and agent_runtime_profile/"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()
        return pm, project_dir

    def test_repair_creates_missing_symlinks(self, tmp_path):
        """Should create missing symlinks."""
        pm, project_dir = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_symlink()

    def test_repair_fixes_broken_symlink(self, tmp_path):
        """Broken symlinks (is_symlink but not exists) should be deleted and recreated."""
        pm, project_dir = self._make_env(tmp_path)
        # Manually create a broken symlink pointing to a non-existent path
        broken = project_dir / ".claude"
        broken.symlink_to(Path("../../nonexistent/.claude"))
        assert broken.is_symlink() and not broken.exists()

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / ".claude").exists()

    def test_repair_skips_valid_symlink(self, tmp_path):
        """A correctly set symlink should not be modified (readlink value unchanged)."""
        pm, project_dir = self._make_env(tmp_path)
        # First create the correct symlink
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
        original_target = Path((project_dir / ".claude").readlink())

        pm.repair_claude_symlink(project_dir)

        assert Path((project_dir / ".claude").readlink()) == original_target

    def test_repair_skips_when_profile_missing(self, tmp_path):
        """Should silently skip without error when agent_runtime_profile does not exist."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()

        pm.repair_claude_symlink(project_dir)  # Should not raise

        assert not (project_dir / ".claude").exists()


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats(self, tmp_path):
        """repair_all_symlinks should return a dict with created/repaired/skipped/errors."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        # An old project without symlinks
        (projects_root / "old-proj").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert stats["created"] == 2  # one each for .claude and CLAUDE.md

    def test_repair_all_skips_hidden_dirs(self, tmp_path):
        """Directories starting with . should be skipped (e.g. the directory containing .arcreel.db)."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (tmp_path / "agent_runtime_profile" / ".claude").mkdir(parents=True)
        (tmp_path / "agent_runtime_profile" / "CLAUDE.md").write_text("prompt")
        (projects_root / ".hidden").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert stats["created"] == 0
```

**Step 2: Run tests to confirm all fail**

```bash
python -m pytest tests/test_project_manager_symlink.py::TestRepairClaudeSymlink tests/test_project_manager_symlink.py::TestRepairAllSymlinks -v
```

Expected: All FAIL with `AttributeError: 'ProjectManager' object has no attribute 'repair_claude_symlink'`

**Step 3: Commit failing tests**

```bash
git add tests/test_project_manager_symlink.py
git commit -m "test: add failing tests for repair_claude_symlink and repair_all_symlinks"
```

---

### Task 2: Implement `repair_claude_symlink` and `repair_all_symlinks`

**Files:**
- Modify: `lib/project_manager.py:145-173` (replace `_create_claude_symlink`)

**Step 1: Replace `_create_claude_symlink` method, add `repair_all_symlinks`**

Find `def _create_claude_symlink` at line 145 in `lib/project_manager.py` and replace the entire method with:

```python
def repair_claude_symlink(self, project_dir: Path) -> dict:
    """Repair the .claude and CLAUDE.md symlinks in a project directory.

    For each symlink:
    - Broken (is_symlink but not exists) → delete and recreate
    - Missing (not exists and not is_symlink) → create
    - Normal (exists) → skip

    Returns:
        {"created": int, "repaired": int, "skipped": int}
    """
    project_root = self.projects_root.parent
    profile_dir = project_root / "agent_runtime_profile"

    SYMLINKS = {
        ".claude": profile_dir / ".claude",
        "CLAUDE.md": profile_dir / "CLAUDE.md",
    }
    REL_TARGETS = {
        ".claude": Path("../../agent_runtime_profile/.claude"),
        "CLAUDE.md": Path("../../agent_runtime_profile/CLAUDE.md"),
    }

    stats = {"created": 0, "repaired": 0, "skipped": 0}
    for name, target_source in SYMLINKS.items():
        if not target_source.exists():
            continue
        symlink_path = project_dir / name
        if symlink_path.is_symlink() and not symlink_path.exists():
            # Broken symlink
            try:
                symlink_path.unlink()
                symlink_path.symlink_to(REL_TARGETS[name])
                stats["repaired"] += 1
            except OSError as e:
                logger.warning("Cannot repair %s symlink for project %s: %s", name, project_dir.name, e)
        elif not symlink_path.exists() and not symlink_path.is_symlink():
            # Missing
            try:
                symlink_path.symlink_to(REL_TARGETS[name])
                stats["created"] += 1
            except OSError as e:
                logger.warning("Cannot create %s symlink for project %s: %s", name, project_dir.name, e)
        else:
            stats["skipped"] += 1
    return stats

def repair_all_symlinks(self) -> dict:
    """Scan all project directories and repair symlinks.

    Returns:
        {"created": int, "repaired": int, "skipped": int, "errors": int}
    """
    totals = {"created": 0, "repaired": 0, "skipped": 0, "errors": 0}
    if not self.projects_root.exists():
        return totals
    for project_dir in sorted(self.projects_root.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        try:
            result = self.repair_claude_symlink(project_dir)
            for key in ("created", "repaired", "skipped"):
                totals[key] += result[key]
        except Exception as e:
            logger.warning("Error repairing symlinks for project %s: %s", project_dir.name, e)
            totals["errors"] += 1
    return totals
```

Also find the line calling `self._create_claude_symlink(project_dir)` in the `create_project` method (around line 141) and change it to:

```python
        self.repair_claude_symlink(project_dir)
```

**Step 2: Run tests to confirm they pass**

```bash
python -m pytest tests/test_project_manager_symlink.py -v
```

Expected: All PASS (4 existing + 6 new = 10 total)

**Step 3: Run all tests to confirm no regressions**

```bash
python -m pytest tests/ -x -q
```

Expected: All PASS

**Step 4: Commit**

```bash
git add lib/project_manager.py
git commit -m "feat: add repair_claude_symlink and repair_all_symlinks to ProjectManager"
```

---

### Task 3: Call repair in lifespan startup

**Files:**
- Modify: `server/app.py:47-71`

**Step 1: Insert call in the lifespan function**

Find line 54 in `server/app.py` (after `await init_db()`), insert:

```python
    # Repair agent_runtime symlinks for existing projects
    from lib.project_manager import ProjectManager
    _pm = ProjectManager(PROJECT_ROOT / "projects")
    _symlink_stats = _pm.repair_all_symlinks()
    if any(v > 0 for v in _symlink_stats.values()):
        logger.info("agent_runtime symlink repair complete: %s", _symlink_stats)
```

**Step 2: Verify app startup tests are not affected**

```bash
python -m pytest tests/test_app_module.py -v
```

Expected: All PASS

**Step 3: Run all tests**

```bash
python -m pytest tests/ -x -q
```

Expected: All PASS

**Step 4: Commit**

```bash
git add server/app.py
git commit -m "feat: repair agent_runtime symlinks on server startup"
```

---

### Task 4: Fix the skip bug in the migration script

**Files:**
- Modify: `scripts/migrate_claude_symlinks.py:47-63`

**Step 1: Fix the skip logic**

Find line 53:
```python
            if symlink_path.exists() or symlink_path.is_symlink():
```

Replace with the following logic (rewrite the for loop body at lines 46-62):

```python
            if symlink_path.is_symlink() and not symlink_path.exists():
                # Broken symlink
                if args.dry_run:
                    print(f"  WOULD REPAIR {project_dir.name}/{name} (broken symlink)")
                else:
                    symlink_path.unlink()
                    symlink_path.symlink_to(Path(rel_target))
                    print(f"  REPAIRED {project_dir.name}/{name} -> {rel_target}")
                created += 1
            elif symlink_path.exists():
                print(f"  SKIP {project_dir.name}/{name} (already exists)")
                skipped += 1
            else:
                # Missing
                if args.dry_run:
                    print(f"  WOULD CREATE {project_dir.name}/{name} -> {rel_target}")
                else:
                    symlink_path.symlink_to(Path(rel_target))
                    print(f"  CREATED {project_dir.name}/{name} -> {rel_target}")
                created += 1
```

**Step 2: Manually verify the script (dry-run)**

```bash
python scripts/migrate_claude_symlinks.py --dry-run
```

Expected: Shows SKIP status for existing projects, no errors

**Step 3: Commit**

```bash
git add scripts/migrate_claude_symlinks.py
git commit -m "fix: repair broken symlinks in migrate_claude_symlinks script"
```
