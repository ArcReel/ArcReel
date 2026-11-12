# openai-images-generations

- 协议：[Create image](https://developers.openai.com/api/reference/resources/images/methods/generate)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 画质：[Customize Image Output](https://developers.openai.com/api/docs/guides/image-generation#customize-image-output)，请求参数 `quality` 见 [Create image](https://developers.openai.com/api/reference/resources/images/methods/generate)；代码 `lib/backends/image_backends/openai.py::OpenAIImageBackend.CHANNEL_ACCEPTS_QUALITY`（接受显式画质）、`lib/backends/image_backends/openai.py::_resolve_openai_params`（未选择画质时按分辨率档位推出），有效画质可选值见 `lib/backends/image_capabilities.py::custom_image_quality_options`
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-images-generations"]`、`lib/backends/image_backends/openai.py::OpenAIImageBackend`
