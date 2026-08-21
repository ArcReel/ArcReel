# croco

Croco GPU 是自建 GPU 调度中枢，通过 Bearer Token 和统一任务协议提供视频、图像与音频能力。

- 官方 OpenAPI：[GPU Orchestrator 0.4.0](https://8.137.116.27:5888/openapi.json)
- 带鉴权的模型合同目录：[`GET /api/v2/models`](https://8.137.116.27:5888/api/v2/models)

## 代码入口

- 共享客户端：`lib/croco_shared.py::CrocoClient`
- 视频 backend：`lib/video_backends/croco.py::CrocoVideoBackend`
- 图像 backend：`lib/image_backends/croco.py::CrocoImageBackend`
- 音频 backend：`lib/audio_backends/croco.py::CrocoAudioBackend`
- 注册表：`lib/config/registry.py::PROVIDER_REGISTRY["croco"]`
