# 研究资料

这里保存影响 ArcReel 决策的有日期证据，例如固定版本源码、跨产品对照和本地实测。查当前设计从相应 ADR 或实现入口进入。第三方协议、模型与价格分别查[官方文档索引](../api-docs/)和[模型目录](../../lib/catalog/)。

| 主题 | 研究证据 | 采用的决定或实现 |
| --- | --- | --- |
| 广告剧本节奏 | [时长配比依据](arcreel-ad-section-timing-research.md) | [时长分配模板](../../lib/prompts/prompt_templates/templates/partials/shared/ad_pacing/allocation.md) |
| 图像与视频提示词 | [通用提示词](arcreel-prompt-best-practices-research.md)、[资产图版式](arcreel-asset-sheet-prompt-research.md) | [资产图版式 ADR](../adr/0066-asset-sheet-layout-by-consumer.md)、[提示词模板](../../lib/prompts/prompt_templates/templates/) |
| 分镜板生视频 | [业界做法](storyboard-to-video-industry-survey.md) | [路线决策 #1604](https://github.com/ArcReel/ArcReel/pull/1604)、[工作流指南](../../website/docs/guide/workflows.md) |
| 声明式调用端点 | [视频协议取样](arcreel-video-api-protocol-research.md) | [声明式端点 ADR](../adr/0067-custom-endpoint-declarative-definition.md) |
| 创作 Agent 记忆 | [Claude 官方材料](agent-memory-definition-and-practice.md)、[Agent SDK 固定版本调查](agent-auto-memory-under-sdk.md)、[OpenClaw 取样](openclaw-text-memory.md) | [项目记忆与用户记忆 ADR](../adr/0074-agent-memory-two-level-storage.md)、[数据布局 ADR](../adr/0088-data-root-layered-projects-subdirectory.md) |
| 供应商素材与对象存储 | [上传链路源码取样](create-asset-local-upload-research.md)、[正式存储改造范围](material-object-storage-scope.md) | [素材存储与供应商关联 ADR](../adr/0108-material-storage-independent-of-provider-registration.md) |
| 使用记录趋势图 | [图表库选型实测](chart-library.md) | [趋势图组件](../../frontend/src/components/usage/UsageTrendChart.tsx) |
