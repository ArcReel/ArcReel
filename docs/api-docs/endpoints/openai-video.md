# openai-video

- 协议：[OpenAI video generation](https://developers.openai.com/api/docs/guides/video-generation)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-video"]`、`lib/video_backends/openai.py::OpenAIVideoBackend`
- 任务状态：官方 Sora 只发协议文档列出的状态串，代理网关转发非 Sora 型号时会透传底层厂商的写法，故过共享归一 `lib/video_backends/base.py::normalize_provider_status`
