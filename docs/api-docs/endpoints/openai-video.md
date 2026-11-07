# openai-video

- Protocol: [OpenAI video generation](https://developers.openai.com/api/docs/guides/video-generation)
- Pricing: [API pricing](https://developers.openai.com/api/docs/pricing)
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-video"]`, `lib/backends/video_backends/openai.py::OpenAIVideoBackend`
- Task statuses: official Sora uses only the documented statuses. Proxy gateways serving non-Sora models pass through underlying maker strings, so use shared normalization in `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::normalize_provider_status`
