# minimax-s2v-01

- 协议：[MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/custom_provider/builtin_endpoints/minimax-s2v-01.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 请求结构：单脸参考通过 `subject_reference` 传入，不接受首帧及 resolution / duration 参数；结果获取与 minimax-hailuo-v1 同样分为两步
