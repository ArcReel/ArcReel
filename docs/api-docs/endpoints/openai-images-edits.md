# openai-images-edits

- 协议：[Edit image](https://developers.openai.com/api/reference/resources/images/methods/edit)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 画质：[Customize Image Output](https://developers.openai.com/api/docs/guides/image-generation#customize-image-output)，请求参数 `quality` 见 [Edit image](https://developers.openai.com/api/reference/resources/images/methods/edit)；代码入口见 `lib/backends/image_backends/openai.py::OpenAIImageBackend.CHANNEL_ACCEPTS_QUALITY`（支持显式指定画质）与 `lib/backends/image_backends/openai.py::_resolve_openai_params`（未指定画质时按分辨率档位推导），有效画质选项见 `lib/backends/image_capabilities.py::custom_image_quality_options`
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-images-edits"]`、`lib/backends/image_backends/openai.py::OpenAIImageBackend`
