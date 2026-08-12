# openai

OpenAI 官方文本、图片与视频媒体 provider。TTS 仅由自定义 endpoint 复用 OpenAI 协议，不是该 provider 的内置 audio lane。

- 总入口：[OpenAI API documentation](https://developers.openai.com/api/docs)
- 接口与能力：[Models](https://developers.openai.com/api/docs/models)、[Image generation](https://developers.openai.com/api/docs/guides/image-generation)、[Video generation](https://developers.openai.com/api/docs/guides/video-generation)、[Text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["openai"]`、`lib/text_backends/openai.py`、`lib/image_backends/openai.py`、`lib/video_backends/openai.py`、`lib/audio_backends/openai.py`
