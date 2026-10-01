---
paths:
  - "server/agent_runtime/**"
  - "server/agent_toolset/**"
  - "lib/agent/**"
  - "agent_runtime_profile/**"
---

# Agent 运行时与内嵌 Agent 配置

## 运行时不变量

### 会话的 SDK client 调用全部经该会话的 `SessionActor` 串行执行

每个会话的 `ClaudeSDKClient` 调用都由该会话专属的 `SessionActor` task 串行执行（`docs/adr/0028`）。新增会话操作通过 actor 投递，不直接持有 client；绕开 actor 的调用会与 actor 内的调用并发访问同一个 client。

### Agent 工具只在 `server/agent_toolset/` 声明一次

内嵌会话经 `server/agent_runtime/arcreel_mcp.py` 构建进程内 MCP server 供 Skill 调用，外部 Agent 经远程 MCP 暴露同一份声明（`docs/adr/0087`）。在某一侧单独注册工具，两类 Agent 的能力就会分叉。

### 新增 Agent 工具以沙箱开启为前提设计

沙箱默认开启：Linux 用 bwrap、macOS 用 sandbox-exec，在工具调用外围隔离文件系统、网络与子进程。路径越界与白名单外的网络请求会被拒绝，工具所需的权限要显式声明。只在关闭沙箱的开发环境里测过的工具，到用户那里会被拒绝。Windows 的降级要求见 `docs/standards/windows.md`。

transcript 的 DB 镜像由 `ARCREEL_SDK_SESSION_STORE`（`db` / `off`）控制，`off` 时回退到 SDK 自带的 jsonl 路径（`docs/adr/0029`）；新增读取 transcript 的代码两种模式都要能工作。

## 内嵌 Agent 配置

### 改配置源 `agent_runtime_profile/`，不改项目侧物化文件

`agent_runtime_profile/` 是内嵌 Agent 的配置源：`.claude/skills/`、`.claude/agents/` 与按 `content_mode` 拆分的 `CLAUDE.*.md`（运行时按项目创作类型注入）。`lib/agent/profile_manifest.py` 把它们物化到各用户项目的 `.claude/` 与 CLAUDE.md，以 manifest + sha256 识别并保留用户改过的项目侧文件。只改项目侧文件的修复对其他项目和新项目都不生效。

### Skill 的 SKILL.md 与其脚本同步修改

SKILL.md 描述的参数、输出或步骤与脚本实际行为不一致时，Agent 会按文档调用并失败。Skill 的写作规范见 `/writing-for-agents`。
