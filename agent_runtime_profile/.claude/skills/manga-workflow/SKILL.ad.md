---
name: manga-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流

本项目为**广告/短片模式**（ad）：单视频、恒单集（剧本即 `scripts/episode_1.json`）、按 `target_duration` 规划镜头。**没有分集概念**——不要做分集规划、拆分或小说源文件处理。

## 工作流步骤

1. **确认项目状态**：Read `project.json`，确认 `title`、`content_mode`（固定 `ad`）、`target_duration`（目标总时长，秒）、`brief`（创作诉求，可为空）、`generation_mode`（`storyboard` / `reference_video`，`grid` 不开放）、`products`（产品资产）
2. **创作输入**：`brief` 为空时引导用户补充创作诉求（产品/主题、卖点、目标人群）；通过 `mcp__arcreel__patch_project` 写入
3. **起草卖点（selling_points）**：产品已登记但 `selling_points` 为空时，先从 `brief`、产品描述与产品原图（`reference_images`）中起草卖点列表，与用户确认后经 `mcp__arcreel__patch_project` 写入 products 表——剧本生成会把卖点注入带货框架的 selling_point/demo 段
4. **资产定义与设计图**：角色/场景/道具定义写入 `project.json` 后 dispatch `generate-assets` subagent 生成设计图；产品 sheet 在产品资产页生成
5. **一键生成剧本**：调 `mcp__arcreel__generate_episode_script({"episode": 1})`。ad 不需要 step1 中间文件，prompt 直接来自 brief + 产品信息 + 审定的带货八段框架配比表（按 `target_duration` 选档）；`products` 为空时自动分流为通用短片脚本。生成后剧本总时长偏离 `target_duration` 过大只会记日志提醒，不阻塞
6. **镜头编排与生成**：每镜头口播文案/时长/section 可经 `patch_episode_script` 调整；镜头**顺序**调整只在 WebUI 剧本页提供（agent 侧没有重排工具，用户要求调顺序时引导其到剧本页操作，不要用逐字段互换内容模拟）；storyboard 路径用 `generate-storyboard` / `generate-video` 逐镜头出图出视频

## 边界

- 分镜/视频层的产品保真参考注入、参考直达（reference_video 派生分组）出片、剪映导出收口等环节尚未上线；用户问到时如实说明即将提供，**不要**套用 narration/drama 的小说拆分流程替代
- 剧本骨架唯一：`shots[]` 不随 `generation_mode` 更换；reference_video 路径下单镜头时长为 1-15 秒自由整数，storyboard 路径取视频模型 `supported_durations` 成员
