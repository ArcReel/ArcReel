---
name: video-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色资产图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流

本项目为**广告/短片**（ad）：单视频、恒单集（剧本即 `scripts/episode_1.json`）、按 `target_duration` 规划镜头。**没有分集概念**——不要做分集规划、拆分或小说源文件处理。

## 工作流步骤

先调用 `mcp__arcreel__get_workflow_plan({})` 取回权威计划。把 `steps[]`、`blockers` 与 `next_action`
当作阶段判断的唯一真相源（`plan.status` 内嵌 `project` / `target` / `gates` / `artifacts` 快照）；
Read 只补充创作输入与商品 soft gate 信息。每次动作完成后刷新计划。
`next_action.type == "none"` 时展示 blockers 并停止变更。

计划的字段含义、完整受控动作表、旁白交付、整批准入判定、四条状态轴与 stale / 历史纪律，见
[.claude/references/workflow-plan.md](../../references/workflow-plan.md)。**本 skill 不重复一张按生成模式
展开的步骤表**：哪些步骤适用由 `plan.steps[].required` 表达。

按 `next_action.type` 直接进入对应步骤：

- `next_action.type == "collect_project_input"` → 步骤 2
- `next_action.type == "draft_selling_points"` → 步骤 3
- `next_action.type == "generate_script"` → 步骤 5
- `next_action.type == "generate_asset_sheets"` → 步骤 4
- `next_action.type == "repair_video_units"` → 步骤 7 的视频单元修复
- `next_action.type == "generate_storyboards"` → 步骤 7 的 storyboard 单图路径
- `next_action.type == "generate_grid"` → 步骤 7 的 storyboard 宫格路径
- `next_action.type == "generate_videos"` → 步骤 7 的视频生成
- `next_action.type == "export"` → 步骤 8

调用工具或 dispatch 子智能体时带入 `target.episode`、`next_action.args` 与 `requested_ids`，不二次检查 `generation_mode` 或 `grid_storyboard` 来改选阶段。步骤内的商品原图与 sheet 过目规则是执行动作前的 soft gate。

1. **确认项目状态**：按计划确认 `content_mode=ad` 与项目级 `generation_mode`；Read `project.json` 补充 `title`、`target_duration`、`brief` 与 `products`。生成模式创建后不可更改。
2. **创作输入**：带货项目未登记商品或缺原图时，引导用户在 WebUI 上传；原图是保真锚点。用 `mcp__arcreel__patch_project` 写商品描述、品牌与 `brief`。通用短片不索要商品。
3. **起草卖点**：商品的 `selling_points` 为空时，根据 brief、描述与原图起草，与用户确认后用 `patch_project` 写回。
4. **资产定义与资产图**：定义角色、场景、道具后，对每个类型取 `artifacts.asset_sheets[type].missing_ids` 与 `requested_ids` 的交集，调用 `mcp__arcreel__generate_assets({"type": type, "names": [该类型 requested_ids]})`。商品 sheet 在商品资产页生成。
5. **一键生成剧本**：调用 `mcp__arcreel__generate_episode_script({"episode": 1})`。广告不走 script_plan；分镜图生视频直接产出 `shots[]`，参考生视频直接产出自包含 `video_units[]`。总时长偏离 `target_duration` 时提醒用户，不阻塞保存。
6. **sheet 过目（软门禁）**：商品有 `product_sheet` 时，请用户在首次分镜或参考生视频生成前确认它与真品一致；只有原图时直接继续。
7. **编排与生成**：

   - `repair_video_units`：调用 `mcp__arcreel__get_episode_script({"script": target.script_filename})` 读取正文与 revision，只处理 `requested_ids` 对应的视频单元；再用一次 `mcp__arcreel__patch_episode_script({"script": target.script_filename, "base_revision": revision, "operations": [{"op": "update", "id": unit_id, "fields": {"text": "...", "duration_seconds": ...}}]})` 写回全部视频单元的完整规划（每个视频单元一条有序 update）；由工具重算 `needs_replan`，不要直接编辑标记。每个视频单元保持单一发声归属，商品/角色/场景/道具都用 `@[名称]`。修复后立即用 `generate_videos` 的 `selected` scope 与 `force: true` 点名重做这些视频单元，再刷新状态。
   - `next_action.type == "generate_storyboards"` → 调
     `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
   - `next_action.type == "generate_grid"` → 调
     `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": requested_ids})`
   - `next_action.type == "choose_narration_delivery"` → 本次请求含叙述旁白。**显式说明**并在
     「使用当前 TTS」与「后期配音」之间二选一，选择经 `narration_delivery` 带进下一次
     `mcp__arcreel__get_workflow_plan`（不持久化，每次查询都要重新带上）。未配置 TTS 时默认后期配音，
     不要为了让视频继续而建议用户去配置 TTS 供应商；选 TTS 时先显式生成并让用户试听，再按
     预检返回的 `problems[].action` 处理（action 是权威，不要按 `code` 自己推）
   - `next_action.type == "confirm_request_duration"` → 按 `admission.confirmation.tiers[]` 逐档位展示
     涉及的视频单元与费用，确认后经 `confirmed_request_durations` 连同仍成立的 `narration_delivery` 一起带回
   - `next_action.type == "generate_videos"` → 先看 `plan.steps[].admission.decision`：只有 `admitted`
     才入队，`blocked` / `confirmation_required` 时**一个任务都不入队**，逐视频单元报告 `unit_id`、
     `problems[].code`、原因与 `problems[].action`（被 `blocked_unit_ids` 连累的视频单元带
     `generation_batch_admission_withheld`，如实说明不是它自身有问题）；修掉被拒视频单元后整批重来，
     不拆批先跑通过的那一半。入队时若 `requested_ids` 非空则调
     `mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "selected", "ids": requested_ids}, "force": true, "narration_delivery": chosen_narration_delivery})`；
     `requested_ids` 为空时才调 `mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "episode", "episode": target.episode}, "narration_delivery": chosen_narration_delivery})`。
     `narration_delivery` 必填，填本次已向用户确认的那个值：省略或写错值一律返回工具错误、不入队
     任何任务，也不退回后期配音；没和用户确认过就先走 `choose_narration_delivery`，不要自己填。
     返回后按逐 ID 分账陈述结果（`succeeded` / `failed` / `blocked` / `skipped`），并把 workflow 步骤
     状态、队列任务、供应商 checkpoint、产物时效四轴分开说——「任务成功」不等于「当前产物有效」；
     stale 产物照常可用，是否重做由用户决定，不自动删除或重生已付费产物
   - 带货项目走分镜图生视频时，先审核商品分镜保真度，再产生视频费用；通用短片没有商品分镜，不设这道审核。
   - 参考生视频按自包含视频单元生成，跳过分镜；参考图在执行期按正文 `@[名称]` 的首次提及顺序解析，商品与角色、场景、道具同规则。用户不满意时按 `unit_id` 点名重做。

8. **导出剪映草稿**：视频齐全后引导用户在 Web 端导出。声音归属与字幕时序由服务端 presentation 结果
   决定，预览、下载与剪映草稿消费同一份——**不要自行估算字幕时间轴、不要静音 供应商原音、
   也不要替用户判断 TTS 是否必需**。stale 产物照常可导出，导出不清空也不覆盖旧付费媒体。
   广告不走 in-app `compose-video`。

## 通用短片（无商品）

`products` 为空即通用短片。跳过商品上传、卖点与 product sheet；把 `brief` 补充到足以表达主题、情绪、画面风格与节奏。角色、场景、道具资产照常可用。

## 镜头时长约束

分镜图生视频的分镜时长取视频模型 `supported_durations` 成员，可用 `mcp__arcreel__get_video_capabilities` 查询。参考生视频的视频单元使用剧本模型允许的正整数编排时长，生成预检再投影到供应商档位。发现非法值时先用 `patch_episode_script` 修正。

## 边界

- storyboard 广告以 `shots[]` 为唯一真相源；reference 广告以自包含 `video_units[]` 为唯一真相源。
- 参考生视频的视频单元自持引用语法正文、编排时长、生成资产与规划状态；编辑这些字段后刷新计划。
- 视频单元顺序调整使用 WebUI，字段修复使用 `patch_episode_script`，视频生成使用 `generate-video` skill。
