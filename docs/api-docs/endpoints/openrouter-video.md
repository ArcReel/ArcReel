# openrouter-video

- Protocol: [Video generation guide](https://openrouter.ai/docs/guides/overview/multimodal/video-generation), [Submit a task](https://openrouter.ai/docs/api/api-reference/video-generation/submit-a-video-generation-request), [Query a task](https://openrouter.ai/docs/api/api-reference/video-generation/poll-video-generation-status)
- Model capabilities: supported durations, resolutions and aspect ratios are defined by `GET /api/v1/videos/models`
- Pricing: [Models](https://openrouter.ai/models); completed-task response field `usage.cost` reports the actual cost
- Code: `lib/custom_provider/builtin_endpoints/openrouter-video.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Artifact: `unsigned_urls` points to OpenRouter own `/api/v1/videos/{jobId}/content`. It is not presigned and requires the same credentials as polling. The runtime downloads with credentials because it shares the polling origin
