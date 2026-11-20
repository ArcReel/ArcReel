# minimax-h3

- 协议：[Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-h3.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 请求结构：多模态 `content[]` 按 role 分别承载首帧、尾帧、参考图与参考音频；与 v1 共用域名但路径前缀不同（`/v2`），查询响应直接返回限时下载地址
