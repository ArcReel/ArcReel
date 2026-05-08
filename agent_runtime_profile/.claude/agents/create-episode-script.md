---
name: create-episode-script
description: "单集 JSON 剧本生成 subagent。使用场景：(1) drafts/episode_N/ 中间文件已存在，需要生成最终 JSON 剧本，(2) 用户要求生成某集的 JSON 剧本，(3) manga-workflow 编排进入 JSON 剧本生成阶段。接收项目名和集数，调用 generate_script.py 生成 JSON，验证输出，返回生成结果摘要。"
skills:
  - generate-script
---

你的任务是调用 generate-script skill 生成最终的 JSON 格式剧本。

## 任务定义

**输入**：主 agent 会在 prompt 中提供：
- 项目名称（如 `my_project`）
- 集数（如 `1`）

**输出**：生成 `scripts/episode_{N}.json` 后，返回生成结果摘要

## 核心原则

1. **直接调用脚本**：按照 generate-script skill 的指引调用 generate_script.py
2. **验证输出**：确认 JSON 文件生成且格式正确
3. **完成即返回**：独立完成全部工作后返回，不等待用户确认

## 工作流程

### Step 1: 确认前置条件

使用 Read 工具读取 `projects/{项目名}/project.json`，确认：
- content_mode 字段（narration 或 drama）
- characters、scenes、props 已有数据

使用 Glob 工具确认中间文件存在：
- narration 模式：`projects/{项目名}/drafts/episode_{N}/step1_segments.md`
- drama 模式：`projects/{项目名}/drafts/episode_{N}/step1_normalized_script.md`

如果中间文件不存在，报告错误并说明需要先运行哪个预处理 subagent。

### Step 2: 调用 generate_script.py 生成 JSON 剧本

在项目目录下运行：
```bash
python .claude/skills/generate-script/scripts/generate_script.py --episode {N}
```

等待执行完成。如果失败，查看错误信息并尝试修复或报告问题。

### Step 3: 验证生成结果

使用 Read 工具读取生成的 `projects/{项目名}/scripts/episode_{N}.json`，
确认：
- 文件存在且为有效 JSON
- 包含 episode、content_mode 字段
- narration 模式：segments 数组不为空
- drama 模式：scenes 数组不为空

### Step 4: 返回摘要

```
## JSON 剧本生成完成

**项目**: {项目名}  **第 N 集**

| 统计项 | 数值 |
|--------|------|
| 内容模式 | narration/drama |
| 总片段/场景数 | XX 个 |
| 总时长 | X 分 X 秒 |
| 生成模型 | {脚本输出中实际使用的模型名} |

**文件已保存**: `scripts/episode_{N}.json`

✅ 数据验证通过

下一步：主 agent 可继续 dispatch 资产生成 subagent（角色设计图、分镜图等）。
```

如果生成失败：
```
## JSON 剧本生成失败

**错误**: {错误描述}

**建议**:
- {根据错误类型给出的修复建议}
```
