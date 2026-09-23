# minimax

MiniMax 文本、图片与视频媒体 provider。

- 总入口：[MiniMax API overview](https://platform.minimax.io/docs/api-reference/api-overview)
- 接口与能力：[Text generation](https://platform.minimax.io/docs/guides/text-generation)、[Chat Completions（OpenAI 兼容；thinking / reasoning_split 参数，思考内容默认以 `<think>` 标签内嵌于 content）](https://platform.minimax.io/docs/api-reference/text-openai-api)、[Image generation](https://platform.minimax.io/docs/guides/image-generation)、[Video generation](https://platform.minimax.io/docs/guides/video-generation)、[Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["minimax"]`、`lib/backends/text_backends/openai.py::OpenAITextBackend`、`lib/backends/image_backends/minimax.py::MiniMaxImageBackend`、`lib/custom_provider/builtin_endpoints/minimax-hailuo-v1.json`、`lib/custom_provider/builtin_endpoints/minimax-hailuo-v1-fast.json`、`lib/custom_provider/builtin_endpoints/minimax-s2v-01.json`、`lib/custom_provider/builtin_endpoints/minimax-h3.json`
