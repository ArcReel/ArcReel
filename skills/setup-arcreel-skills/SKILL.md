---
name: setup-arcreel-skills
description: 将当前 Agent 宿主连接到 ArcReel 远程 MCP 服务并验证访问。
---

# 接入 ArcReel

配置当前 Agent 宿主以使用 ArcReel 远程 MCP 服务。只修改宿主的 MCP 配置，不修改 ArcReel 项目。

## 收集凭证

向用户询问尚未提供的值：

- 以 `/mcp` 结尾的 ArcReel MCP 端点 URL。
- 在 **设置 → API Key** 中创建的 `arc-` API Key。ArcReel 只在创建时完整显示一次新密钥。

把 API Key 视为秘密：不出现在回复、shell 历史、项目文件或提交的配置中，只发送给用户提供的 MCP 端点。

## 接线

1. 确认端点使用 `https`、以 `/mcp` 结尾，且 API Key 以 `arc-` 开头。仅 `localhost`、`127.0.0.1` 或 `[::1]` 等回环端点可以使用 `http`。
2. 使用当前宿主原生的 MCP 配置方式，添加名为 `arcreel` 的服务，传输方式为 streamable HTTP，鉴权方式为 Bearer。宿主支持环境变量或秘密引用时，用它保存 API Key；宿主只能保存明文请求头时，先说明密钥将写入的位置并取得确认。
3. 使用 Codex 时，以 `ARCREEL_API_KEY` 作为 `bearer_token_env_var`，并把 `arcreel` 服务的 `tool_timeout_sec` 设为至少 `600`；长时间生成任务可能超过 Codex 的默认超时。
4. 宿主需要重载 MCP 配置时，完成重载。

## 验证

无参数调用一次 ArcReel MCP 工具 `list_projects`。调用成功并返回结构化 `projects` 列表即完成接入；空列表也是有效结果。

失败时报告出错边界，不得暴露 API Key：

- 鉴权失败：创建或复制有效的 `arc-` API Key，并更新 Bearer 秘密。
- 连接或宿主校验失败：检查公开的 `/mcp` URL 及服务端 MCP host/origin 白名单。
- 工具缺失：重载宿主 MCP 配置，并确认服务名为 `arcreel`。
