# agnes

Agnes OpenAI-compatible media provider for text, images and video.

- Main entry: [Agnes AI official model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models)
- Interfaces and capabilities: [Model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md) and its links to official model documentation
- Pricing: [Pricing](https://wiki.agnes-ai.com/en/docs/pricing.md) (official pricing page; platform announcements and account invoices govern promotional prices)
- Rate limits and quotas: [Current rate limits](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md#current-rate-limits), [Current subscription quotas](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md#current-subscription-quotas)
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_agnes`; describe only the platform, not models or capabilities): [Agnes AI](https://agnes-ai.com), [Agnes AI API overview](https://agnes-ai.com/en/docs/overview)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["agnes"]`, `lib/backends/text_backends/agnes.py::AgnesTextBackend`, `lib/backends/image_backends/agnes.py::AgnesImageBackend`, `lib/backends/video_backends/agnes.py::AgnesVideoBackend`, `lib/backends/agnes_shared.py::agnes_base_url`

Video endpoints are `agnes-video-2` (`AgnesVideoBackend`) and `agnes-video-2-5` (`Agnes25VideoBackend`). The endpoint determines submission bodies, polling, audio and reference-image slots. Version 2.5 reads resolution limits from the entry's `resolutions`; when absent, only the tiers supported by the endpoint constrain it.
