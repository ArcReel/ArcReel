---
name: generate-storyboard
description: 为剧本场景生成分镜图。当用户说"生成分镜"、"预览场景画面"、想重新生成某些分镜图、或剧本中有场景缺少分镜图时使用。自动保持角色和画面连续性。
---

# 生成分镜图

通过生成队列创建分镜图，画面比例根据 content_mode 自动设置。

> 生成模式规格详见 `.claude/references/generation-modes.md`。

## 工具调用

通过 SDK in-process MCP tool 入队（主进程跑，绕过 sandbox）：

| 操作 | 工具 |
|------|------|
| 提交所有缺失分镜图 | `mcp__arcreel__generate_storyboards({"script": "episode_1.json"})` |
| 重生指定 ID | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |
| 重生多个 ID | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |

> **选择规则**：`segment_ids` 兼容 narration 的 segment_id 与 drama 的 scene_id；未传则提交所有缺失项。
>
> **依赖**：generation worker 必须在线（图像/视频两条独立通道），worker 负责实际生成与速率控制。

### 旧 CLI → 新 tool 对照

| 旧脚本（已删除） | 新 tool |
|---|---|
| `python ... generate_storyboard.py script.json` | `mcp__arcreel__generate_storyboards({"script": "script.json"})` |
| `... --scene ID` 或 `--scene-ids ID1 ID2` | 加 `"segment_ids": [...]` |
| `... --segment-ids ...` | 加 `"segment_ids": [...]` |

## 工作流程

1. **加载项目和剧本** — 确认所有角色都有 `character_sheet` 图像
2. **生成分镜图** — 脚本自动检测 content_mode，按相邻关系串联依赖任务
3. **审核检查点** — 展示每张分镜图，用户可批准或要求重新生成
4. **更新剧本** — 更新 `storyboard_image` 路径和场景状态

## 角色一致性机制

脚本自动处理以下参考图传入，无需手动指定：
- **character_sheet**：场景中出场角色的设计图，保持外貌一致
- **scene_sheet / prop_sheet**：场景中出现的场景 / 道具设计图
- **上一张分镜图**：相邻片段默认引用，提升画面连续性
- 当片段标记 `segment_break=true` 时，跳过上一张分镜图参考

## Prompt 模板

脚本从剧本 JSON 读取以下字段构建 prompt：

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
- 使用 `--scene-ids` 重新生成失败场景
