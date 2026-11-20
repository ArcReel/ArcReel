# minimax-hailuo-v1

- 协议：[MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-hailuo-v1.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 结果获取：查询响应返回 `file_id`，需再调用 `/v1/files/retrieve` 换取限时 `file.download_url`（对应端点定义的 `result` 节）
