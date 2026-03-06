# 权限控制优化设计

## 目标

将 Agent Runtime 的权限控制从自定义 Hook + Bash 全放行迁移到 **SDK 声明式规则 + 简化 Hook** 体系，实现：

1. **Bash 白名单机制**：从全部自动放行变为精确到路径的白名单，默认拒绝
2. **声明式规则替代静态配置**：用 `settings.json` 的 `deny` 规则替代代码中的 `_READONLY_DIRS` / `_READONLY_FILES`
3. **简化 Hook 代码**：Hook 只保留动态的"项目范围"检查，删除约 60 行静态配置代码
4. **canUseTool 兜底拒绝**：未匹配任何规则的工具调用一律拒绝

## 当前问题

### Bash 全放行（最大安全隐患）

`DEFAULT_ALLOWED_TOOLS` 包含 `Bash`，意味着所有 Bash 命令自动放行，无任何约束。
Agent 可以执行 `curl`、`wget`、`pip install` 等危险命令。

### 自定义 Hook 承担过多职责

`_build_file_access_hook` 同时处理：
- 项目范围检查（动态，依赖 per-session 的 `project_cwd`）
- 只读目录检查（静态，可用 settings.json 替代）

### canUseTool 全部放行

`_can_use_tool` 回调对非 `AskUserQuestion` 的工具一律返回 `PermissionResultAllow`，
相当于绕过了所有未匹配的权限检查。

### 未使用 settings.json

`agent_runtime_profile/.claude/` 下没有 `settings.json`，完全不使用 SDK 的声明式权限规则。

## 设计方案

### 权限评估流程

```
工具调用
    │
    ▼
① PreToolUse Hook ←── 第一道防线，对所有工具生效（包括 auto-approved）
    │                   检查 Read/Write/Edit/Glob/Grep 的路径是否在
    │                   project_cwd（写）或 project_root（读）内
    │                   路径非法 → deny，流程终止
    ▼
② settings.json deny 规则 ←── Read(//app/.env), Edit(//app/docs/**) 等
    │                          匹配 deny → 拒绝，流程终止
    ▼
③ Permission mode (default) ←── 不处理，继续
    │
    ▼
④ settings.json allow 规则 ←── Read, Grep, Glob, Bash(python .../script.py *) 等
    │                           匹配 → 自动放行，流程终止
    ▼
⑤ canUseTool 回调 ←── 只有前面都没匹配的才到这里
    │                   AskUserQuestion → 异步用户交互
    │                   其他 → PermissionResultDeny("未授权的工具调用")
    ▼
   拒绝
```

### 1. 创建 settings.json

**文件**：`agent_runtime_profile/.claude/settings.json`

```json
{
  "permissions": {
    "deny": [
      "Edit(//app/docs/**)",
      "Edit(//app/lib/**)",
      "Edit(//app/agent_runtime_profile/**)",
      "Edit(//app/scripts/**)",
      "Edit(//app/alembic/**)",
      "Read(//app/.env)",
      "Read(//app/.env.*)",
      "Read(//app/vertex_keys/**)"
    ],
    "allow": [
      "Bash(python .claude/skills/generate-video/scripts/generate_video.py *)",
      "Bash(python .claude/skills/generate-storyboard/scripts/generate_storyboard.py *)",
      "Bash(python .claude/skills/generate-characters/scripts/generate_character.py *)",
      "Bash(python .claude/skills/generate-clues/scripts/generate_clue.py *)",
      "Bash(python .claude/skills/generate-script/scripts/generate_script.py *)",
      "Bash(python .claude/skills/compose-video/scripts/compose_video.py *)",
      "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py *)",
      "Bash(ffmpeg *)",
      "Bash(ffprobe *)",
      "Read",
      "Grep",
      "Glob"
    ]
  }
}
```

**设计决策**：

- **Bash 白名单**：只允许 Skill 的 Python 脚本和 ffmpeg/ffprobe。
  文件操作（ls、rm、cp、mv 等）不通过 Bash，而是使用 SDK 内置工具
  （Read/Write/Edit/Glob/Grep），这些工具经过 Hook 路径检查。
- **deny 规则**：保护只读目录（docs、lib 等）和敏感文件（.env、vertex_keys）。
  使用 `//app/` 绝对路径前缀（Docker 内固定路径）。
- **allow 规则**：Python 脚本路径精确到 `.claude/skills/<skill>/scripts/<script>.py`
  （相对于 agent cwd，通过 symlink 解析到 `agent_runtime_profile/.claude/`）。
- **Read/Grep/Glob 全局 allow**：只读工具自动放行，由 Hook（项目范围）和 deny
  规则（敏感文件）双重保护。

### 2. 修改 DEFAULT_ALLOWED_TOOLS

```python
# 修改前
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Bash", "Grep", "Glob", "AskUserQuestion",
]

# 修改后 — 移除 Bash（由 settings.json 白名单控制）
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Grep", "Glob", "AskUserQuestion",
]
```

### 3. 修改 canUseTool 回调

```python
# 修改前 — 全部放行
async def _can_use_tool(tool_name, input_data, _context):
    if normalized_tool == "askuserquestion":
        return await self._handle_ask_user_question(...)
    return PermissionResultAllow(updated_input=input_data)

# 修改后 — 默认拒绝（白名单兜底）
async def _can_use_tool(tool_name, input_data, _context):
    if normalized_tool == "askuserquestion":
        return await self._handle_ask_user_question(...)
    return PermissionResultDeny(message="未授权的工具调用")
```

### 4. 简化 Hook

**删除**：
- `_READONLY_DIRS` 常量
- `_READONLY_FILES` 常量
- `_check_file_access()` 方法
- `_deny_path_access()` 方法

**简化 `_is_path_allowed()`**：

```python
_PATH_TOOLS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}
_WRITE_TOOLS = {"Write", "Edit"}

def _is_path_allowed(self, file_path: str, tool_name: str, project_cwd: Path) -> bool:
    try:
        p = Path(file_path)
        resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
    except (ValueError, OSError):
        return False

    # 1. Within project directory — full access (read + write)
    if resolved.is_relative_to(project_cwd):
        return True

    # 2. Write tools: only project directory allowed
    if tool_name in self._WRITE_TOOLS:
        return False

    # 3. Read tools: allow entire project_root for shared resources
    #    Sensitive files protected by settings.json deny rules
    if resolved.is_relative_to(self.project_root):
        return True

    return False
```

**变化**：
- 移除 `_READONLY_DIRS` 循环 → 改为允许读取整个 `project_root`（`/app/`）
- 敏感文件由 settings.json deny 规则保护（deny 在权限评估中优先于 Hook 的 allow）
- 写操作仍严格限制在 `project_cwd` 内

### 5. 本地开发环境

settings.json 中的 `//app/` 绝对路径在本地开发环境（macOS）下不匹配，
deny 规则不生效。这是可接受的——开发环境由 `.claude/settings.local.json` 独立控制。

建议同步更新 `.claude/settings.local.json` 使用新的规则语法
（移除已废弃的 `:*` 后缀）。

## 影响分析

### 变更文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `agent_runtime_profile/.claude/settings.json` | 新建 | 声明式权限规则 |
| `server/agent_runtime/session_manager.py` | 修改 | 简化 Hook、修改 DEFAULT_ALLOWED_TOOLS、canUseTool 兜底拒绝 |
| `tests/test_session_manager_project_scope.py` | 修改 | 适配新的 Hook 逻辑 |
| `tests/test_session_manager_more.py` | 修改 | 适配 canUseTool 行为变更 |

### 安全性提升

| 维度 | 修改前 | 修改后 |
|------|--------|--------|
| Bash 命令 | 全部自动放行 | 白名单放行（精确到脚本路径） |
| 只读目录 | 自定义 Hook 检查 | settings.json deny + Hook |
| 敏感文件 | 无保护 | settings.json deny 规则 |
| canUseTool 兜底 | 全部放行 | 默认拒绝 |
| 文件操作 | Bash + SDK 工具 | 仅 SDK 工具（有路径检查） |

### 风险

- **Bash 白名单过紧**：如果后续新增 Skill 脚本，需要同步更新 settings.json 的 allow 规则
- **本地 deny 规则不生效**：开发环境下 `//app/` 路径不匹配，依赖 settings.local.json

## 未来扩展

### Sandboxing（下一阶段）

当前方案不包含 OS 级 Sandboxing。未来可在此基础上启用：

```json
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    "filesystem": {
      "allowWrite": ["//tmp"]
    },
    "network": {
      "allowedDomains": ["*.googleapis.com"]
    }
  }
}
```

需要验证：
1. Agent SDK（Python）是否支持 sandbox runtime
2. Docker 内需要 `bubblewrap` + `socat` + `enableWeakerNestedSandbox`
3. Docker 镜像需要增加 Node.js（sandbox runtime 是 npm 包）
