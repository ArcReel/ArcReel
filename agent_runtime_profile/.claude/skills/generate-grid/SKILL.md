---
name: generate-grid
description: 生成宫格分镜图。当用户说"生成宫格"、"宫格生图"、"宫格模式生成分镜"时使用。自动按 segment_break 分组，选择最优宫格大小，生成首尾帧链式宫格图并切割分配。
---

# 生成宫格分镜图

为 grid 模式项目生成宫格分镜图。自动按 segment_break 分组，每组生成一张宫格大图，切割后形成首尾帧链式结构。

## 前置条件

- 项目 `generation_mode` 为 `"grid"`
- 剧本已生成（scripts/episode_N.json 存在）
- 角色/场景/道具设计图已生成（用作参考图）

## 工具调用

| 操作 | 工具 |
|------|------|
| 整集生成 | `mcp__arcreel__generate_grid({"script": "episode_1.json"})` |
| 指定场景所在的组 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S02", "E1S03"]})` |
| 列出当前分组信息 | `mcp__arcreel__generate_grid({"script": "episode_1.json", "list_only": true})` |

## 输出

- 宫格大图保存到 `grids/grid_{id}.png`
- 切割后的首帧/尾帧保存到 `storyboards/scene_{id}_first.png` / `scene_{id}_last.png`
- 帧链元数据保存到 `grids/grid_{id}.json`
