---
name: generate-video
description: 为分镜或自包含视频单元生成视频。当用户要求生成或重做视频时使用；支持整集、单项与批量自选。
---

# 生成视频

## 路由

让 MCP 工具读取 `project.json`，按 `generation_mode` × `content_mode` 分派，并校验剧本骨架：

| 生成模式×创作类型 | 应有骨架 | 分派 | 输出目录 |
|---|---|---|---|
| `reference_video` × narration / drama / ad | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | 同上 | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | 同上 | `videos/scene_{shot_id}.mp4` |

骨架失配时停止入队，按项目生成模式重生成剧本。参考生视频直接消费自包含 `video_units[]`，跳过分镜图。

### 参考生视频

把每个 `video_units[]` 条目视为一次独立生成调用：

- 从视频单元正文（`text`）构造统一引用语法 prompt。
- 参考图执行期从正文的 `@[名称]` 按首次提及顺序解析，无特殊排序；有资产图用资产图，否则用该资产的全部原图。
- 让生成预检把视频单元编排时长投影到供应商申请档位。
- 遇到 `needs_replan` 或发声归属问题时停止该视频单元，先修复规划内容。
- 整集生成只复用 `generated_assets.video_clip` 明确指向的现行成片；同名孤儿文件不代表该视频单元已完成。

让项目配置、剧本模型与视频能力决定比例、时长和参考图上限，不在调用参数中另写一套数值。

## 工具调用

使用 MCP 工具入队；本 skill 不提供 Python 或 Shell 生成脚本。

| 操作 | 工具 |
|------|------|
| 整集生成（默认操作） | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1}})` |
| 单分镜 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1S01"]}})` |
| 批量自选 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1S01", "E1S05", "E1S10"]}})` |
| 全部待处理 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "all"}})` |

把 `target.ids` 在分镜图生视频解释为分镜 ID，在参考生视频解释为 `unit_id`。整集生成的 `target.episode_id` 是剧本所属那一集的集 ID（与文件名 `episode_{集 ID}.json` 中的数字相同，取计划 `target.episode`），不是第几集。

### 点名重新生成视频单元

在参考生视频传 `video_units[].unit_id`：

| 操作 | 工具 |
|------|------|
| 重新生成单个视频单元 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1U2"]}, "force": true})` |
| 重新生成多个视频单元 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1U2", "E1U3"]}, "force": true})` |

一次调用完成入队并返回 durable batch；按返回的 `poll_after_seconds` 调用 `get_generation_batch`，直到 `done: true` 后再处理结果：

- 把点名视为强制重做，覆盖已有成片。
- 已有在途任务时不自动 force 重做；等待并读取其 batch 结果，避免对刚完成的目标再次付费提交。
- 只生成剧本中点名的自包含视频单元；未命中的 ID 记为 `blocked`，带 `generation_unit_not_found`。
- 调用中断后查询 durable batch；只把未成功的 ID 用 `selected`、`force: false` 重发，已完成项归 `skipped`。
- 结果按 `requested / succeeded / failed / blocked` 逐 ID 返回，
  结构与问题码见 `.claude/references/generation-results.md`。

### 切换 current 版本

视频单元的 current 版本决定预览、剪辑与导出用哪一版。挑更好的版本时调用
`mcp__arcreel__select_video_version({"unit_id": "E1S01", "version": 2})`，立即生效，无需用户确认、不收费：

- 首轮审阅后，某单元的另一候选版本更好，改用它。
- 重新生成后验收，新版本不如旧版，改回旧版本。
- 版本号不存在时，按返回的 `params.available_versions` 重选。

### 视频与旁白

视频请求只看剧本：一律按视频单元的编排时长申请档位，准入、报价与恢复都与项目的旁白交付方式无关，
未配置 TTS 的项目照常生成与恢复视频。旁白配音在剪辑阶段单独生成（`generate-narration-audio`）。
`generate_videos` 没有 `narration_delivery` 参数，带上会被拒绝。

### 整批准入判定与档位确认

视频整批请求是**全有或全无**：准入 `admitted` 时整批入队，`blocked` 或 `confirmation_required` 时
**一个任务都不入队**。Web 与 Agent 走同一套准入与同一套请求选择语义，没有 Agent 专属的宽松通道。

参考生视频按视频单元的引用状态选择生效档位，把编排时长投影到模型支持的申请档位。申请档位不同于编排时长时
预检返回 `reference_duration_confirmation_required`，逐档位向用户说明涉及的视频单元、编排秒数、申请秒数
与变长/变短；确认后经 `confirmed_request_durations`（按 unit_id 记档位）让**原目标集合仍作为一批重发**：

```text
mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1},
                               "confirmed_request_durations": {"E1U1": 8}})
```

被拒时逐视频单元报告 `unit_id`、`problem.code`、原因与 `problem.action`；通过的视频单元带
`generation_batch_admission_withheld`，其 `blocked_unit_ids` 指出是被谁挡住的，如实说明这层因果。
**不要把整批拆小去先跑通过的那一半**——那既绕开全有或全无，也会重复提交已经付过费的视频单元。
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
2. 在分镜图生视频确认分镜图可用；在参考生视频确认视频单元正文非空、编排时长合法。
3. 调用 MCP 工具入队，处理准入拒绝与档位确认。
4. 展示结果，按用户选择点名重做不满意的分镜或视频单元。
5. 以工具写回的 `generated_assets.video_clip` 作为成片归属。

## Prompt 构建

让 MCP 工具按生成模式构建 Prompt：

- 分镜图生视频读取 `image_prompt`、`video_prompt` 与分镜图。
- 参考生视频读取视频单元正文（`text`）与编排时长。
- 旁白/解说的分镜图生视频不把 `novel_text` 放入视频 Prompt；旁白由独立音频流程处理。
- 自动应用音频开关、角色发声归属与负面 Prompt 规则。

## 生成前检查

按项目生成模式检查：

- storyboard：每个目标分镜都有可用分镜图，动作与发声内容可执行。
- 参考生视频：每个目标视频单元有非空正文、合法编排时长、单一发声归属，且未标记 `needs_replan`。
- reference：参考图由服务端在执行期从正文 `@[名称]` 的首次提及顺序解析；未登记的提及只产生警告、不阻断入队，让服务端按 `max_reference_images` 裁剪。
- reference：输出路径为 `reference_videos/{unit_id}.mp4`。
