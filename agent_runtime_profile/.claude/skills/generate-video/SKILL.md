---
name: generate-video
description: 为剧本场景生成视频片段。当用户说"生成视频"、"把分镜图变成视频"、想重新生成某个场景的视频、或视频生成中断需要续传时使用。支持整集批量、单场景、断点续传等模式。
---

# 生成视频

## 模式自动分派

脚本在读取剧本后检测顶层结构，自动路由到对应 executor：

| 剧本特征 | 路由 | 输出目录 |
|---|---|---|
| `content_mode == "reference_video"` 或存在 `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `segments[]`（narration） | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `scenes[]`（drama） | 同上 | `videos/scene_{scene_id}.mp4` |

参考模式跳过分镜图要求，直接把 `{script_file}` 丢给 executor；executor 自行读取 unit.references → 从 characters/scenes/props 三 bucket 解析 sheet 图 → 内存压缩 → 渲染 prompt → 调 VideoBackend。

为每个场景/片段/unit 创建视频。storyboard/grid 模式用分镜图作为起始帧；reference_video 模式用角色/场景/道具参考图作为 `reference_images`，跳过分镜环节。

> 画面比例、时长等规格由项目配置和视频模型能力决定，脚本自动处理。

## 工具调用

通过 MCP 工具入队：

| 操作 | 工具 |
|------|------|
| 整集生成（默认） | `mcp__arcreel__generate_video_episode({"script": "episode_1.json"})` |
| 断点续传 | `mcp__arcreel__generate_video_episode({"script": "episode_1.json", "resume": true})` |
| 单场景 | `mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1S01"})` |
| 批量自选 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S05", "E1S10"]})` |
| 自选 + 续传 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": [...], "resume": true})` |
| 全部待处理（独立模式） | `mcp__arcreel__generate_video_all({"script": "episode_1.json"})` |

> 所有任务一次性提交到生成队列，由 Worker 按 per-provider 并发配置自动调度。
> 集号从 script 顶层 `episode` 或文件名推导，无需手动传。
> `reference_video` 模式下 `scene_id` / `scene_ids` 会被忽略，转整集生成。

## 工作流程

1. **加载项目和剧本** — 确认所有场景都有 `storyboard_image`
2. **生成视频** — 脚本自动构建 Prompt、调用 API、保存 checkpoint
3. **审核检查点** — 展示结果，用户可重新生成不满意的场景
4. **更新剧本** — 自动更新 `video_clip` 路径和场景状态

## Prompt 构建

Prompt 由脚本内部自动构建，根据 content_mode 选择不同策略。脚本从剧本 JSON 读取以下字段：

**image_prompt**（用于分镜图参考）：scene、composition（shot_type、lighting、ambiance）

**video_prompt**（用于视频生成）：action、camera_motion、ambiance_audio、dialogue、narration（仅 drama）

- 说书模式：`novel_text` 不参与视频生成（后期人工配音），`dialogue` 仅包含原文中的角色对话
- 剧集动画模式：包含完整的对话、旁白、音效
- Negative prompt 自动排除 BGM

## 生成前检查

- [ ] 所有场景都有已批准的分镜图
- [ ] 对话文本长度适当
- [ ] 动作描述清晰简单

### reference_video 模式

- [ ] 所有 unit 引用的角色 / 场景 / 道具在 project.json 三 bucket 中已注册且 `*_sheet` 文件存在
- [ ] 每 unit shots 数 ≤ 4，总时长 ≤ 模型上限
- [ ] references 数 ≤ 模型 `max_reference_images`

> 参考生视频模式下，脚本输出命名为 `{unit_id}.mp4`，位于 `reference_videos/` 目录。
