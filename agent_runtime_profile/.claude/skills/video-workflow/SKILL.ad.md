---
name: video-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色资产图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流

广告/短片只有一集，剧本是 `scripts/episode_1.json`。按 `target_duration` 规划镜头，跳过源文导入和分集规划。

## 读取计划

调用 `mcp__arcreel__get_workflow_plan({})`，按 `next_action.type` 执行。用户点名操作时，先查 `status.operations`。说明结果并刷新计划，由用户决定是否继续下一动作。阻断、分岔、未单列的动作与 `none` 见 [工作流计划](../../references/workflow-plan.md)。

## `collect_project_input` / `draft_selling_points`：创作输入

- 带货项目缺商品或原图时，引导用户在 Web 端添加商品和上传原图。用户可在项目概览填写「创作灵感」，也可由 Agent 按对话通过 `mcp__arcreel__patch_project` 写入 `brief`、商品描述和品牌。
- 通用短片只需把主题、情绪、画面风格和节奏写进 `brief`。无需询问商品，也不套用带货段落。角色、场景、道具照常可用。
- `status.content.products_without_selling_points` 非空时可提出起草卖点。按创作诉求、商品描述与原图起草，用户确认后写回。没有卖点也可生成剧本。

## `generate_script`：生成广告剧本

有 `brief` 或商品后，调用 `mcp__arcreel__generate_episode_script({"episode_id": target.episode})`。结果直接成为正式脚本，分镜图生视频为 `shots[]`，参考生视频为 `video_units[]`。回执列出新增登记的引用资产。向用户展示分镜或视频单元与口播内容。总时长偏离 `target_duration` 时提示。

用户要求整份重做时按 `generate-script` 的丢失清单确认流程执行。只改部分正文或顺序时用 `patch_episode_script`。

## `generate_asset_sheets`：生成本集资产图

调用 `mcp__arcreel__generate_assets({"episode_id": next_action.args.episode_id})`，由服务端选出本集引用且缺图的商品、角色、衍生、场景与道具。生成后按 [生成资产图](../generate-assets/SKILL.md) 的「审核检查点：审图」派发 `review-images`，将结果交给用户确认。

## `generate_storyboards`：生成分镜图

先执行下节的商品保真检查，再调用 `mcp__arcreel__generate_storyboards`。计划点名分镜时把 `requested_ids` 作为 `segment_ids`，补缺时省略。写入后按 [生成分镜图](../generate-storyboard/SKILL.md) 的「审核检查点：审图」派发 `review-images`，将结果交给用户确认。带货项目确认商品分镜保真后再提交视频生成。

## 商品保真

- 开始生成分镜图前，或参考生视频模式下首次生成视频前，若商品有 `product_sheet`，请用户在商品资产页确认它与原图一致。不一致时按用户要求重做参考图。只有原图时直接继续。
- 分镜图生视频按 `products_in_shot` 注入商品参考图，有资产图时同时带资产图和原图。提示词用 `@[商品名]` 指认，无需重述商品外观。后续视频使用分镜图，不重复注入商品参考；氛围分镜仅用项目风格。
- 参考生视频用资产图，按正文 `@[名称]` 首次出现的顺序排列。商品没有资产图时用原图。角色、场景、道具缺图时，先补资产图。
- 原图含清晰真人面孔时提示用户可能触发供应商审核。规划画面时可用商品特写、手部、背影或剪影。遇到审核拒绝时报告原因，由用户选择是否调整构图、在设置页换用支持该场景的供应商或更换素材。若人脸来自自动注入的参考图，修改构图无法移除参考图中的人脸，引导用户裁剪或更换原图。

## `repair_video_units`：修正视频单元

用 `mcp__arcreel__get_episode_script({"script": target.script_filename})` 读取正文与 revision。对 `requested_ids` 对应单元编写有序的 `update`，再用 `mcp__arcreel__patch_episode_script` 一次提交。每个单元只容纳角色发声、无归属旁白或无人声中的一种。工具根据正文和编排时长重算 `needs_replan`。向用户确认重做意愿后点名重新生成。

分镜图生视频用 `mcp__arcreel__get_video_capabilities` 读取生效分镜时长。参考生视频使用剧本模型允许的正整数编排时长，申请档位由预检决定。具体字段见 `.claude/references/generation-modes.md`。

## `confirm_request_duration` / `generate_videos`：视频生成

按 [工作流计划](../../references/workflow-plan.md) 核对整批准入。需要确认的申请时长按 `admission.confirmation.tiers[]` 说明涉及的单元、时长与费用。把用户确认的 `confirmed_request_durations` 带回下一次查询及原目标集合的生成请求。

准入为 `admitted` 且用户已授权时，按计划提交：

- 点名重做：`mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "selected", "ids": requested_ids}, "force": true})`。
- 整集补缺：`mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "episode", "episode_id": target.episode}})`。

按 [生成结果](../../references/generation-results.md) 报告 `succeeded`、`failed`、`blocked` 和 `skipped`，分别说明步骤、任务、供应商提交与产物时效。

## `create_edit_timeline`：剪辑

TTS 项目在首轮自动剪辑前，按 `generate-narration-audio` 补齐缺失旁白。调用 `mcp__arcreel__create_timeline({"from": "script", "episode": target.episode, "name": "完整版"})` 新建首条剪辑时间线，随后按 `edit-video` 剪辑，按用户要求出成片或导出剪映草稿。以服务端返回的声音归属和字幕时序为准。
