# ark-seedance-2-5

- 协议：[火山方舟创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)；Seedance 2.5 请求体不包含 `service_tier`，传入首帧时画幅比例仅支持自适应；参考图槽位支持素材引用，详见 [Seedance 人像素材使用指南](https://docs.volcengine.com/docs/ark/seedance-portrait-asset-guide?lang=zh)
- 计费：[火山方舟定价](https://www.volcengine.com/pricing?product=ark_bd&tab=1)；视频 token 按 2.5 版本的输出像素表预估
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["ark-seedance-2-5"]`、`lib/backends/video_backends/ark.py::SEEDANCE_2_5`、`lib/backends/video_backends/ark.py::Seedance25VideoBackend`
