# Agent Runtime 双向隔离设计

## 背景

项目的 Claude Code 运行环境配置（`.claude/`、`CLAUDE.md`）混合了两种场景：

1. **智能体态**（WebUI 内嵌 Claude Agent SDK）：面向终端用户的视频创作助手
2. **开发态**（开发者本地 CLI）：面向开发者的编码助手

当前问题：
- `CLAUDE.md` 同时承担系统 Prompt 和开发指南，互相干扰
- 业务 Skills 和第三方开发 Skills 混在 `.claude/skills/`
- 智能体会加载开发者的 `CLAUDE.md`、`CLAUDE.local.md` 和开发态 Skills

## 实验验证结论

通过 Claude Agent SDK 实验验证了以下行为：

| 条件 | skill 发现 | CLAUDE.md 加载 |
|------|-----------|---------------|
| `setting_sources=[]` | 不发现任何 skill | 不加载 |
| `setting_sources=["project"]` + cwd 无 git | 只扫描 cwd | 只加载 cwd |
| `setting_sources=["project"]` + cwd 在 git repo 内 | 扫描 cwd + git root | 加载两级 |
| `setting_sources=["project"]` + cwd 有符号链接 `.claude/` | 符号链接目标被发现 | 加载 |
| `add_dirs` 参数 | 不参与 skill 发现 | 不参与 |

**关键推论**：Docker 部署环境无 git → `setting_sources=["project"]` 只扫描 cwd → 天然零泄漏。

## 设计方案

### 核心策略

- `setting_sources=["project"]`：保留 Skill 工具的原生发现能力
- Docker 无 git：天然隔离，cwd 只发现自身的 `.claude/`
- 符号链接：项目目录 `.claude/` → `agent_runtime_profile/.claude/`
- 系统 Prompt：从 `agent_runtime_profile/CLAUDE.md` 编程式加载

### 目录结构

```
agent_runtime_profile/                 # 智能体专用运行环境（新建）
├── CLAUDE.md                          # 智能体系统 Prompt
└── .claude/
    ├── skills/                        # 业务 Skills（从 .claude/skills/ 迁入）
    │   ├── generate-characters/
    │   ├── generate-clues/
    │   ├── generate-storyboard/
    │   ├── generate-video/
    │   ├── generate-script/
    │   ├── compose-video/
    │   ├── manga-workflow/
    │   └── edit-script-items/
    └── agents/                        # 业务 Agents（从 .claude/agents/ 迁入）
        ├── novel-to-narration-script.md
        └── novel-to-storyboard-script.md

.claude/                               # 回归纯开发态
├── commands/
├── settings.local.json
├── plans/
└── skills/                            # 仅第三方开发 skills（openspec-* 等）

CLAUDE.md                              # 瘦身为纯开发者 Codebase 指南
```

### SessionManager 改动

#### `_build_options()` 变更

```python
ClaudeAgentOptions(
    cwd=str(project_cwd),
    setting_sources=["project"],       # Docker 无 git，安全
    allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
    system_prompt=self._build_system_prompt(project_name),
    agents=self._load_agent_definitions(),  # 编程式加载（双保险）
    ...
)
```

#### `DEFAULT_ALLOWED_TOOLS` 修正

对齐 SDK 文档中的实际工具名：

```python
# 之前
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Read", "Write", "Edit", "MultiEdit",
    "Bash", "Grep", "Glob", "LS", "AskUserQuestion",
]

# 之后
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Bash", "Grep", "Glob", "AskUserQuestion",
]
```

变更说明：
- 新增 `Task`（子 agent 调度，SDK 实际工具名）
- 移除 `MultiEdit`、`LS`（SDK 文档中不存在）

#### `_PATH_TOOLS` 修正

```python
# 移除 LS
_PATH_TOOLS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}
```

#### `_READONLY_DIRS` / `_READONLY_FILES` 更新

```python
_READONLY_DIRS = [
    "docs", "lib", "agent_runtime_profile",
    "scripts",
]
# 移除 ".claude/skills", ".claude/agents", ".claude/plans"
# 新增 "agent_runtime_profile"

_READONLY_FILES = []
# 移除 "CLAUDE.md"（agent 不需要读取 git root 的开发文档）
```

#### `_build_system_prompt()` 改造

从 `agent_runtime_profile/CLAUDE.md` 读取基础 prompt（替代环境变量），再拼接项目上下文：

```python
def _build_system_prompt(self, project_name: str) -> str:
    # 1. 从 agent_runtime_profile/CLAUDE.md 加载基础 prompt
    profile_prompt_path = self.project_root / "agent_runtime_profile" / "CLAUDE.md"
    base_prompt = profile_prompt_path.read_text(encoding="utf-8")

    # 2. 拼接项目上下文（现有逻辑）
    ...
```

#### `_load_agent_definitions()` 新方法

扫描 `agent_runtime_profile/.claude/agents/*.md`，解析为 `dict[str, AgentDefinition]`。
作为双保险——即使 `setting_sources=["project"]` 未能自动发现 agents，编程式注入确保 agents 可用。

### 项目创建时符号链接

`ProjectManager` 创建新项目时，自动创建相对符号链接：

```python
# projects/{name}/.claude → ../../agent_runtime_profile/.claude
symlink_path = project_dir / ".claude"
target = Path("../../agent_runtime_profile/.claude")
symlink_path.symlink_to(target)
```

已有项目：提供迁移脚本，为缺少符号链接的项目补建。

### Dockerfile 更新

```dockerfile
# 之前
COPY .claude/skills/ .claude/skills/
COPY .claude/agents/ .claude/agents/

# 之后
COPY agent_runtime_profile/ agent_runtime_profile/
```

### CLAUDE.md 瘦身

当前 `CLAUDE.md`（git root）拆分为：

| 内容 | 去向 |
|------|------|
| 视频规格、音频规范 | `agent_runtime_profile/CLAUDE.md` |
| 内容模式（说书/剧集） | `agent_runtime_profile/CLAUDE.md` |
| 可用 Skills 列表 | `agent_runtime_profile/CLAUDE.md` |
| 工作流程（两种模式） | `agent_runtime_profile/CLAUDE.md` |
| 视频生成模式 | `agent_runtime_profile/CLAUDE.md` |
| 剧本核心字段 | `agent_runtime_profile/CLAUDE.md` |
| Veo 3.1 技术参考 | `agent_runtime_profile/CLAUDE.md` |
| project.json 结构 | `agent_runtime_profile/CLAUDE.md` |
| 项目目录结构 | `agent_runtime_profile/CLAUDE.md` |
| API 使用 | `agent_runtime_profile/CLAUDE.md` |
| 关键原则 | `agent_runtime_profile/CLAUDE.md` |
| 环境要求 | `agent_runtime_profile/CLAUDE.md` |
| API 后端配置 | `agent_runtime_profile/CLAUDE.md` |
| 重要总则（语言规范等） | 两边各保留相关部分 |

`CLAUDE.md`（git root）保留：
- 项目简介（一句话）
- 架构概览（引用 `CLAUDE.local.md`）
- 语言规范（回答用中文）

### 兼容性

- Skill 脚本的 `lib/` import 路径不变（Python path 不受文件位置影响）
- 前端 API 调用无需修改
- `generation_queue_client.py` 路径不变
- 现有项目需运行一次迁移脚本补建符号链接

## 隔离效果总结

### Docker 部署（生产环境）

| 资源 | 智能体是否可见 | 原因 |
|------|--------------|------|
| `agent_runtime_profile/CLAUDE.md` | 是（system_prompt 注入） | 编程式加载 |
| `agent_runtime_profile/.claude/skills/` | 是 | 符号链接 + setting_sources |
| `agent_runtime_profile/.claude/agents/` | 是 | 编程式 + 符号链接 |
| `CLAUDE.md`（git root） | 否 | 未打包进镜像 |
| `CLAUDE.local.md` | 否 | 未打包进镜像 |
| `.claude/skills/`（开发态） | 否 | 未打包进镜像 |
| 第三方 Skills | 否 | 未打包进镜像 |

### 本地开发（开发者 CLI）

| 资源 | 开发者是否可见 | 影响 |
|------|--------------|------|
| `CLAUDE.md`（瘦身后） | 是 | 纯开发指南，正确 |
| `.claude/skills/`（第三方） | 是 | 开发工具，正确 |
| `agent_runtime_profile/` | 可读但不自动加载 | 无干扰 |
| 业务 Skills | 不自动加载 | 无噪音 |
