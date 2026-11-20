# openai-video

- 协议：[OpenAI video generation](https://developers.openai.com/api/docs/guides/video-generation)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-video"]`、`lib/backends/video_backends/openai.py::OpenAIVideoBackend`
- 任务状态：官方 Sora 只返回协议文档中列出的状态串，而代理网关转发非 Sora 型号时会透传底层厂商的写法，因此统一经过共享归一函数 `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::normalize_provider_status`
