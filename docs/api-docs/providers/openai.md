# openai

OpenAI 官方文本、图片与视频媒体 provider。TTS 仅由自定义 endpoint 复用 OpenAI 协议，不是该 provider 的内置 audio lane。

- 总入口：[OpenAI API documentation](https://developers.openai.com/api/docs)
- 接口与能力：[Models](https://developers.openai.com/api/docs/models)、[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Image generation](https://developers.openai.com/api/docs/guides/image-generation)、[Video generation](https://developers.openai.com/api/docs/guides/video-generation)、[Text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["openai"]`、`lib/text_backends/openai.py::OpenAITextBackend`、`lib/image_backends/openai.py::OpenAIImageBackend`、`lib/video_backends/openai.py::OpenAIVideoBackend`；TTS 的消费入口见 [openai-tts endpoint 索引](../endpoints/openai-tts.md)
