# newapi-video

- Protocol: [Video generation API](https://doc.newapi.pro/api/generate-video/), [Kling and Jimeng formats](https://doc.newapi.pro/api/kling-jimeng/)
- Pricing: [Model pricing information](https://doc.newapi.pro/guide/pricing/); actual prices are configured by the deployment operator
- Code: `lib/custom_provider/builtin_endpoints/newapi-video.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Task statuses and response shapes: underlying maker status strings pass through and use shared normalization in `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::normalize_provider_status`; statuses, video URLs and metadata are probed using the union of paths in the definition `poll.extract`, accepting both flat responses and responses wrapped in `data`
