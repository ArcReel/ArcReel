---
name: generate-narration-audio
description: 说书旁白：在 narration storyboard 项目中生成、重生或补齐逐段 TTS。
---

# 生成说书旁白

只适用于 narration + storyboard；reference-video 没有独立 segments 音频。

```text
mcp__arcreel__generate_narration_audio({"script": "episode_1.json"})
mcp__arcreel__generate_narration_audio({
  "script": "episode_1.json",
  "segment_ids": ["E1S02", "E1S05"]
})
```

## 步骤

1. 调 workflow status 取得 missing / stale 音频 ID。
2. 用户修改项目音色或语速时，先用 `patch_project` 写 `narration_voice` / `narration_speed`；旧音频随后变为 stale。
3. 省略 IDs 处理 missing 与 stale；显式 IDs 强制重生。
4. 按完成契约验证每段 current、文件存在、input hash 对应当前 `novel_text`、音色、语速与模型。

单段失败不冒充整批成功；返回逐段 current / failed。
