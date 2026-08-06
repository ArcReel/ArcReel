# Narration 模式契约

本项目是说书模式。storyboard 路线的正式剧本为 `segments[]`；reference-video 路线为 `video_units[]`。

## 内容保真

- `novel_text` 是配音与字幕真相源，逐字保留源文语言、标点与顺序。
- 片段资产只引用 `project.json` 已登记名称；没有对应资产时使用空数组。
- `segment_break` 只标记真实的时间、地点或情节切换。

## 状态顺序

| workflow state | 完成条件 | 动作 |
|---|---|---|
| `PROJECT_INPUT` | 源文可读，overview 与语言口径可用 | 补充或修正项目输入 |
| `ASSET_INVENTORY` | 当前 source revision 已完成资产清单；空 bucket 合法 | dispatch `analyze-assets` |
| `EPISODE_PLAN` | 目标集存在于 episodes 账本且不是待重新规划 | 调 `plan_episodes`；调整走 reset + replan |
| `STEP1_CONTENT` | 当前路线的 step1 current | storyboard 调 `split-narration-segments`；reference 调 `split-reference-video-units` |
| `STEP1_REVIEW` | 当前 step1 revision 已确认 | 用户确认后调 `confirm_script_review` |
| `FINAL_SCRIPT` | 当前 step1 对应的正式剧本 current | dispatch `create-episode-script` |
| `ASSET_SHEETS` | 当前剧本引用的 sheet 全部 current | dispatch `run-generation-task` |
| `STORYBOARD` | storyboard 路线所有 segment 分镜 current | 单图或宫格生成；reference 跳过 |
| `VIDEO` | 所有 segment / unit 视频 current | dispatch 视频生成 |
| `AUDIO` | storyboard 路线所有 segment TTS current | dispatch 旁白生成；reference 跳过 |
| `EXPORT_READY` | 核心产物 current | 引导 Web 端导出剪映草稿 |

旁白只依赖最终剧本，可在用户显式要求时提前执行；正常自动流程按表中顺序推进。

## 目标集

未指定集数时，选择 episodes 账本中最小的 `planned` 或 `stale` 集。全部已消费且源文未规划完时进入 `EPISODE_PLAN`；源文已结束且所有集 current 时进入 `EXPORT_READY`。
