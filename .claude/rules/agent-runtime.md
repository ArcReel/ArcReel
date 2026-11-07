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

# Agent Runtime and Agent configuration

When SDK calls, options, sessions, streaming, hooks, permissions, or message types change, first consult the [official Claude Agent SDK online documentation](https://code.claude.com/docs/en/agent-sdk/overview), then use the corresponding Python verifier from the project's enabled `agent-sdk-dev@claude-plugins-official` plugin to verify current SDK usage. Ordinary Agent runtime business-logic changes do not trigger the verifier.

That plugin belongs to the ArcReel repository's development Claude Code configuration; the embedded creation Agent does not inherit it. Base claims about historical versions on pinned upstream source or current contract tests, not line numbers from mutable web pages.

Read SDK transcripts through `SdkTranscriptAdapter` in `server/agent_runtime/sdk_transcript_adapter.py`: it switches between the DB mirror and SDK jsonl according to `ARCREEL_SDK_SESSION_STORE` (`docs/adr/0029`). Code that reads the store directly finds no records in `off` deployments.

Before adding Agent tools or session operations, changing embedded Agent behavior, or allowing Agent file or network access, read “5.3 确定性工具,” “5.4 会话串行,” “5.5 内嵌 Agent 配置源,” and “15. 沙箱和安全边界” in `website/docs/dev/architecture.md`.
