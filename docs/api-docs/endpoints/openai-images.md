# openai-images

- 协议：[OpenAI image generation and editing](https://developers.openai.com/api/docs/guides/image-generation)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 输出 token 估算：[Calculating costs](https://developers.openai.com/api/docs/guides/image-generation#calculating-costs)，代码 `lib/backends/image_backends/openai.py::estimate_output_tokens`
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-images"]`、`lib/backends/image_backends/openai.py::OpenAIImageBackend`
