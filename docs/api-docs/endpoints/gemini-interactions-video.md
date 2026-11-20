# gemini-interactions-video

AI Studio Gemini Interactions 视频调用端点，以 API Key 认证。Vertex AI 使用独立的服务账号端点，入口见 [Vertex AI 供应商](../providers/gemini-vertex.md)。

- 请求、视频输入组合与尾帧时长限制：[Gemini Omni](https://aistudio.google.com/docs/omni) 的生成、插值与时长章节。ArcReel 的时长约束在模型条目中声明，见 [ADR 0104](../../adr/0104-capability-sources-split-by-dimension.md)。
- 提交、轮询、内联视频获取与用量字段：[Interactions API](https://aistudio.google.com/api-reference/interactions-api) 的 beta 方法与资源定义。
- 计费与视频 token 预估：[Gemini API pricing](https://aistudio.google.com/docs/pricing) 的 Gemini Omni 条目。
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["gemini-interactions-video"]`、`lib/backends/video_backends/gemini_interactions.py::GeminiInteractionsVideoBackend`。调用端点的请求名和默认路径以注册表为准，覆盖契约见 [ADR 0101](../../adr/0101-path-override-on-provider-model.md)。
