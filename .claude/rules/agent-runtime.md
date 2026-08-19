---
paths:
  - "server/agent_runtime/**"
  - "lib/agent_session_store/**"
  - "lib/profile_manifest.py"
  - "agent_runtime_profile/**"
  - "tests/agent_runtime/**"
  - "tests/agent_session_store/**"
  - "tests/test_session_actor.py"
  - "tests/test_session_manager*.py"
  - "tests/server/agent_runtime/**"
  - "pyproject.toml"
  - "uv.lock"
---

# Agent Runtime 与智能体配置

## Claude Agent SDK 开发依据

SDK 调用、options、session、streaming、hooks、permissions 或消息类型发生变化时，先查 [Claude Agent SDK 官方在线文档](https://code.claude.com/docs/en/agent-sdk/overview)，再调用项目已启用的 `agent-sdk-dev@claude-plugins-official` 对应 Python verifier 核验当前 SDK 用法。普通的 agent runtime 业务逻辑改动不触发 verifier。

该 plugin 属于 ArcReel 仓库的开发态 Claude Code 配置；内嵌创作 agent 不继承它。历史版本行为使用固定版本的上游源码或当前契约测试作证，不引用可变网页的行号。

## 运行时不变量

- 每个会话的 ClaudeSDKClient 调用全部经由该会话专属的 `SessionActor` task 串行执行（`docs/adr/0028`）；新增会话操作走 actor 投递，而非直接持有 client。
- transcript 的 DB 镜像由 `ARCREEL_SDK_SESSION_STORE`（`db` / `off`）控制，`off` 时回退到 SDK 自带的 jsonl 路径（`docs/adr/0029`）。
- `sdk_tools/` 内的进程内 MCP 工具由 agent profile manifest 注入、供 Skill 调用。
- 沙箱默认开启：Linux bwrap、macOS sandbox-exec，在 Agent 工具调用外围隔离文件系统 / 网络 / 子进程。写新 Agent 工具时按沙箱开启设计——路径越界与白名单外网络请求会被拒绝，需要的权限显式声明。Windows 原生无沙箱，降级为 Bash 前缀白名单（`docs/adr/0025`、`docs/adr/0026`）；依赖沙箱专属能力的工具须提供 Windows 降级路径，或在沙箱不可用时显式拒绝运行。

## 智能体配置源

`agent_runtime_profile/` 是内嵌智能体的配置源：`.claude/skills/`、`.claude/agents/` 与按 `content_mode` 拆分的 `CLAUDE.*.md`（运行时按项目内容模式注入）。`lib/profile_manifest.py` 把它们同步到各用户项目的 `.claude/` 与 CLAUDE.md，以 manifest + sha256 识别用户改过的项目侧文件并保留——改配置改源目录，项目侧文件由同步生成。

Skill 的 SKILL.md 与其脚本同步修改；Skill 写法依 `/writing-for-agents`。
