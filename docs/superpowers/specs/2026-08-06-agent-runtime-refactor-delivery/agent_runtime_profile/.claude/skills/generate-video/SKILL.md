---
name: generate-video
description: 视频生成：生成、重生或续传当前剧本的视频，并按项目路线自动分派。
---

# 生成视频

先读取 `.claude/references/generation-routing.md` 和完成契约。

## 调用

```text
mcp__arcreel__generate_video_episode({"script": "episode_1.json"})
mcp__arcreel__generate_video_episode({"script": "episode_1.json", "resume": true})
mcp__arcreel__generate_video_scene({"script": "episode_1.json", "scene_id": "E1S03"})
mcp__arcreel__generate_video_selected({
  "script": "episode_1.json",
  "scene_ids": ["E1S03", "E1S07"],
  "resume": true
})
```

- storyboard：ID 指向 segment / scene / shot；需要 current 分镜。
- narration / drama reference-video：按 `video_units[]` 生成，场景选择参数不缩小 unit 范围。
- ad reference-video：服务端从 `shots[]` 派生 units。
- 省略选择处理 missing 与 stale；显式选择强制重生。

工具返回时长确认清单时读取 `.claude/references/video-duration-confirmation.md`，状态为 `NEEDS_CONFIRMATION`。未经确认不入队。

## 验证

再次调用 workflow status，逐项确认视频 current、文件存在、input hash 对应当前分镜或参考集、prompt、时长和模型设置。queued 与 current 分开报告。
