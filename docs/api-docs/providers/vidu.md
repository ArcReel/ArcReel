# vidu

Vidu 图片与视频媒体 provider。

- 总入口：[Vidu 开放平台文档](https://platform.vidu.cn/docs)
- 接口与能力：[模型地图](https://platform.vidu.cn/docs/overview/model-map.md)（按模型查询能力与参数）、[功能清单](https://platform.vidu.cn/docs/overview/function-list.md)（功能分类，不含模型 ID）、[图片生成](https://platform.vidu.cn/docs/reference-to-image)、[文生视频](https://platform.vidu.cn/docs/text2video)、[图生视频](https://platform.vidu.cn/docs/img2video)、[首尾帧生视频](https://platform.vidu.cn/docs/start-end2video)、[参考生视频](https://platform.vidu.cn/docs/reference2video)、[各型号接口文档索引](https://platform.vidu.cn/docs/llms.txt)（Vidu Q4 Preview 只有图生视频与参考生视频）
- 计费：[价格说明（积分消耗明细）](https://platform.vidu.cn/docs/overview/pricing)；出厂快照价目表见 `lib/catalog/snapshot/catalog.json`。视频按价目表计价（输出秒数或按条，按分辨率与是否参考生视频分档），不再按响应 `credits` 委托计价
- 简介来源（`lib/i18n/<locale>/providers.py` 的 `provider_desc_vidu`，只写平台是什么，不列模型与能力）：[Vidu API 平台](https://platform.vidu.cn)、[生数科技](https://www.shengshu.com/zh/)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["vidu"]`、`lib/backends/image_backends/vidu.py::ViduImageBackend`、`lib/backends/video_backends/vidu.py::ViduVideoBackend`（计量：`ViduVideoBackend.metered_usage`）
