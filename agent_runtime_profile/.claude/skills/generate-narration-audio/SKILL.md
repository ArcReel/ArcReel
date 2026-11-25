---
name: generate-narration-audio
description: 为 TTS 项目生成旁白配音。首轮自动剪辑需补齐配音、用户要求生成或重合成某个剧本条目或某集配音、或批次中断需恢复时使用。
---

# 生成旁白配音

从剧本条目的旁白正文合成语音，写回该条目的 `generated_assets.narration_audio`。覆盖分镜图生视频和参考生视频中由旁白发声的条目。只依赖剧本，无需先生成分镜图或视频。

## 工具调用

通过 MCP 工具入队：

| 操作 | 工具 |
|------|------|
| 全集补齐（默认，所有缺旁白配音的旁白条目） | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json"})` |
| 指定批量范围 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |
| 单条目重合成 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |

> **选择规则**：`segment_ids` 使用当前剧本结构的条目 ID，参考生视频传视频单元 ID。省略时只处理缺配音的旁白条目，已有配音继续复用。明确点名才重合成。
>
> **调用时机**：TTS 项目首轮自动剪辑时按缺失补齐，项目选择 TTS 即已授权，无需另行确认；
> 用户也可随时要求生成。stale 配音仅按用户要求重合成；后期配音项目无需 TTS。
> 视频生成不依赖旁白配音，两者互不等待。
> 新旁白配音完成后请用户试听确认。
>
> **依赖**：generation worker 必须在线（audio 独立通道）；项目的旁白交付方式是「TTS 配音」。TTS 模型、音色与配音语速
> 默认跟随全局；项目显式选择的配音组优先。通过 `mcp__arcreel__patch_project` 的 `settings` 修改。
> 建立项目覆盖时传入 `audio_backend`，可同时指定音色与配音语速；已有覆盖可仅修改音色或配音语速。
> 单独传 `audio_backend: null` 恢复跟随全局。设置变动只影响后续合成，用户要求时再重新合成。

## 结果

查看剧本的 `generated_assets.narration_audio` 后，按需提交补缺或重合成请求。收到 `generation_batch` 时按 `poll_after_seconds` 调用 `get_generation_batch`，等 `done: true` 后汇报逐条结果。

## 断点续传

中断后先查原批次和仍在途的任务。需要补齐且原任务已结束时，重新调用不传 `segment_ids` 的全集补齐。

## 错误处理

- 单条目失败不影响批次：工具返回 `requested / succeeded / failed / blocked` 的逐 ID 结果
- 按 `failed` / `blocked` 里每一项自带的问题码与下一步动作决定重试还是先改输入，不要读文本猜
- 需要重试的条目用 `segment_ids` 精确重试
- 工具返回 `narration_delivery_post_production` 时，告知用户这个项目的旁白由后期配音交付、视频不受影响；
  用户主动想要 in-app 旁白时，请其在 Web 项目设置里把交付方式改为 TTS 配音后重试
- 工具返回 `narration_tts_*` 项目配置问题时，同样说明后期配音这条路照常可用；**继续做视频不需要 TTS**。
  只有用户主动想要 in-app 旁白时，才引导其到 Web 项目设置补齐 TTS 模型或音色后重试
