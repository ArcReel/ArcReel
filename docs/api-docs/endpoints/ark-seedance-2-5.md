# ark-seedance-2-5

- Protocol: [Create a Volcengine Ark video generation task](https://www.volcengine.com/docs/82379/1520757?lang=zh), Seedance 2.5 request bodies omit `service_tier`; with a first frame, only the adaptive aspect ratio is allowed; reference-image slots accept provider asset references; see [Seedance portrait asset guide](https://docs.volcengine.com/docs/ark/seedance-portrait-asset-guide?lang=zh)
- Pricing: [Volcengine Ark pricing](https://www.volcengine.com/pricing?product=ark_bd&tab=1); estimate video tokens from the 2.5 output-pixel table
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-2-5"]`, `lib/backends/video_backends/ark.py::SEEDANCE_2_5`, `lib/backends/video_backends/ark.py::Seedance25VideoBackend`
