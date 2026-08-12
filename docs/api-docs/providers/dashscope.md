# dashscope

阿里云百炼 / DashScope 文本、图片、视频与 TTS 媒体 provider。

- 总入口：[DashScope API 概览](https://help.aliyun.com/zh/model-studio/getting-started/models)
- 接口与能力：[文本生成 API](https://help.aliyun.com/zh/model-studio/qwen-api-reference/)、[图像生成](https://help.aliyun.com/zh/model-studio/image-generation)、[Qwen-Image API](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[Qwen-Image-Edit API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide)、[Wan 图像生成与编辑 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)、[视频生成](https://help.aliyun.com/zh/model-studio/use-video-generation)、[Wan 文生视频](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference)、[Wan 图生视频](https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference)、[Wan 参考生视频](https://help.aliyun.com/zh/model-studio/wan-video-to-video-api-reference)、[HappyHorse 文生视频](https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference)、[图生视频](https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference)、[参考生视频](https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference)、[Qwen TTS](https://help.aliyun.com/zh/model-studio/qwen-tts)
- 计费：[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["dashscope"]`、`lib/text_backends/openai.py::OpenAITextBackend`、`lib/image_backends/dashscope.py::DashScopeImageBackend`、`lib/video_backends/dashscope.py::DashScopeVideoBackend`、`lib/audio_backends/dashscope.py::DashScopeAudioBackend`
