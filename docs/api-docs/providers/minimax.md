# minimax

MiniMax text, image, and video media provider.

- Overview: [MiniMax API overview](https://platform.minimax.io/docs/api-reference/api-overview)
- APIs and capabilities: [Text generation and multimodal chat input](https://platform.minimax.io/docs/guides/text-generation), [Image generation](https://platform.minimax.io/docs/guides/image-generation), [Video generation](https://platform.minimax.io/docs/guides/video-generation), [Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["minimax"]`, `lib/text_backends/base.py::VideoInput`, `lib/text_backends/openai.py::OpenAITextBackend`, `lib/image_backends/minimax.py::MiniMaxImageBackend`, `lib/video_backends/minimax.py::MiniMaxVideoBackend`
