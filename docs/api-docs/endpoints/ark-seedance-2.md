# ark-seedance-2

- Protocol: [Create a Volcengine Ark video generation task](https://www.volcengine.com/docs/82379/1520757?lang=zh), Seedance 2.0 request bodies omit `service_tier`; reference-image slots accept provider asset references; see [Seedance portrait asset guide](https://docs.volcengine.com/docs/ark/seedance-portrait-asset-guide?lang=zh)
- Pricing: [Volcengine Ark pricing](https://www.volcengine.com/pricing?product=ark_bd&tab=1); estimate video tokens from the output-pixel table
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-2"]`, `lib/backends/video_backends/ark.py::SEEDANCE_2`, `lib/backends/video_backends/ark.py::Seedance2VideoBackend`
