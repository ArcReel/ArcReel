# minimax-h3

- Protocol: [Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Code: `lib/custom_provider/builtin_endpoints/minimax-h3.json`, `lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- Request shape: multimodal `content[]` carries first frames, last frames, reference images and reference audio by role; it shares the v1 host but uses a different path prefix (`/v2`); query responses directly include an expiring download URL
