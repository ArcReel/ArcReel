# ark

火山方舟标准 `/api/v3` 媒体 provider。

- 总入口：[火山方舟文档](https://www.volcengine.com/docs/82379/?lang=zh)
- 接口与能力：[对话 Chat API](https://api.volcengine.com/api-docs/view?action=ChatCompletions&serviceCode=ark&version=2024-01-01)、[图片生成 API](https://www.volcengine.com/docs/82379/1666946?lang=zh)、[创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- 计费：[火山方舟定价](https://www.volcengine.com/pricing?product=ark_bd&tab=1)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["ark"]`、`lib/text_backends/ark.py::ArkTextBackend`、`lib/image_backends/ark.py::ArkImageBackend`、`lib/video_backends/ark.py::ArkVideoBackend`
