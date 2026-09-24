---
name: generate-grid
description: 生成宫格分镜图。当用户说"生成宫格"、"宫格生图"、"用宫格装配生成分镜"时使用。自动按 segment_break 分组，选择最优宫格大小，生成链式过渡帧宫格联合图；用户审阅联合图并同意后，再切分落格为各分镜的起始分镜图。
---

# 生成宫格分镜图

为开启宫格装配的项目生成宫格分镜图。自动按 segment_break 分组，每组生成一张宫格联合图（分镜数超过单张宫格格数上限的分组会切为多张，末张不足一档时落到更小档并补占位格）。

宫格分两段，两段之间要经用户审阅：

1. `generate_grid` 只生成联合图并记版本，不写任何分镜图。
2. 用户在 Web 宫格面板审阅联合图（可重新生成、上传替换或回滚）。**用户明确同意切分后**，再调 `split_grids` 切分落格：它覆写宫格覆盖的全部分镜图，旧分镜图留在版本历史里可回滚。不要在生成完成后自行切分。

切分后的分镜图仍走 i2v，与逐张生成的分镜图输入契约相同。

## 前置条件

- 项目 `generation_mode` 为 `"storyboard"` 且 `grid_storyboard` 为 `true`（宫格装配由用户在 Web 设置页开关，项目创建后不可经 Agent 改）
- 剧本已生成（scripts/episode_N.json 存在）
- 角色/场景/道具资产图（已生成的会作为参考图带入；一张都没有时退化为纯文生图，画面一致性会明显变差）

## 工具调用

| 操作 | 工具 |
|------|------|
| 整集补缺 | `mcp__arcreel__generate_grid({"script": "episode_1.json"})` |
| 重做指定分镜所在的宫格 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S02", "E1S03"]})` |
| 预览规划与现有宫格状态 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "list_only": true})` |
| 用户同意后切分落格 | `mcp__arcreel__split_grids({"grid_ids": ["grid_a1b2c3d4e5f6"]})` |

- 不传 `scene_ids` 时只补缺：分镜图已齐备的分组复用，联合图已就绪而未切分的宫格不重生成，正在生成的宫格沿用在途任务，都不会重复计费。
- 传 `scene_ids` 是重做请求：包含这些分镜的宫格会重新生成并计费，只在用户明确要求重做时使用。
- 准入是整批的：任一分镜受阻（引用缺口、提示词待生成、与在途宫格部分重叠等），整批不建任务；本身健康的分镜带 `generation_batch_admission_withheld`，已在生成中的宫格照常跑完、其分镜带 `generation_active_task_conflict`（`wait_for_task`），修复全部缺口后重试即可一次提交。

结果按 `requested / succeeded / failed / blocked` 逐**分镜** ID 返回：同组分镜共享一张宫格，这张宫格的入队与任务结果投影到它覆盖的每个分镜。成功分镜的 `artifact_path` 是它所在宫格的联合图 `grids/<grid_id>.png`，未切分的宫格同时列在 `grid_ids_awaiting_split`。结构详见 `.claude/references/generation-results.md`。

`split_grids` 逐宫格返回结果：已切分的列出写入的分镜；仍在生成、没有联合图或不存在的宫格跳过并说明原因。

## 输出

- 宫格联合图保存到 `grids/{grid_id}.png`（`grid_id` 自身即带 `grid_` 前缀，如 `grids/grid_a1b2c3d4e5f6.png`），每次生成/上传记为一个 grids 版本
- 帧链元数据保存到 `grids/{grid_id}.json`（`split_at` 记录最近一次按当前联合图切分落格的时间）
- 切分落格后的单元格按 `next_scene_id` 分配落盘，文件名与普通分镜图对齐为 `storyboards/scene_{id}.png`（无 first/last 后缀），每格覆写前后均入版本史
