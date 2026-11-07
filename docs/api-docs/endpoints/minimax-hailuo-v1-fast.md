# minimax-hailuo-v1-fast

- Protocol: [MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Code: `lib/custom_provider/builtin_endpoints/minimax-hailuo-v1-fast.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Difference from minimax-hailuo-v1: the request shape is the same, but 2.3-Fast only accepts image-to-video. Its definition makes the first frame required, so `text_to_video` is inferred as false
