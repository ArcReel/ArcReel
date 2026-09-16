# muapi-video

- 协议：[MuAPI video generation](https://muapi.ai/docs/video-generation)
- 默认 base URL：`https://api.muapi.ai/api/v1`
- 代码：`lib/custom_provider/builtin_endpoints/muapi-video.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 鉴权：`x-api-key` header
- 提交：`POST /{model}`，响应中的 `request_id` 作为任务 id
- 取件：`GET /predictions/{request_id}/result`，轮询 `queued` / `processing` / `completed` / `failed`，成功结果从 `outputs[0]` 下载
- 能力：文生视频与可选首帧图生视频；端点不声明尾帧、参考图或音频轨道

The endpoint intentionally sends only the common MuAPI request fields documented for the unified
video API: `prompt`, `duration`, and optional `image_url`. Model-specific controls can vary across
MuAPI's catalog and should not be sent blindly to every model.
