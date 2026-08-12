# kling

可灵 Kling 图片与视频媒体 provider。

- 总入口：[Kling AI developer documentation](https://app.klingai.com/global/dev/document-api)
- 接口与能力：总入口中的 Image Generation、Text to Video、Image to Video 与 Multi-image to Video API
- 模型说明：[Kling Video 3 model guide](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)
- 计费：[Kling AI API Pricing](https://app.klingai.com/global/dev/document-api/productBilling/prePaidResourcePackage)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["kling"]`、`lib/image_backends/kling.py::KlingImageBackend`、`lib/video_backends/kling.py::KlingVideoBackend`
