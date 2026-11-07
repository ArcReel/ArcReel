# gemini-aistudio

Media provider for the Google Gemini Developer API / AI Studio.

- Main entry: [Gemini API documentation](https://ai.google.dev/gemini-api/docs)
- Interfaces and capabilities: [generateContent API](https://ai.google.dev/api/generate-content), [Image generation](https://ai.google.dev/gemini-api/docs/image-generation), [Video generation with Veo](https://ai.google.dev/gemini-api/docs/video), [Gemini Omni](https://ai.google.dev/gemini-api/docs/omni), [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview), [Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation)
- Pricing: [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_gemini-aistudio`; describe only the platform, not models or capabilities): [Wikipedia: Google AI Studio](https://en.wikipedia.org/wiki/Google_AI_Studio)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["gemini-aistudio"]`, `lib/backends/text_backends/gemini.py::GeminiTextBackend`, `lib/backends/image_backends/gemini.py::GeminiImageBackend`, `lib/backends/video_backends/gemini.py::GeminiVideoBackend`, `lib/backends/video_backends/gemini_interactions.py::GeminiInteractionsVideoBackend`, `lib/backends/audio_backends/gemini.py::GeminiAudioBackend`
