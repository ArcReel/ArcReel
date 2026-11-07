# minimax-hailuo-v1

- Protocol: [MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Code: `lib/custom_provider/builtin_endpoints/minimax-hailuo-v1.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Retrieval: the query response supplies `file_id`, then use `/v1/files/retrieve` to obtain an expiring `file.download_url` (the definition `result` section)
