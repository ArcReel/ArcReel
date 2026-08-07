---
name: generate-grid
description: 生成宫格分镜图。当用户说"生成宫格"、"宫格生图"、"宫格模式生成分镜"时使用。自动按 segment_break 分组，选择最优宫格大小，生成链式过渡帧宫格图并切割分配为各场景起始分镜图。
---

# 生成宫格分镜图

为开启宫格装配的项目生成宫格分镜图。自动按 segment_break 分组，每组生成一张宫格大图，切割后按链式过渡帧结构分配为各场景的起始分镜图（仍走 i2v，与逐张生成的分镜图输入契约相同）。

## 前置条件

- 项目 `generation_mode` 为 `"storyboard"` 且 `grid_storyboard` 为 `true`（宫格装配由用户在 Web 设置页开关，项目创建后不可经 agent 改）
- 剧本已生成（scripts/episode_N.json 存在）
- 角色/场景/道具设计图已生成（用作参考图）

## 工具调用

| 操作 | 工具 |
|------|------|
| 整集生成 | `mcp__arcreel__generate_grid({"script": "episode_1.json"})` |
| 指定场景所在的组 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S02", "E1S03"]})` |
| 列出当前分组信息 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "list_only": true})` |

## 输出

- 宫格大图保存到 `grids/{grid_id}.png`（`grid_id` 自身即带 `grid_` 前缀，如 `grids/grid_a1b2c3d4e5f6.png`）
- 帧链元数据保存到 `grids/{grid_id}.json`
- 切割后的单元格按 `next_scene_id` 分配落盘，文件名与普通分镜图对齐为 `storyboards/scene_{id}.png`（无 first/last 后缀）
