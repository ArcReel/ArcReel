# Auto-Repair Agent Runtime Symlinks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 服务器启动时自动修复所有项目目录中损坏或缺失的 `.claude` / `CLAUDE.md` 软连接。

**Architecture:** 在 `ProjectManager` 中新增 `repair_claude_symlink()` 和 `repair_all_symlinks()` 方法，处理三种状态（损坏/缺失/正常）；在 `server/app.py` 的 lifespan startup 中调用 `repair_all_symlinks()`；同时修复 `scripts/migrate_claude_symlinks.py` 的 skip bug。

**Tech Stack:** Python 3.12, pathlib, FastAPI lifespan, pytest

---

### Task 1: 为 `repair_claude_symlink` 编写失败测试

**Files:**
- Modify: `tests/test_project_manager_symlink.py`

**Step 1: 在测试文件末尾追加两个新测试类**

```python
class TestRepairClaudeSymlink:
    def _make_env(self, tmp_path):
        """创建标准测试环境：projects/ 和 agent_runtime_profile/"""
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
        """缺失软连接时应新建。"""
        pm, project_dir = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_symlink()

    def test_repair_fixes_broken_symlink(self, tmp_path):
        """损坏的软连接（is_symlink but not exists）应被删除并重建。"""
        pm, project_dir = self._make_env(tmp_path)
        # 手动创建一个指向不存在路径的损坏软连接
        broken = project_dir / ".claude"
        broken.symlink_to(Path("../../nonexistent/.claude"))
        assert broken.is_symlink() and not broken.exists()

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / ".claude").exists()

    def test_repair_skips_valid_symlink(self, tmp_path):
        """已正确的软连接不应被修改（readlink 值不变）。"""
        pm, project_dir = self._make_env(tmp_path)
        # 先建好正确软连接
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
        original_target = Path((project_dir / ".claude").readlink())

        pm.repair_claude_symlink(project_dir)

        assert Path((project_dir / ".claude").readlink()) == original_target

    def test_repair_skips_when_profile_missing(self, tmp_path):
        """agent_runtime_profile 不存在时静默跳过，不报错。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()

        pm.repair_claude_symlink(project_dir)  # 不应抛异常

        assert not (project_dir / ".claude").exists()


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats(self, tmp_path):
        """repair_all_symlinks 应返回含 created/repaired/skipped/errors 的字典。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        # 一个无软连接的老项目
        (projects_root / "old-proj").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert stats["created"] == 2  # .claude 和 CLAUDE.md 各一条

    def test_repair_all_skips_hidden_dirs(self, tmp_path):
        """以 . 开头的目录应跳过（如 .arcreel.db 所在目录）。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (tmp_path / "agent_runtime_profile" / ".claude").mkdir(parents=True)
        (tmp_path / "agent_runtime_profile" / "CLAUDE.md").write_text("prompt")
        (projects_root / ".hidden").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert stats["created"] == 0
```

**Step 2: 运行测试，确认全部失败**

```bash
python -m pytest tests/test_project_manager_symlink.py::TestRepairClaudeSymlink tests/test_project_manager_symlink.py::TestRepairAllSymlinks -v
```

期望：全部 FAIL，报 `AttributeError: 'ProjectManager' object has no attribute 'repair_claude_symlink'`

**Step 3: Commit 失败测试**

```bash
git add tests/test_project_manager_symlink.py
git commit -m "test: add failing tests for repair_claude_symlink and repair_all_symlinks"
```

---

### Task 2: 实现 `repair_claude_symlink` 和 `repair_all_symlinks`

**Files:**
- Modify: `lib/project_manager.py:145-173`（替换 `_create_claude_symlink`）

**Step 1: 替换 `_create_claude_symlink` 方法，新增 `repair_all_symlinks`**

找到 `lib/project_manager.py` 第 145 行的 `def _create_claude_symlink`，将整个方法替换为：

```python
def repair_claude_symlink(self, project_dir: Path) -> dict:
    """修复项目目录的 .claude 和 CLAUDE.md 软连接。

    对每条软连接执行：
    - 损坏（is_symlink but not exists）→ 删除并重建
    - 缺失（not exists and not is_symlink）→ 创建
    - 正常（exists）→ 跳过

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
            # 损坏的软连接
            try:
                symlink_path.unlink()
                symlink_path.symlink_to(REL_TARGETS[name])
                stats["repaired"] += 1
            except OSError as e:
                logger.warning("无法修复项目 %s 的 %s 符号链接: %s", project_dir.name, name, e)
        elif not symlink_path.exists() and not symlink_path.is_symlink():
            # 缺失
            try:
                symlink_path.symlink_to(REL_TARGETS[name])
                stats["created"] += 1
            except OSError as e:
                logger.warning("无法为项目 %s 创建 %s 符号链接: %s", project_dir.name, name, e)
        else:
            stats["skipped"] += 1
    return stats

def repair_all_symlinks(self) -> dict:
    """扫描所有项目目录，修复软连接。

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
            logger.warning("修复项目 %s 软连接时出错: %s", project_dir.name, e)
            totals["errors"] += 1
    return totals
```

同时找到 `create_project` 方法中调用 `self._create_claude_symlink(project_dir)` 的那行（约第 141 行），将其改为：

```python
        self.repair_claude_symlink(project_dir)
```

**Step 2: 运行测试，确认通过**

```bash
python -m pytest tests/test_project_manager_symlink.py -v
```

期望：全部 PASS（包含原有 4 个 + 新增 6 个，共 10 个）

**Step 3: 运行全量测试，确认无回归**

```bash
python -m pytest tests/ -x -q
```

期望：全部 PASS

**Step 4: Commit**

```bash
git add lib/project_manager.py
git commit -m "feat: add repair_claude_symlink and repair_all_symlinks to ProjectManager"
```

---

### Task 3: 在 lifespan startup 中调用修复

**Files:**
- Modify: `server/app.py:47-71`

**Step 1: 在 lifespan 函数中插入调用**

找到 `server/app.py` 第 54 行（`await init_db()` 之后），插入：

```python
    # 修复存量项目的 agent_runtime 软连接
    from lib.project_manager import ProjectManager
    _pm = ProjectManager(PROJECT_ROOT / "projects")
    _symlink_stats = _pm.repair_all_symlinks()
    if any(v > 0 for v in _symlink_stats.values()):
        logger.info("agent_runtime 软连接修复完成: %s", _symlink_stats)
```

**Step 2: 验证 app 启动测试不受影响**

```bash
python -m pytest tests/test_app_module.py -v
```

期望：全部 PASS

**Step 3: 运行全量测试**

```bash
python -m pytest tests/ -x -q
```

期望：全部 PASS

**Step 4: Commit**

```bash
git add server/app.py
git commit -m "feat: repair agent_runtime symlinks on server startup"
```

---

### Task 4: 修复迁移脚本的 skip bug

**Files:**
- Modify: `scripts/migrate_claude_symlinks.py:47-63`

**Step 1: 修复 skip 逻辑**

找到第 53 行：
```python
            if symlink_path.exists() or symlink_path.is_symlink():
```

替换为以下逻辑（重写 46-62 行的 for 循环体）：

```python
            if symlink_path.is_symlink() and not symlink_path.exists():
                # 损坏的软连接
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
                # 缺失
                if args.dry_run:
                    print(f"  WOULD CREATE {project_dir.name}/{name} -> {rel_target}")
                else:
                    symlink_path.symlink_to(Path(rel_target))
                    print(f"  CREATED {project_dir.name}/{name} -> {rel_target}")
                created += 1
```

**Step 2: 手动验证脚本（dry-run）**

```bash
python scripts/migrate_claude_symlinks.py --dry-run
```

期望：输出现有项目的 SKIP 状态，无报错

**Step 3: Commit**

```bash
git add scripts/migrate_claude_symlinks.py
git commit -m "fix: repair broken symlinks in migrate_claude_symlinks script"
```
