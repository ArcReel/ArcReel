# gemini-interactions-video

- 协议：[Gemini Omni](https://ai.google.dev/gemini-api/docs/omni)、[Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)、[Interactions 请求与响应字段](https://ai.google.dev/api/interactions)
- 模型：[Gemini Omni Flash](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash)
- 计费：[Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["gemini-interactions-video"]`、`lib/backends/video_backends/gemini_interactions.py::GeminiInteractionsVideoBackend`

## 自定义供应商

此端点使用 AI Studio API Key。Base URL 指向兼容 Gemini Interactions API 的服务；支持分别覆盖提交与轮询路径。默认提交 `POST /v1beta/interactions`，轮询 `GET /v1beta/interactions/{task_id}`，成片从完成响应的内联视频数据取回，不另发下载请求。续跑按已保存的 interaction ID 查询，不重新提交。

`gemini-vertex-interactions-video` 使用服务账号，仍只供内置 Vertex AI 供应商使用。它与此端点的凭证不可互换。

## Omni 1.2 契约核对

截至 2026-11-06，官方示例使用 `gemini-omni-1.2-flash` 原名调用 `interactions.create`。现有的 `input` 图片与文本、`generation_config.video_config.task`、`response_format` 和后台轮询适用于文生视频、首帧、首尾帧与参考图生成。音轨仍由模型生成，请求没有独立的音轨开关，也不支持参考音频。完成结果仍可从 `output_video.data` 解码。

1.2 支持最多 6 张参考图，普通生成支持 3～15 秒，首尾帧插值仍为 3～10 秒；这些是模型事实，由模型目录条目声明，端点不按版本号写入能力上限。按模型名预填覆盖 `gemini-omni-1.2-*`，不改写发送的模型名。官方说明从 2026-12-03 起，1.1 的请求会自动路由到 1.2。

计量沿用 Interactions 响应的输出视频 token，缺少模态拆分时读取总输出 token；预估沿用分辨率到视频 token 的换算。HTTP 替身验证请求序列化、取件与计量，不代表真实服务的回报已验证。

官方建议超过 4 MB 的成片使用 `delivery="uri"`，此通道目前只实现内联取件。真实账户可用性、服务端限制与实际 usage 回报仍需外部平台验证；不应把离线替身结果当作真实调用成功。
