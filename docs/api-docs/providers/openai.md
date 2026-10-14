# openai

OpenAI 官方文本与图片媒体 provider。文本经 Responses API 调用。视频与 TTS 仅由自定义 endpoint 复用 OpenAI 协议（见 [openai-video](../endpoints/openai-video.md)、[openai-tts](../endpoints/openai-tts.md)），不是该 provider 的内置 lane。

- 总入口：[OpenAI API documentation](https://developers.openai.com/api/docs)
- 接口与能力：[Models](https://developers.openai.com/api/docs/models)、[Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)、[Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Image generation](https://developers.openai.com/api/docs/guides/image-generation)、[Text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["openai"]`、`lib/backends/text_backends/openai_responses.py::OpenAIResponsesTextBackend`、`lib/backends/image_backends/openai.py::OpenAIImageBackend`
