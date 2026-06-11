---
name: generate-narration-audio
description: 为说书模式剧本逐段生成旁白配音（TTS）。当用户说"生成旁白"、"配音"、"生成全集旁白"、想重新生成某段配音、或批量配音中断需要补齐时使用。
---

# 生成旁白配音

为说书（narration）模式剧本的每个片段，以该段 `novel_text` 原文合成一段旁白音频，
写回该段 `generated_assets.narration_audio`（输出 `audio/segment_{segment_id}.wav`）。
只依赖剧本，不依赖分镜图/视频——剧本生成后即可推进。

## 工具调用

**重要：生成旁白配音必须调用下列 MCP 工具入队。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

通过 MCP 工具入队：

| 操作 | 工具 |
|------|------|
| 全集补齐（默认，所有缺音频的段） | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json"})` |
| 指定批量范围 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |
| 单段重生 | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |

> **选择规则**：不传 `segment_ids` 则只为缺 `narration_audio` 的段入队；显式传入的段即使已有音频也会重新合成（用于换音色/语速后重生）。
>
> **依赖**：generation worker 必须在线（audio 独立通道）；audio 供应商、模型与旁白音色由用户在 Web 设置页配置。

## 工作流程

1. **状态检测** — 读取剧本，检查各段 `generated_assets.narration_audio`，统计缺失段并告知用户
2. **入队生成** — 调用 MCP 工具，任务经生成队列由 worker 处理，工具等待全部完成后返回逐段结果
3. **汇报** — 汇总成功/失败明细展示给用户

## 断点续传

中断（服务重启、任务失败、会话断开）后重新调用**不传 `segment_ids` 的全集补齐**即可：
已有音频的段自动跳过，只补缺失段，不重复扣费。

## 错误处理

- 单段失败不影响批次，工具返回逐段结果
- 失败段用 `segment_ids` 精确重试
- 工具提示未配置 audio 供应商时，引导用户到 Web 设置页配置后重试
