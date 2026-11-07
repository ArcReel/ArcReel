# ark-seedance-1

- Protocol: [Create a Volcengine Ark video generation task](https://www.volcengine.com/docs/82379/1520757?lang=zh), Seedance 1.x request bodies include `service_tier`
- Pricing: [Volcengine Ark pricing](https://www.volcengine.com/pricing?product=ark_bd&tab=1); metering reports the service tier and does not estimate video tokens
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-1"]`, `lib/backends/video_backends/ark.py::SEEDANCE_1`, `lib/backends/video_backends/ark.py::Seedance1VideoBackend`
