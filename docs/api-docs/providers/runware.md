# runware

Runware 聚合推理平台（图像 / 视频 / 音频 / 3D / 文本）。当前规划接入其**图像生成**能力（目标模型：Nano Banana Lite、Image 02）。

> 状态：图像 backend 已落地（2026-08-19）。集成调研正文见 [docs/research/runware-integration-research.md](../research/runware-integration-research.md)。

- 总入口：[Runware Docs — Introduction](https://runware.ai/docs/platform/introduction)
- 认证与连接（REST / WebSocket）：[Connection & Authentication](https://runware.ai/docs/platform/authentication)
- OpenAI 兼容范围（仅文本）：[OpenAI Compatibility](https://runware.ai/docs/platform/openai)
- 模型发现：[Model Search API](https://runware.ai/docs/platform/model-search)
- 计费：[Pricing](https://runware.ai/docs/platform/pricing)

## 代码入口

- 供应商常量：`lib/providers.py::PROVIDER_RUNWARE`
- 共享工具：`lib/runware_shared.py`（`RUNWARE_API_BASE` / `resolve_runware_api_key` / `runware_base_url` / `runware_headers`）
- 图像 backend：`lib/image_backends/runware.py::RunwareImageBackend`（T2I + I2I + 尺寸档位 + mediaStorage 上传）
- 注册表：`lib/config/registry.py::PROVIDER_REGISTRY["runware"]`（`google:nano-banana@2-lite` / `openai:gpt-image@2`）
