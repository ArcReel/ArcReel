---
name: manga-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流

本项目为**广告/短片模式**（ad）：单视频、恒单集（剧本即 `scripts/episode_1.json`）、按 `target_duration` 规划镜头。**没有分集概念**——不要做分集规划、拆分或小说源文件处理。

## 工作流步骤

先调用 `mcp__arcreel__get_workflow_status({})`。把返回的 `project`、`target`、`blockers`、`gates`、
`artifacts` 与 `next_action` 当作阶段判断的唯一真相源；Read 只补充创作输入与产品 soft gate 信息。每次动作完成后刷新状态。
`next_action.type == "none"` 时展示 blockers 并停止变更。

按 `next_action.type` 直接进入对应步骤：

- `next_action.type == "collect_project_input"` → 步骤 2
- `next_action.type == "draft_selling_points"` → 步骤 3
- `next_action.type == "generate_script"` → 步骤 5
- `next_action.type == "generate_asset_sheets"` → 步骤 4
- `next_action.type == "repair_video_units"` → 步骤 7 的 unit 修复
- `next_action.type == "generate_storyboards"` → 步骤 7 的 storyboard 单图路径
- `next_action.type == "generate_grid"` → 步骤 7 的 storyboard 宫格路径
- `next_action.type == "generate_videos"` → 步骤 7 的视频生成
- `next_action.type == "export"` → 步骤 8

调用工具或 dispatch subagent 时带入 `target.episode`、`next_action.args` 与 `requested_ids`，不二次检查 `generation_mode` 或 `grid_storyboard` 来改选阶段。步骤内的产品原图与 sheet 过目规则是执行动作前的 soft gate。

1. **确认项目状态**：按 workflow-status 确认 `content_mode=ad` 与项目级 `generation_mode`；Read `project.json` 补充 `title`、`target_duration`、`brief` 与 `products`。生成路线创建后不可更改。
2. **创作输入**：带货项目未登记产品或缺原图时，引导用户在 WebUI 上传；原图是保真锚点。用 `mcp__arcreel__patch_project` 写产品描述、品牌与 `brief`。通用短片不索要产品。
3. **起草卖点**：产品的 `selling_points` 为空时，根据 brief、描述与原图起草，与用户确认后用 `patch_project` 写回。
4. **资产定义与设计图**：定义角色、场景、道具后，对每个类型取 `artifacts.asset_sheets[type].missing_ids` 与 `requested_ids` 的交集，调用 `mcp__arcreel__generate_assets({"type": type, "names": [该类型 requested_ids]})`。产品 sheet 在产品资产页生成。
5. **一键生成剧本**：调用 `mcp__arcreel__generate_episode_script({"episode": 1})`。广告不走 step1；storyboard 路线直接产出 `shots[]`，reference 路线直接产出自包含 `video_units[]`。总时长偏离 `target_duration` 时提醒用户，不阻塞保存。
6. **sheet 过目（软门禁）**：产品有 `product_sheet` 时，请用户在首次分镜或参考视频生成前确认它与真品一致；只有原图时直接继续。
7. **编排与生成**：

   - `repair_video_units`：Read `target.script`，只处理 `requested_ids` 对应的 unit。用一次 `mcp__arcreel__patch_episode_script({"script": target.script_filename, "edits": {unit_id: {"shots": [...], "references": [...], "duration_seconds": ...}}})` 写回完整规划；由工具重算 `needs_replan`，不要直接编辑标记。每个 unit 保持单一发声归属，产品/角色/场景/道具都用 `@[名称]`。修复后立即用 `generate_video_selected` 点名重做这些 unit，再刷新状态。
   - `next_action.type == "generate_storyboards"` → 调
     `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
   - `next_action.type == "generate_grid"` → 调
     `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": requested_ids})`
   - `next_action.type == "generate_videos"` → `requested_ids` 非空时调
     `mcp__arcreel__generate_video_selected({"script": target.script_filename, "scene_ids": requested_ids})`；
     `requested_ids` 为空时才调 `mcp__arcreel__generate_video_episode({"script": target.script_filename})`
   - storyboard 路线先审核产品分镜保真度，再产生视频费用。
   - reference 路线按自包含 unit 生成，跳过分镜；产品参考先注入 sheet 与原图，再注入角色、场景、道具 sheet。用户不满意时按 `unit_id` 点名重做。

8. **导出剪映草稿**：视频齐全后引导用户在 Web 端导出。storyboard 路线沿用视频与口播字幕；reference 路线在该骨架阶段只收集视频单元，不承诺配音、字幕或混音。广告不走 in-app `compose-video`。

## 通用短片（无产品）

`products` 为空即通用短片。跳过产品上传、卖点与 product sheet；把 `brief` 补充到足以表达主题、情绪、画面风格与节奏。角色、场景、道具资产照常可用。

## 镜头时长约束

storyboard 分镜时长取视频模型 `supported_durations` 成员，可用 `mcp__arcreel__get_video_capabilities` 查询。reference unit 使用剧本模型允许的正整数编排时长，生成预检再投影到供应商档位。发现非法值时先用 `patch_episode_script` 修正。

## 边界

- storyboard 广告以 `shots[]` 为唯一真相源；reference 广告以自包含 `video_units[]` 为唯一真相源。
- reference unit 自持书写层、编排时长、references、生成资产与规划状态；编辑这些字段后刷新 workflow-status。
- unit 顺序调整使用 WebUI，字段修复使用 `patch_episode_script`，视频生成使用 `generate-video` skill。
