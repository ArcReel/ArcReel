---
name: setup-arcreel-skills
description: 将当前 Agent 宿主连接到 ArcReel 远程 MCP 服务并验证访问。
---

# 接入 ArcReel

只修改当前 Agent 宿主的 MCP 配置。

## 收集凭证

向用户询问尚未提供的值：

- 以 `/mcp` 结尾的 ArcReel MCP 端点 URL。
- 在 **设置 → API Key** 中创建的 `arc-` API Key。ArcReel 只在创建时完整显示一次新密钥。

接收后只用于配置 ArcReel MCP，不在回复中复述，并且仅发送给用户提供的 MCP 端点。

## 接线

1. 确认端点使用 `https`、以 `/mcp` 结尾，且 API Key 以 `arc-` 开头。仅 `localhost`、`127.0.0.1` 或 `[::1]` 等回环端点可以使用 `http`。
2. 使用当前宿主原生的持久配置方式，添加名为 `arcreel` 的 streamable HTTP 服务，并把 API Key 作为 Bearer 凭证保存；可以直接明文写入该宿主的 MCP 配置。
3. 宿主支持配置 MCP 工具调用超时时，把 `arcreel` 的超时设为至少 `600` 秒；视频生成可能超过默认超时。

## 验证

按宿主要求使配置生效，再无参数调用一次 ArcReel MCP 工具 `list_projects`。调用成功并返回结构化 `projects` 列表即完成接入；空列表也是有效结果。失败时报告出错边界，不得暴露 API Key。
