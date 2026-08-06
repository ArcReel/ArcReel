---
name: split-reference-video-units
description: Reference-video 内容层：生成、修改或修复单集视频 unit step1。
---

你负责 narration / drama 的 reference-video step1。每个 unit 对应一次视频生成调用；正式结构由服务端从书写层正文派生。

## 首次生成

1. 调 workflow status，确认目标集、source 与路线。
2. 调：

```text
mcp__arcreel__split_reference_video_units({
  "episode": N,
  "source": "source/episode_N.txt"
})
```

3. 成功时验证正式 `step1_reference_units.json` current、每 unit 1–4 个 shot、references 未超限、review gate pending。
4. 工具返回 invalid draft 时读取 `.claude/references/reference-draft-repair.md`，修复同一草稿后晋升。

## 修改已有 step1

1. 调 capabilities，取得当前两套 reference unit 时长档位和参考图上限。
2. 调：

```text
mcp__arcreel__open_reference_step1_for_edit({
  "episode": N,
  "source": "source/episode_N.txt"
})
```

3. 按 `.claude/references/reference-draft-repair.md` 编辑隔离草稿并晋升。正式文件不直接 Edit。

## 内容契约

- 正文用 `镜头N：`、`@[角色]：{台词}`、`{画外音}` 三类行；
- 资产名逐字来自项目登记；正文不重复描写参考图已提供的外貌、服装与环境细节；
- `source_text` 是源文连续逐字子串；
- unit 时长取其引用状态对应档位；
- `unit_id`、`shots`、`references` 由服务端派生。

## 完成条件

正式 step1 current，隔离草稿不存在，review gate pending，已有最终剧本 stale，所有 violations 已清零。返回 unit 数、shot 数、时长、声音降级提示与 next state。
