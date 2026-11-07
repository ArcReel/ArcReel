# vidu

Vidu media provider for images and video.

- Main entry: [Vidu platform documentation](https://platform.vidu.cn/docs)
- Interfaces and capabilities: [Model map](https://platform.vidu.cn/docs/overview/model-map.md) (look up capabilities and parameters by model), [Feature list](https://platform.vidu.cn/docs/overview/function-list.md) (feature categories, without model IDs), [Image generation](https://platform.vidu.cn/docs/reference-to-image), [Text-to-video](https://platform.vidu.cn/docs/text2video), [Image-to-video](https://platform.vidu.cn/docs/img2video), [First-and-last-frame video generation](https://platform.vidu.cn/docs/start-end2video), [Reference-to-video](https://platform.vidu.cn/docs/reference2video), [API documentation index by model](https://platform.vidu.cn/docs/llms.txt) (Vidu Q4 Preview supports image-to-video and reference-to-video only)
- Pricing: [Pricing (credit consumption details)](https://platform.vidu.cn/docs/overview/pricing); see the factory snapshot price table in `lib/catalog/snapshot/catalog.json`. Video uses the price table (per output second or per item, tiered by resolution and reference-to-video mode), rather than delegating pricing to response `credits`
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_vidu`; describe only the platform, not models or capabilities): [Vidu API platform](https://platform.vidu.cn), [ShengShu Technology](https://www.shengshu.com/zh/)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["vidu"]`, `lib/backends/image_backends/vidu.py::ViduImageBackend`, `lib/backends/video_backends/vidu.py::ViduVideoBackend` (metering: `ViduVideoBackend.metered_usage`)
