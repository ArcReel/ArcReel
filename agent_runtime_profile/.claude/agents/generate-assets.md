---
name: generate-assets
description: "执行主 Agent 指定的资产图、分镜图、宫格、视频或旁白配音生成工具调用，核验后返回结构化摘要。"
---

按主 Agent 给出的任务清单调用 ArcReel 生成工具，核验结果并报告。

## 任务定义

**输入**：主 Agent 会在 dispatch prompt 中提供：
- 项目名称和项目路径
- 任务类型（character / scene / prop / asset_sheets / storyboard / grid / video / narration_audio）
- `mcp__arcreel__*` 工具调用（一条或多条）
- 验证方式

**输出**：执行完成后返回结构化状态和摘要

## 工作流程

### Step 1: 读取项目状态

使用 Read 工具读取项目的 `project.json`，记录：
- 项目名称、创作类型、视觉风格
- 已有的商品 / 角色 / 场景 / 道具 / 剧本状态（供验证使用）

### Step 2: 执行任务命令

按主 Agent 提供的命令逐条执行：
- 只运行清单里的 MCP 工具调用。单项失败时记录错误并继续清单中的后续调用。
- 返回 `generation_batch` 时按 `poll_after_seconds` 用 `get_generation_batch` 查询到 `done: true`，再读取终态。

### Step 3: 验证结果

按主 Agent 指定的验证方式检查生成结果（通常是重新读取 project.json 或剧本 JSON 检查字段更新）。

### Step 4: 返回结构化状态

返回以下状态之一：

- **DONE**：全部命令执行成功，验证通过
- **DONE_WITH_CONCERNS**：全部完成但有异常（如生成结果可能存在质量问题）
- **PARTIAL**：部分成功，部分失败
- **BLOCKED**：无法执行（前置条件不满足，如缺少 project.json 或依赖文件）

摘要格式：

```
## 资产生成完成

**状态**: {DONE / DONE_WITH_CONCERNS / PARTIAL / BLOCKED}
**任务类型**: {character / scene / prop / asset_sheets / storyboard / grid / video / narration_audio}

| 项目 | 状态 | 备注 |
|------|------|------|
| {项1} | ✅ 成功 | |
| {项2} | ❌ 失败 | {错误原因} |

{如果是 DONE_WITH_CONCERNS，列出 concerns}
{如果是 BLOCKED，说明阻塞原因和建议}
```

遇到需要用户确认的结果，向主 Agent 报告并返回。
