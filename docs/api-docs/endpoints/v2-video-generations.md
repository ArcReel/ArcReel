# v2-video-generations

- 协议：[AIMLAPI video generation](https://docs.aimlapi.com/api-references/video-models)
- 边界：ArcReel 只实现多家中转站共享的 `/v2/video/generations` 公共子集，具体模型字段仍以所接中转站官方文档为准。
- 计费：[AIMLAPI pricing](https://aimlapi.com/ai-ml-api-pricing)；共享协议没有跨中转站统一价格
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["v2-video-generations"]`、`lib/video_backends/v2_video_generations.py::V2VideoGenerationsBackend`
- 任务状态：共享协议下各中转站状态串写法不一，过共享归一 `lib/video_backends/base.py::normalize_provider_status`；本协议无过期语义，`v2_video_generations.py::normalize_status` 在其上折叠为四值
