# grok

xAI Grok / Imagine 媒体 provider。

- 总入口：[xAI API documentation](https://docs.x.ai/)
- 接口与能力：[Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text)、[Chat API](https://docs.x.ai/developers/rest-api-reference/inference/chat)、[Image generation](https://docs.x.ai/developers/model-capabilities/images/generation)、[Video generation](https://docs.x.ai/developers/model-capabilities/video/generation)
- 模型与计费：[Models and pricing](https://docs.x.ai/developers/models)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["grok"]`、`lib/text_backends/grok.py::GrokTextBackend`、`lib/image_backends/grok.py::GrokImageBackend`、`lib/video_backends/grok.py::GrokVideoBackend`
