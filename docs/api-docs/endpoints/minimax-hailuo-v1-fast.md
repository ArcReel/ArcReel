# minimax-hailuo-v1-fast

- 协议：[MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-hailuo-v1-fast.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 与 minimax-hailuo-v1 的差异：请求形状相同，但 2.3-Fast 仅接受图生视频，定义把首帧声明为必需输入，`text_to_video` 据此推导为 false
