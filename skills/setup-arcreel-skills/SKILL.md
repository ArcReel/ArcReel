---
name: setup-arcreel-skills
description: 将当前 Agent 宿主连接到 ArcReel 远程 MCP 服务并验证访问。
---

# 接入 ArcReel

将当前 Agent 宿主连接到 ArcReel，并为当前工作区持久化同一连接。

## 配置

1. 检查宿主中名为 `arcreel` 的 MCP 配置与当前工作区的 `.arcreel/settings.json`。复用两处一致的已有值；值缺失时再询问用户，两处指向不同实例时请用户选择。
2. 确认 MCP 端点以 `/mcp` 结尾、API Key 以 `arc-` 开头。端点使用 `https`；回环端点可使用 `http`。
3. 使用宿主原生的持久配置方式，添加名为 `arcreel` 的 streamable HTTP 服务，并把 API Key 作为 Bearer 凭证持久化（可明文）。若宿主支持 MCP 工具调用超时，设为至少 `600` 秒。
4. 按当前工作区的本地配置惯例写入 `.arcreel/settings.json`，仅保存同一连接的 `mcp_url` 与 `api_key`。

## 验证

按宿主的生效方式加载配置，再无参数调用 `list_projects`。返回结构化 `projects` 列表，且宿主 MCP 与工作区配置指向同一实例时即完成；空列表有效。已有配置满足这些条件时无需改写。
