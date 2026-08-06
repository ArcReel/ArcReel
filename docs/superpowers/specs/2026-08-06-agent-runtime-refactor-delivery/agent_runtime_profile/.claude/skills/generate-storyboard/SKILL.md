---
name: generate-storyboard
description: 单图分镜：在 storyboard 且未启用宫格时，生成、重生或编辑独立分镜图。
---

# 单图分镜

1. 读取 generation routing，确认 `generation_mode=storyboard` 且 `grid_storyboard=false`。
2. 调 workflow status 取得当前 episode 的 missing / stale 分镜 ID。
3. 用户要求修改现有图时读取 `edit-or-regenerate.md`。
4. 调：

```text
mcp__arcreel__generate_storyboards({"script": "episode_1.json"})
mcp__arcreel__generate_storyboards({
  "script": "episode_1.json",
  "segment_ids": ["E1S03", "E1S07"]
})
```

`segment_ids` 同时承载 narration segment、drama scene 与 ad shot ID。省略时处理 missing 与 stale；显式 ID 强制重生。
5. 按完成契约验证每个 requested ID current。分镜输入改变后对应视频应由 provenance 标为 stale。

项目启用宫格时使用 `/generate-grid`，不以单图工具模拟宫格。
