# Agent Runtime 双向隔离 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将智能体运行环境与开发态 CLI 配置物理分离，实现 Docker 部署零泄漏。

**Architecture:** 创建 `agent_runtime_profile/` 目录存放智能体专用的 CLAUDE.md、skills、agents。项目创建时在 `projects/{name}/` 下创建指向 `agent_runtime_profile/.claude` 的符号链接。`SessionManager` 从 `agent_runtime_profile/CLAUDE.md` 加载系统 prompt，工具名对齐 SDK 文档。

**Tech Stack:** Python 3.12, Claude Agent SDK, FastAPI, pytest

---

### Task 1: 创建 agent_runtime_profile 目录结构并迁移 skills

**Files:**
- Create: `agent_runtime_profile/.claude/skills/` (directory tree)
- Create: `agent_runtime_profile/.claude/agents/` (directory tree)
- Move: `.claude/skills/{business-skills}/` → `agent_runtime_profile/.claude/skills/`
- Move: `.claude/agents/*.md` → `agent_runtime_profile/.claude/agents/`

**Step 1: 创建目录结构**

```bash
mkdir -p agent_runtime_profile/.claude/skills
mkdir -p agent_runtime_profile/.claude/agents
```

**Step 2: 迁移业务 skills**

```bash
# 业务 skills 列表：generate-characters, generate-clues, generate-storyboard,
# generate-video, generate-script, compose-video, manga-workflow, edit-script-items
for skill in generate-characters generate-clues generate-storyboard generate-video generate-script compose-video manga-workflow edit-script-items; do
    git mv ".claude/skills/$skill" "agent_runtime_profile/.claude/skills/$skill"
done
```

**Step 3: 迁移业务 agents**

```bash
git mv .claude/agents/novel-to-narration-script.md agent_runtime_profile/.claude/agents/
git mv .claude/agents/novel-to-storyboard-script.md agent_runtime_profile/.claude/agents/
```

**Step 4: 验证 .claude/ 只剩开发态内容**

```bash
ls .claude/skills/
# Expected: openspec-apply-change  openspec-archive-change  openspec-explore  openspec-propose
ls .claude/agents/
# Expected: (empty or directory removed)
```

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate business skills/agents to agent_runtime_profile/"
```

---

### Task 2: 创建 agent_runtime_profile/CLAUDE.md（智能体系统 Prompt）

**Files:**
- Create: `agent_runtime_profile/CLAUDE.md`
- Modify: `CLAUDE.md` (root, 瘦身)

**Step 1: 从当前 CLAUDE.md 拆分内容到 agent_runtime_profile/CLAUDE.md**

创建 `agent_runtime_profile/CLAUDE.md`，包含当前 `CLAUDE.md` 中的全部业务内容：

- 重要总则（语言规范、视频规格、音频规范、脚本调用、虚拟环境）
- 内容模式表格和详细说明
- 可用 Skills 列表
- 快速开始
- 工作流程（说书+画面模式、剧集动画模式）
- 视频生成模式（标准、断点续传、单场景、分段标记）
- 剧本核心字段
- Veo 3.1 技术参考
- 关键原则
- 环境要求
- API 后端配置
- API 使用
- 项目目录结构
- project.json 结构（含数据分层、写时同步 vs 读时计算、完整示例）
- 线索数据结构

注意：`agent_runtime_profile/CLAUDE.md` 中引用 skill 脚本路径需要更新为 `agent_runtime_profile/.claude/skills/{skill-name}/scripts/`。

**Step 2: 瘦身 root CLAUDE.md**

`CLAUDE.md`（git root）只保留：

```markdown
# AI 视频生成工作空间

你是一个专业的 AI 视频内容创作助手，帮助用户将小说转化为可发布的短视频内容。

## 语言规范
- **回答用户必须使用中文**

## 项目概述

这是 ArcReel 视频生成平台。详细架构和开发指南见 `CLAUDE.local.md`。

## 智能体运行环境

智能体专用配置（skills、agents、系统 prompt）位于 `agent_runtime_profile/` 目录，
与开发态 `.claude/` 物理分离。详见 `docs/plans/2026-03-06-agent-runtime-isolation-design.md`。
```

**Step 3: 验证两个 CLAUDE.md 内容不重叠**

手动检查：root CLAUDE.md 不包含视频规格、Skill 触发表、工作流程等业务内容。

**Step 4: Commit**

```bash
git add CLAUDE.md agent_runtime_profile/CLAUDE.md
git commit -m "refactor: split CLAUDE.md into dev guide + agent system prompt"
```

---

### Task 3: ProjectManager 创建项目时自动创建符号链接

**Files:**
- Modify: `lib/project_manager.py:106-126` (`create_project` method)
- Test: `tests/test_project_manager_symlink.py`

**Step 1: 写失败的测试**

创建 `tests/test_project_manager_symlink.py`：

```python
"""Tests for .claude symlink creation on project creation."""

from pathlib import Path
from lib.project_manager import ProjectManager


class TestProjectSymlink:
    def test_create_project_creates_claude_symlink(self, tmp_path):
        """New project should have .claude symlink pointing to agent_runtime_profile."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        # Create agent_runtime_profile structure
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude" / "skills"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / ".claude"
        assert symlink.is_symlink()
        target = symlink.resolve()
        expected = (tmp_path / "agent_runtime_profile" / ".claude").resolve()
        assert target == expected

    def test_create_project_symlink_is_relative(self, tmp_path):
        """Symlink should use relative path for portability."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / ".claude"
        link_target = Path(symlink.readlink())
        # Should be relative, not absolute
        assert not link_target.is_absolute()

    def test_create_project_skips_symlink_when_profile_missing(self, tmp_path):
        """If agent_runtime_profile doesn't exist, skip symlink (no error)."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        # No agent_runtime_profile created

        pm = ProjectManager(projects_root)
        project_dir = pm.create_project("test-proj")

        symlink = project_dir / ".claude"
        assert not symlink.exists()
```

**Step 2: 运行测试验证失败**

```bash
python -m pytest tests/test_project_manager_symlink.py -v
```

Expected: 3 FAIL（`create_project` 还没有符号链接逻辑）

**Step 3: 实现符号链接创建**

修改 `lib/project_manager.py` 的 `create_project` 方法，在创建子目录后添加：

```python
def create_project(self, name: str) -> Path:
    name = self.normalize_project_name(name)
    project_dir = self.projects_root / name

    if project_dir.exists():
        raise FileExistsError(f"项目 '{name}' 已存在")

    # 创建所有子目录
    for subdir in self.SUBDIRS:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # 创建 .claude 符号链接指向 agent_runtime_profile
    self._create_claude_symlink(project_dir)

    return project_dir

def _create_claude_symlink(self, project_dir: Path) -> None:
    """Create .claude symlink pointing to agent_runtime_profile/.claude."""
    # Resolve profile relative to projects_root parent (project_root)
    project_root = self.projects_root.parent
    profile_claude = project_root / "agent_runtime_profile" / ".claude"
    if not profile_claude.exists():
        return

    symlink_path = project_dir / ".claude"
    if symlink_path.exists() or symlink_path.is_symlink():
        return

    # Build relative path from project_dir to profile_claude
    try:
        rel = Path("../../agent_runtime_profile/.claude")
        symlink_path.symlink_to(rel)
    except OSError:
        pass  # Non-fatal: symlink creation may fail on some platforms
```

**Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_project_manager_symlink.py -v
```

Expected: 3 PASS

**Step 5: 运行现有 project_manager 测试确保无回归**

```bash
python -m pytest tests/test_project_manager.py -v
```

Expected: All PASS

**Step 6: Commit**

```bash
git add lib/project_manager.py tests/test_project_manager_symlink.py
git commit -m "feat: create .claude symlink on project creation for agent isolation"
```

---

### Task 4: SessionManager 工具名修正和常量更新

**Files:**
- Modify: `server/agent_runtime/session_manager.py:199-222`
- Modify: `tests/test_session_manager_project_scope.py`

**Step 1: 写失败的测试**

在 `tests/test_session_manager_project_scope.py` 中添加：

```python
class TestAllowedToolsAndConstants:
    @pytest.mark.asyncio
    async def test_default_allowed_tools_matches_sdk(self, tmp_path):
        """Verify allowed tools align with SDK documentation."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        tools = manager.DEFAULT_ALLOWED_TOOLS
        # SDK tools that must be present
        assert "Task" in tools
        assert "Skill" in tools
        assert "Read" in tools
        assert "AskUserQuestion" in tools
        # Removed non-SDK tools
        assert "MultiEdit" not in tools
        assert "LS" not in tools
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_path_tools_no_ls(self, tmp_path):
        """LS should not be in _PATH_TOOLS."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        assert "LS" not in manager._PATH_TOOLS
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_readonly_dirs_includes_agent_profile(self, tmp_path):
        """agent_runtime_profile should be in readonly dirs."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        assert "agent_runtime_profile" in manager._READONLY_DIRS
        assert ".claude/skills" not in manager._READONLY_DIRS
        await engine.dispose()
```

**Step 2: 运行测试验证失败**

```bash
python -m pytest tests/test_session_manager_project_scope.py::TestAllowedToolsAndConstants -v
```

Expected: FAIL

**Step 3: 更新常量**

修改 `server/agent_runtime/session_manager.py`：

```python
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Bash", "Grep", "Glob", "AskUserQuestion",
]

_PATH_TOOLS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}
_WRITE_TOOLS = {"Write", "Edit"}
_READONLY_DIRS = [
    "docs", "lib", "agent_runtime_profile",
    "scripts",
]
_READONLY_FILES: list[str] = []
```

**Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_session_manager_project_scope.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "fix: align DEFAULT_ALLOWED_TOOLS with SDK docs, update access control constants"
```

---

### Task 5: _build_system_prompt 从 agent_runtime_profile/CLAUDE.md 加载

**Files:**
- Modify: `server/agent_runtime/session_manager.py:256-307` (`_load_config`, `_build_system_prompt`)
- Modify: `tests/test_session_manager_project_scope.py`

**Step 1: 写失败的测试**

在 `tests/test_session_manager_project_scope.py` 中添加：

```python
class TestAgentProfileSystemPrompt:
    @pytest.mark.asyncio
    async def test_build_system_prompt_loads_from_agent_profile(self, tmp_path):
        """System prompt should load from agent_runtime_profile/CLAUDE.md."""
        # Create agent_runtime_profile/CLAUDE.md
        profile_dir = tmp_path / "agent_runtime_profile"
        profile_dir.mkdir()
        profile_claude = profile_dir / "CLAUDE.md"
        profile_claude.write_text("你是视频创作助手。使用中文回答。")

        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")
        assert "你是视频创作助手" in prompt
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_fallback_when_profile_missing(self, tmp_path):
        """Should use fallback prompt when agent_runtime_profile/CLAUDE.md missing."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")
        # Should not crash, should contain fallback
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_combines_profile_and_project_context(self, tmp_path):
        """Profile prompt + project.json context should both be present."""
        profile_dir = tmp_path / "agent_runtime_profile"
        profile_dir.mkdir()
        (profile_dir / "CLAUDE.md").write_text("你是视频创作助手。")

        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps({"title": "测试项目"}, ensure_ascii=False),
            encoding="utf-8",
        )

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")
        assert "你是视频创作助手" in prompt
        assert "项目标题：测试项目" in prompt
        await engine.dispose()
```

**Step 2: 运行测试验证失败**

```bash
python -m pytest tests/test_session_manager_project_scope.py::TestAgentProfileSystemPrompt -v
```

Expected: FAIL（当前从环境变量加载，不从文件加载）

**Step 3: 实现**

修改 `server/agent_runtime/session_manager.py`：

```python
FALLBACK_SYSTEM_PROMPT = (
    "你是视频项目协作助手。优先复用项目中的 Skills 与现有文件结构，避免擅自改写数据格式。"
)

def _load_config(self) -> None:
    """Load configuration from environment."""
    max_turns_env = os.environ.get("ASSISTANT_MAX_TURNS", "").strip()
    self.max_turns = int(max_turns_env) if max_turns_env else None

def _load_base_prompt(self) -> str:
    """Load base system prompt from agent_runtime_profile/CLAUDE.md."""
    profile_prompt = self.project_root / "agent_runtime_profile" / "CLAUDE.md"
    try:
        return profile_prompt.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Failed to load agent profile prompt: %s", exc)
        return self.FALLBACK_SYSTEM_PROMPT

def _build_system_prompt(self, project_name: str) -> str:
    """Build system prompt with project context injected."""
    base_prompt = self._load_base_prompt()

    try:
        project_cwd = self._resolve_project_cwd(project_name)
    except (ValueError, FileNotFoundError):
        return base_prompt

    project_json = project_cwd / "project.json"
    if not project_json.exists():
        return base_prompt

    # ... rest of existing logic, but using base_prompt instead of self.system_prompt
```

**Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_session_manager_project_scope.py -v
```

Expected: All PASS（包括旧测试——需要为旧测试创建 `agent_runtime_profile/CLAUDE.md`，或更新旧测试的 assert 使用 `FALLBACK_SYSTEM_PROMPT`）

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "feat: load system prompt from agent_runtime_profile/CLAUDE.md"
```

---

### Task 6: _load_agent_definitions 编程式加载 agents

**Files:**
- Modify: `server/agent_runtime/session_manager.py:325-358` (`_build_options`)
- Test: `tests/test_session_manager_project_scope.py`

**Step 1: 写失败的测试**

```python
class TestAgentDefinitions:
    @pytest.mark.asyncio
    async def test_load_agent_definitions_from_profile(self, tmp_path):
        """Should load agents from agent_runtime_profile/.claude/agents/."""
        agents_dir = tmp_path / "agent_runtime_profile" / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test-agent.md").write_text(
            "You are a test agent. Help the user with testing."
        )

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        agents = manager._load_agent_definitions()
        assert "test-agent" in agents
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_load_agent_definitions_empty_when_no_dir(self, tmp_path):
        """Should return empty dict when agents dir doesn't exist."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        agents = manager._load_agent_definitions()
        assert agents == {}
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_includes_agents(self, tmp_path):
        """_build_options should pass agents to ClaudeAgentOptions."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        agents_dir = tmp_path / "agent_runtime_profile" / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "my-agent.md").write_text("Agent prompt")

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        with patch("server.agent_runtime.session_manager.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.session_manager.ClaudeAgentOptions",
                _FakeOptions,
            ):
                options = manager._build_options("demo")

        assert "agents" in options.kwargs
        assert "my-agent" in options.kwargs["agents"]
        await engine.dispose()
```

**Step 2: 运行测试验证失败**

```bash
python -m pytest tests/test_session_manager_project_scope.py::TestAgentDefinitions -v
```

Expected: FAIL

**Step 3: 实现**

在 `session_manager.py` 中添加：

```python
def _load_agent_definitions(self) -> dict[str, Any]:
    """Load agent definitions from agent_runtime_profile/.claude/agents/."""
    agents_dir = self.project_root / "agent_runtime_profile" / ".claude" / "agents"
    if not agents_dir.exists() or not agents_dir.is_dir():
        return {}

    try:
        from claude_agent_sdk import AgentDefinition
    except ImportError:
        AgentDefinition = None

    agents: dict[str, Any] = {}
    for md_file in sorted(agents_dir.glob("*.md")):
        agent_name = md_file.stem
        try:
            prompt = md_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not prompt:
            continue

        if AgentDefinition is not None:
            agents[agent_name] = AgentDefinition(
                description=f"Agent: {agent_name}",
                prompt=prompt,
            )
        else:
            agents[agent_name] = {
                "description": f"Agent: {agent_name}",
                "prompt": prompt,
            }

    return agents
```

修改 `_build_options` 添加 `agents=self._load_agent_definitions()`。

**Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_session_manager_project_scope.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "feat: programmatic agent loading from agent_runtime_profile"
```

---

### Task 7: AssistantService.list_available_skills 更新

**Files:**
- Modify: `server/agent_runtime/service.py:743-787` (`list_available_skills`)
- Test: `tests/test_assistant_service_skills.py`

**Step 1: 写失败的测试**

创建 `tests/test_assistant_service_skills.py`：

```python
"""Tests for AssistantService.list_available_skills with agent_runtime_profile."""

from pathlib import Path
from unittest.mock import patch

from server.agent_runtime.service import AssistantService


class TestListAvailableSkills:
    def test_lists_skills_from_agent_runtime_profile(self, tmp_path):
        """Should scan agent_runtime_profile/.claude/skills/ instead of .claude/skills/."""
        # Create agent_runtime_profile skill
        skill_dir = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\n---\n"
        )

        # Create a dev-only skill in .claude/skills/ (should NOT appear)
        dev_skill = tmp_path / ".claude" / "skills" / "dev-tool"
        dev_skill.mkdir(parents=True)
        (dev_skill / "SKILL.md").write_text(
            "---\nname: dev-tool\ndescription: Dev only\n---\n"
        )

        with patch.object(AssistantService, '__init__', lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            service.project_root = tmp_path
            from lib.project_manager import ProjectManager
            service.pm = ProjectManager(tmp_path / "projects")

        skills = service.list_available_skills()
        names = [s["name"] for s in skills]
        assert "test-skill" in names
        assert "dev-tool" not in names
```

**Step 2: 运行测试验证失败**

```bash
python -m pytest tests/test_assistant_service_skills.py -v
```

Expected: FAIL（当前扫描 `.claude/skills/`）

**Step 3: 实现**

修改 `service.py` 的 `list_available_skills`：

```python
def list_available_skills(self, project_name: Optional[str] = None) -> list[dict[str, str]]:
    """List available skills from agent_runtime_profile."""
    if project_name:
        self.pm.get_project_path(project_name)

    skills_root = self.project_root / "agent_runtime_profile" / ".claude" / "skills"

    skills: list[dict[str, str]] = []
    if not skills_root.exists() or not skills_root.is_dir():
        return skills

    try:
        directories = sorted(skills_root.iterdir())
    except OSError:
        return skills

    for skill_dir in directories:
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            metadata = self._load_skill_metadata(skill_file, skill_dir.name)
        except OSError:
            continue

        skills.append({
            "name": metadata["name"],
            "description": metadata["description"],
            "scope": "agent",
            "path": str(skill_file),
        })

    return skills
```

**Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_assistant_service_skills.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/service.py tests/test_assistant_service_skills.py
git commit -m "refactor: list_available_skills scans agent_runtime_profile"
```

---

### Task 8: Dockerfile 更新

**Files:**
- Modify: `Dockerfile:45-46`

**Step 1: 更新 COPY 指令**

将：
```dockerfile
COPY .claude/skills/ .claude/skills/
COPY .claude/agents/ .claude/agents/
```

替换为：
```dockerfile
COPY agent_runtime_profile/ agent_runtime_profile/
```

**Step 2: 验证构建**

```bash
docker build -t arcreel-test --target production . 2>&1 | tail -5
```

Expected: build succeeds

**Step 3: Commit**

```bash
git add Dockerfile
git commit -m "build: copy agent_runtime_profile instead of .claude in Dockerfile"
```

---

### Task 9: 为已有项目补建符号链接的迁移脚本

**Files:**
- Create: `scripts/migrate_claude_symlinks.py`

**Step 1: 创建迁移脚本**

```python
#!/usr/bin/env python3
"""
Migrate existing projects to use .claude symlinks.

Creates .claude -> ../../agent_runtime_profile/.claude symlinks
for projects that don't have them yet.

Usage:
    python scripts/migrate_claude_symlinks.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Create .claude symlinks for existing projects")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    projects_dir = project_root / "projects"
    profile_claude = project_root / "agent_runtime_profile" / ".claude"

    if not profile_claude.exists():
        print(f"ERROR: {profile_claude} does not exist")
        sys.exit(1)

    if not projects_dir.exists():
        print("No projects directory found")
        return

    created = 0
    skipped = 0
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        symlink_path = project_dir / ".claude"

        if symlink_path.exists() or symlink_path.is_symlink():
            print(f"  SKIP {project_dir.name} (already has .claude)")
            skipped += 1
            continue

        rel_target = Path("../../agent_runtime_profile/.claude")
        if args.dry_run:
            print(f"  WOULD CREATE {project_dir.name}/.claude -> {rel_target}")
        else:
            symlink_path.symlink_to(rel_target)
            print(f"  CREATED {project_dir.name}/.claude -> {rel_target}")
        created += 1

    action = "Would create" if args.dry_run else "Created"
    print(f"\n{action} {created} symlink(s), skipped {skipped}")


if __name__ == "__main__":
    main()
```

**Step 2: 测试 dry-run**

```bash
python scripts/migrate_claude_symlinks.py --dry-run
```

Expected: Lists projects that would get symlinks

**Step 3: 运行迁移**

```bash
python scripts/migrate_claude_symlinks.py
```

**Step 4: Commit**

```bash
git add scripts/migrate_claude_symlinks.py
git commit -m "feat: migration script for .claude symlinks in existing projects"
```

---

### Task 10: 清理实验脚本和最终验证

**Files:**
- Delete: `scripts/test_add_dirs_isolation.py`

**Step 1: 删除实验脚本**

```bash
rm scripts/test_add_dirs_isolation.py
```

**Step 2: 运行全部测试**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All PASS

**Step 3: 验证 .gitignore 或 .dockerignore**

确认 `agent_runtime_profile/.claude/` 不会被 gitignore 忽略（`.claude/` 模式可能匹配子目录）。

```bash
git status agent_runtime_profile/
```

Expected: 新文件可被 git 追踪

**Step 4: 最终 commit**

```bash
git add -A
git commit -m "chore: cleanup experiment script, finalize agent runtime isolation"
```
