---
name: generate-storyboard
description: 为剧本场景生成分镜图。当用户说"生成分镜"、"预览场景画面"、想重新生成某些分镜图、或剧本中有场景缺少分镜图时使用。自动保持角色和画面连续性。
---

# 生成分镜图

通过生成队列创建分镜图，画面比例根据 content_mode 自动设置。

> 生成模式规格详见 `.claude/references/generation-modes.md`。

## 工具调用

**重要：生成分镜图必须调用下列 MCP 工具入队。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

通过 MCP 工具入队：

| 操作 | 工具 |
|------|------|
| 提交所有缺失分镜图 | `mcp__arcreel__generate_storyboards({"script": "episode_1.json"})` |
| 重新生成指定 ID | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |
| 重新生成多个 ID | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |

> **选择规则**：`segment_ids` 兼容 narration 的 segment_id 与 drama 的 scene_id；未传则提交所有缺失项。
>
> **依赖**：generation worker 必须在线（图像/视频两条独立通道），worker 负责实际生成与速率控制。

## 工作流程

1. **加载项目和剧本** — 确认所有角色都有 `character_sheet` 图像
2. **生成分镜图** — MCP 工具自动检测 content_mode，按相邻关系串联依赖任务
3. **审核检查点** — 展示每张分镜图，用户可批准、要求重新生成，或要求编辑
4. **更新剧本** — 更新 `storyboard_image` 路径和场景状态

## 审核检查点：编辑 vs 重新生成

- **只想改局部**（如某个分镜的手部畸形、背景杂物、光线偏差），且构图/角色一致性满意 →
  用 `mcp__arcreel__edit_images({"resource_type": "storyboard", "script_file": "episode_1.json", "edits": [{"id": "E1S05", "instruction": "去掉背景里多余的路人"}]})`
  保底图微调，不影响 `image_prompt`，一次可批量下发多个分镜
- **想推翻构图或角色/参考图关系**，或 `image_prompt` 本身要改 → 用
  `patch_episode_script` 改 `image_prompt` 后，紧接着调 `generate_storyboards` 重新生成
- 编辑不会更新 `image_prompt`——编辑后再触发 `generate_storyboards` 仍按原 `image_prompt`
  重画，编辑效果只能从版本历史找回

## 角色一致性机制

MCP 工具自动处理以下参考图传入，无需手动指定：
- **character_sheet**：场景中出场角色的设计图，保持外貌一致
- **scene_sheet / prop_sheet**：场景中出现的场景 / 道具设计图
- **产品参考（广告/短片项目）**：镜头 `products_in_shot` 非空时自动注入产品参考并排在所有参考之前（有 product sheet 时 sheet + 原图，无 sheet 时原图直注），同时附加高保真还原指令——image_prompt 无需复述产品外观
- **上一张分镜图**：相邻片段默认引用，提升画面连续性
- 当片段标记 `segment_break=true` 时，跳过上一张分镜图参考

## Prompt 模板

从剧本 JSON 读取以下字段构建 prompt：

```
场景 [scene_id/segment_id] 的分镜图：

- 画面描述：[visual.description]
- 镜头构图：[visual.shot_type]
- 镜头运动起点：[visual.camera_movement]
- 光线条件：[visual.lighting]
- 画面氛围：[visual.mood]
- 角色：[characters_in_scene]
- 动作：[action]

风格要求：电影分镜图风格，根据项目 style 设定。
角色必须与提供的角色参考图完全一致。
```

> 画面比例通过 API 参数设置，不写入 prompt。

## 生成前检查

- [ ] 所有角色都有已批准的 character_sheet 图像
- [ ] 场景视觉描述完整
- [ ] 角色动作已指定

## 错误处理

- 单场景失败不影响批次，记录失败场景后继续
- 生成结束后汇总报告所有失败场景和原因
- 支持增量生成（跳过已存在的场景图）
- 使用 `mcp__arcreel__generate_storyboards({"script": "...", "segment_ids": [...]})` 重新生成失败场景
