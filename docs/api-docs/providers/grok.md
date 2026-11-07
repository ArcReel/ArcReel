# grok

xAI Grok / Imagine media provider.

- Main entry: [xAI API documentation](https://docs.x.ai/)
- Interfaces and capabilities: [Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text), [Chat API](https://docs.x.ai/developers/rest-api-reference/inference/chat), [Image generation](https://docs.x.ai/developers/model-capabilities/images/generation), [Multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing), [Video generation](https://docs.x.ai/developers/model-capabilities/video/generation), [Reference-to-video](https://docs.x.ai/developers/model-capabilities/video/reference-to-video)
- Models and pricing: [Models and pricing](https://docs.x.ai/developers/models); see each model page for resolution-tiered per-second video prices: [grok-imagine-video](https://docs.x.ai/developers/models/grok-imagine-video), [grok-imagine-video-1.5](https://docs.x.ai/developers/models/grok-imagine-video-1.5), [grok-imagine-video-1.5-lite](https://docs.x.ai/developers/models/grok-imagine-video-1.5-lite)
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_grok`; describe only the platform, not models or capabilities): [Wikipedia: Grok](https://en.wikipedia.org/wiki/Grok_(chatbot)), [Wikipedia: SpaceXAI](https://en.wikipedia.org/wiki/SpaceXAI), [Grok API overview](https://docs.x.ai/overview)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["grok"]`, `lib/backends/text_backends/grok.py::GrokTextBackend`, `lib/backends/image_backends/grok.py::GrokImageBackend`, `lib/backends/video_backends/grok.py::GrokVideoBackend`
