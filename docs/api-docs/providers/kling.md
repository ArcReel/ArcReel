# kling

可灵 Kling 图片与视频媒体 provider。

- 总入口：[Kling AI developer documentation](https://app.klingai.com/global/dev/document-api)
- 接口与能力：总入口中的 Image Generation、Text to Video、Image to Video 与 Multi-image to Video API；新版 API（模型名在路径里）见 [API 更新公告](https://klingai.com/document-api/updates/api)与[视频能力地图](https://klingai.com/document-api/guides/capability-map/video)
- 模型说明：[Kling Video 3 model guide](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)
- 错误码：[Kling AI Error Codes](https://kling.ai/document-api/api/get-started/error-codes)
- 计费：[视频计费（国内站）](https://klingai.com/document-api/pricing/base/video)、[图像计费（国内站）](https://klingai.com/document-api/pricing/base/image)、[资源包](https://app.klingai.com/global/dev/document-api/productBilling/prePaidResourcePackage)；出厂快照价目表见 `lib/catalog/snapshot/catalog.json`
- 简介来源（`lib/i18n/<locale>/providers.py` 的 `provider_desc_kling`，只写平台是什么，不列模型与能力）：[Wikipedia: Kling AI](https://en.wikipedia.org/wiki/Kling_AI)、[可灵 AI API 文档](https://klingai.com/document-api)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["kling"]`、`lib/backends/image_backends/kling.py::KlingImageBackend`、`lib/backends/video_backends/kling.py::KlingVideoBackend`、`lib/backends/video_backends/kling_model_path.py::KlingModelPathVideoBackend`
