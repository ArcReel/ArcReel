---
status: accepted
---

# 用声明式调用端点表达 JSON 提交与轮询协议

不少聚合站和中转站的差异只在 JSON 请求字段、响应取值或任务状态。允许用户维护声明式调用端点，以固定结构模板和受限 JSONPath 表达提交、轮询、可选取件与状态映射，覆盖这类协议而无需部署新的 Python 实现。SDK 接入、签名类鉴权和 multipart 等专用传输由各自通道实现。ComfyUI 专用定义见 [ADR 0081](0081-comfyui-endpoint-as-second-custom-endpoint-kind.md)，协议取样见 [视频 API 协议调研](../research/arcreel-video-api-protocol-research.md)。

声明式端点明确声明媒体类型和输入能力。图片编辑需能接收参考图，能力门控沿用 [ADR 0015](0015-image-endpoint-split-by-capability-no-fallback.md)。图片可从 JSON 响应提取 URL 或内联 base64，视频描述提交任务与后续取件。模板变量、输入形态及响应约束见 [端点定义校验器](../../packages/arcreel-market-core/README.md)。

自定义调用端点保存完整 JSON 定义，用于列表和筛选的镜像列随定义派生，导入、导出和市场安装共用同一格式。供应商模型仍引用端点时，删除返回引用清单，由用户先处理各引用，避免级联丢掉模型配置。内置调用端点也可使用[随版声明式定义](../../lib/custom_provider/builtin_endpoints/README.md)，继续用稳定端点名装配，与 Python 内置通道的装配边界见 [ADR 0039](0039-builtin-backend-construction-declarative-seam.md)。

自定义端点与内置端点共享模型行的 `endpoint` 字段和查表入口，通过 `ce-` 前缀划分互不重叠的命名空间：

- 自定义端点键由本地数据库自增 ID 构造为 `ce-<id>`，解析只接受无前导零的正整数 ASCII 规范形；分享端点定义时不携带实例本地键。
- 内置端点（含随版声明式定义）禁止占用 `ce-` 前缀，由模块导入期校验强制守住。
- 查表按前缀单向分流：命中 `ce-` 前缀时仅读取自定义端点表并按定义的 `kind` 实时构造 spec，格式非法或记录已删除时直接报未知端点；非 `ce-` 前缀仅查内置注册表，两侧不互相回退。这样既保留同步内置查表，也避免全量预加载可编辑定义造成滞后或双侧试查引发歧义。
- `ce-<id>` 键本身不编码媒体类型，自定义端点的媒体类型须先解析定义再读取。代码入口见 [\_\_init\_\_.py](../../lib/custom_provider/__init__.py)、[endpoints.py](../../lib/custom_provider/endpoints.py) 与 [endpoint_resolution.py](../../lib/custom_provider/endpoint_resolution.py)。
