# minimax-h3

- 协议：[Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-h3.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 请求形状：多模态 `content[]` 按 role 承载首帧、尾帧、参考图与参考音频；与 v1 同 host 不同路径前缀（`/v2`），查询响应直接带限时下载地址
