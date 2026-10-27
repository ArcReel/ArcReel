---
name: setup-arcreel-skills
description: 将当前 Agent 宿主连接到 ArcReel 远程 MCP 服务并验证访问。
---

# 接入 ArcReel

将当前 Agent 宿主连接到 ArcReel，并为当前工作区持久化同一连接。

## 配置

1. 检查宿主中名为 `arcreel` 的 MCP 配置与当前工作区的 `.arcreel/settings.json`。两处连接完全一致时复用；值缺失时再询问用户，任一值不一致时请用户选择后同步。
2. 确认 MCP 端点以 `/mcp` 结尾、API Key 以 `arc-` 开头。端点使用 `https`；回环端点可使用 `http`。
3. 使用宿主原生的持久配置方式，添加名为 `arcreel` 的 streamable HTTP 服务，并把 API Key 作为 Bearer 凭证持久化（可明文）。若宿主支持 MCP 工具调用超时，设为至少 `600` 秒。
4. 按当前工作区的本地配置惯例写入 `.arcreel/settings.json`，仅保存同一连接的 `mcp_url` 与 `api_key`。
5. 写入 API Key 后，处理第 3、4 步写入的文件中位于 Git 仓库内的那些，例如 Claude Code 项目范围的 `.mcp.json` 与 `.arcreel/settings.json`。每个文件须同时满足两点：仓库内的 `.gitignore` 有规则忽略它，用 `git check-ignore -v --no-index` 看命中来源，只命中全局 excludes 或 `.git/info/exclude` 的不算，这些规则不随仓库走；它没有被跟踪，`git ls-files` 能列出的须 `git rm --cached`，否则修改仍会被提交。列出不满足的文件与需要的操作，提醒用户处理，或改用仓库外的配置位置；用户明确同意后才可代为执行，用户拒绝或未答复时不改动。已跟踪的文件若在历史提交中含有同一个 API Key，停止跟踪不会清除历史，提醒用户到 ArcReel 撤销该令牌并重新创建。

## 验证

按宿主的生效方式加载配置，再无参数调用 `list_projects`。返回结构化 `projects` 列表，且宿主 MCP 与工作区配置指向同一实例时即完成；空列表有效。已有配置满足这些条件时无需改写。
