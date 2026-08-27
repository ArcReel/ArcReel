# ArcReel 外部 Agent 接入任务

你正在帮助用户把当前 Agent 宿主连接到 ArcReel。完成以下步骤后再结束任务；项目创作流程由安装后的 skill 与 MCP 工具说明提供。

## 1. 安装 ArcReel skills

在当前 Agent 的工作目录运行：

```bash
npx skills add ArcReel/skills
```

确认安装结果同时包含 `setup-arcreel-skills` 与 `video-workflow`。

## 2. 获取接入信息

让用户打开 [ArcReel 设置页]({{BASE_URL}}/app/settings?section=api-keys)，创建并保存一个 `arc-` 前缀的 API Key。完整密钥只显示一次；把它存入宿主支持的安全凭证位置，只发送给下方 ArcReel MCP 端点。

MCP 端点：

```text
{{BASE_URL}}/mcp
```

## 3. 执行接线

显式调用 `/setup-arcreel-skills`，按 skill 指引配置 MCP 端点、Bearer API Key 并验证连通。这个 setup skill 不会自行触发；`video-workflow` 会在后续创作请求中按需触发。

如果使用 Codex，请把 ArcReel MCP server 的 `tool_timeout_sec` 调大；Codex 默认的 60 秒不适合视频生成等长任务。

## 4. 完成判据

`list_projects` 调用成功并返回结构化 `projects` 列表即完成；空列表也是成功。失败时按 setup skill 的诊断分支处理，不得输出 API Key。
