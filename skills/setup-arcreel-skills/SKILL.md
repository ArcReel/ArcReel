---
name: setup-arcreel-skills
description: 将当前 Agent 宿主连接到 ArcReel 远程 MCP 服务并验证访问。
---

# 接入 ArcReel

配置当前 Agent 宿主，并在当前工作区持久化 ArcReel 连接信息。

## 收集凭证

先检查宿主中名为 `arcreel` 的 MCP 配置与当前工作区的 `.arcreel/settings.json`，复用两处一致的已有值；
值缺失时再向用户询问：

- 以 `/mcp` 结尾的 ArcReel MCP 端点 URL。
- 在 **设置 → API Key** 中创建的 `arc-` API Key。ArcReel 只在创建时完整显示一次新密钥。

接收后只用于配置 ArcReel MCP，不在回复中复述，并且仅发送给用户提供的 MCP 端点。

## 接线

1. 确认端点使用 `https`、以 `/mcp` 结尾，且 API Key 以 `arc-` 开头。仅 `localhost`、`127.0.0.1` 或 `[::1]` 等回环端点可以使用 `http`。
2. 使用当前宿主原生的持久配置方式，添加名为 `arcreel` 的 streamable HTTP 服务，并把 API Key 作为 Bearer 凭证保存；可以直接明文写入该宿主的 MCP 配置。
3. 在当前工作区创建 `.arcreel/settings.json`，以 JSON 保存同一连接的 `mcp_url` 与 `api_key`；供 ArcReel skills 的本地脚本跨会话复用。若工作区受 Git 管理，则先确保 `.arcreel/` 已被 `git check-ignore` 覆盖；未覆盖时，将它追加到 `git rev-parse --git-path info/exclude` 返回的本地 exclude 文件。两处已有配置指向不同实例时，请用户选择后再同步。
4. 若宿主支持配置 MCP 工具调用超时，则把 `arcreel` 的超时设为至少 `600` 秒；视频生成可能超过默认超时。

## 验证

按宿主要求使配置生效，再无参数调用一次 ArcReel MCP 工具 `list_projects`。调用成功并返回结构化 `projects` 列表，且 `.arcreel/settings.json` 与生效的 MCP 配置指向同一实例，即完成接入；Git 工作区还须确认 `git check-ignore -q .arcreel/settings.json` 成功。空列表也是有效结果。已有配置满足这些条件时无需改写。失败时报告出错边界，不得暴露 API Key。
