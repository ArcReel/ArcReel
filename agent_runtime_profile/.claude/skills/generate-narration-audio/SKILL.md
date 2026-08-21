---
name: generate-narration-audio
description: 为旁白/解说剧本逐分镜生成旁白配音（TTS）。当用户要求生成或重新生成某个分镜、某集旁白配音，或批量配音中断需要补齐时使用。
---

# 生成旁白配音

为旁白/解说创作类型剧本的每个分镜，以该分镜的 `novel_text` 原文合成一段旁白配音，
写回该分镜的 `generated_assets.narration_audio`（输出 `audio/segment_{segment_id}.wav`）。
只依赖剧本，不依赖分镜图/视频——剧本生成后即可推进。

## 工具调用

**重要：生成旁白配音必须调用下列 MCP 工具入队。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

通过 MCP 工具入队：

| 操作 | 工具 |
|------|------|
| 全集补齐（默认，所有缺旁白配音的分镜） | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json"})` |
| 指定批量范围 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |
| 单分镜重生 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |

> **选择规则**：不传 `segment_ids` 则只为缺 `narration_audio` 的分镜入队——已失效但仍可用的旧配音会被复用，不自动重生；
> 显式传入的分镜即使已有旁白配音也会重新合成（用于换音色/语速后重生）。
>
> **只在用户要求时调用**：缺 TTS 不是工作流缺口，也不拦导出；后期配音方式的旁白根本不需要 TTS。
> 不要因为计划报了缺失旁白配音就自动补齐。用户在某次视频请求上选了「使用当前 TTS」时，按预检返回的
> `problems[].action` 处理——**action 是权威，不要按 `code` 自己推**：`generate_tts` 为对应分镜生成、
> `regenerate_tts`（`tts_stale` / `tts_duration_unavailable`）重新合成且旧旁白配音保留、`wait_for_task`
> 等在跑的任务结束后重查而不是重复提交。新旁白配音完成后请用户试听确认。
>
> **`regenerate_tts` 必须显式传 `segment_ids`**（取自 `problems[]` 涉及的分镜）。省略即「只补缺失」，
> 而 stale 旁白配音算可复用、会被跳过，不带 ID 重合成等于什么都没做，视频请求会一直卡在同一个问题上。
>
> **依赖**：generation worker 必须在线（audio 独立通道）；audio 供应商、模型与全局默认音色/语速由用户在 Web 设置页配置。
>
> **项目级音色/语速覆盖**：用户要求"这个项目旁白用 X 音色 / 语速 1.2"时，调
> `mcp__arcreel__patch_project({"settings": {"narration_voice": "X", "narration_speed": 1.2}})`
> 写项目级覆盖（优先于全局设置，只影响当前项目；传 `null` 清除回退全局）。改完后对已有旁白配音的分镜重新合成才会生效。

## 工作流程

1. **状态检测** — 读取剧本，检查各分镜的 `generated_assets.narration_audio`，统计缺失分镜并告知用户
2. **入队生成** — 调用 MCP 工具，任务经生成队列由 worker 处理，工具等待全部完成后返回逐分镜结果
3. **汇报** — 汇总成功/失败明细展示给用户

## 断点续传

中断（服务重启、任务失败、会话断开）后重新调用**不传 `segment_ids` 的全集补齐**即可：
已有旁白配音的分镜自动跳过，只补缺失分镜，不重复扣费。

## 错误处理

- 单分镜失败不影响批次：工具返回 `requested / succeeded / failed / blocked` 的逐 ID 结果
- 按 `failed` / `blocked` 里每一项自带的问题码与下一步动作决定重试还是先改输入，不要读文本猜
- 可重试的分镜用 `segment_ids` 精确重试
- 工具提示未配置 audio 供应商时，说明后期配音这条路照常可用、视频不受影响；**不要建议用户为了
  继续做视频去配置 TTS 供应商**。只有用户主动想要 in-app 旁白时，才引导其到 Web 设置页配置后重试
