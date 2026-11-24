# grok

xAI Grok / Imagine 媒体 provider。

- 总入口：[xAI API documentation](https://docs.x.ai/)
- 接口与能力：[Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text)、[Chat API](https://docs.x.ai/developers/rest-api-reference/inference/chat)、[Image generation](https://docs.x.ai/developers/model-capabilities/images/generation)、[Multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing)、[Video generation](https://docs.x.ai/developers/model-capabilities/video/generation)、[Reference-to-video](https://docs.x.ai/developers/model-capabilities/video/reference-to-video)、[Text to Speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)
- 模型与计费：[Models and pricing](https://docs.x.ai/developers/models)；视频按分辨率分档的秒费率见各模型页：[grok-imagine-video](https://docs.x.ai/developers/models/grok-imagine-video)、[grok-imagine-video-1.5](https://docs.x.ai/developers/models/grok-imagine-video-1.5)、[grok-imagine-video-1.5-lite](https://docs.x.ai/developers/models/grok-imagine-video-1.5-lite)
- 简介来源（`lib/i18n/<locale>/providers.py` 的 `provider_desc_grok`，只写平台是什么，不列模型与能力）：[Wikipedia: Grok](https://en.wikipedia.org/wiki/Grok_(chatbot))、[Wikipedia: SpaceXAI](https://en.wikipedia.org/wiki/SpaceXAI)、[Grok API overview](https://docs.x.ai/overview)
- 图片计量：分辨率按实际请求参数上报；请求中不发送 `quality`，画质按上游默认规则（[Image generation · quality](https://docs.x.ai/developers/model-capabilities/images/generation#quality)）上报，相关声明见 `lib/backends/image_backends/grok.py::GrokImageBackend.CHANNEL_DEFAULT_QUALITY`
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["grok"]`、`lib/backends/text_backends/grok.py::GrokTextBackend`、`lib/backends/image_backends/grok.py::GrokImageBackend`、`lib/backends/video_backends/grok.py::GrokVideoBackend`、`lib/backends/audio_backends/grok.py::GrokAudioBackend`
