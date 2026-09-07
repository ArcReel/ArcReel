---
name: generate-storyboard
description: 为分镜生成分镜图。当用户说"生成分镜"、"预览分镜画面"、想重新生成某些分镜图、或剧本中有分镜缺少分镜图时使用。自动保持角色和画面连续性。
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
- **character_sheet**：场景中出场角色的资产图，保持外貌一致
- **scene_sheet / prop_sheet**：场景中出现的场景 / 道具资产图
- **商品参考（广告/短片项目）**：分镜 `products_in_shot` 非空时自动注入商品参考并排在所有参考之前（有 product sheet 时 sheet + 原图，无 sheet 时原图直注）；声明行把这些序位标为商品参考图并要求画面中的商品与之完全一致——image_prompt 无需复述商品外观
- **上一张分镜图**：相邻片段默认引用，排在参考图列表最后，只参考构图与色调
- 当片段标记 `segment_break=true` 时，跳过上一张分镜图参考

参考图对模型只是一个按顺序排列的图数组，prompt 用「图N」指认它们（N = 参考图在数组中的位置，商品在前，
然后是角色 / 场景 / 道具资产图，上一张分镜图最后）：
- 生成端在 `Style` 与 `Scene` 之间插一行 `Reference_Images` 类型声明，例如
  `图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，只参考构图与色调。`；商品分镜为
  `图1为商品参考图，画面中的商品须与之完全一致；……`
- `image_prompt.scene` 里用 `@[登记名]` 指认资产（衍生形态写 `@[角色/衍生]`），生成端按上述编号把它换成对应的「图N」；
  该名字须同时写进条目的引用字段（`characters_in_*` / `scenes` / `props` / `products_in_shot`），参考图只由引用字段决定，`@[]` 不会额外带图
- 未登记或不在引用字段里的 `@[名称]` 按名字原样发送；`generate_episode_script` / `patch_episode_script` 的回执 `warnings` 会列出这类引用，按提示补登记或补引用字段后重新生成

## Prompt 模板

生成端按项目 style、参考图列表与条目 `image_prompt` 渲染最终 prompt（`get_prompt_preview` 可查看逐字文本）：

```
Style: [项目 style]
Reference_Images: 图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，只参考构图与色调。
Scene: [image_prompt.scene，其中 @[登记名] 已换成对应的图N]
Composition:
  shot_type: [image_prompt.composition.shot_type]
  lighting: [image_prompt.composition.lighting]
  ambiance: [image_prompt.composition.ambiance]
Avoid: 水印、多余文字、Logo
```

> 画面比例通过 API 参数设置，不写入 prompt。

## 生成前检查

- [ ] 所有角色都有已批准的 character_sheet 图像
- [ ] 场景视觉描述完整
- [ ] 角色动作已指定

## 错误处理

结果结构与逐 ID 问题码见 `.claude/references/generation-results.md`。

- 单场景失败不影响批次，工具返回 `requested / succeeded / failed / blocked` 的逐 ID 结果
- 按每一项自带的 `problem.code` 与 `problem.action` 决定重试还是先改输入，不要读文本猜
- 不传 `segment_ids` 即只补缺；已失效但可用的旧分镜图会被复用，不自动重生
- 可重试的场景用 `mcp__arcreel__generate_storyboards({"script": "...", "segment_ids": [...]})` 点名重做
