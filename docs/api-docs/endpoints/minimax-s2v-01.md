# minimax-s2v-01

- Protocol: [MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Code: `lib/custom_provider/builtin_endpoints/minimax-s2v-01.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Request shape: a single-face reference uses `subject_reference`; first frames and resolution / duration are not accepted. Retrieval uses two steps, as with minimax-hailuo-v1
