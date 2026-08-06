---
name: generate-grid
description: 宫格分镜：在 grid_storyboard 已启用时，生成、重生或查看宫格分组。
---

# 宫格分镜

适用于 narration / drama 的 `generation_mode=storyboard` 且 `grid_storyboard=true`。

```text
mcp__arcreel__generate_grid({"script": "episode_1.json", "list_only": true})
mcp__arcreel__generate_grid({"script": "episode_1.json"})
mcp__arcreel__generate_grid({
  "script": "episode_1.json",
  "scene_ids": ["E1S01", "E1S02"]
})
```

## 步骤

1. 调 workflow status 获取 missing / stale scene IDs。
2. 需要解释分组时先 `list_only`；不把列表操作表述为生成。
3. 生成时记录 requested IDs；服务端按切换点分组、生成大图并分配切块。
4. 验证每个 requested ID 的 storyboard current，宫格大图和帧文件均存在。
5. 新分镜 revision 会使对应视频 stale。

ad 项目与 reference-video 路线不使用本 skill。
