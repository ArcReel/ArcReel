# 内嵌 Agent 通过兼容协议接入模型

内嵌创作 Agent 基于 Claude Agent SDK 构建。更换运行时不仅需要重建会话、恢复、权限与沙箱等机制，还需重新适配项目内的 Skill 和 Subagent，这些成本已超出新增模型供应商的范围。

可以使用 Anthropic Messages API 兼容服务作为内嵌 Agent 的供应商，或通过远程 MCP 接入能使用 ArcReel 创作工具的外部 Agent。工具声明的两个宿主见 [ADR 0087](../docs/adr/0087-agent-toolset-single-declaration.md)。

为特定本地推理服务适配 Anthropic Messages API 的协议代理会随上游行为变化，更适合独立维护。如果某类本地服务成为大量用户的主要接入方式，且无法通过现有凭证配置接入，届时再评估是否提供正式支持。

## Prior requests

- #1231 — 内嵌 Agent 增加 Codex 支持
- PR #2445 — 为本地 llama.cpp Agent 供应商添加协议代理
