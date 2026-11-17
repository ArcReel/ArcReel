# ark-seedance-2

- 协议：[火山方舟创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)，Seedance 2.0 请求体不带 `service_tier`；参考图槽位接受素材引用，见 [Seedance 人像素材使用指南](https://docs.volcengine.com/docs/ark/seedance-portrait-asset-guide?lang=zh)
- 计费：[火山方舟定价](https://www.volcengine.com/pricing?product=ark_bd&tab=1)，视频 token 按输出像素表预估
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-2"]`、`lib/backends/video_backends/ark.py::SEEDANCE_2`、`lib/backends/video_backends/ark.py::Seedance2VideoBackend`
