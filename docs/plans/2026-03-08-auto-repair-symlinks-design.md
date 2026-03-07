# 设计文档：启动时自动修复 agent_runtime 软连接

**日期**：2026-03-08
**状态**：已批准

## 背景

每个视频项目目录下需要两条软连接，让 Claude Agent SDK 能发现 skill/agent 配置：

- `.claude` → `../../agent_runtime_profile/.claude`
- `CLAUDE.md` → `../../agent_runtime_profile/CLAUDE.md`

新建项目时由 `ProjectManager._create_claude_symlink()` 自动创建。但存量项目存在两类问题：

1. **缺失**：在引入 `agent_runtime_profile` 机制前创建的项目，从未有过软连接
2. **损坏**：软连接存在（`is_symlink()=True`）但目标不可达（`exists()=False`），例如 `agent_runtime_profile` 目录曾被删除重建

此外，`scripts/migrate_claude_symlinks.py` 中存在 bug：损坏的软连接会被 skip 而非修复。

## 目标

- 每次服务器启动时，自动确保所有项目的软连接正确
- 修复策略：损坏 → 删除重建；缺失 → 创建；正常 → 跳过
- 不影响任何现有 API 行为

## 架构改动

### 1. `lib/project_manager.py`

将 `_create_claude_symlink()` 升级为 `repair_claude_symlink(project_dir)`，处理三种状态：

```
对 .claude 和 CLAUDE.md 各自：
  - is_symlink() and not exists()  → unlink + symlink_to（修复损坏）
  - not exists() and not is_symlink() → symlink_to（补创缺失）
  - exists()                       → skip（正常，无论是软连接还是真实文件）
```

新增 `repair_all_symlinks() -> dict`，扫描 `projects/` 下所有非隐藏子目录，返回统计：

```python
{"repaired": int, "created": int, "skipped": int, "errors": int}
```

### 2. `server/app.py`

在 `lifespan` startup 的 `init_db()` 之后，插入调用：

```python
from lib.project_manager import ProjectManager
pm = ProjectManager(PROJECT_ROOT / "projects")
stats = pm.repair_all_symlinks()
logger.info("软连接修复完成: %s", stats)
```

### 3. `scripts/migrate_claude_symlinks.py`（顺手修复 bug）

将第 53 行 skip 逻辑改为：损坏软连接也纳入修复，保持脚本独立可用。

## 数据流

```
lifespan startup
  └── init_db()
  └── repair_all_symlinks()        ← 新增
        └── for each project_dir in projects/
              └── repair_claude_symlink(project_dir)
                    ├── .claude: 损坏 → unlink + symlink_to → repaired++
                    ├── .claude: 缺失 → symlink_to → created++
                    ├── .claude: 正常 → skipped++
                    └── CLAUDE.md: 同上
  └── assistant_service.startup()
  └── GenerationWorker.start()
```

## 影响范围

| 文件 | 改动类型 |
|------|---------|
| `lib/project_manager.py` | 升级 `_create_claude_symlink` → `repair_claude_symlink`，新增 `repair_all_symlinks` |
| `server/app.py` | lifespan startup 插入一行调用 |
| `scripts/migrate_claude_symlinks.py` | 修复 skip bug |

不影响任何现有 API 端点和测试。
