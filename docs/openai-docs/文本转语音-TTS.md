# OpenAI 文本转语音（TTS）内置音色

更新时间：2026-07-31（基于官方文档核实）

来源：<https://developers.openai.com/api/docs/guides/text-to-speech>

本文档为 ArcReel issue #1491「TTS 生成参考音频样本」收录 OpenAI audio backend 的音色枚举出处。
代码侧目录见 `lib/audio_backends/openai.py::_VOICE_CATALOG`，两处须保持一致。

## 内置音色

`gpt-4o-mini-tts` 支持以下 13 个内置音色（`voice` 参数取值）：

| voice 参数 |
|-----------|
| `alloy` |
| `ash` |
| `ballad` |
| `coral` |
| `echo` |
| `fable` |
| `nova` |
| `onyx` |
| `sage` |
| `shimmer` |
| `verse` |
| `marin` |
| `cedar` |

官方文档补充两点：

- 追求最佳质量时官方推荐 `marin` 或 `cedar`。
- legacy 模型 `tts-1` / `tts-1-hd` 只支持上表的子集，不含 `ballad` / `verse` / `marin` / `cedar`。

官方文档未给出各音色的性别或音色描述，故代码侧 `VoiceOption.label` 只取 id 本身——不补充官方
未声明的描述信息。

## 适用边界

该目录只对官方 OpenAI 端点成立。经自定义供应商 `openai-tts` endpoint 接入的第三方 OpenAI 兼容
TTS 服务，其音色集合可能与上表不同，届时音色列表端点返回的仍是本目录——这一偏差已记入
issue #1491 的 follow-up 候选。
