# minimax-s2v-01

- 协议：[MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-s2v-01.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 请求形状：单脸参考走 `subject_reference`，不接受首帧与 resolution / duration；取件与 minimax-hailuo-v1 同为两步
