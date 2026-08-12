# agnes

Agnes OpenAI-compatible 文本、图片与视频媒体 provider。

- 总入口：[Agnes AI official model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models)
- 接口与能力：[Model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md) 及其中的官方模型文档链接
- 计费：官方 catalog 中各模型的 Pricing / Rate Limits 入口
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["agnes"]`、`lib/text_backends/agnes.py::AgnesTextBackend`、`lib/image_backends/agnes.py::AgnesImageBackend`、`lib/video_backends/agnes.py::AgnesVideoBackend`、`lib/agnes_shared.py::agnes_base_url`
