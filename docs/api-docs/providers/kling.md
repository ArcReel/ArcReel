# kling

Kling media provider for images and video.

- Main entry: [Kling AI developer documentation](https://app.klingai.com/global/dev/document-api)
- Interfaces and capabilities: Image Generation, Text to Video, Image to Video and Multi-image to Video APIs in the main entry; for the newer API with model names in paths, see [API update announcements](https://klingai.com/document-api/updates/api) and [Video capability map](https://klingai.com/document-api/guides/capability-map/video)
- Model guide: [Kling Video 3 model guide](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)
- Error codes: [Kling AI Error Codes](https://kling.ai/document-api/api/get-started/error-codes)
- Pricing: [Video pricing (mainland China site)](https://klingai.com/document-api/pricing/base/video), [Image pricing (mainland China site)](https://klingai.com/document-api/pricing/base/image), [Resource packages](https://app.klingai.com/global/dev/document-api/productBilling/prePaidResourcePackage); see the factory snapshot price table in `lib/catalog/snapshot/catalog.json`
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_kling`; describe only the platform, not models or capabilities): [Wikipedia: Kling AI](https://en.wikipedia.org/wiki/Kling_AI), [Kling AI API documentation](https://klingai.com/document-api)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["kling"]`, `lib/backends/image_backends/kling.py::KlingImageBackend`, `lib/backends/video_backends/kling.py::KlingVideoBackend`, `lib/backends/video_backends/kling_model_path.py::KlingModelPathVideoBackend`
