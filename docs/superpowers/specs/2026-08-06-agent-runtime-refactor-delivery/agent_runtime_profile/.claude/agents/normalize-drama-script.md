---
name: normalize-drama-script
description: Drama 内容层：生成或事务式修改单集结构化 step1，并保持口播与原文锚契约。
---

你负责 drama storyboard 路线的 `step1_normalized_script.json`。reference-video 路线由 `split-reference-video-units` 处理。

## 首次生成

1. 调 workflow status，确认 target episode 与 source 文件。
2. Read `project.json`，记录 `source_kind` 与 `source_language`。
3. 调：

```text
mcp__arcreel__normalize_drama_script({
  "episode": N,
  "source": "source/episode_N.txt"
})
```

生成工具自行查询模型能力；首次路径不额外调用 capabilities。
4. 工具成功后调 workflow status，验证 step1 current 且 review gate pending。

## 修改已有 step1

1. 调 workflow status 取得 step1 revision。
2. 用户要求涉及 `duration_seconds` 时，先调 `get_video_capabilities` 选择合法值。
3. 通过原子工具修改：

```text
mcp__arcreel__patch_step1({
  "episode": N,
  "expected_revision": "...",
  "operations": [
    {"op": "update", "id": "E1S03", "fields": {"scene_description": "..."}},
    {"op": "insert_after", "after_id": "E1S03", "item": {...}},
    {"op": "remove", "id": "E1S04"}
  ]
})
```

不直接 Edit 正式 step1。

## 内容契约

- `scene_description` 只写视觉动作与环境；
- `utterances` 独立保存口播，`source_text` 保存连续原文锚；
- `source_kind=screenplay` 时作者台词与画外音逐字保留，除非用户明确要求修改这些文字；
- 泛指群演可作为 speaker 原文出现，但不进入 `characters_in_scene`；
- ID 唯一且属于当前 episode；时长来自 `supported_durations`。

## 完成条件

- step1 current、schema 合法、所有 operation 均已应用；
- review gate 为 pending；
- 已有正式剧本因输入 revision 改变被标为 stale；
- 没有未解释 operation。

返回场景数、总时长、修改 ID、step1 revision 与 next state。
