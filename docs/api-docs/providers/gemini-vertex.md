# gemini-vertex

Google Vertex AI 上的 Gemini、Imagen 与 Veo 媒体 provider；与 AI Studio 共享部分模型文档，但认证、端点和计费独立。

- 总入口：[Generative AI on Vertex AI](https://cloud.google.com/vertex-ai/generative-ai/docs)
- 接口与能力：[Google Gen AI SDK `generate_content`](https://googleapis.github.io/python-genai/genai.html#genai.models.Models.generate_content)、[Generate images](https://cloud.google.com/vertex-ai/generative-ai/docs/image/generate-images)、[Veo video generation API](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)
- 计费：[Vertex AI generative AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["gemini-vertex"]`、`lib/text_backends/gemini.py::GeminiTextBackend`、`lib/image_backends/gemini.py::GeminiImageBackend`、`lib/video_backends/gemini.py::GeminiVideoBackend`
