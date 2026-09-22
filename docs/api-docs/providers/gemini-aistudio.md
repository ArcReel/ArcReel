# gemini-aistudio

Google Gemini Developer API / AI Studio 的媒体 provider。

- 总入口：[Gemini API documentation](https://ai.google.dev/gemini-api/docs)
- 接口与能力：[generateContent API](https://ai.google.dev/api/generate-content)、[Image generation](https://ai.google.dev/gemini-api/docs/image-generation)、[Video generation with Veo](https://ai.google.dev/gemini-api/docs/video)
- 计费：[Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["gemini-aistudio"]`、`lib/backends/text_backends/gemini.py::GeminiTextBackend`、`lib/backends/image_backends/gemini.py::GeminiImageBackend`、`lib/backends/video_backends/gemini.py::GeminiVideoBackend`
