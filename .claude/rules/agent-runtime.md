---
paths:
  - "server/agent_runtime/**"
  - "server/agent_toolset/**"
  - "lib/agent/**"
  - "agent_runtime_profile/**"
  - "tests/unit/server/agent_runtime/**"
  - "tests/integration/server/agent_runtime/**"
  - "tests/unit/lib/agent/**"
  - "tests/integration/lib/agent/**"
  - "pyproject.toml"
  - "uv.lock"
---

# Agent Runtime 与 Agent 配置

SDK 调用、options、session、streaming、hooks、permissions 或消息类型发生变化时，先查 [Claude Agent SDK 官方在线文档](https://code.claude.com/docs/en/agent-sdk/overview)，再调用项目已启用的 `agent-sdk-dev@claude-plugins-official` 对应 Python verifier 核验当前 SDK 用法。普通的 Agent 运行时业务逻辑改动不触发 verifier。

该 plugin 属于 ArcReel 仓库的开发态 Claude Code 配置；内嵌创作 Agent 不继承它。历史版本行为以固定版本的上游源码或当前契约测试为依据，不引用可变网页的行号。

读取 SDK transcript 经 `server/agent_runtime/sdk_transcript_adapter.py` 的 `SdkTranscriptAdapter`：它按 `ARCREEL_SDK_SESSION_STORE` 在 DB 镜像与 SDK jsonl 之间切换（`docs/adr/0029`），直接读 store 的代码在 `off` 部署下读不到记录。

新增 Agent 工具或会话操作、改内嵌 Agent 的行为、放行 Agent 的文件或网络访问前，先读 `website/docs/dev/architecture.md` 的「5.3 确定性工具」「5.4 会话串行」「5.5 内嵌 Agent 配置源」「15. 沙箱和安全边界」。
