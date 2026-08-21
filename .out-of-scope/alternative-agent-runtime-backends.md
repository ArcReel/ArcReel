# Alternative Agent Runtime Backends

ArcReel 的内嵌 Agent 不提供「更换 Agent 运行时后端」的选项——不支持把 Claude Agent SDK 替换为 Codex、其他 agent 框架或任何非 Anthropic 协议的运行时。

## Why this is out of scope

Agent 运行时（`server/agent_runtime/`）在架构上构建于 Claude Agent SDK 之上，且产品的核心 Agent 能力全部依赖该 SDK 的专属机制：

- **会话层**：`SessionActor` / `session_manager.py` 直接封装 `ClaudeSDKClient`，`options_assembler.py` 构造的 `ClaudeAgentOptions`（hooks、mcp_servers、sandbox、resume、session_store）是 SDK 专属数据结构
- **能力层**：Skill、Subagent、SDK 进程内 MCP 工具（`sdk_tools/`）、沙箱（bwrap / sandbox-exec）、`CLAUDE.*.md` profile 注入与 manifest 同步（`lib/profile_manifest.py`），均为 Claude Agent SDK 生态的机制

更换后端不是「加一个选择项」，而是重写整个 Agent Runtime 层并放弃上述能力，成本与产品收益不成比例。

「想用更便宜的模型/供应商」这一底层诉求已有产品级出路：Agent 配置页支持在 Anthropic Messages API 协议兼容的范围内任意切换供应商——`lib/agent_provider_catalog.py` 预置了一批低价供应商（GLM、DeepSeek、Kimi、MiniMax、小米 MiMo、方舟 coding plan 等），并支持自定义 base_url + API Key 接入任意兼容中转站。协议兼容是唯一门槛；满足该门槛的供应商无需改代码即可使用。

## Prior requests

- #1231 — 「Agent 请求增加 Codex 支持」（理由：日常用 GPT 多、费用便宜）
