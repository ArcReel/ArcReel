---
paths:
  - "server/agent_runtime/**"
  - "tests/agent_runtime/**"
  - "tests/test_session_actor.py"
  - "tests/test_session_manager*.py"
  - "tests/server/agent_runtime/**"
  - "pyproject.toml"
  - "uv.lock"
---

# Claude Agent SDK 开发依据

SDK 调用、options、session、streaming、hooks、permissions 或消息类型发生变化时，先查 [Claude Agent SDK 官方在线文档](https://code.claude.com/docs/en/agent-sdk/overview)，再调用项目已启用的 `agent-sdk-dev@claude-plugins-official` 对应 Python verifier 核验当前 SDK 用法。普通的 agent runtime 业务逻辑改动不触发 verifier。

该 plugin 属于 ArcReel 仓库的开发态 Claude Code 配置；内嵌创作 agent 不继承它。历史版本行为使用固定版本的上游源码或当前契约测试作证，不引用可变网页的行号。
