# minimax

MiniMax media provider for text, images, video and speech.

- Main entry: [MiniMax API overview](https://platform.minimax.io/docs/api-reference/api-overview)
- Interfaces and capabilities: [Text generation](https://platform.minimax.io/docs/guides/text-generation), [Chat Completions (OpenAI-compatible; thinking / reasoning_split parameters; thinking is embedded in content within `<think>` tags by default)](https://platform.minimax.io/docs/api-reference/text-openai-api), [Image generation](https://platform.minimax.io/docs/guides/image-generation), [Video generation](https://platform.minimax.io/docs/guides/video-generation), [Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md), [Synchronous T2A speech synthesis](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http.md)
- Pricing: [Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_minimax`; describe only the platform, not models or capabilities): [Wikipedia: MiniMax Group](https://en.wikipedia.org/wiki/MiniMax_Group), [Wikipedia: MiniMax (Xiyu Technology)](https://zh.wikipedia.org/wiki/MiniMax)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["minimax"]`, `lib/backends/text_backends/openai.py::OpenAITextBackend`, `lib/backends/image_backends/minimax.py::MiniMaxImageBackend`, `lib/backends/audio_backends/minimax.py::MiniMaxAudioBackend`, `lib/custom_provider/builtin_endpoints/minimax-hailuo-v1.json`, `lib/custom_provider/builtin_endpoints/minimax-hailuo-v1-fast.json`, `lib/custom_provider/builtin_endpoints/minimax-s2v-01.json`, `lib/custom_provider/builtin_endpoints/minimax-h3.json`
