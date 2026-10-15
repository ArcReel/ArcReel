# xai-video

- 协议：[Videos REST 参考](https://docs.x.ai/developers/rest-api-reference/inference/videos)、[Video generation](https://docs.x.ai/developers/model-capabilities/video/generation)、[Reference-to-video](https://docs.x.ai/developers/model-capabilities/video/reference-to-video)
- 模型与计费：[Models and pricing](https://docs.x.ai/developers/models)
- 代码：`lib/custom_provider/builtin_endpoints/xai-video.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 任务状态：轮询状态为 `pending`、`done`、`failed`、`expired`；`done` 不在共享同义词表内，由定义的 `status_map` 映射为成功
- 与内置 `grok` 供应商的关系：后者经 xai-sdk 走 gRPC（`lib/backends/video_backends/grok.py::GrokVideoBackend`），本端点供自定义供应商经 REST 调用
