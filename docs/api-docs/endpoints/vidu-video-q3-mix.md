# vidu-video-q3-mix

此端点用于官方直连 `viduq3-mix` 的参考生视频。支持 3～15 秒时长，成片固定有声。

- 协议：[Vidu Q3 参考生视频](https://platform.vidu.cn/docs/api-reference/video-models/vidu-q3/reference-to-video.md)中的「Q3 Mix 创建任务」与「Q3 Mix 查询生成物」（参考图张数、比例、提示词上限与时长）
- 能力：[模型地图](https://platform.vidu.cn/docs/overview/model-map.md)中的 Vidu Q3 系列
- 计费：[产品价格](https://platform.vidu.cn/pricing)中的 ViduQ3 参考生视频、[产品定价](https://platform.vidu.cn/docs/overview/pricing.md)中的 Vidu Q3
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["vidu-video-q3-mix"]`、`lib/backends/video_backends/vidu.py::ViduQ3MixVideoBackend`
