# openrouter-video

- 协议：[Video generation 指南](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)、[提交任务](https://openrouter.ai/docs/api/api-reference/video-generation/submit-a-video-generation-request)、[查询任务](https://openrouter.ai/docs/api/api-reference/video-generation/poll-video-generation-status)
- 模型能力：各模型支持的时长、分辨率与比例以 `GET /api/v1/videos/models` 为准
- 计费：[Models](https://openrouter.ai/models)；任务完成后响应 `usage.cost` 回报实际费用
- 代码：`lib/custom_provider/builtin_endpoints/openrouter-video.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 产物：`unsigned_urls` 指向 OpenRouter 自身的 `/api/v1/videos/{jobId}/content`，未预签名，下载须带与轮询相同的凭证；与轮询同源，运行时会带凭证下载
