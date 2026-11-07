# ark-agent-plan

Volcengine Ark `/api/plan/v3` media provider. This ID also appears in the separate Agent credential preset namespace; this page describes only the `PROVIDER_REGISTRY` identity.

- Main entry: [Agent/Coding Plan API reference resources](https://www.volcengine.com/docs/82379/1326340?lang=zh)
- Interfaces and capabilities: [Chat API](https://api.volcengine.com/api-docs/view?action=ChatCompletions&serviceCode=ark&version=2024-01-01), [Agent Plan image generation API](https://www.volcengine.com/docs/82379/1666945?lang=zh), [Create a video generation task](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- Plan entry: [Volcengine Ark Agent Plan](https://www.volcengine.com/product/ark)
- Agent credential presets (Anthropic protocol, in a separate namespace from this media provider): [Connect Agent Plan to Claude Code](https://www.volcengine.com/docs/82379/2373740?lang=zh), [Agent Plan supported models and harnesses](https://www.volcengine.com/docs/82379/2366394?lang=zh), [Coding Plan model configuration](https://www.volcengine.com/docs/82379/1928262?lang=zh); code `lib/agent/agent_provider_catalog.py::PRESET_PROVIDERS["ark-agent-plan"]`, `["ark-coding-plan"]`
- Description sources (`lib/i18n/<locale>/providers.py` for `provider_desc_ark-agent-plan`; describe only the platform, not models or capabilities): [Agent Plan overview](https://docs.volcengine.com/docs/82379/2366394)
- Code: `lib/config/registry.py::PROVIDER_REGISTRY["ark-agent-plan"]`, `lib/backends/text_backends/ark.py::ArkTextBackend`, `lib/backends/image_backends/ark.py::ArkImageBackend`, `lib/backends/video_backends/ark.py::ArkVideoBackend`
