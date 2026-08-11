---
name: manga-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流

本项目为**广告/短片模式**（ad）：单视频、恒单集（剧本即 `scripts/episode_1.json`）、按 `target_duration` 规划镜头。**没有分集概念**——不要做分集规划、拆分或小说源文件处理。

## 工作流步骤

每次进入工作流、用户说“继续/下一步/查看进度”、以及每次工具或 subagent 完成后，先调用
`mcp__arcreel__get_workflow_status({})`。其 `project`、`target`、`state`、`blockers`、`gates`、
`artifacts` 与 `next_action` 是阶段判断的唯一真相源；Read 只补充创作输入与产品 soft gate 信息，
不根据文件存在性重建阶段。`next_action.type == "none"` 时展示 blockers 并停止变更；其余动作按下列步骤执行，
完成后再次刷新状态。产品 sheet 过目等明确标注的 soft gate 继续叠加在服务端状态之上。

按 `next_action.type` 直接进入对应步骤：

- `next_action.type == "draft_selling_points"` → 步骤 3
- `next_action.type == "generate_script"` → 步骤 5
- `next_action.type == "generate_asset_sheets"` → 步骤 4
- `next_action.type == "generate_storyboards"` → 步骤 7 的 storyboard 单图路径
- `next_action.type == "generate_grid"` → 步骤 7 的 storyboard 宫格路径
- `next_action.type == "generate_videos"` → 步骤 7 的视频生成
- `next_action.type == "export"` → 步骤 8

调用工具或 dispatch subagent 时，把 `target.episode`、`next_action.args` 和 `requested_ids` 原样带入；
不得再按 products、sheet、剧本或媒体文件的存在性改选另一个阶段。步骤内的产品原图与 sheet 过目规则仅是
执行已选动作前的 soft gate。

1. **确认项目状态**：按 workflow-status 返回的 `project` 确认 `content_mode`（固定 `ad`）与 `generation_mode`；Read `project.json` 补充 `title`、`target_duration`、`brief` 与 `products`。用户要求更改生成模式时明确告知路线创建后不可更改，agent 无对应写入权限，也无绕过方式
2. **创作输入**：带货项目而产品未登记或缺原图（`reference_images` 为空）时，引导用户在 WebUI 初始化页或产品资产页上传产品图——原图是产品保真的验收锚点，agent 不能代传图片；产品描述/品牌可经 `mcp__arcreel__patch_project` 代写。`brief` 为空时引导用户补充创作诉求（产品/主题、目标人群、期望风格——卖点留给下一步起草，不在此重复索要），同样经 `patch_project` 写入
3. **起草卖点（selling_points）**：产品已登记但 `selling_points` 为空时，先从 `brief`、产品描述与产品原图（`reference_images`）中起草卖点列表，与用户确认后经 `mcp__arcreel__patch_project` 写入 products 表——剧本生成会把卖点注入带货框架的 selling_point/demo 段
4. **资产定义与设计图**：角色/场景/道具定义写入 `project.json` 后，对每个类型取
   `artifacts.asset_sheets[type].missing_ids` 与 `requested_ids` 的交集，调
   `mcp__arcreel__generate_assets({"type": type, "names": [该类型 requested_ids]})`；产品 sheet 在产品资产页生成
5. **一键生成剧本**：调 `mcp__arcreel__generate_episode_script({"episode": 1})`。ad 不需要 step1 中间文件，prompt 直接来自 brief + 产品信息 + 审定的带货八段框架配比表（按 `target_duration` 选档）；`products` 为空时自动分流为通用短片脚本。生成后剧本总时长偏离 `target_duration` 过大只会记日志提醒，不阻塞
6. **sheet 过目（软门禁）**：产品生成了 `product_sheet` 时，分镜开工前（参考直出路径为首次视频生成前——sheet 直接进 unit 参考集）先请用户到产品资产页确认 sheet 与真品一致（不一致就重新生成 sheet），确认后才继续；无 sheet（仅原图）时直接开工。这是工作流约定，没有系统状态强制
7. **镜头编排与生成**：每镜头口播文案/时长/section 可经 `patch_episode_script` 调整；镜头**顺序**调整只在 WebUI 剧本页提供（agent 侧没有重排工具，用户要求调顺序时引导其到剧本页操作，不要用逐字段互换内容模拟）。两条生成路径：
   - `next_action.type == "generate_storyboards"` → 调
     `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
   - `next_action.type == "generate_grid"` → 调
     `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": requested_ids})`
   - `next_action.type == "generate_videos"` → 调
     `mcp__arcreel__generate_video_episode({"script": target.script_filename})`
   - **storyboard 路径**：分镜生成后引导用户审核产品形象，不合格的点名重生成分镜，在产生视频费用前拦截
   - **reference_video 路径（参考直出）**：视频动作会自动把连续镜头派生分组为 video_unit（每 unit ≤4 镜头、总长受供应商上限约束）、把产品参考与资产 sheet 注入各 unit 并入队生成，跳过分镜步骤。镜头编辑后再次调用即自动重新派生，未变化的 unit 不重复生成；编排变了（stale）或用户对成片不满意的 unit 按 `unit_id` 点名重做，见 `generate-video` skill 的「点名重新生成 unit」

   以上工具选择只看 `next_action.type`，不二次检查 `generation_mode` 或 `grid_storyboard`；dispatch 时把
   `target.episode`、`next_action.args` 与 `requested_ids` 原样传递。

   产品镜头（`products_in_shot` 非空）的分镜与视频生成会自动注入产品参考并附高保真指令，prompt 不必复述产品外观

8. **导出剪映草稿**：视频齐全后引导用户在 Web 端导出剪映草稿（视频轨 + 口播文案字幕轨，字幕在竖屏 safe-zone 内），打开剪映即完整时间线，照口播文案配音后成片。in-app 成片（compose-video）对 ad 不适用——直接走草稿出口

## 通用短片（无产品）

`products` 为空即通用短片，剧本生成自动分流通用 prompt。带货还是通用看**用户诉求**：用户要推某个产品而产品未登记时走步骤 2 的上传引导（剧本生成前给齐产品），诉求不涉及具体产品才按通用短片引导。引导差异：跳过产品相关环节——步骤 2 的产品上传引导、步骤 3 卖点起草、步骤 6 sheet 过目，不向用户索要产品信息；`brief` 是唯一创作输入，引导用户写充实（主题、情绪基调、画面风格、叙事节奏）再生成剧本；角色/场景/道具资产照常可用。

## 镜头时长约束

镜头时长须符合项目路线的约束：storyboard 路径取视频模型 `supported_durations` 成员（可经 `mcp__arcreel__get_video_capabilities` 自查），reference_video 路径为 1-15 秒整数。发现越界镜头时**主动**列出并建议调整值，经 `patch_episode_script` 修正后再生成——不要直接入队让执行层报错。

## 边界

- 剧本骨架唯一：`shots[]` 不随 `generation_mode` 更换；reference_video 路径下单镜头时长为 1-15 秒自由整数，storyboard 路径取视频模型 `supported_durations` 成员
- reference_video 路径的分组索引（剧本 `reference_units` 字段）由工具派生维护，不要手工编辑；shots 才是内容唯一真相
