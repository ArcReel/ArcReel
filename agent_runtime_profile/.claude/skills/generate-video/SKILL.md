---
name: generate-video
description: 为剧本场景生成视频片段。当用户说"生成视频"、"把分镜图变成视频"、想重新生成某个场景的视频、或视频生成中断需要续传时使用。支持整集批量、单场景、断点续传等模式。
---

# 生成视频

## 模式自动分派

路由由项目 `generation_mode` × `content_mode` 唯一决定（剧本不携带路线信息）。MCP 工具读 `project.json` 定路线，再校验剧本骨架是否与该路线匹配——失配即拒绝入队，不按骨架改判路线：

| 项目路线 × 内容模式 | 应有骨架 | 路由 | 输出目录 |
|---|---|---|---|
| `reference_video` × narration / drama | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `reference_video` × ad | `shots[]`（工具自动派生分组） | 同上 | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | 同上 | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | 同上 | `videos/scene_{shot_id}.mp4` |

骨架失配多见于路线收敛前遗留的旧剧本（例如 `storyboard` 项目里躺着一份 `video_units[]` 剧本）：存量迁移不改剧本文件，正解是按项目当前路线重跑预处理与剧本生成，而不是指望旧剧本被执行。

参考模式跳过分镜图要求，直接把 `{script_file}` 丢给 executor；executor 自行读取 unit.references → 从 characters/scenes/props 三 bucket 解析 sheet 图 → 内存压缩 → 渲染 prompt → 调 VideoBackend。

为每个场景/片段/unit 创建视频。storyboard 模式用分镜图作为起始帧（`grid_storyboard=true` 时起始帧来自宫格切割）；reference_video 模式用角色/场景/道具参考图作为 `reference_images`，跳过分镜环节。

### ad 参考直出（派生分组）

ad 剧本骨架唯一（平铺 `shots[]`，不存在 `video_units`）。项目 `generation_mode == "reference_video"` 时，`generate_video_*` 工具会自动：

1. 把连续镜头**派生分组**为 video_unit（每 unit ≤4 个 shot，unit 总时长受供应商单次生成上限约束），索引（unit → shot_ids + 参考集）写入剧本 `reference_units` 字段——仅引用 shot_id，shots 仍是内容唯一真相
2. 每个 unit 的参考集从成员镜头继承：产品参考全量注入且绝对优先（有 sheet 时 sheet + 原图，无 sheet 时原图直注，附高保真指令），其后是角色/场景/道具 sheet
3. 按 unit 入队 `reference_video` 任务，prompt 由镜头的 image_prompt/video_prompt 自动拼装（含 `Shot N (Xs):` 切镜结构），口播文案不进画面 prompt

镜头编辑（增删/改时长/重排）后再次调用生成工具即自动重新派生；成员与参考集未变的 unit 保留已生成的视频，不重复消耗。

广告/短片项目的产品镜头（`products_in_shot` 非空）在视频层自动二次注入产品参考：视频后端支持在首帧请求上叠加参考输入时把产品参考随请求注入并附高保真指令，不支持时正常降级——无需手动指定，video_prompt 不必复述产品外观。

> 画面比例、时长等规格由项目配置和视频模型能力决定，MCP 工具自动处理。

## 工具调用

**重要：生成视频必须调用下列 MCP 工具入队。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

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

### reference_video 模式的时长确认

`video_units` / ad 派生 unit 的申请时长按档位取整（容量语义：申请能装下剧本总时长的最小
档位，成片不裁剪；总时长超过最大档位时按最大档位申请，成片会短于剧本编排——此时首选回到
阶段 3 重拆该 unit 把内容分摊开，确认接受变短再继续）。取档用的是该 unit **引用状态对应**的那套
生效档位——带 `@` 引用与不带引用可能不同（narration / drama 见 `get_video_capabilities` 的
`reference_unit_durations.with_references` / `.without_references`；ad 的镜头时长为自由整数，
不走档位枚举），不是型号声明的 `supported_durations` 全集。申请秒数与剧本
编排的总时长不一致时，`generate_video_episode` / `generate_video_scene` /
`generate_video_all` / `generate_video_selected` 四个工具在 reference_video 路径下
**首次调用不会入队任何任务**，只返回待确认清单（每个 unit 的剧本总时长、将申请的
秒数、成片会更长还是更短）。据此如实转述给用户，用户同意后带 `confirm_duration:
true` 重新调用同一工具完成入队；不带该参数的重复调用仍不入队。总时长本身就是档位
成员、或模型能力当前不可解析时，单次调用直接入队，行为与不启用取档时一致。

```text
mcp__arcreel__generate_video_episode({"script": "episode_1.json", "confirm_duration": true})
```

## 工作流程

1. **加载项目和剧本** — storyboard 模式确认所有场景都有 `storyboard_image`；reference_video 模式无分镜图，改为确认各 unit 的引用资产 sheet 图齐备（narration / drama 按此准备；ad 参考直出缺图不阻断入队，见下方软口径）
2. **生成视频** — MCP 工具自动构建 Prompt、调用 API、保存 checkpoint
3. **审核检查点** — 展示结果，用户可重新生成不满意的场景
4. **更新剧本** — 自动更新 `video_clip` 路径和场景状态

## Prompt 构建

Prompt 由 MCP 工具内部自动构建，根据 content_mode 选择不同策略。从剧本 JSON 读取以下字段：

**image_prompt**（用于分镜图参考）：scene、composition（shot_type、lighting、ambiance）

**video_prompt**（用于视频生成）：action、camera_motion、ambiance_audio、dialogue、narration（仅 drama）

- 说书模式：`novel_text` 不参与视频生成（旁白经 `generate-narration-audio` 单独配音），`dialogue` 仅包含原文中的角色对话
- 剧集动画模式：包含完整的对话、旁白、音效
- Negative prompt 自动排除 BGM

## 生成前检查

按项目 `generation_mode` 取对应清单，不要交叉套用——reference_video 剧本是 `video_units[]`（ad 为 `shots[]` 派生分组），没有场景级 `storyboard_image`，拿 storyboard 清单去卡会把合法任务挡在入队之前。

**通用**

- [ ] 对话文本长度适当
- [ ] 动作描述清晰简单

### storyboard 模式（含 `grid_storyboard=true`）

- [ ] 所有场景都有已批准的分镜图

### reference_video 模式

- [ ] 每 unit shots 数 ≤ 4，总时长 ≤ 模型上限
- [ ] references 数 ≤ 模型 `max_reference_images`
- [ ] （narration / drama）所有 unit 引用的角色 / 场景 / 道具在 project.json 三 bucket 中已注册且 `*_sheet` 文件存在
- [ ] （ad）产品原图已上传——缺图不阻断入队，但会让保真注入退化为纯文本

> 参考生视频模式下，输出命名为 `{unit_id}.mp4`，位于 `reference_videos/` 目录。
> ad 参考直出按软口径处理参考：缺失的 sheet/原图跳过并在任务结果里告警（不像
> narration/drama 那样硬失败）；产品镜头缺产品图会让保真注入退化为纯文本，
> 生成前应确认产品原图已上传。
