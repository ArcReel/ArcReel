---
name: manga-workflow
description: 广告/短片项目的工作流入口。当用户提到做视频、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我做一条带货视频"、"继续"、"下一步"、"看看项目进度"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: ad -->

# 广告/短片工作流（当前阶段）

本项目为**广告/短片模式**（ad）：单视频、恒单集（剧本即 `scripts/episode_1.json`）、按 `target_duration` 规划镜头。**没有分集概念**——不要做分集规划、拆分或小说源文件处理。

广告/短片的端到端编排工作流（brief → 带货框架脚本 → 产品保真分镜 → 视频 → 剪映导出）正在分阶段落地。本 skill 当前只做轻量引导：

## 当前可执行的步骤

1. **确认项目状态**：Read `project.json`，确认 `title`、`content_mode`（固定 `ad`）、`target_duration`（目标总时长，秒）、`brief`（创作诉求，可为空）、`generation_mode`（`storyboard` / `reference_video`，`grid` 不开放）
2. **创作输入**：`brief` 为空时引导用户补充创作诉求（产品/主题、卖点、目标人群）；通过 `mcp__arcreel__patch_project` 写入
3. **资产定义与设计图**：角色/场景/道具定义写入 `project.json` 后 dispatch `generate-assets` subagent 生成设计图
4. **单镜头生成**：已有剧本时可用 `generate-storyboard` / `generate-video` 针对单个镜头生成或重生成

## 边界

- 带货框架脚本自动生成、产品资产与产品保真注入、参考直达出片等环节尚未上线；用户问到时如实说明即将提供，**不要**套用 narration/drama 的小说拆分流程替代
- 剧本数据结构为平铺 `shots[]`（`shot_id` 格式 `E1S{n}`），每镜头携带 `section` 标签与一等口播文案 `voiceover_text`；剧本总时长应贴近 `target_duration`，偏差大时提醒而非阻塞
