---
name: generate-video
description: 为剧本场景或自包含 video unit 生成视频。当用户要求生成、重做或续传视频时使用；支持整集、单项与批量自选。
---

# 生成视频

## 路由

让 MCP 工具读取 `project.json`，按 `generation_mode` × `content_mode` 分派，并校验剧本骨架：

| 生成模式 × 创作类型 | 应有骨架 | 分派 | 输出目录 |
|---|---|---|---|
| `reference_video` × narration / drama / ad | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | 同上 | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | 同上 | `videos/scene_{shot_id}.mp4` |

骨架失配时停止入队，按项目生成模式重生成剧本。参考生视频直接消费自包含 `video_units[]`，跳过分镜图。

### 参考生视频

把每个 `video_units[]` 条目视为一次独立生成调用：

- 从 unit 正文（`text`）构造统一引用语法 prompt。
- 参考图执行期从正文的 `@[名称]` 按首次提及顺序解析，无特殊排序；有资产图用资产图，否则用该资产的全部原图。
- 让生成预检把 unit 编排时长投影到供应商申请档位。
- 遇到 `needs_replan` 或发声归属问题时停止该 unit，先修复规划内容。
- 整集生成只复用 `generated_assets.video_clip` 明确指向的现行成片；同名孤儿文件不代表该 unit 已完成。

让项目配置、剧本模型与视频能力决定比例、时长和参考图上限，不在调用参数中另写一套数值。

## 工具调用

使用 MCP 工具入队；本 skill 不提供 Python 或 Shell 生成脚本。

| 操作 | 工具 |
|------|------|
| 整集生成（默认操作） | `mcp__arcreel__generate_video_episode({"script": "episode_1.json", "narration_delivery": chosen_narration_delivery})` |
| 断点续传 | `mcp__arcreel__generate_video_episode({"script": "episode_1.json", "narration_delivery": chosen_narration_delivery, "resume": true})` |
| 单场景 | `mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": chosen_narration_delivery})` |
| 批量自选 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S05", "E1S10"], "narration_delivery": chosen_narration_delivery})` |
| 自选 + 续传 | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": [...], "narration_delivery": chosen_narration_delivery, "resume": true})` |
| 全部待处理（独立模式） | `mcp__arcreel__generate_video_all({"script": "episode_1.json", "narration_delivery": chosen_narration_delivery})` |

每次调用都必须带 `narration_delivery`（见「旁白交付」）：省略或写错值一律返回工具错误、不入队任何任务。
上表的 `chosen_narration_delivery` 是占位符，调用前换成本次已向用户确认的那个值，不要照抄一个具体值。

把 `scene_id` / `scene_ids` 在分镜图生视频解释为分镜 ID，在参考生视频解释为 `unit_id`。集号由剧本元数据或文件名解析。

### 点名重新生成 unit

在参考生视频传 `video_units[].unit_id`：

| 操作 | 工具 |
|------|------|
| 重新生成单个 unit | `mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1U2", "narration_delivery": chosen_narration_delivery})` |
| 重新生成多个 unit | `mcp__arcreel__generate_video_selected({"script": "episode_1.json", "scene_ids": ["E1U2", "E1U3"], "narration_delivery": chosen_narration_delivery})` |

一次调用完成入队、等待与结果回报：

- 把点名视为强制重做，覆盖已有成片。
- 任一目标已有在途任务时等待其完成，再重做整批目标。
- 只生成剧本中点名的自包含 unit；未命中的 ID 记为 `blocked`，带 `generation_unit_not_found`。
- 点名重做不落 checkpoint，忽略 `resume`。
- 结果按 `requested / succeeded / failed / blocked` 逐 ID 返回，
  结构与问题码见 `.claude/references/generation-results.md`。

### 旁白交付

叙述旁白有两条交付路线，**每次请求逐次选择、从不持久化**，经 `narration_delivery` 传入，该参数在四个视频工具上均为必填：

| 取值 | 含义 |
|---|---|
| `post_production` | 后期配音：视频照常生成，旁白留到剪映等后期工具里补 |
| `use_tts` | 使用当前 TTS：按 fresh 旁白音频的实际媒体时长参与时长求解 |

对每次叙述旁白视频请求都要**显式向用户说明并选择**，不要默默沿用上一次，也不要在没问过用户时
直接填 `post_production` 凑够必填项。未配置 TTS 时用户通常选后期配音——那不是工作流缺口，视频照常成片，**不要为了让视频继续而建议用户去配置 TTS 供应商**。
选 `use_tts` 时先显式生成并让用户试听旁白（`generate-narration-audio`），再按预检返回的
`problems[].action` 处理——**action 是权威，不要按 `code` 自己推**：`tts_missing` 先生成、
`tts_stale` / `tts_duration_unavailable` 先重新合成（旧音频保留）、`tts_generating` 与
`tts_conflicts_with_active_narrated_video` 等待在跑的任务后重查（不要重复提交）、
`tts_not_applicable` 改选后期配音、`tts_state_unavailable` 报为独立缺口而不是当作缺失去重生。

`generation_mode == "reference_video"` **只跳过分镜图**，不跳过 audio：旁白交付选择在两种生成模式下都要做。

### 整批准入判定与档位确认

视频批量请求是**全有或全无**：准入 `admitted` 时整批入队，`blocked` 或 `confirmation_required` 时
**一个任务都不入队**。Web 与 agent 走同一套准入与同一套请求选择语义，没有 agent 专属的宽松通道。

按 unit 的引用状态选择生效档位，把编排时长投影到能容纳内容的申请档位。申请档位不同于当前视觉时长时
预检返回 `reference_duration_confirmation_required`，逐档位向用户说明涉及的 unit、编排秒数、申请秒数
与变长/变短；确认后经 `confirmed_request_durations`（按 unit_id 记档位）让**原目标集合仍作为一批重发**。
重发要连同本次请求已选的 `narration_delivery` 一起带上——该参数不持久化，省略会让重发直接失败，
不会退回后期配音把用户选的「使用当前 TTS」悄悄换掉：

```text
mcp__arcreel__generate_video_episode({"script": "episode_1.json", "narration_delivery": "use_tts",
                                      "confirmed_request_durations": {"E1U1": 8}})
```

被拒时逐 unit 报告 `unit_id`、`problem.code`、原因与 `problem.action`；通过的 unit 带
`generation_batch_admission_withheld`，其 `blocked_unit_ids` 指出是被谁挡住的，如实说明这层因果。
**不要把整批拆小去先跑通过的那一半**——那既绕开全有或全无，也会重复提交已经付过费的 unit。
能力无法解析时把工具错误作为 blocker，先修复模型能力声明。

### 结果怎么读、怎么说

`task_state`（队列任务）、`provider_checkpoint`（供应商是否已提交）、`artifact_status`（产物
current / stale / missing / blocked）与 workflow 步骤状态互相独立，**分开陈述**：「任务成功」不等于
「当前产物有效」。`provider_checkpoint.submitted` 为真表示供应商侧很可能已计费；任务
`interrupted` 表示没有供应商裁决，一律按 `problem.action` 决定；该情形通常交回
`wait_for_task`（任务可能仍在跑并正常落地），不要自行改成 `retry`。

stale 产物照常可预览、可导出、可参与成片，服务端会复用、不会自动重生；是否重做由用户明确决定。
不自动删除、覆盖或重生任何已付费产物与历史版本。

## 工作流程

1. 加载项目和剧本，确认骨架与生成模式一致。
2. 在分镜图生视频确认分镜图可用；在参考生视频确认 unit 正文非空、编排时长合法。
3. 与用户确定本次旁白交付方式，调用相应 MCP 工具，处理准入拒绝与档位确认。
4. 展示结果，按用户选择点名重做不满意的分镜或 unit。
5. 以工具写回的 `generated_assets.video_clip` 作为成片归属。

## Prompt 构建

让 MCP 工具按生成模式构建 Prompt：

- 分镜图生视频读取 `image_prompt`、`video_prompt` 与分镜图。
- 参考生视频读取 unit 正文（`text`）与编排时长。
- 旁白/解说的分镜图生视频不把 `novel_text` 放入视频 Prompt；旁白由独立音频流程处理。
- 自动应用音频开关、角色发声归属与负面 Prompt 规则。

## 生成前检查

按项目生成模式检查：

- storyboard：每个目标分镜都有可用分镜图，动作与发声内容可执行。
- reference：每个目标 unit 有非空正文、合法编排时长、单一发声归属，且未标记 `needs_replan`。
- reference：参考图由服务端在执行期从正文 `@[名称]` 的首次提及顺序解析；未登记的提及只产生警告、不阻断入队，让服务端按 `max_reference_images` 裁剪。
- reference：输出路径为 `reference_videos/{unit_id}.mp4`。
