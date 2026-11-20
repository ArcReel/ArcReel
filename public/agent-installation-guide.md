# ArcReel 外部 Agent 接入任务

你正在协助用户将当前 Agent 宿主连接到 ArcReel。请先完成以下步骤再结束任务；后续的项目创作流程由安装好的 skill 与 MCP 工具说明提供。

## 1. 安装 ArcReel skills

在当前 Agent 的工作目录中运行：

```bash
npx skills add ArcReel/skills
```

确认安装结果同时包含 `setup-arcreel-skills` 与 `video-workflow`。

## 2. 接入 ArcReel

请立即按 `setup-arcreel-skills` 配置同一实例的宿主 MCP 服务与工作区连接；若已有连接符合 skill 的复用条件，直接复用即可。需要新令牌时，引导用户前往 [ArcReel 设置页的「访问令牌」]({{BASE_URL}}/app/settings?section=access-tokens) 创建一个以 `arc-` 开头的访问令牌（完整令牌仅显示一次，由用户提供给所选 Agent）。按宿主与工作区的本地配置惯例持久化这一连接。

MCP 端点：

```text
{{BASE_URL}}/mcp
```

## 3. 验证连接

当 `list_projects` 返回结构化的 `projects` 列表，且宿主 MCP 与工作区配置均指向同一连接时，即表示接入完成（空列表同样有效）。后续的创作请求由 `video-workflow` 提供工作流指引。
