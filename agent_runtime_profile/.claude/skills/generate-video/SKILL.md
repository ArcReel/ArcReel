---
name: generate-video
description: 为剧本场景或自包含 video unit 生成视频。当用户要求生成、重做或续传视频时使用；支持整集、单项与批量自选。
---

# 生成视频

## 路由

让 MCP 工具读取 `project.json`，按 `generation_mode` × `content_mode` 选择路线，并校验剧本骨架：

| 项目路线 × 内容模式 | 应有骨架 | 路由 | 输出目录 |
|---|---|---|---|
| `reference_video` × narration / drama / ad | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | 同上 | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | 同上 | `videos/scene_{shot_id}.mp4` |

骨架失配时停止入队，按项目路线重生成剧本。参考路线直接消费自包含 `video_units[]`，跳过分镜图。

### 参考路线

把每个 `video_units[]` 条目视为一次独立生成调用：

- 从 `shots[].text` 构造统一书写层 prompt。
- 从 `references` 解析产品、角色、场景、道具。广告产品参考优先于其他资产；产品按 sheet、原图展开，随后注入其他资产 sheet。
- 让生成预检把 unit 编排时长投影到供应商申请档位。
- 遇到 `needs_replan` 或发声归属问题时停止该 unit，先修复规划内容。
- 整集生成只复用 `generated_assets.video_clip` 明确指向的现行成片；同名孤儿文件不代表该 unit 已完成。

让项目配置、剧本模型与视频能力决定比例、时长和参考图上限，不在调用参数中另写一套数值。

## 工具调用

使用 MCP 工具入队；本 skill 不提供 Python 或 Shell 生成脚本。

| 操作 | 工具 |
|------|------|
| 整集生成（默认） | `mcp__arcreel__generate_video_episode({"script": "episode_1.json"})` |
| 断点续传 | `mcp__arcreel__generate_video_episode({"script": "episode_1.json", "resume": true})` |
| 单场景 | `mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1S01"})` |
| 批量自选 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S05", "E1S10"]})` |
| 自选 + 续传 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": [...], "resume": true})` |
| 全部待处理（独立模式） | `mcp__arcreel__generate_video_all({"script": "episode_1.json"})` |

把 `scene_id` / `scene_ids` 在 storyboard 路线解释为分镜 ID，在 reference 路线解释为 `unit_id`。集号由剧本元数据或文件名解析。

### 点名重新生成 unit

在 reference 路线传 `video_units[].unit_id`：

| 操作 | 工具 |
|------|------|
| 重新生成单个 unit | `mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1U2"})` |
| 重新生成多个 unit | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": ["E1U2", "E1U3"]})` |

一次调用完成入队、等待与结果回报：

- 把点名视为强制重做，覆盖已有成片。
- 任一目标已有在途任务时等待其完成，再重做整批目标。
- 只生成剧本中点名的自包含 unit；未命中的 ID 记为 `blocked`，带 `generation_unit_not_found`。
- 点名重做不落 checkpoint，忽略 `resume`。
- 结果按 `requested / succeeded / failed / blocked` 逐 ID 返回，
  结构与问题码见 `.claude/references/generation-results.md`。

### reference_video 模式的时长确认

按 unit 的引用状态选择生效档位，把编排时长投影到能容纳内容的申请档位。申请时长不同于编排时长时，首次调用只返回确认清单，不入队。向用户说明每个 unit 的编排秒数、申请秒数及变长或变短；用户同意后给同一工具加 `confirm_duration: true`。能力解析成功且无需调整时直接入队；能力无法解析时把工具错误作为 blocker，先修复模型能力声明。

```text
mcp__arcreel__generate_video_episode({"script": "episode_1.json", "confirm_duration": true})
```

## 工作流程

1. 加载项目和剧本，确认骨架与路线一致。
2. 在 storyboard 路线确认分镜图可用；在 reference 路线确认 unit 可生成且声明的参考资产可解析。
3. 调用相应 MCP 工具，处理可能出现的时长确认。
4. 展示结果，按用户选择点名重做不满意的分镜或 unit。
5. 以工具写回的 `generated_assets.video_clip` 作为成片归属。

## Prompt 构建

让 MCP 工具按路线构建 Prompt：

- storyboard 路线读取 `image_prompt`、`video_prompt` 与分镜图。
- reference 路线读取 `shots[].text`、`references` 与 unit 编排时长。
- 说书 storyboard 路线不把 `novel_text` 放入视频 Prompt；旁白由独立音频流程处理。
- 自动应用音频开关、角色发声归属与负面 Prompt 规则。

## 生成前检查

按项目路线检查：

- storyboard：每个目标分镜都有可用分镜图，动作与发声内容可执行。
- reference：每个目标 unit 有非空书写层、合法编排时长、单一发声归属，且未标记 `needs_replan`。
- reference：所有声明引用已登记且图片可解析；让服务端按 `max_reference_images` 裁剪，产品参考保持优先。
- reference：输出路径为 `reference_videos/{unit_id}.mp4`。
