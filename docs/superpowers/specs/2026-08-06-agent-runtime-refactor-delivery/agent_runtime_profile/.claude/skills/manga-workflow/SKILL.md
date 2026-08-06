---
name: manga-workflow
description: 项目流程：开始、继续、检查或自动推进当前 ArcReel 项目的端到端制作。
---

# ArcReel 视频工作流

本 skill 只编排状态和职责；服务端决定事实。

## 进入流程

1. 读取 `.claude/references/workflow-mode.md`。
2. 从用户话语确定 `run_policy`：
   - `interactive`：默认；在质量审核点询问；
   - `autonomous`：用户明确要求自动跑完时采用；普通审核自动通过，硬确认门保留。
3. 调：

```text
mcp__arcreel__get_workflow_status({"episode": <用户指定时传>})
```

## 编排循环

按 status 的 `next_action.type` 执行：

| action type | 执行者 |
|---|---|
| `collect_project_input` | 主 agent 对话补齐；可写字段经 `patch_project`，上传走 WebUI |
| `draft_selling_points` | 主 agent 起草并确认后经 `patch_project` 写入 |
| `analyze_assets` | dispatch `analyze-assets` |
| `plan_episodes` | 主 agent 调 `plan_episodes`；用户调整走 `reset_episode_planning` 后重规划 |
| `prepare_step1` | 按 action 的 `preprocessor` dispatch 对应 subagent |
| `confirm_step1` | interactive 获取确认；autonomous 可按总体授权调 `confirm_script_review` |
| `generate_script` | dispatch `create-episode-script` |
| `generate_asset_sheets` | dispatch `run-generation-task`，使用 action 给出的 tool、args、IDs |
| `confirm_product_sheet` | 引导用户检查当前 sheet revision；明确确认后调 `confirm_product_sheet_review` |
| `generate_storyboards` / `generate_grid` | dispatch `run-generation-task` |
| `generate_videos` | dispatch `run-generation-task`；时长清单按专用 reference 处理 |
| `generate_narration_audio` | dispatch `run-generation-task` |
| `export` | 汇报 current 产物并引导 WebUI 导出 |
| `none` | 报告 COMPLETE 或 blocker |

每个动作返回后再次调用 workflow status。只有以下任一条件成立才结束本轮：

- state 发生预期迁移；
- requested IDs 全部 current；
- 出现明确 blocker / confirmation；
- 项目进入 `EXPORT_READY`。

若 action 执行后 state、revision 与 blockers 全部不变，停止重试并报告原始原因。

## 规划重置

重置从用户意见影响的最早 episode 开始。工具报告会波及 consumed 集时，展示完整影响范围并取得明确确认，再用 `confirm_consumed: true` 重试。全局分集偏好在每一批 `plan_episodes` 调用中重复传入，直到规划结束。

## 直接诉求

用户只要求生成或修改某类资产、分镜、视频、旁白或成片时，使用对应专用 skill，不启动完整编排循环。
