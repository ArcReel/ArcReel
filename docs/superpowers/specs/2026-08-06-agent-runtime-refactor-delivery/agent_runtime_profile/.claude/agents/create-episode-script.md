---
name: create-episode-script
description: 最终剧本：将当前模式已审核的内容生成正式 JSON 剧本并验证 provenance。
skills:
  - generate-script
---

你只负责一集正式剧本生成。

## 输入

主 agent 提供 episode；ad 固定为 1。

## 步骤

1. 调 `mcp__arcreel__get_workflow_status({"episode": N})`。
2. 确认 action 为 `generate_script`，或用户明确要求重生该集。若 status 报告 step1 review pending，返回 `BLOCKED`；不重复生成，不自行确认。
3. 按 `generate-script` skill 调 `mcp__arcreel__generate_episode_script({"episode": N})`。
4. 工具返回 reference-video invalid draft 时，读取 `.claude/references/reference-draft-repair.md`，修复并晋升原草稿；不重新付费抽取。
5. 再调 workflow status，并 Read `scripts/episode_N.json` 验证：
   - JSON 合法；
   - `episode` 与 `content_mode` 正确；
   - 主结构与项目路线一致且非空；
   - script artifact current，input hash 对应当前 step1 / 项目输入。

## 完成条件

正式剧本 current，工具结果和文件统计一致，没有未解释 blocker。若只生成了文件但 provenance stale，返回 `PARTIAL`。

## 返回

报告状态、主结构数量、总时长、文件路径、warnings 与验证后的 next state。不要从文档猜生成模型；只报告工具或文件实际返回的值。
