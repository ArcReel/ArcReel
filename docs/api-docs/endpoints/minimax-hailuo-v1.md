# minimax-hailuo-v1

- 协议：[MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-hailuo-v1.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 取件：查询响应给 `file_id`，再经 `/v1/files/retrieve` 换限时 `file.download_url`（定义的 `result` 节）
