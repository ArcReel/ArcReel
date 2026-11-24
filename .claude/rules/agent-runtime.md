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

修改 SDK 调用、选项、会话、流式传输、钩子、权限或消息类型时，先查阅 [Claude Agent SDK 官方文档](https://code.claude.com/docs/en/agent-sdk/overview)，再通过项目开发环境提供的 Python verifier 核验。若需确认锁定版本的具体语义，查阅对应上游源码或仓库内的契约测试。

读取 SDK transcript 须经由 [SdkTranscriptAdapter](../../server/agent_runtime/sdk_transcript_adapter.py)，由其按部署配置选择数据源。相关决策见 [ADR 0029](../../docs/adr/0029-sdk-transcript-mirrored-to-db.md)。

修改 Agent 工具、会话操作或内嵌配置时，先阅读 [Agent Runtime](../../website/docs/dev/architecture.md#agent-runtime)。修改文件或网络访问时，先阅读 [沙箱和安全边界](../../website/docs/dev/architecture.md#sandbox-and-security)。
