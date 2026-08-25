# ArcReel 外部 Agent 安装指引

本页只说明如何安装 ArcReel skills、连接远程 MCP 并验证连通。项目创作流程由安装后的 skill 与 MCP 工具说明提供。

## 1. 获取 API Key

打开 [ArcReel 设置页]({{BASE_URL}}/app/settings?section=api-keys)，创建并复制一个 `arc-` 前缀的 API Key。密钥只显示一次，请存入外部 Agent 支持的安全凭证位置，不要粘贴到对话或提交到代码仓库。

远程 MCP 端点是：

```text
{{BASE_URL}}/mcp
```

## 2. 安装两个 skills

在外部 Agent 的工作目录运行：

```bash
npx skills add ArcReel/ArcReel@setup-arcreel-skills
npx skills add ArcReel/ArcReel@video-workflow
```

## 3. 显式触发接线

安装后，显式调用 `$setup-arcreel-skills`，并按提示提供 MCP 端点和 API Key。这个 setup skill 不会自行触发；`video-workflow` 独立安装并在创作请求中按需触发。

如果使用 Codex，请把 ArcReel MCP server 的 `tool_timeout_sec` 调大；Codex 默认的 60 秒不适合视频生成等长任务。

## 4. 验证连通

让 setup skill 完成一次连通验证。外部 Agent 能列出 ArcReel MCP 工具并成功调用验证步骤，即表示安装与认证均已生效。
