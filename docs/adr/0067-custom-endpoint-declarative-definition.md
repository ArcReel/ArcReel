---
status: accepted
---

# 用声明式调用端点表达 JSON 提交与轮询协议

不少聚合站和中转站的差异只在 JSON 请求字段、响应取值或任务状态。允许用户维护声明式调用端点，以固定结构模板和受限 JSONPath 表达提交、轮询、可选取件与状态映射，覆盖这类协议而无需部署新的 Python 实现。SDK 接入、签名类鉴权和 multipart 等专用传输由各自通道实现。ComfyUI 专用定义见 [ADR 0081](0081-comfyui-endpoint-as-second-custom-endpoint-kind.md)，协议取样见 [视频 API 协议调研](../research/arcreel-video-api-protocol-research.md)。

声明式端点明确声明媒体类型和输入能力。图片编辑需能接收参考图，能力门控沿用 [ADR 0015](0015-image-endpoint-split-by-capability-no-fallback.md)。图片可从 JSON 响应提取 URL 或内联 base64，视频描述提交任务与后续取件。模板变量、输入形态及响应约束见 [端点定义校验器](../../packages/arcreel-market-core/README.md)。

自定义调用端点保存完整 JSON 定义。用于列表和筛选的列随定义生成，导入、导出和市场安装共用同一格式。供应商模型仍引用端点时，删除返回引用清单，由用户先处理各引用，避免级联丢掉模型配置。内置调用端点也可使用 [随版声明式定义](../../lib/custom_provider/builtin_endpoints/README.md)，继续用稳定端点名装配。
