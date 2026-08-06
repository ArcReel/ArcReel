---
name: split-narration-segments
description: Narration 内容层：生成或事务式修改逐字说书片段 step1。
---

你负责 narration storyboard 路线的 `step1_segments.json`。reference-video 路线由 `split-reference-video-units` 处理。

## 首次生成

1. 调 workflow status，确认 target episode 与 source 文件。
2. 调：

```text
mcp__arcreel__split_narration_segments({
  "episode": N,
  "source": "source/episode_N.txt"
})
```

生成工具自行查询模型能力。
3. 调 workflow status，验证 step1 current 且 review gate pending。

## 修改已有 step1

1. 取得 workflow status 中的 step1 revision。
2. 修改时长前调 `get_video_capabilities`。
3. 调 `mcp__arcreel__patch_step1`，operations 使用 `update`、`insert_after`、`remove` 或 `move_after`，并传 `expected_revision`。

## 内容契约

- `novel_text` 逐字保留源文语言、标点和顺序；除非用户明确要求修改原文文字；
- `duration_seconds` 属于当前 `supported_durations`；
- `segment_id` 当前集内唯一；
- 资产数组只引用 `project.json` 已登记名称；
- `segment_break` 只标真实切换点。

## 完成条件

- step1 current，所有 operation 已应用且 schema 合法；
- 全部 `novel_text` 能与源文连续对齐，没有重复、遗漏或新增；
- review gate pending；已有最终剧本 stale；
- 所有 requested ID 被解释。

返回片段数、总字数、总时长、修改 ID、step1 revision 与 next state。
