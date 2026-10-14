# kling

可灵 Kling 图片与视频媒体 provider。

- 总入口：[Kling AI developer documentation](https://app.klingai.com/global/dev/document-api)
- 接口与能力：总入口中的 Image Generation、Text to Video、Image to Video 与 Multi-image to Video API
- 模型说明：[Kling Video 3 model guide](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)
- 错误码：[Kling AI Error Codes](https://kling.ai/document-api/api/get-started/error-codes)
- 计费：[视频计费（国内站）](https://klingai.com/document-api/pricing/base/video)、[图像计费（国内站）](https://klingai.com/document-api/pricing/base/image)、[资源包](https://app.klingai.com/global/dev/document-api/productBilling/prePaidResourcePackage)；出厂快照价目表见 `lib/catalog/snapshot/catalog.json`
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["kling"]`、`lib/backends/image_backends/kling.py::KlingImageBackend`、`lib/backends/video_backends/kling.py::KlingVideoBackend`
