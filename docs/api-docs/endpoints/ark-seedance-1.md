# ark-seedance-1

- 协议：[火山方舟创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)；Seedance 1.x 请求体包含 `service_tier`
- 计费：[火山方舟定价](https://www.volcengine.com/pricing?product=ark_bd&tab=1)；计量时上报服务档位，不预估视频 token
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-1"]`、`lib/backends/video_backends/ark.py::SEEDANCE_1`、`lib/backends/video_backends/ark.py::Seedance1VideoBackend`
