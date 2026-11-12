# openai-images

- 协议：[OpenAI image generation and editing](https://developers.openai.com/api/docs/guides/image-generation)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 输出 token 估算：[Calculating costs](https://developers.openai.com/api/docs/guides/image-generation#calculating-costs)，代码 `lib/backends/image_backends/openai.py::estimate_output_tokens`
- 画质：[Customize Image Output](https://developers.openai.com/api/docs/guides/image-generation#customize-image-output)，请求参数 `quality`；代码 `lib/backends/image_backends/openai.py::OpenAIImageBackend.CHANNEL_ACCEPTS_QUALITY`（接受显式画质）、`lib/backends/image_backends/openai.py::_resolve_openai_params`（未选择画质时按分辨率档位推出），有效画质可选值见 `lib/backends/image_capabilities.py::custom_image_quality_options`（自定义供应商）、`lib/backends/image_capabilities.py::builtin_image_quality_options`（内置供应商 `openai`）
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-images"]`、`lib/backends/image_backends/openai.py::OpenAIImageBackend`
