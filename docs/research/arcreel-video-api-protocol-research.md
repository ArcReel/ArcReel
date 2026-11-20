# 视频 API 协议取样

本次取样截至 2026-05-27，对比了中转站与官方接口在任务提交、状态轮询、结果获取及鉴权方式上的形态差异，为 ArcReel 扩展视频调用端点提供依据。样本均来自公开接口文档，所覆盖的差异仅用于理解设计取舍，不能作为当前服务可用性的判断依据。

ArcReel 采用声明式定义的理由见 [ADR 0067](../adr/0067-custom-endpoint-declarative-definition.md)；核对现行协议可从[端点索引](../api-docs/endpoints/README.md)进入；已随版本提供的声明式定义见[内置端点目录](../../lib/custom_provider/builtin_endpoints/)。

## 提交与取件形态

| 样本 | 提交与结果 | 定义需要表达的差异 |
| --- | --- | --- |
| [OpenAI 视频接口](https://platform.openai.com/docs/guides/video-generation)、[AiHubMix](https://docs.aihubmix.com/en/api/Video-Gen)、[七牛云](https://developer.qiniu.com/aitokenapi) | 使用 `/v1/videos` 一类的资源路径。OpenAI 原生接口需单独拉取结果，七牛云样本则直接在轮询响应中返回视频地址 | 即使路径相似，仍需逐一核对请求编码、媒体字段与结果获取方式 |
| [NewAPI](https://doc.newapi.pro/api/generate-video/) | 向 `/v1/video/generations` 提交 JSON，再按任务 ID 轮询 | `metadata` 可用于承载供应商扩展字段，但是否支持透传仍取决于部署方的具体实现 |
| [AI/ML API](https://docs.aimlapi.com/api-references/video-models)、[APIMart](https://docs.apimart.ai/en/api-reference/videos/)、[xAI](https://docs.x.ai/developers/model-capabilities/video/generation) | 多个模型共用 generations 路径，通过 `model` 字段区分 | 接口路径并不决定参数集合，不同模型在首尾帧、参考图、音频和分辨率字段上可能各不相同 |
| [Kie.ai](https://docs.kie.ai/market/)、[PiAPI](https://piapi.ai/docs/) | 提交与查询操作分离，业务字段可能嵌套在 `input` 等对象内 | 请求嵌套结构、状态字段名与结果提取路径均需逐端点确认 |

## 哪些观察影响了设计

- 调研发现，大量中转站都是 JSON 请求与响应，再按任务 ID 轮询。定制集中在路径、请求字段与响应取值，适合以定义表达。
- 相似命名没有提供兼容性保证。模型名可能被中转站重新命名，内外站可以采用不同 ID，状态可以用字符串或数字表示。失败判定应依据明确的状态和响应契约。
- 轮询成功后，接口可能直接返回下载链接，也可能要求再次发起请求拉取结果。此外，不能仅凭已拿到临时下载地址就推断其有效期。
- 签名鉴权、SDK 特有传输和复杂素材组织应单独评估。声明式定义的边界由 ADR 0067 持有。

运行时 Python 插件的信任与加载模型属于独立议题 [#872](https://github.com/ArcReel/ArcReel/issues/872)。取样时记录的具体供应商参数、未采用的接入草案及插件对标内容，可查阅[原始调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/arcreel-video-api-protocol-research.md)。
