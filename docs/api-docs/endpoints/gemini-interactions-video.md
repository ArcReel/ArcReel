# gemini-interactions-video

- 协议：[Gemini Omni](https://ai.google.dev/gemini-api/docs/omni)、[Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)、[Interactions 请求与响应字段](https://ai.google.dev/api/interactions)
- 模型：[Gemini Omni Flash](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash)
- 计费：[Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["gemini-interactions-video"]`、`lib/backends/video_backends/gemini_interactions.py::GeminiInteractionsVideoBackend`

## 自定义供应商

此端点使用 AI Studio API Key。Base URL 指向兼容 Gemini Interactions API 的服务；支持分别覆盖提交与轮询路径。默认提交 `POST /v1beta/interactions`，轮询 `GET /v1beta/interactions/{task_id}`，成片从完成响应的内联视频数据取回，不另发下载请求。续跑按已保存的 interaction ID 查询，不重新提交。

`gemini-vertex-interactions-video` 使用服务账号，仍只供内置 Vertex AI 供应商使用。它与此端点的凭证不可互换。

## 依赖的契约

- 请求：`input` 图片与文本、`generation_config.video_config.task`、`response_format` 与后台轮询，覆盖文生视频、首帧、首尾帧与参考图生成；见 [Gemini Omni](https://ai.google.dev/gemini-api/docs/omni)。音轨由模型生成，请求没有独立的音轨开关，不支持参考音频。
- 取件：从完成响应的 `output_video.data` 内联解码。官方对较大成片建议的 `delivery="uri"` 此通道尚未实现。
- 计量：读取 Interactions 响应的输出视频 token，缺少模态拆分时读取总输出 token；字段见 [Interactions 请求与响应字段](https://ai.google.dev/api/interactions)。预估沿用 backend 内分辨率到视频 token 的换算，换算依据见计费页。
- 模型名：按模型名预填覆盖 `gemini-omni-1.2-*`，不改写发送的模型名；型号路由与版本说明见[模型页](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash)。

参考图张数与时长范围由模型目录条目声明。首尾帧插值的时长范围比普通生成更窄；目录的时长约束目前只能按分辨率或参考图收窄，无法表达首尾帧形态，此通道也不做该项前置校验，超出范围的首尾帧请求由上游拒绝。

HTTP 替身只验证请求序列化、取件与计量；真实账户可用性、服务端限制与实际 usage 回报仍需外部平台验证。
