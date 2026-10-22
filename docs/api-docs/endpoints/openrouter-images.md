# openrouter-images

- 协议：[Image generation 指南](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)、[Generate an image](https://openrouter.ai/docs/api/api-reference/images/generate-an-image)；文生图与图生图同走 `POST /api/v1/images`，参考图经 `input_references` 下发，响应 `data[].b64_json`
- 模型能力：各模型支持的尺寸参数（`size` / `resolution` / `aspect_ratio` / `quality`）与参考图上限以 `GET /api/v1/images/models` 的 `supported_parameters` 为准
- 计费：[Models](https://openrouter.ai/models)；响应 `usage.cost` 回报实际费用（尚未接入，按张通用口径计量）
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openrouter-images"]`、`lib/backends/image_backends/openrouter.py::OpenRouterImageBackend`
