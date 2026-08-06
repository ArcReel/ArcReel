---
name: manage-project
description: 项目数据：内部读取流程状态，修改项目资产、概述与设置，并查询视频能力。
user-invocable: false
---

# 项目数据

优先使用工具 schema；本文只保留跨工具语义。

## 工具

- `mcp__arcreel__get_workflow_status`：阶段、缺失、陈旧、审核门、下一动作；
- `mcp__arcreel__patch_project`：资产 upsert、settings 或 overview，三种形态一次只选一种；
- `mcp__arcreel__complete_asset_inventory`：标记当前 source revision 的资产分析已完成，空 bucket 合法；
- `mcp__arcreel__get_video_capabilities`：当前项目路线下的时长和参考限制；
- `mcp__arcreel__plan_episodes` / `reset_episode_planning`：账本规划与重置；
- `mcp__arcreel__confirm_product_sheet_review`：确认当前产品 sheet revision。

## 语义约束

- 修改已有人工资产描述或 overview 需要用户显式意图；新增提取默认保留已有字段。
- `generation_mode` 创建后不可变；用户要切换路线时说明需要新建项目。
- `grid_storyboard` 由 Web 设置页控制。切换后 provenance 会将受影响分镜与视频标为 stale。
- `source_language` 以用户显式配置优先；未获用户确认时由 overview 自动推断，不由 agent 猜测写入。
- `narration_voice` / `narration_speed` 修改后，已有音频因输入 hash 变化变为 stale。

所有写入返回后用 workflow status 验证 revision 与预期状态。
