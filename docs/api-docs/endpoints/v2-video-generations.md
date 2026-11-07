# v2-video-generations

- Protocol: [AIMLAPI video generation](https://docs.aimlapi.com/api-references/video-models)
- Boundary: ArcReel implements only the common `/v2/video/generations` subset shared by multiple proxy services. Consult the connected service official documentation for model-specific fields.
- Pricing: [AIMLAPI pricing](https://aimlapi.com/ai-ml-api-pricing); the shared protocol has no uniform cross-service prices
- Code: `lib/custom_provider/builtin_endpoints/v2-video-generations.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Task statuses: proxy services use different strings under the shared protocol; apply shared normalization in `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::normalize_provider_status`; this protocol has no expiration semantics, so the definition `status_map` additionally maps `expired` into `failed`
