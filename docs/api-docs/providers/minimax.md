# minimax

MiniMax 文本、图片与视频媒体 provider。

- 总入口：[MiniMax API overview](https://platform.minimax.io/docs/api-reference/api-overview)
- 接口与能力：[Text generation](https://platform.minimax.io/docs/guides/text-generation)、[Image generation](https://platform.minimax.io/docs/guides/image-generation)、[Video generation](https://platform.minimax.io/docs/guides/video-generation)、[Video generation V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
- 计费：[Pay-as-you-go pricing](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["minimax"]`、`lib/text_backends/openai.py::OpenAITextBackend`、`lib/image_backends/minimax.py::MiniMaxImageBackend`、`lib/video_backends/minimax.py::MiniMaxVideoBackend`
