# dashscope

阿里云百炼 / DashScope 文本、图片、视频与 TTS 媒体 provider。

- 总入口：[DashScope API 概览](https://help.aliyun.com/zh/model-studio/getting-started/models)
- 接口与能力：[文本生成 API](https://help.aliyun.com/zh/model-studio/qwen-api-reference/)、[图像生成](https://help.aliyun.com/zh/model-studio/image-generation)、[视频生成](https://help.aliyun.com/zh/model-studio/use-video-generation)、[Qwen TTS](https://help.aliyun.com/zh/model-studio/qwen-tts)
- 计费：[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["dashscope"]`、`lib/text_backends/openai.py::OpenAITextBackend`、`lib/image_backends/dashscope.py::DashScopeImageBackend`、`lib/video_backends/dashscope.py::DashScopeVideoBackend`、`lib/audio_backends/dashscope.py::DashScopeAudioBackend`
