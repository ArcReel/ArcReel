# grok

xAI Grok / Imagine 媒体 provider。

- 总入口：[xAI API documentation](https://docs.x.ai/)
- 接口与能力：[Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text)、[Chat API](https://docs.x.ai/developers/rest-api-reference/inference/chat)、[Image generation](https://docs.x.ai/developers/model-capabilities/images/generation)、[Multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing)、[Video generation](https://docs.x.ai/developers/model-capabilities/video/generation)、[Reference-to-video](https://docs.x.ai/developers/model-capabilities/video/reference-to-video)
- 模型与计费：[Models and pricing](https://docs.x.ai/developers/models)；视频按分辨率分档的秒费率见各模型页：[grok-imagine-video](https://docs.x.ai/developers/models/grok-imagine-video)、[grok-imagine-video-1.5](https://docs.x.ai/developers/models/grok-imagine-video-1.5)、[grok-imagine-video-1.5-lite](https://docs.x.ai/developers/models/grok-imagine-video-1.5-lite)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["grok"]`、`lib/backends/text_backends/grok.py::GrokTextBackend`、`lib/backends/image_backends/grok.py::GrokImageBackend`、`lib/backends/video_backends/grok.py::GrokVideoBackend`
