# Alternative Agent Runtime Backends

ArcReel’s embedded Agent offers no runtime-backend replacement: it does not replace Claude Agent SDK with Codex, other agent frameworks, or non-Anthropic-protocol runtimes.

This boundary concerns only the embedded runtime backend. External Agents connect as a second host through remote MCP, without replacing Claude Agent SDK. Both hosts can coexist, so that integration is outside this rejection boundary.

## Why this is out of scope

The runtime (`server/agent_runtime/`) is architected on Claude Agent SDK, and core Agent capabilities depend on its specific mechanisms:

- **Sessions**: `SessionActor` / `session_manager.py` directly wrap `ClaudeSDKClient`; `options_assembler.py` builds SDK-specific `ClaudeAgentOptions` (hooks, mcp_servers, sandbox, resume, session_store)
- **Capabilities**: Skills, Subagents, in-process SDK MCP server (`server/agent_runtime/arcreel_mcp.py`), sandboxing (bwrap / sandbox-exec), `CLAUDE.*.md` profile injection, and manifest synchronization (`lib/agent/profile_manifest.py`) all belong to the Claude Agent SDK ecosystem

Replacing the backend means rewriting the entire Agent Runtime and abandoning these capabilities, with costs disproportionate to product benefit, rather than adding a selector.

The underlying desire for cheaper models/providers already has a product solution: Agent configuration supports arbitrary Anthropic Messages API-compatible providers. `lib/agent/agent_provider_catalog.py` presets inexpensive options (GLM, DeepSeek, Kimi, MiniMax, Xiaomi MiMo, Ark coding plan, etc.), with custom base_url + API Key for compatible relays. Protocol compatibility is the only requirement; compatible providers need no code changes.

## Prior requests

- #1231 — 「Agent 请求增加 Codex 支持」 (“request Codex support”; reasons: frequent GPT use and lower cost)
