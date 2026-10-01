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

新增会话操作通过 actor 投递，不直接持有 `ClaudeSDKClient`（`docs/adr/0028`）。绕开 actor 的调用会与 actor 内的调用并发访问同一个 client。

### Agent 工具只在 `server/agent_toolset/` 声明一次

内嵌会话经 `server/agent_runtime/arcreel_mcp.py` 构建进程内 MCP server 供 Skill 调用，同一份声明经远程 MCP 暴露给外部 Agent（`docs/adr/0087`）。在某一侧单独注册工具，两类 Agent 的能力就会分叉。

### 新增 Agent 工具以沙箱开启为前提设计

沙箱是 Linux / macOS 上 server 的启动前提（`docs/adr/0025`）：Linux 用 bwrap、macOS 用 sandbox-exec，在工具调用外围隔离文件系统、网络与子进程。路径越界与白名单外的网络请求会被拒绝，工具所需的权限要显式声明，否则工具在用户环境里会被拒绝执行。Windows 的降级要求见 `docs/standards/windows.md`。

### 读取 transcript 的代码在 `ARCREEL_SDK_SESSION_STORE=db` 与 `off` 下都能工作

`ARCREEL_SDK_SESSION_STORE` 控制 transcript 是否镜像到 DB，`off` 时回退到 SDK 自带的 jsonl 路径（`docs/adr/0029`）。只按其中一种模式实现的读取逻辑，在另一种部署配置下读不到会话记录。

## 内嵌 Agent 配置

### 改配置源 `agent_runtime_profile/`，不改项目侧物化文件

`agent_runtime_profile/` 是内嵌 Agent 的配置源：`.claude/skills/`、`.claude/agents/` 与按 `content_mode` 拆分的 `CLAUDE.*.md`（运行时按项目创作类型注入）。`lib/agent/profile_manifest.py` 把它们物化到各用户项目的 `.claude/` 与 CLAUDE.md，以 manifest + sha256 识别并保留用户改过的项目侧文件。只改项目侧文件的修复对其他项目和新项目都不生效。

### Skill 的 SKILL.md 与其脚本同步修改

SKILL.md 描述的参数、输出或步骤与脚本实际行为不一致时，Agent 会按文档调用并失败。Skill 的写作规范见 `.agents/skills/writing-for-agents/SKILL.md`。
