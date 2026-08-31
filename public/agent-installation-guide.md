# ArcReel 外部 Agent 接入任务

你正在帮助用户把当前 Agent 宿主连接到 ArcReel。完成以下步骤后再结束任务；项目创作流程由安装后的 skill 与 MCP 工具说明提供。

## 1. 安装 ArcReel skills

在当前 Agent 的工作目录运行：

```bash
npx skills add ArcReel/skills
```

确认安装结果同时包含 `setup-arcreel-skills` 与 `video-workflow`。

## 2. 获取接入信息

让用户打开 [ArcReel 设置页]({{BASE_URL}}/app/settings?section=api-keys)，创建一个 `arc-` 前缀的 API Key。完整密钥只显示一次；用户可以把它提供给其明确选择的当前 Agent。`setup-arcreel-skills` 会按宿主与工作区的本地配置惯例持久化同一连接，供后续会话与 ArcReel skills 复用。

MCP 端点：

```text
{{BASE_URL}}/mcp
```

## 3. 执行接线

安装完成后立即使用 `setup-arcreel-skills` skill，按其指引配置 MCP 端点、Bearer API Key 并验证连通；`video-workflow` 会在后续创作请求中按需触发。

## 4. 完成判据

`list_projects` 调用成功并返回结构化 `projects` 列表，且宿主 MCP 与工作区配置指向同一连接时即完成；空列表也是成功。
